import csv
import io
import math
import os
import re
import secrets
import socket
import struct
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from functools import wraps

import psycopg2
import psycopg2.extras
from psycopg2 import sql
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from forecast import fetch_latest_forecasts
from prediction import PredictionRejected, enqueue_prediction, get_prediction_state


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

DB_HOST = os.environ.get("DB_HOST", "postgres_db")
DB_NAME = os.environ.get("DB_NAME", "air_quality")
DB_USER = os.environ.get("DB_USER", "admin")
DB_PASS = os.environ.get("DB_PASS", "notsosecretpass")

READING_COLUMNS = (
    "recorded_at",
    "temperature_c",
    "humidity_perc",
    "co2_ppm",
    "pm1_ugm3",
    "pm25_ugm3",
    "pm10_ugm3",
    "mq4_ppm",
    "mq137_ppm",
)
CSV_HEADERS = (
    "Timestamp (UTC)",
    "Temp (°C)",
    "Humidity (%)",
    "CO2 (ppm)",
    "PM1.0 (µg/m³)",
    "PM2.5 (µg/m³)",
    "PM10 (µg/m³)",
    "Methane (ppm)",
    "Ammonia (ppm)",
)
TABLE_HEADERS = (
    "Timestamp (PHT)",
    "Temp (°C)",
    "Humidity (%)",
    "CO2 (ppm)",
    "PM1.0 (µg/m³)",
    "PM2.5 (µg/m³)",
    "PM10 (µg/m³)",
    "Methane (ppm)",
    "Ammonia (ppm)",
)
DATE_RANGES = {"all", "today", "7d", "30d", "custom"}
PAGE_SIZES = {10, 25, 50, 100}
READING_FILTERS = (
    ("all", "All readings"),
    ("warning_critical", "Warning and critical"),
    ("critical_only", "Critical only"),
    ("ammonia", "MQ-137 Ammonia outside normal"),
    ("methane", "MQ-4 Methane outside normal"),
    ("temperature", "Temperature outside normal"),
    ("humidity", "Humidity outside normal"),
    ("co2", "MH-Z19E Carbon Dioxide outside normal"),
    ("pm1", "PM 1.0 outside normal"),
    ("pm25", "PM 2.5 outside normal"),
    ("pm10", "PM 10 outside normal"),
)
READING_FILTER_MAP = dict(READING_FILTERS)
SENSORS = (
    ("temperature_c", "Temperature", "°C"),
    ("humidity_perc", "Humidity", "%"),
    ("co2_ppm", "CO2", "ppm"),
    ("pm1_ugm3", "PM1.0", "µg/m³"),
    ("pm25_ugm3", "PM2.5", "µg/m³"),
    ("pm10_ugm3", "PM10", "µg/m³"),
    ("mq4_ppm", "Methane", "ppm"),
    ("mq137_ppm", "Ammonia", "ppm"),
)
LIVE_SENSORS = (
    ("t", "Temperature", "°C"),
    ("h", "Humidity", "%"),
    ("co2", "CO₂", "ppm"),
    ("pm1", "PM1.0", "µg/m³"),
    ("pm25", "PM2.5", "µg/m³"),
    ("pm10", "PM10", "µg/m³"),
    ("mq4_ppm", "Methane", "ppm"),
    ("mq137_ppm", "Ammonia", "ppm"),
)
# Match the forecast worker so cleanup cannot race with simulation or inference.
DELETE_WORKER_LOCK_ID = 739_783_247_071_623_201
SIMULATED_DEVICE_IDS = ("sim-farm-series", "sim-farm-good", "sim-farm-mid", "sim-farm-bad", "sim-farm-random", "python-simulator")


def parse_delete_cutoff(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value or ""):
        raise ValueError("invalid UTC timestamp")
    timestamp_format = "%Y-%m-%dT%H:%M:%S" if len(value) == 19 else "%Y-%m-%dT%H:%M"
    return datetime.strptime(value, timestamp_format).replace(tzinfo=timezone.utc)


SEVERITY_RANK = {"unavailable": 0, "normal": 1, "warning": 2, "critical": 3}
SEVERITY_BADGE = {
    "normal": "NORMAL",
    "warning": "WARNING",
    "critical": "CRITICAL",
    "unavailable": "NO DATA",
}
PHT = timezone(timedelta(hours=8))


def to_pht(dt):
    if not dt:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PHT)


def format_pht_timestamp(dt):
    """Convert UTC timestamp to Philippine Standard Time (PHT, UTC+8) in 12-hour format."""
    if not dt:
        return "—"
    pht_dt = to_pht(dt)
    return f"{pht_dt.strftime('%b')} {pht_dt.day}, {pht_dt.year}, {pht_dt.strftime('%I:%M:%S %p')}"


app.jinja_env.filters["pht_time"] = format_pht_timestamp
app.jinja_env.globals.update(to_pht=to_pht, format_pht_timestamp=format_pht_timestamp)


def classify_sensor_reading(column, value):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "unavailable"
    val = float(value)
    if column == "temperature_c":
        return "critical" if (val < 18 or val > 35) else ("warning" if (val < 21 or val > 32) else "normal")
    if column == "humidity_perc":
        if val < 0 or val > 100:
            return "unavailable"
        return "critical" if (val < 40 or val > 80) else ("warning" if (val < 50 or val > 70) else "normal")
    if val < 0:
        return "unavailable"
    if column == "co2_ppm":
        return "critical" if val > 3000 else ("warning" if val > 2500 else "normal")
    if column == "pm1_ugm3":
        return "critical" if val > 35 else ("warning" if val > 10 else "normal")
    if column == "pm25_ugm3":
        return "critical" if val > 150 else ("warning" if val > 35 else "normal")
    if column == "pm10_ugm3":
        return "critical" if val > 1000 else ("warning" if val > 150 else "normal")
    if column == "mq4_ppm":
        return "critical" if val > 20 else ("warning" if val > 10 else "normal")
    if column == "mq137_ppm":
        return "critical" if val > 5000 else ("warning" if val > 1000 else "normal")
    return "unavailable"


def reading_direction(value, previous_value):
    if value is None or previous_value is None:
        return None
    try:
        current, previous = float(value), float(previous_value)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(current) and math.isfinite(previous)):
        return None
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def enrich_metrics(metrics):
    for item in metrics:
        severity = classify_sensor_reading(item["column"], item.get("value"))
        item["severity"] = severity
        item["badge"] = SEVERITY_BADGE[severity]
        item["direction"] = reading_direction(item.get("value"), item.get("previous"))
    return metrics


def group_severity(items):
    known = [item.get("severity") for item in items if item.get("severity") in {"normal", "warning", "critical"}]
    severity = max(known, key=lambda sev: SEVERITY_RANK[sev]) if known else "unavailable"
    return severity, SEVERITY_BADGE[severity]


def classify_reading_row(row):
    """Determine the worst severity across all sensor values in a data row."""
    if not row or len(row) < 2:
        return "unavailable", "NO DATA"
    severities = [
        classify_sensor_reading(col, val)
        for col, val in zip(READING_COLUMNS[1:], row[1:])
    ]
    valid_severities = [s for s in severities if s != "unavailable"]
    if not valid_severities:
        return "unavailable", "NO DATA"
    worst = max(valid_severities, key=lambda s: SEVERITY_RANK.get(s, 0))
    return worst, SEVERITY_BADGE.get(worst, "NORMAL")


def fetch_dashboard_data(sensor):
    """Measured analytics; saved forecasts are fetched separately by the panel."""
    now = datetime.now(timezone.utc)
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), MIN(recorded_at), MAX(recorded_at) FROM air_quality_logs")
            total, earliest, latest = cursor.fetchone()
            if latest is None:
                start, end = now - timedelta(hours=24), now
                recorded_start, recorded_end = now - timedelta(hours=1), now
                latest_row = None
                summary = (None, None, None, 0) * len(SENSORS)
                series = []
                recorded_series = []
            else:
                start = earliest.replace(minute=0, second=0, microsecond=0)
                end = latest.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                recorded_start, recorded_end = latest - timedelta(hours=1), latest
                lead_cols = ", ".join(f"LEAD({col}) OVER (ORDER BY recorded_at DESC, id DESC)" for col, _, _ in SENSORS)
                cursor.execute(
                    f"SELECT {', '.join(READING_COLUMNS)}, {lead_cols} FROM ("
                    f"SELECT id, {', '.join(READING_COLUMNS)} FROM air_quality_logs "
                    "ORDER BY recorded_at DESC, id DESC LIMIT 2"
                    ") AS recent_two ORDER BY recorded_at DESC, id DESC LIMIT 1"
                )
                latest_row = cursor.fetchone()
                aggregates = ", ".join(f"MIN({col}), AVG({col}), MAX({col}), COUNT({col})" for col, _, _ in SENSORS)
                cursor.execute(f"SELECT {aggregates} FROM air_quality_logs WHERE recorded_at >= %s AND recorded_at <= %s",
                               (earliest, latest))
                summary = cursor.fetchone()
                # sensor is selected from SENSORS by the route, never interpolated from raw input.
                cursor.execute(f"SELECT date_trunc('hour', recorded_at), AVG({sensor}) FROM air_quality_logs "
                               "WHERE recorded_at >= %s AND recorded_at <= %s "
                               "GROUP BY 1 ORDER BY 1", (earliest, latest))
                series = cursor.fetchall()
                all_sensor_cols = ", ".join(col for col, _, _ in SENSORS)
                cursor.execute(f"SELECT recorded_at, {sensor}, device_id, {all_sensor_cols} FROM air_quality_logs "
                               "WHERE recorded_at >= %s AND recorded_at <= %s "
                               "ORDER BY device_id NULLS FIRST, recorded_at, id",
                               (recorded_start, recorded_end))
                recorded_series = cursor.fetchall()
            metrics = []
            for index, (column, label, unit) in enumerate(SENSORS):
                low, average, high, count = summary[index * 4:index * 4 + 4]
                metrics.append(dict(column=column, label=label, unit=unit,
                                    value=latest_row[index + 1] if latest_row else None,
                                    previous=latest_row[index + 1 + len(SENSORS)] if latest_row and len(latest_row) > index + 1 + len(SENSORS) else None,
                                    minimum=low, average=average, maximum=high, count=count))
    return dict(total=total, earliest=earliest, latest=latest, metrics=enrich_metrics(metrics), series=series,
                start=start, end=end, recorded_series=recorded_series,
                recorded_start=recorded_start, recorded_end=recorded_end)


def build_trend(series, start, end=None, hourly=True):
    """Plot hourly averages or individual readings, separating gaps and devices."""
    duration = (end - start) if end is not None else timedelta(hours=24 if hourly else 1)
    if duration.total_seconds() <= 0:
        duration = timedelta(hours=1)
    max_gap = timedelta(hours=1) if hourly else timedelta(minutes=2)
    valid = [float(row[1]) for row in series if row[1] is not None and math.isfinite(float(row[1]))]
    if not valid:
        return dict(segments=[], points=[], low=None, high=None)
    low, high = min(valid), max(valid)
    padding = (high - low) * .1 or 1
    bottom, top = low - padding, high + padding
    segments, points, segment = [], [], []
    previous = None
    previous_device = None
    for timestamp, value, *device in series:
        device = device[0] if device else None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if value is None or not math.isfinite(float(value)):
            if segment:
                segments.append(" ".join(segment))
            segment, previous = [], None
            continue
        if previous is not None and (timestamp - previous > max_gap or device != previous_device):
            segments.append(" ".join(segment))
            segment = []
        # Put each hourly average at the midpoint of its actual time window.
        position = timestamp
        if hourly:
            position = max(timestamp, start) + (min(timestamp + timedelta(hours=1), start + duration) - max(timestamp, start)) / 2
        x = 60 + (position - start).total_seconds() / duration.total_seconds() * 700
        y = 210 - (float(value) - bottom) / (top - bottom) * 180
        point = dict(x=round(x, 2), y=round(y, 2), time=timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                     value=round(float(value), 2), device=device or "Unspecified device")
        points.append(point)
        segment.append(f"{point['x']},{point['y']}")
        previous = timestamp
        previous_device = device
    if segment:
        segments.append(" ".join(segment))
    return dict(segments=segments, points=points, low=round(bottom, 2), high=round(top, 2))


SENSOR_CHART_SPECS = (
    dict(
        column="mq137_ppm",
        chart_id="chart-mq137_ppm",
        aliases=(),
        title="Ammonia",
        unit="ppm",
        scale="linear",
        base_low=0.0,
        base_high=5500.0,
        ticks=(0, 1000, 3000, 5000),
        threshold_summary="Normal \u2264 1,000 ppm \u00b7 Warning up to 5,000 ppm \u00b7 Critical above 5,000 ppm",
        series=(("mq137_ppm", "Ammonia", "ppm", "#38bdf8"),),
        thresholds=(
            ("normal", "Normal", 500.0),
            ("warning", "Warning", 1000.0),
            ("critical", "Critical", 5000.0),
        ),
    ),
    dict(
        column="mq4_ppm",
        chart_id="chart-mq4_ppm",
        aliases=(),
        title="Methane",
        unit="ppm",
        scale="linear",
        base_low=0.0,
        base_high=24.0,
        ticks=(0, 5, 10, 15, 20),
        threshold_summary="Normal \u2264 10 ppm \u00b7 Warning up to 20 ppm \u00b7 Critical above 20 ppm",
        series=(("mq4_ppm", "Methane", "ppm", "#a78bfa"),),
        thresholds=(
            ("normal", "Normal", 5.0),
            ("warning", "Warning", 10.0),
            ("critical", "Critical", 20.0),
        ),
    ),
    dict(
        column="temperature_c",
        chart_id="chart-temperature_c",
        aliases=("chart-humidity_perc",),
        title="Temperature and Humidity",
        unit="\u00b0C / %",
        scale="linear",
        base_low=10.0,
        base_high=90.0,
        ticks=(18, 32, 50, 70, 80),
        threshold_summary="Temp normal 21\u201332 \u00b0C (Crit < 18 or > 35 \u00b0C) \u00b7 Humidity normal 50\u201370% (Crit < 40% or > 80%)",
        series=(
            ("temperature_c", "Temperature", "\u00b0C", "#22d3ee"),
            ("humidity_perc", "Humidity", "%", "#c084fc"),
        ),
        thresholds=(),
    ),
    dict(
        column="pm1_ugm3",
        chart_id="chart-pm1_ugm3",
        aliases=("chart-pm25_ugm3", "chart-pm10_ugm3"),
        title="Particulate matter",
        unit="\u00b5g/m\u00b3",
        scale="log",
        base_low=1.0,
        base_high=1500.0,
        ticks=(1, 10, 35, 150, 1000),
        threshold_summary="PM 1.0 warning > 10, critical > 35 \u00b7 PM 2.5 warning > 35, critical > 150 \u00b7 PM 10 warning > 150, critical > 1,000 \u00b5g/m\u00b3",
        series=(
            ("pm1_ugm3", "PM 1.0", "\u00b5g/m\u00b3", "#22d3ee"),
            ("pm25_ugm3", "PM 2.5", "\u00b5g/m\u00b3", "#818cf8"),
            ("pm10_ugm3", "PM 10", "\u00b5g/m\u00b3", "#c084fc"),
        ),
        thresholds=(),
    ),
    dict(
        column="co2_ppm",
        chart_id="chart-co2_ppm",
        aliases=(),
        title="Carbon Dioxide",
        unit="ppm",
        scale="linear",
        base_low=0.0,
        base_high=3400.0,
        ticks=(0, 1500, 2500, 3000),
        threshold_summary="Normal \u2264 2,500 ppm \u00b7 Warning up to 3,000 ppm \u00b7 Critical above 3,000 ppm",
        series=(("co2_ppm", "Carbon Dioxide", "ppm", "#a78bfa"),),
        thresholds=(
            ("normal", "Normal", 1500.0),
            ("warning", "Warning", 2500.0),
            ("critical", "Critical", 3000.0),
        ),
    ),
)


def _scale_y(value, bottom, top, scale):
    if scale == "log":
        lo = math.log10(max(1.0, bottom))
        hi = math.log10(max(lo + 0.1, top))
        val = math.log10(max(1.0, float(value)))
        return 210 - (val - lo) / (hi - lo) * 180
    return 210 - (float(value) - bottom) / (top - bottom) * 180


def _parse_utc_timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _resolve_chart_forecast(forecast, end):
    if end is None or not isinstance(forecast, dict) or forecast.get("status") != "available":
        return None
    series = forecast.get("series")
    if not isinstance(series, dict) or not series:
        return None
    cutoff = _parse_utc_timestamp(forecast.get("input_cutoff_at"))
    target = _parse_utc_timestamp(forecast.get("target_at"))
    end_utc = _parse_utc_timestamp(end)
    if cutoff is None or target is None or end_utc is None:
        return None
    if target <= cutoff or target <= end_utc or cutoff < end_utc - timedelta(minutes=15):
        return None
    return dict(cutoff=cutoff, target=target, device_id=forecast.get("device_id"), series=series)


def build_chart_ticks(start, end, has_forecast=False):
    if start is None or end is None:
        return []
    start_utc = _parse_utc_timestamp(start)
    end_utc = _parse_utc_timestamp(end)
    if start_utc is None or end_utc is None:
        return []
    if not has_forecast:
        return [
            (60 + minute / 60 * 700, to_pht(start_utc + timedelta(minutes=minute)).strftime("%I:%M %p"))
            for minute in (0, 15, 30, 45, 60)
        ]
    total_seconds = (end_utc - start_utc).total_seconds()
    if total_seconds <= 0:
        total_seconds = 3600.0
    steps = 6
    return [
        (
            round(60 + step / steps * 700, 2),
            to_pht(start_utc + timedelta(seconds=total_seconds * step / steps)).strftime("%I:%M %p"),
        )
        for step in range(steps + 1)
    ]


def build_sensor_charts(recorded_series, start, end, selected_sensor, metrics_by_key, forecast=None):
    col_index = {col: idx for idx, (col, _, _) in enumerate(SENSORS)}
    active_forecast = _resolve_chart_forecast(forecast, end)
    chart_end = active_forecast["target"] if active_forecast else end
    duration = (chart_end - start) if (start is not None and chart_end is not None) else timedelta(hours=1)
    if duration.total_seconds() <= 0:
        duration = timedelta(hours=1)
    max_gap = timedelta(minutes=2)
    charts = []
    for spec in SENSOR_CHART_SPECS:
        scale = spec["scale"]
        series_defs = spec["series"]
        series_cols = {s[0] for s in series_defs}
        all_valid = []
        raw_by_col = {}
        forecast_by_col = {}
        for col, _, _, _ in series_defs:
            idx = col_index[col]
            s_rows = []
            for row in recorded_series:
                if len(row) >= 3 + len(SENSORS):
                    val = row[3 + idx]
                elif col == selected_sensor and len(row) >= 2:
                    val = row[1]
                else:
                    val = None
                s_rows.append((row[0], val, row[2] if len(row) > 2 else None))
            raw_by_col[col] = s_rows
            all_valid.extend(float(r[1]) for r in s_rows if r[1] is not None and math.isfinite(float(r[1])))
            if active_forecast:
                f_vals = active_forecast["series"].get(col)
                if isinstance(f_vals, list) and f_vals:
                    clean_vals = [float(v) for v in f_vals if v is not None and math.isfinite(float(v))]
                    if len(clean_vals) == len(f_vals):
                        forecast_by_col[col] = clean_vals
                        all_valid.extend(clean_vals)
        bottom = spec["base_low"]
        top = spec["base_high"]
        if all_valid:
            if min(all_valid) < bottom:
                bottom = max(1.0 if scale == "log" else min(all_valid) - 2.0, min(all_valid) * 0.9)
            if max(all_valid) > top:
                top = max(all_valid) * 1.08
        series_list = []
        all_points = []
        for col, s_label, s_unit, s_color in series_defs:
            segments, points, segment = [], [], []
            previous, previous_device = None, None
            anchor, preferred_anchor = None, None
            for timestamp, value, device in raw_by_col[col]:
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if value is None or not math.isfinite(float(value)):
                    if segment:
                        segments.append(" ".join(segment))
                    segment, previous = [], None
                    continue
                if previous is not None and (timestamp - previous > max_gap or device != previous_device):
                    segments.append(" ".join(segment))
                    segment = []
                x = 60 + (timestamp - start).total_seconds() / duration.total_seconds() * 700
                y = _scale_y(value, bottom, top, scale)
                point = dict(
                    x=round(x, 2),
                    y=round(y, 2),
                    time=timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    value=round(float(value), 2),
                    device=device or "Unspecified device",
                    series_label=s_label,
                    unit=s_unit,
                )
                points.append(point)
                all_points.append(point)
                segment.append(f"{point['x']},{point['y']}")
                anchor = (timestamp, point["x"], point["y"])
                if active_forecast and device == active_forecast.get("device_id"):
                    preferred_anchor = anchor
                previous, previous_device = timestamp, device
            if segment:
                segments.append(" ".join(segment))
            forecast_segments = []
            f_vals = forecast_by_col.get(col)
            if active_forecast and f_vals:
                cutoff = active_forecast["cutoff"]
                chosen_anchor = preferred_anchor or anchor
                f_segment = []
                if chosen_anchor is not None:
                    anchor_ts, anchor_x, anchor_y = chosen_anchor
                    f_segment.append(f"{anchor_x},{anchor_y}")
                else:
                    anchor_ts = None
                for step_idx, f_val in enumerate(f_vals, start=1):
                    step_ts = cutoff + timedelta(minutes=step_idx)
                    if anchor_ts is not None and step_ts <= anchor_ts:
                        continue
                    fx = round(60 + (step_ts - start).total_seconds() / duration.total_seconds() * 700, 2)
                    fy = round(_scale_y(f_val, bottom, top, scale), 2)
                    f_segment.append(f"{fx},{fy}")
                if len(f_segment) >= 2:
                    forecast_segments.append(" ".join(f_segment))
            m = metrics_by_key.get(col, {})
            series_list.append(dict(
                column=col,
                label=s_label,
                unit=s_unit,
                color=s_color,
                segments=segments,
                forecast_segments=forecast_segments,
                points=points,
                latest_value=m.get("value"),
                severity=m.get("severity", "unavailable"),
            ))
        threshold_lines = []
        for kind, label_text, level_val in spec["thresholds"]:
            if bottom <= level_val <= top:
                ty = round(_scale_y(level_val, bottom, top, scale), 2)
                threshold_lines.append(dict(kind=kind, label=label_text, value=level_val, y=ty))
        y_ticks = []
        for tick_val in spec["ticks"]:
            if bottom <= tick_val <= top:
                ty = round(_scale_y(tick_val, bottom, top, scale), 2)
                y_ticks.append((ty, f"{tick_val:,}" if tick_val >= 1000 else str(tick_val)))
        group_sev, group_badge = group_severity([metrics_by_key.get(col, {}) for col, _, _, _ in series_defs])
        charts.append(dict(
            column=spec["column"],
            chart_id=spec["chart_id"],
            aliases=spec["aliases"],
            title=spec["title"],
            unit=spec["unit"],
            threshold_summary=spec["threshold_summary"],
            severity=group_sev,
            badge=group_badge,
            is_selected=(selected_sensor in series_cols),
            series_list=series_list,
            points=all_points,
            y_ticks=y_ticks,
            threshold_lines=threshold_lines,
        ))
    return charts


def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER,
                            password=DB_PASS, connect_timeout=5, options="-c timezone=UTC")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def parse_positive_int(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def parse_filters(args):
    date_range = args.get("date_range", "all")
    if date_range not in DATE_RANGES:
        date_range = "all"

    per_page = parse_positive_int(args.get("per_page"), 25)
    if per_page not in PAGE_SIZES:
        per_page = 25

    reading_filter = args.get("filter", "all").strip().lower()
    filter_aliases = {
        "mq137": "ammonia",
        "mq137_ammonia": "ammonia",
        "mq4_ammonia": "ammonia",
        "mq4": "methane",
        "mq4_methane": "methane",
        "mq137_methane": "methane",
        "temp": "temperature",
        "hum": "humidity",
        "co2_ppm": "co2",
        "carbon_dioxide": "co2",
        "pm1_0": "pm1",
        "pm2_5": "pm25",
        "pm10_0": "pm10",
        "warning": "warning_critical",
        "critical": "critical_only",
    }
    reading_filter = filter_aliases.get(reading_filter, reading_filter)
    if reading_filter not in READING_FILTER_MAP:
        reading_filter = "all"

    return {
        "date_range": date_range,
        "start_date": args.get("start_date", "").strip(),
        "end_date": args.get("end_date", "").strip(),
        "filter": reading_filter,
        "q": args.get("q", "").strip()[:100],
        "page": parse_positive_int(args.get("page"), 1),
        "per_page": per_page,
    }


def build_pagination_items(current_page, total_pages):
    """Return a sliding window of at most four page numbers."""
    if total_pages <= 4:
        return list(range(1, total_pages + 1))

    current_page = min(max(current_page, 1), total_pages)
    first_page = min(max(current_page - 1, 1), total_pages - 3)
    return list(range(first_page, first_page + 4))


def build_where_clause(filters):
    conditions = []
    params = []
    error = None
    now = datetime.now(timezone.utc)

    if filters["date_range"] == "today":
        conditions.append("recorded_at >= %s")
        params.append(datetime.combine(now.date(), time.min, tzinfo=timezone.utc))
    elif filters["date_range"] == "7d":
        conditions.append("recorded_at >= %s")
        params.append(now - timedelta(days=7))
    elif filters["date_range"] == "30d":
        conditions.append("recorded_at >= %s")
        params.append(now - timedelta(days=30))
    elif filters["date_range"] == "custom":
        try:
            if not filters["start_date"] or not filters["end_date"]:
                raise ValueError
            start = datetime.strptime(filters["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(filters["end_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if start > end:
                raise ValueError
            conditions.extend(("recorded_at >= %s", "recorded_at < %s"))
            params.extend((start, end + timedelta(days=1)))
        except ValueError:
            error = "Choose a valid date range. The start date must not be after the end date."

    reading_filter = filters.get("filter", "all")
    if reading_filter in ("warning_critical", "warning"):
        conditions.append(
            "(temperature_c < 21 OR temperature_c > 32 "
            "OR humidity_perc < 50 OR humidity_perc > 70 "
            "OR co2_ppm > 2500 "
            "OR pm1_ugm3 > 10 "
            "OR pm25_ugm3 > 35 "
            "OR pm10_ugm3 > 150 "
            "OR mq4_ppm > 10 "
            "OR mq137_ppm > 1000)"
        )
    elif reading_filter in ("critical_only", "critical"):
        conditions.append(
            "(temperature_c < 18 OR temperature_c > 35 "
            "OR humidity_perc < 40 OR humidity_perc > 80 "
            "OR co2_ppm > 3000 "
            "OR pm1_ugm3 > 35 "
            "OR pm25_ugm3 > 150 "
            "OR pm10_ugm3 > 1000 "
            "OR mq4_ppm > 20 "
            "OR mq137_ppm > 5000)"
        )
    elif reading_filter == "ammonia":
        conditions.append("mq137_ppm > 1000")
    elif reading_filter == "methane":
        conditions.append("mq4_ppm > 10")
    elif reading_filter == "temperature":
        conditions.append("(temperature_c < 21 OR temperature_c > 32)")
    elif reading_filter == "humidity":
        conditions.append("(humidity_perc < 50 OR humidity_perc > 70)")
    elif reading_filter == "co2":
        conditions.append("co2_ppm > 2500")
    elif reading_filter == "pm1":
        conditions.append("pm1_ugm3 > 10")
    elif reading_filter == "pm25":
        conditions.append("pm25_ugm3 > 35")
    elif reading_filter == "pm10":
        conditions.append("pm10_ugm3 > 150")

    if filters["q"]:
        searchable = ("recorded_at",) + READING_COLUMNS[1:]
        conditions.append(
            "(" + " OR ".join(f"COALESCE(CAST({column} AS TEXT), '') ILIKE %s" for column in searchable) + ")"
        )
        params.extend([f"%{filters['q']}%"] * len(searchable))

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, params, error


def fetch_readings(filters, paginate=True):
    where, params, filter_error = build_where_clause(filters)
    if filter_error:
        return [], 0, filter_error

    columns = ", ".join(READING_COLUMNS)
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM air_quality_logs{where}", params)
            total = cursor.fetchone()[0]

            query = f"SELECT {columns} FROM air_quality_logs{where} ORDER BY recorded_at DESC"
            query_params = list(params)
            if paginate:
                query += " LIMIT %s OFFSET %s"
                query_params.extend((filters["per_page"], (filters["page"] - 1) * filters["per_page"]))
            cursor.execute(query, query_params)
            return cursor.fetchall(), total, None


def shell_context(page_name):
    return {
        "page_name": page_name,
        "username": session.get("username", "Admin"),
        "role": session.get("role", "admin"),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("login.html", error="Enter your username and password."), 400
        try:
            with closing(get_db_connection()) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT password_hash, role FROM dashboard_users WHERE username = %s",
                        (username,),
                    )
                    user = cursor.fetchone()

            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session.update(logged_in=True, username=username, role=user["role"])
                return redirect(url_for("dashboard"))
            error = "Invalid username or password."
        except psycopg2.Error:
            app.logger.exception("Login database error")
            error = "Unable to connect to the database. Please try again."

    return render_template("login.html", error=error)


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    session.setdefault("prediction_token", secrets.token_hex(32))
    if session.get("role") == "admin":
        session.setdefault("shutdown_token", secrets.token_hex(32))
    sensor_param = request.args.get("sensor")
    sensor = sensor_param if sensor_param in {column for column, _, _ in SENSORS} else None
    query_sensor = sensor or "temperature_c"
    now = datetime.now(timezone.utc)
    data = dict(total=None, earliest=None, latest=None, metrics=[dict(column=c, label=l, unit=u, value=None,
                minimum=None, average=None, maximum=None, count=0) for c, l, u in SENSORS], series=[],
                start=now - timedelta(hours=24), end=now, recorded_series=[],
                recorded_start=now - timedelta(hours=1), recorded_end=now)
    database_error = False
    try:
        data = fetch_dashboard_data(query_sensor)
    except psycopg2.Error:
        app.logger.exception("Unable to load collection summary")
        database_error = True
    enrich_metrics(data["metrics"])
    metrics_by_key = {m["column"]: m for m in data["metrics"]}
    temp_humidity_severity, temp_humidity_badge = group_severity(
        [metrics_by_key.get("temperature_c"), metrics_by_key.get("humidity_perc")]
    )
    pm_severity, pm_badge = group_severity(
        [metrics_by_key.get("pm1_ugm3"), metrics_by_key.get("pm25_ugm3"), metrics_by_key.get("pm10_ugm3")]
    )
    sensor_charts = build_sensor_charts(
        data["recorded_series"], data["recorded_start"], data["recorded_end"], query_sensor, metrics_by_key
    )
    selected_metric = next((m for m in data["metrics"] if m["column"] == sensor), None) if sensor else None
    return render_template("dashboard.html", **shell_context("Dashboard"),
                           sensor_charts=sensor_charts,
                           metrics_by_key=metrics_by_key,
                           temp_humidity_group=dict(severity=temp_humidity_severity, badge=temp_humidity_badge),
                           pm_group=dict(severity=pm_severity, badge=pm_badge),
                           **data, selected=selected_metric,
                           trend=build_trend(data["series"], data["start"], data["end"]), database_error=database_error,
                           recorded_trend=build_trend(data["recorded_series"], data["recorded_start"],
                                                      data["recorded_end"], hourly=False),
                           recorded_ticks=build_chart_ticks(data["recorded_start"], data["recorded_end"]),
                           live_sensors=LIVE_SENSORS)


def _dbus_string_field(code, kind, value):
    encoded = value.encode("utf-8")
    return struct.pack("<B3sI", code, b"\x01" + kind.encode("ascii") + b"\x00", len(encoded)) + encoded + b"\x00"


def _dbus_message(serial, path, interface, member, destination, signature="", body=b""):
    fields = [
        _dbus_string_field(1, "o", path),
        _dbus_string_field(2, "s", interface),
        _dbus_string_field(3, "s", member),
        _dbus_string_field(6, "s", destination),
    ]
    if signature:
        sig_bytes = signature.encode("ascii")
        fields.append(struct.pack("<B3sB", 8, b"\x01g\x00", len(sig_bytes)) + sig_bytes + b"\x00")
    array_buf = bytearray()
    for index, field in enumerate(fields):
        if index:
            array_buf.extend(b"\x00" * ((-len(array_buf)) % 8))
        array_buf.extend(field)
    header = bytearray(struct.pack("<BBBBIII", ord("l"), 1, 0, 1, len(body), serial, len(array_buf)))
    header.extend(array_buf)
    header.extend(b"\x00" * ((-len(header)) % 8))
    return bytes(header) + body


def trigger_host_poweroff(socket_path=None):
    bus_path = socket_path or os.environ.get("DBUS_SYSTEM_BUS_PATH", "/run/dbus/system_bus_socket")
    family = getattr(socket, "AF_UNIX", None)
    if family is None or not os.path.exists(bus_path):
        raise OSError("System D-Bus socket is not available")
    uid_hex = str(os.getuid() if hasattr(os, "getuid") else 0).encode("ascii").hex().encode("ascii")
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(bus_path)
        sock.sendall(b"\x00AUTH EXTERNAL " + uid_hex + b"\r\n")
        reply = b""
        while not reply.endswith(b"\r\n"):
            chunk = sock.recv(256)
            if not chunk:
                raise OSError("D-Bus closed connection during authentication")
            reply += chunk
        if not reply.startswith(b"OK "):
            raise OSError("D-Bus authentication rejected")
        sock.sendall(b"BEGIN\r\n")
        sock.sendall(_dbus_message(1, "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello", "org.freedesktop.DBus"))
        sock.recv(4096)
        sock.sendall(_dbus_message(
            2, "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
            "PowerOff", "org.freedesktop.login1", "b", struct.pack("<I", 1),
        ))
        response = sock.recv(4096)
        if len(response) >= 2 and response[1] == 3:
            raise OSError("System poweroff was rejected by login1")


@app.route("/api/system/shutdown", methods=["POST"])
@login_required
def shutdown_server_api():
    if session.get("role") != "admin":
        return jsonify(status="error", message="Administrator access is required."), 403
    body = request.get_json(silent=True)
    submitted = body.get("csrf_token") if isinstance(body, dict) else None
    token = session.get("shutdown_token")
    try:
        valid_token = (isinstance(submitted, str) and bool(token)
                       and secrets.compare_digest(token.encode(), submitted.encode()))
    except UnicodeError:
        valid_token = False
    if not valid_token:
        return jsonify(status="error", message="Invalid shutdown request token."), 400
    try:
        trigger_host_poweroff()
    except OSError:
        app.logger.exception("Unable to trigger host poweroff")
        return jsonify(status="error", message="Unable to shut down the server from this environment."), 503
    return jsonify(status="shutting_down", message="Server is shutting down."), 202


@app.route("/api/charts")
@login_required
def charts_api():
    query_sensor = request.args.get("sensor") or "temperature_c"
    try:
        data = fetch_dashboard_data(query_sensor)
    except psycopg2.Error:
        app.logger.exception("Unable to load dashboard charts")
        response = jsonify(configured=False, charts=[], message="Charts are temporarily unavailable.")
        response.status_code = 503
        response.headers["Cache-Control"] = "no-store"
        return response

    enrich_metrics(data["metrics"])
    metrics_by_key = {m["column"]: m for m in data["metrics"]}
    active_forecast = None
    try:
        with closing(get_db_connection()) as conn:
            forecasts = fetch_latest_forecasts(conn, SENSORS)
        active_forecast = forecasts[0] if forecasts else None
    except psycopg2.Error as error:
        if not (isinstance(error, psycopg2.errors.UndefinedTable) or error.pgcode in {"42P01", "42703"}):
            app.logger.exception("Unable to load chart forecasts")

    chart_forecast = _resolve_chart_forecast(active_forecast, data["recorded_end"])
    chart_end = chart_forecast["target"] if chart_forecast else data["recorded_end"]
    sensor_charts = build_sensor_charts(
        data["recorded_series"], data["recorded_start"], data["recorded_end"],
        query_sensor, metrics_by_key, forecast=active_forecast,
    )
    ticks = build_chart_ticks(data["recorded_start"], chart_end, has_forecast=bool(chart_forecast))
    serialized_charts = [
        dict(
            chart_id=chart["chart_id"],
            column=chart["column"],
            title=chart["title"],
            unit=chart["unit"],
            severity=chart["severity"],
            badge=chart["badge"],
            has_points=bool(chart["points"] or any(s["forecast_segments"] for s in chart["series_list"])),
            y_ticks=chart["y_ticks"],
            threshold_lines=chart["threshold_lines"],
            series_list=[
                dict(
                    column=s["column"],
                    label=s["label"],
                    unit=s["unit"],
                    color=s["color"],
                    segments=s["segments"],
                    forecast_segments=s["forecast_segments"],
                    latest_value=s["latest_value"],
                    severity=s["severity"],
                )
                for s in chart["series_list"]
            ],
        )
        for chart in sensor_charts
    ]
    recorded_end = data["recorded_end"]
    forecast_end = chart_forecast["target"] if chart_forecast else recorded_end
    payload = dict(
        configured=True,
        has_forecast=bool(chart_forecast),
        recorded_start=data["recorded_start"].isoformat() if data["recorded_start"] else None,
        recorded_end=recorded_end.isoformat() if recorded_end else None,
        forecast_end=forecast_end.isoformat() if forecast_end else None,
        ticks=[[x, label] for x, label in ticks],
        charts=serialized_charts,
    )
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/forecasts")
@login_required
def forecasts_api():
    try:
        with closing(get_db_connection()) as conn:
            payload = {"configured": True, "forecasts": fetch_latest_forecasts(conn, SENSORS)}
        response = jsonify(payload)
    except psycopg2.Error as error:
        if isinstance(error, psycopg2.errors.UndefinedTable) or error.pgcode == "42P01":
            response = jsonify(configured=False, forecasts=[],
                               message="Forecasting is not configured yet.")
        else:
            app.logger.exception("Unable to load forecasts")
            response = jsonify(configured=True, forecasts=[],
                               message="Forecasts are temporarily unavailable.")
            response.status_code = 503
    response.headers["Cache-Control"] = "no-store"
    return response


def _prediction_unavailable(message, configured, status_code):
    response = jsonify(
        configured=configured,
        status="idle",
        request_id=None,
        device_id=None,
        input_row_id=None,
        cooldown_seconds=0,
        message=message,
        can_predict=False,
    )
    response.status_code = status_code
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/predictions", methods=["GET", "POST"])
@login_required
def predictions_api():
    if request.method == "POST":
        body = request.get_json(silent=True)
        submitted = body.get("csrf_token") if isinstance(body, dict) else None
        token = session.get("prediction_token")
        try:
            valid_token = (isinstance(submitted, str) and bool(token)
                           and secrets.compare_digest(token.encode(), submitted.encode()))
        except UnicodeError:
            valid_token = False
        if not valid_token:
            return _prediction_unavailable("Invalid prediction request token.", True, 400)

    try:
        with closing(get_db_connection()) as conn:
            if request.method == "POST":
                try:
                    payload = enqueue_prediction(conn)
                    status_code = 202
                except PredictionRejected as error:
                    payload = error.payload
                    status_code = error.status_code
            else:
                payload = get_prediction_state(conn)
                status_code = 200
        response = jsonify(payload)
        response.status_code = status_code
    except psycopg2.Error as error:
        missing_schema = isinstance(error, psycopg2.errors.UndefinedTable) or error.pgcode == "42P01"
        if not missing_schema:
            app.logger.exception("Unable to access prediction requests")
        return _prediction_unavailable(
            "Manual prediction is not configured yet."
            if missing_schema else "Manual prediction is temporarily unavailable.",
            not missing_schema,
            200 if missing_schema and request.method == "GET" else 503,
        )

    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/data")
@login_required
def data_page():
    if session.get("role") == "admin":
        session.setdefault("delete_readings_token", secrets.token_hex(32))
    filters = parse_filters(request.args)
    readings = []
    total = 0
    filter_error = None
    database_error = False

    try:
        readings, total, filter_error = fetch_readings(filters)
    except psycopg2.Error:
        app.logger.exception("Unable to load sensor data")
        database_error = True

    total_pages = max(1, math.ceil(total / filters["per_page"]))
    if filters["page"] > total_pages and not database_error and not filter_error:
        query_args = request.args.to_dict()
        query_args["page"] = total_pages
        return redirect(url_for("data_page", **query_args))

    query_args = {
        "date_range": filters["date_range"],
        "start_date": filters["start_date"],
        "end_date": filters["end_date"],
        "filter": filters["filter"],
        "q": filters["q"],
        "per_page": filters["per_page"],
    }
    previous_url = url_for("data_page", page=filters["page"] - 1, **query_args) if filters["page"] > 1 else None
    next_url = url_for("data_page", page=filters["page"] + 1, **query_args) if filters["page"] < total_pages else None
    pagination_items = [
        {
            "page": page,
            "url": url_for("data_page", page=page, **query_args) if page is not None else None,
            "current": page == filters["page"],
        }
        for page in build_pagination_items(filters["page"], total_pages)
    ]
    first_record = (filters["page"] - 1) * filters["per_page"] + 1 if total else 0
    last_record = min(filters["page"] * filters["per_page"], total)
    export_args = {key: value for key, value in query_args.items() if key != "per_page"}

    reading_rows = [
        {
            "timestamp": row[0],
            "formatted_time": format_pht_timestamp(row[0]),
            "cells": [
                {
                    "column": col,
                    "value": val,
                    "severity": classify_sensor_reading(col, val),
                }
                for col, val in zip(READING_COLUMNS[1:], row[1:])
            ],
            "sensor_values": row[1:],
            "severity": classify_reading_row(row)[0],
            "badge": classify_reading_row(row)[1],
            "raw": row,
        }
        for row in readings
    ]

    return render_template(
        "data.html",
        **shell_context("Data"),
        readings=readings,
        reading_rows=reading_rows,
        csv_headers=CSV_HEADERS,
        table_headers=TABLE_HEADERS,
        filters=filters,
        filter_options=READING_FILTERS,
        active_filter_label=READING_FILTER_MAP.get(filters["filter"], "All readings"),
        total=total,
        total_pages=total_pages,
        previous_url=previous_url,
        next_url=next_url,
        pagination_items=pagination_items,
        first_record=first_record,
        last_record=last_record,
        export_url=url_for("export_data", **export_args),
        filter_error=filter_error,
        database_error=database_error,
    )


@app.route("/data/delete", methods=["POST"])
@login_required
def delete_readings():
    if session.get("role") != "admin":
        abort(403)
    token = session.get("delete_readings_token", "")
    submitted = request.form.get("csrf_token", "")
    if not token or not secrets.compare_digest(token.encode(), submitted.encode()):
        abort(400)
    delete_mode = request.form.get("delete_mode")
    expected_confirmation = {"all": "delete_all", "all_simulated": "delete_all_simulated", "incomplete": "delete_incomplete", "from_timestamp": "delete_from_timestamp"}.get(delete_mode)
    if not expected_confirmation or request.form.get("confirmation") != expected_confirmation:
        abort(400)
    cutoff = None
    delete_scope = request.form.get("delete_scope")
    if delete_mode == "from_timestamp":
        if delete_scope not in {"simulated", "all"}:
            abort(400)
        try:
            cutoff = parse_delete_cutoff(request.form.get("delete_from"))
        except ValueError:
            abort(400)
    elif delete_mode == "all_simulated":
        if delete_scope not in {None, "", "simulated"}:
            abort(400)
    elif delete_scope not in {None, "", "all"}:
        abort(400)

    busy = False
    deleted_count = 0
    next_id = None
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                with conn.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '5s'")
                    cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", (DELETE_WORKER_LOCK_ID,))
                    if not cursor.fetchone()[0]:
                        busy = True
                    else:
                        cursor.execute("SELECT to_regclass('public.air_quality_forecasts')")
                        forecast_table_exists = cursor.fetchone()[0] is not None
                        if forecast_table_exists:
                            cursor.execute("LOCK TABLE air_quality_forecasts IN ACCESS EXCLUSIVE MODE")
                        cursor.execute("LOCK TABLE air_quality_logs IN ACCESS EXCLUSIVE MODE")
                        if delete_mode == "all":
                            if forecast_table_exists:
                                cursor.execute("TRUNCATE TABLE air_quality_forecasts")
                            cursor.execute("TRUNCATE TABLE air_quality_logs RESTART IDENTITY")
                        else:
                            scope_sql = " AND device_id = ANY(%s)" if delete_scope == "simulated" else ""
                            if delete_mode == "all_simulated":
                                where_sql = "device_id = ANY(%s)"
                                params = (list(SIMULATED_DEVICE_IDS),)
                            elif delete_mode == "incomplete":
                                where_sql = (
                                    "COALESCE(NULLIF(BTRIM(device_id), ''), 'henvironment-sensor') IN ("
                                    "SELECT COALESCE(NULLIF(BTRIM(device_id), ''), 'henvironment-sensor') "
                                    "FROM air_quality_logs GROUP BY 1 "
                                    "HAVING COUNT(DISTINCT date_trunc('minute', recorded_at AT TIME ZONE 'UTC')) < 120)"
                                )
                                params = ()
                            else:
                                where_sql = f"recorded_at >= %s{scope_sql}"
                                params = (cutoff, list(SIMULATED_DEVICE_IDS)) if scope_sql else (cutoff,)

                            cursor.execute(
                                "WITH deleted AS ("
                                f"DELETE FROM air_quality_logs WHERE {where_sql} RETURNING device_id"
                                ") SELECT COUNT(*), COALESCE(ARRAY_AGG(DISTINCT device_id) "
                                "FILTER (WHERE device_id IS NOT NULL), ARRAY[]::varchar[]) FROM deleted",
                                params,
                            )
                            deleted_count, affected_devices = cursor.fetchone()
                            if deleted_count:
                                if delete_mode in {"all_simulated", "incomplete"}:
                                    cursor.execute(
                                        "DO $$ BEGIN "
                                        "IF to_regclass('public.air_quality_prediction_requests') IS NOT NULL THEN "
                                        "DELETE FROM public.air_quality_prediction_requests; "
                                        "END IF; "
                                        "END $$;"
                                    )
                                    if forecast_table_exists:
                                        cursor.execute("DELETE FROM air_quality_forecasts")
                                elif affected_devices and forecast_table_exists:
                                    cursor.execute(
                                        "DELETE FROM air_quality_forecasts WHERE device_id = ANY(%s)",
                                        (affected_devices,),
                                    )
                                cursor.execute(
                                    "SELECT namespace.nspname, sequence.relname, "
                                    "(SELECT COALESCE(MAX(id), 0) + 1 FROM public.air_quality_logs) "
                                    "FROM pg_class AS sequence JOIN pg_namespace AS namespace "
                                    "ON namespace.oid = sequence.relnamespace "
                                    "WHERE sequence.oid = pg_get_serial_sequence('public.air_quality_logs', 'id')::regclass"
                                )
                                sequence = cursor.fetchone()
                                if sequence is None:
                                    raise psycopg2.DatabaseError("The readings ID sequence is missing")
                                sequence_schema, sequence_name, next_id = sequence
                                next_id = int(next_id)
                                cursor.execute(
                                    sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                                        sql.Identifier(sequence_schema, sequence_name), sql.Literal(next_id)
                                    )
                                )
        if busy:
            flash("Readings were not deleted because a prediction or simulation is active. Wait for it to finish or stop the simulation, then try again.", "error")
        elif delete_mode == "all":
            flash("All sensor readings and saved forecasts were deleted. The next reading ID is 1.", "success")
        elif delete_mode == "all_simulated" and deleted_count:
            flash(
                f"Deleted {deleted_count} simulation readings. "
                f"Existing IDs were kept; the next reading ID is {next_id}.",
                "success",
            )
        elif delete_mode == "incomplete" and deleted_count:
            flash(
                f"Deleted {deleted_count} incomplete readings. "
                f"Existing IDs were kept; the next reading ID is {next_id}.",
                "success",
            )
        elif deleted_count:
            flash(
                f"Deleted {deleted_count} matching readings from {cutoff.strftime('%Y-%m-%d %H:%M:%S')} UTC. "
                f"Existing IDs were kept; the next reading ID is {next_id}.",
                "success",
            )
        else:
            flash("No matching readings were found. No data or ID sequence was changed.", "success")
    except psycopg2.Error:
        app.logger.exception("Unable to delete sensor readings")
        flash("Unable to delete readings. No changes were committed; please try again.", "error")
    return redirect(url_for("data_page"), code=303)


@app.route("/data/export.csv")
@login_required
def export_data():
    filters = parse_filters(request.args)
    try:
        readings, _, filter_error = fetch_readings(filters, paginate=False)
    except psycopg2.Error:
        app.logger.exception("Unable to export sensor data")
        return "Unable to export sensor data right now.", 503

    if filter_error:
        return filter_error, 400

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    for row in readings:
        writer.writerow(
            [row[0].strftime("%Y-%m-%d %H:%M:%S") if row[0] else ""]
            + ["" if value is None else value for value in row[1:]]
        )

    return app.response_class(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=henvironment-data.csv"},
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
