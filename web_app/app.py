import csv
import io
import math
import os
import statistics
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


app = Flask(__name__)
app.secret_key = "defense_pod_2026_secure_key"

DB_HOST = "postgres_db"
DB_NAME = "air_quality"
DB_USER = "admin"
DB_PASS = "notsosecretpass"
PREVIEW_MODE = os.getenv("HENVIRONMENT_PREVIEW", "").lower() in {"1", "true", "yes"}

READING_COLUMNS = (
    "recorded_at",
    "temperature_c",
    "humidity_perc",
    "nh3_ppm",
    "pm25_ugm3",
    "mq135_ppm",
    "mq137_ppm",
)
CSV_HEADERS = (
    "Timestamp (UTC)",
    "Temp (°C)",
    "Humidity (%)",
    "NH3 (ppm)",
    "PM 2.5",
    "MQ135 (ppm)",
    "MQ137 (ppm)",
)
DATE_RANGES = {"all", "today", "7d", "30d", "custom"}
PAGE_SIZES = {10, 25, 50, 100}
DASHBOARD_RANGES = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}
DASHBOARD_RANGE_LABELS = {
    "1h": "Last 1 hour",
    "6h": "Last 6 hours",
    "24h": "Last 24 hours",
    "7d": "Last 7 days",
}
DASHBOARD_BUCKETS = {"1h": None, "6h": "3 minutes", "24h": "12 minutes", "7d": "90 minutes"}
DASHBOARD_METRICS = {
    "temperature_c": {
        "column": "temperature_c",
        "label": "Temperature",
        "short_label": "Temperature",
        "unit": "°C",
        "precision": 1,
        "threshold": None,
        "recommendation": None,
    },
    "humidity_perc": {
        "column": "humidity_perc",
        "label": "Humidity",
        "short_label": "Humidity",
        "unit": "%",
        "precision": 1,
        "threshold": None,
        "recommendation": None,
    },
    "nh3_ppm": {
        "column": "nh3_ppm",
        "label": "NH3",
        "short_label": "NH3",
        "unit": "ppm",
        "precision": 2,
        "threshold": 25,
        "recommendation": "Improve ventilation and inspect or clean accumulated litter or waste.",
    },
    "co2_ppm": {
        "column": "co2_ppm",
        "label": "CO2",
        "short_label": "CO2",
        "unit": "ppm",
        "precision": 0,
        "threshold": None,
        "recommendation": None,
    },
    "pm25_ugm3": {
        "column": "pm25_ugm3",
        "label": "PM2.5",
        "short_label": "PM2.5",
        "unit": "µg/m³",
        "precision": 1,
        "threshold": None,
        "recommendation": None,
    },
}
DASHBOARD_METRIC_KEYS = tuple(DASHBOARD_METRICS)


def build_preview_readings():
    """Create a deterministic local-only dataset for the visual preview server."""
    newest = datetime.now(timezone.utc).replace(microsecond=0)
    rows = []
    for index in range(100):
        rows.append(
            (
                newest - timedelta(minutes=index * 3),
                round(29.4 + index * 0.1, 1),
                round(67.2 - index * 0.2, 1),
                round(7.8 + index * 0.05, 2),
                round(13.6 + index * 0.1, 1),
                round(10.2 + index * 0.1, 1),
                round(27.5 + index * 0.1, 1),
            )
        )
    return rows


PREVIEW_READINGS = build_preview_readings()
PREVIEW_DASHBOARD_READINGS = [
    (row[0], row[1], row[2], row[3], round(820 + index * 3), row[4])
    for index, row in enumerate(PREVIEW_READINGS)
]


def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if PREVIEW_MODE and not session.get("logged_in"):
            session.update(logged_in=True, username="Admin", role="admin")
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

    return {
        "date_range": date_range,
        "start_date": args.get("start_date", "").strip(),
        "end_date": args.get("end_date", "").strip(),
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

    if filters["q"]:
        searchable = ("recorded_at",) + READING_COLUMNS[1:]
        conditions.append(
            "(" + " OR ".join(f"COALESCE(CAST({column} AS TEXT), '') ILIKE %s" for column in searchable) + ")"
        )
        params.extend([f"%{filters['q']}%"] * len(searchable))

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, params, error


def fetch_preview_readings(filters, paginate=True):
    """Filter the local preview rows with the same semantics as the database query."""
    _, _, filter_error = build_where_clause(filters)
    if filter_error:
        return [], 0, filter_error

    now = datetime.now(timezone.utc)
    start = None
    end = None
    if filters["date_range"] == "today":
        start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    elif filters["date_range"] == "7d":
        start = now - timedelta(days=7)
    elif filters["date_range"] == "30d":
        start = now - timedelta(days=30)
    elif filters["date_range"] == "custom":
        start = datetime.strptime(filters["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(filters["end_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)

    rows = PREVIEW_READINGS
    if start is not None:
        rows = [row for row in rows if row[0] >= start and (end is None or row[0] < end)]
    if filters["q"]:
        needle = filters["q"].casefold()
        rows = [
            row
            for row in rows
            if any(
                needle in (value.isoformat() if isinstance(value, datetime) else str(value)).casefold()
                for value in row
            )
        ]

    total = len(rows)
    if paginate:
        offset = (filters["page"] - 1) * filters["per_page"]
        rows = rows[offset : offset + filters["per_page"]]
    return rows, total, None


def fetch_readings(filters, paginate=True):
    if PREVIEW_MODE:
        return fetch_preview_readings(filters, paginate)

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


def build_data_context(filters):
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
    query_args = {
        "date_range": filters["date_range"],
        "start_date": filters["start_date"],
        "end_date": filters["end_date"],
        "q": filters["q"],
        "per_page": filters["per_page"],
    }
    previous_url = url_for("data_page", page=filters["page"] - 1, **query_args) if filters["page"] > 1 else None
    next_url = url_for("data_page", page=filters["page"] + 1, **query_args) if filters["page"] < total_pages else None
    pagination_items = [
        {
            "page": page,
            "url": url_for("data_page", page=page, **query_args),
            "current": page == filters["page"],
        }
        for page in build_pagination_items(filters["page"], total_pages)
    ]
    first_record = (filters["page"] - 1) * filters["per_page"] + 1 if total else 0
    last_record = min(filters["page"] * filters["per_page"], total)

    return {
        "readings": readings,
        "filters": filters,
        "total": total,
        "total_pages": total_pages,
        "previous_url": previous_url,
        "next_url": next_url,
        "pagination_items": pagination_items,
        "first_record": first_record,
        "last_record": last_record,
        "export_url": url_for("export_data", **{key: value for key, value in query_args.items() if key != "per_page"}),
        "filter_error": filter_error,
        "database_error": database_error,
    }


def parse_dashboard_range(value):
    return value if value in DASHBOARD_RANGES else "24h"


def dashboard_timestamp(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def dashboard_number(value):
    if value is None:
        return None
    return float(value)


def dashboard_empty_payload(range_key):
    metrics = {
        key: {"value": None, "unit": config["unit"], "status": "unavailable"}
        for key, config in DASHBOARD_METRICS.items()
    }
    summary = {
        key: {
            "average": None,
            "stddev": None,
            "min": None,
            "max": None,
            "exceedance_count": None,
            "exceedance_percent": None,
        }
        for key in DASHBOARD_METRIC_KEYS
    }
    return {
        "generated_at": dashboard_timestamp(datetime.now(timezone.utc)),
        "range": range_key,
        "latest": {
            "recorded_at": None,
            "age_seconds": None,
            "connection_status": "unavailable",
            "overall_status": "unavailable",
            "metrics": metrics,
        },
        "series": {"timestamps": [], "metrics": {key: [] for key in DASHBOARD_METRIC_KEYS}},
        "summary": summary,
        "alerts": [],
        "prediction": {"available": False, "reason": "The prediction model is not connected."},
    }


def dashboard_metric_status(key, value):
    if value is None:
        return "unavailable"
    threshold = DASHBOARD_METRICS[key]["threshold"]
    if threshold is None:
        return "unclassified"
    return "attention" if value > threshold else "within_range"


def build_preview_dashboard_payload(range_key):
    """Build the Dashboard payload from the local-only preview rows."""
    range_key = parse_dashboard_range(range_key)
    since = datetime.now(timezone.utc) - DASHBOARD_RANGES[range_key]
    selected_rows = [row for row in PREVIEW_DASHBOARD_READINGS if row[0] >= since]
    payload = dashboard_empty_payload(range_key)
    payload["generated_at"] = dashboard_timestamp(datetime.now(timezone.utc))

    if PREVIEW_DASHBOARD_READINGS:
        latest_row = PREVIEW_DASHBOARD_READINGS[0]
        latest_timestamp = latest_row[0]
        age_seconds = max(0, int((datetime.now(timezone.utc) - latest_timestamp).total_seconds()))
        latest_values = dict(zip(DASHBOARD_METRIC_KEYS, latest_row[1:]))
        latest_metrics = {
            key: {
                "value": dashboard_number(latest_values[key]),
                "unit": DASHBOARD_METRICS[key]["unit"],
                "status": dashboard_metric_status(key, latest_values[key]),
            }
            for key in DASHBOARD_METRIC_KEYS
        }
        payload["latest"] = {
            "recorded_at": dashboard_timestamp(latest_timestamp),
            "age_seconds": age_seconds,
            "connection_status": "current" if age_seconds <= 90 else "delayed",
            "overall_status": latest_metrics["nh3_ppm"]["status"],
            "metrics": latest_metrics,
        }

    ascending_rows = list(reversed(selected_rows))
    payload["series"] = {
        "timestamps": [dashboard_timestamp(row[0]) for row in ascending_rows],
        "metrics": {
            key: [dashboard_number(row[index]) for row in ascending_rows]
            for index, key in enumerate(DASHBOARD_METRIC_KEYS, start=1)
        },
    }

    summary = {}
    for index, key in enumerate(DASHBOARD_METRIC_KEYS, start=1):
        values = [row[index] for row in selected_rows if row[index] is not None]
        summary[key] = {
            "average": dashboard_number(statistics.mean(values)) if values else None,
            "stddev": dashboard_number(statistics.pstdev(values)) if values else None,
            "min": dashboard_number(min(values)) if values else None,
            "max": dashboard_number(max(values)) if values else None,
            "exceedance_count": None,
            "exceedance_percent": None,
        }
    nh3_values = [row[3] for row in selected_rows if row[3] is not None]
    exceedance_count = sum(value > 25 for value in nh3_values)
    if nh3_values:
        summary["nh3_ppm"]["exceedance_count"] = exceedance_count
        summary["nh3_ppm"]["exceedance_percent"] = (exceedance_count / len(nh3_values)) * 100
    payload["summary"] = summary

    payload["alerts"] = [
        {
            "metric": "nh3_ppm",
            "label": "NH3",
            "value": dashboard_number(row[3]),
            "unit": "ppm",
            "threshold": DASHBOARD_METRICS["nh3_ppm"]["threshold"],
            "recorded_at": dashboard_timestamp(row[0]),
            "severity": "attention",
            "recommendation": DASHBOARD_METRICS["nh3_ppm"]["recommendation"],
        }
        for row in selected_rows
        if row[3] is not None and row[3] > 25
    ][:3]
    return payload


def build_dashboard_payload(range_key):
    """Build the bounded dashboard response from the database source of truth."""
    if PREVIEW_MODE:
        return build_preview_dashboard_payload(range_key)

    range_key = parse_dashboard_range(range_key)
    since = datetime.now(timezone.utc) - DASHBOARD_RANGES[range_key]
    columns = ", ".join(config["column"] for config in DASHBOARD_METRICS.values())
    metric_keys = list(DASHBOARD_METRIC_KEYS)

    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT recorded_at, {columns} FROM air_quality_logs ORDER BY recorded_at DESC LIMIT 1"
            )
            latest_row = cursor.fetchone()

            aggregate_sql = ", ".join(
                fragment
                for config in DASHBOARD_METRICS.values()
                for fragment in (
                    f"AVG({config['column']})",
                    f"STDDEV_POP({config['column']})",
                    f"MIN({config['column']})",
                    f"MAX({config['column']})",
                )
            )
            cursor.execute(
                f"SELECT {aggregate_sql}, COUNT(nh3_ppm), COUNT(*) FILTER (WHERE nh3_ppm > 25) "
                "FROM air_quality_logs WHERE recorded_at >= %s",
                (since,),
            )
            aggregate_row = cursor.fetchone()

            bucket = DASHBOARD_BUCKETS[range_key]
            if bucket:
                bucket_columns = ", ".join(
                    f"AVG({config['column']})" for config in DASHBOARD_METRICS.values()
                )
                cursor.execute(
                    f"SELECT date_bin(%s::interval, recorded_at, TIMESTAMPTZ '2000-01-01 00:00:00+00'), "
                    f"{bucket_columns} FROM air_quality_logs WHERE recorded_at >= %s "
                    "GROUP BY 1 ORDER BY 1 ASC",
                    (bucket, since),
                )
                series_rows = cursor.fetchall()
            else:
                cursor.execute(
                    f"SELECT recorded_at, {columns} FROM air_quality_logs WHERE recorded_at >= %s "
                    "ORDER BY recorded_at ASC",
                    (since,),
                )
                series_rows = cursor.fetchall()

            cursor.execute(
                "SELECT recorded_at, nh3_ppm FROM air_quality_logs "
                "WHERE recorded_at >= %s AND nh3_ppm > 25 ORDER BY recorded_at DESC LIMIT 3",
                (since,),
            )
            alert_rows = cursor.fetchall()

    payload = dashboard_empty_payload(range_key)
    payload["generated_at"] = dashboard_timestamp(datetime.now(timezone.utc))

    if latest_row:
        latest_timestamp = latest_row[0]
        if latest_timestamp.tzinfo is None:
            latest_timestamp = latest_timestamp.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((datetime.now(timezone.utc) - latest_timestamp).total_seconds()))
        latest_values = dict(zip(metric_keys, latest_row[1:]))
        latest_metrics = {
            key: {
                "value": dashboard_number(latest_values[key]),
                "unit": DASHBOARD_METRICS[key]["unit"],
                "status": dashboard_metric_status(key, latest_values[key]),
            }
            for key in metric_keys
        }
        nh3_status = latest_metrics["nh3_ppm"]["status"]
        overall_status = {
            "attention": "attention",
            "within_range": "within_range",
        }.get(nh3_status, "unclassified")
        payload["latest"] = {
            "recorded_at": dashboard_timestamp(latest_timestamp),
            "age_seconds": age_seconds,
            "connection_status": "current" if age_seconds <= 90 else "delayed",
            "overall_status": overall_status,
            "metrics": latest_metrics,
        }

    series = {key: [] for key in metric_keys}
    timestamps = []
    for row in series_rows:
        timestamps.append(dashboard_timestamp(row[0]))
        for index, key in enumerate(metric_keys, start=1):
            series[key].append(dashboard_number(row[index]))
    payload["series"] = {"timestamps": timestamps, "metrics": series}

    summary = {}
    aggregate_index = 0
    for key in metric_keys:
        summary[key] = {
            "average": dashboard_number(aggregate_row[aggregate_index]),
            "stddev": dashboard_number(aggregate_row[aggregate_index + 1]),
            "min": dashboard_number(aggregate_row[aggregate_index + 2]),
            "max": dashboard_number(aggregate_row[aggregate_index + 3]),
            "exceedance_count": None,
            "exceedance_percent": None,
        }
        aggregate_index += 4
    nh3_count = aggregate_row[aggregate_index]
    nh3_exceedances = aggregate_row[aggregate_index + 1]
    if nh3_count:
        summary["nh3_ppm"]["exceedance_count"] = nh3_exceedances
        summary["nh3_ppm"]["exceedance_percent"] = (int(nh3_exceedances) / int(nh3_count)) * 100
    payload["summary"] = summary

    payload["alerts"] = [
        {
            "metric": "nh3_ppm",
            "label": "NH3",
            "value": dashboard_number(row[1]),
            "unit": "ppm",
            "threshold": DASHBOARD_METRICS["nh3_ppm"]["threshold"],
            "recorded_at": dashboard_timestamp(row[0]),
            "severity": "attention",
            "recommendation": DASHBOARD_METRICS["nh3_ppm"]["recommendation"],
        }
        for row in alert_rows
    ]
    return payload


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        try:
            with closing(get_db_connection()) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT password_hash, role FROM dashboard_users WHERE username = %s",
                        (username,),
                    )
                    user = cursor.fetchone()

            if user and check_password_hash(user["password_hash"], password):
                session.update(logged_in=True, username=username, role=user["role"])
                return redirect(url_for("dashboard"))
            error = "Invalid username or password."
        except psycopg2.Error:
            app.logger.exception("Login database error")
            error = "Unable to connect to the database. Please try again."

    return render_template("login.html", error=error)


@app.route("/")
@login_required
def dashboard():
    range_key = parse_dashboard_range(request.args.get("range"))
    dashboard_error = False
    try:
        dashboard_payload = build_dashboard_payload(range_key)
    except Exception:
        app.logger.exception("Unable to load dashboard data")
        dashboard_payload = dashboard_empty_payload(range_key)
        dashboard_error = True

    return render_template(
        "dashboard.html",
        **shell_context("Dashboard"),
        dashboard_payload=dashboard_payload,
        dashboard_metrics=DASHBOARD_METRICS,
        dashboard_metric_keys=DASHBOARD_METRIC_KEYS,
        dashboard_range=range_key,
        dashboard_range_labels=DASHBOARD_RANGE_LABELS,
        dashboard_error=dashboard_error,
    )


@app.route("/dashboard/live")
@login_required
def dashboard_live():
    range_key = parse_dashboard_range(request.args.get("range"))
    try:
        return jsonify(build_dashboard_payload(range_key))
    except Exception:
        app.logger.exception("Unable to refresh dashboard data")
        return jsonify(error="Dashboard data is unavailable."), 503


@app.route("/data")
@login_required
def data_page():
    filters = parse_filters(request.args)
    context = build_data_context(filters)
    if (
        filters["page"] > context["total_pages"]
        and not context["database_error"]
        and not context["filter_error"]
    ):
        query_args = request.args.to_dict()
        query_args["page"] = context["total_pages"]
        return redirect(url_for("data_page", **query_args))

    return render_template("data.html", **shell_context("Data"), **context)


@app.route("/data/live")
@login_required
def live_data():
    filters = parse_filters(request.args)
    filters.update(page=1, per_page=25)
    context = build_data_context(filters)
    if context["database_error"]:
        return jsonify(error="Sensor data is unavailable."), 503
    if context["filter_error"]:
        return jsonify(error=context["filter_error"]), 400

    return jsonify(
        rows_html=render_template("_data_rows.html", **context),
        pagination_html=render_template("_data_pagination.html", **context),
        record_count=f'{context["total"]} matching record{"s" if context["total"] != 1 else ""}',
    )


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
