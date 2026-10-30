import csv
import io
import math
import os
import re
import secrets
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from functools import wraps

import psycopg2
import psycopg2.extras
from psycopg2 import sql
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from forecast import fetch_latest_forecasts


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
DATE_RANGES = {"all", "today", "7d", "30d", "custom"}
PAGE_SIZES = {10, 25, 50, 100}
SENSORS = (
    ("temperature_c", "Temperature", "°C"),
    ("humidity_perc", "Humidity", "%"),
    ("co2_ppm", "CO2", "ppm"),
    ("pm1_ugm3", "PM1.0", "µg/m³"),
    ("pm25_ugm3", "PM2.5", "µg/m³"),
    ("pm10_ugm3", "PM10", "µg/m³"),
    ("mq4_ppm", "Methane estimate (MQ-4)", "ppm"),
    ("mq137_ppm", "Ammonia estimate (MQ-137)", "ppm"),
)
LIVE_SENSORS = (
    ("t", "Temperature", "°C"),
    ("h", "Humidity", "%"),
    ("co2", "CO₂", "ppm"),
    ("pm1", "PM1.0", "µg/m³"),
    ("pm25", "PM2.5", "µg/m³"),
    ("pm10", "PM10", "µg/m³"),
    ("mq4_ppm", "Methane estimate (MQ-4)", "ppm"),
    ("mq137_ppm", "Ammonia estimate (MQ-137)", "ppm"),
)
# Match the forecast worker so cleanup cannot race with simulation or inference.
DELETE_WORKER_LOCK_ID = 739_783_247_071_623_201
SIMULATED_DEVICE_IDS = ("sim-farm-good", "sim-farm-mid", "sim-farm-bad", "python-simulator")


def parse_delete_cutoff(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", value or ""):
        raise ValueError("invalid UTC timestamp")
    timestamp_format = "%Y-%m-%dT%H:%M:%S" if len(value) == 19 else "%Y-%m-%dT%H:%M"
    return datetime.strptime(value, timestamp_format).replace(tzinfo=timezone.utc)


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
                cursor.execute(f"SELECT {', '.join(READING_COLUMNS)} FROM air_quality_logs ORDER BY recorded_at DESC, id DESC LIMIT 1")
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
                cursor.execute(f"SELECT recorded_at, {sensor}, device_id FROM air_quality_logs "
                               "WHERE recorded_at >= %s AND recorded_at <= %s "
                               "ORDER BY device_id NULLS FIRST, recorded_at, id",
                               (recorded_start, recorded_end))
                recorded_series = cursor.fetchall()
            metrics = []
            for index, (column, label, unit) in enumerate(SENSORS):
                low, average, high, count = summary[index * 4:index * 4 + 4]
                metrics.append(dict(column=column, label=label, unit=unit,
                                    value=latest_row[index + 1] if latest_row else None,
                                    minimum=low, average=average, maximum=high, count=count))
    return dict(total=total, earliest=earliest, latest=latest, metrics=metrics, series=series,
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
    sensor = request.args.get("sensor", "temperature_c")
    if sensor not in {column for column, _, _ in SENSORS}:
        sensor = "temperature_c"
    now = datetime.now(timezone.utc)
    data = dict(total=None, earliest=None, latest=None, metrics=[dict(column=c, label=l, unit=u, value=None,
                minimum=None, average=None, maximum=None, count=0) for c, l, u in SENSORS], series=[],
                start=now - timedelta(hours=24), end=now, recorded_series=[],
                recorded_start=now - timedelta(hours=1), recorded_end=now)
    database_error = False
    try:
        data = fetch_dashboard_data(sensor)
    except psycopg2.Error:
        app.logger.exception("Unable to load collection summary")
        database_error = True
    return render_template("dashboard.html", **shell_context("Dashboard"),
                           **data, selected=next(m for m in data["metrics"] if m["column"] == sensor),
                           trend=build_trend(data["series"], data["start"], data["end"]), database_error=database_error,
                           recorded_trend=build_trend(data["recorded_series"], data["recorded_start"],
                                                      data["recorded_end"], hourly=False),
                           recorded_ticks=[(60 + minute / 60 * 700,
                                            (data["recorded_start"] + timedelta(minutes=minute)).strftime("%H:%M"))
                                           for minute in (0, 15, 30, 45, 60)],
                           live_sensors=LIVE_SENSORS)


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

    return render_template(
        "data.html",
        **shell_context("Data"),
        readings=readings,
        csv_headers=CSV_HEADERS,
        filters=filters,
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
    expected_confirmation = {"all": "delete_all", "from_timestamp": "delete_from_timestamp"}.get(delete_mode)
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
                            params = (cutoff, list(SIMULATED_DEVICE_IDS)) if scope_sql else (cutoff,)
                            cursor.execute(
                                "WITH deleted AS ("
                                f"DELETE FROM air_quality_logs WHERE recorded_at >= %s{scope_sql} RETURNING device_id"
                                ") SELECT COUNT(*), COALESCE(ARRAY_AGG(DISTINCT device_id) "
                                "FILTER (WHERE device_id IS NOT NULL), ARRAY[]::varchar[]) FROM deleted",
                                params,
                            )
                            deleted_count, affected_devices = cursor.fetchone()
                            if deleted_count:
                                if affected_devices and forecast_table_exists:
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
            flash("Readings were not deleted because the forecast or simulation worker is running. Stop it and try again.", "error")
        elif delete_mode == "all":
            flash("All sensor readings and saved forecasts were deleted. The next reading ID is 1.", "success")
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
