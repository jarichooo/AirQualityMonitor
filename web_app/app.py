import csv
import io
import math
import os
import secrets
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


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
    "mq135_raw",
    "mq137_raw",
)
CSV_HEADERS = (
    "Timestamp (UTC)",
    "Temp (°C)",
    "Humidity (%)",
    "CO2 (ppm)",
    "PM1.0 (µg/m³)",
    "PM2.5 (µg/m³)",
    "PM10 (µg/m³)",
    "MQ135 (raw)",
    "MQ137 (raw)",
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
    ("mq135_raw", "MQ135", "raw"),
    ("mq137_raw", "MQ137", "raw"),
)
LIVE_SENSORS = (
    ("t", "Temperature", "°C"),
    ("h", "Humidity", "%"),
    ("co2", "CO₂", "ppm"),
    ("pm1", "PM1.0", "µg/m³"),
    ("pm25", "PM2.5", "µg/m³"),
    ("pm10", "PM10", "µg/m³"),
    ("mq135_raw", "MQ135", "raw"),
    ("mq137_raw", "MQ137", "raw"),
)


def fetch_dashboard_data(sensor):
    """Descriptive analytics only; forecast inference will be integrated later."""
    start = datetime.now(timezone.utc) - timedelta(hours=24)
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), MAX(recorded_at) FROM air_quality_logs")
            total, latest = cursor.fetchone()
            cursor.execute(f"SELECT {', '.join(READING_COLUMNS)} FROM air_quality_logs ORDER BY recorded_at DESC, id DESC LIMIT 1")
            latest_row = cursor.fetchone()
            aggregates = ", ".join(f"MIN({col}), AVG({col}), MAX({col}), COUNT({col})" for col, _, _ in SENSORS)
            cursor.execute(f"SELECT {aggregates} FROM air_quality_logs WHERE recorded_at >= %s AND recorded_at <= %s",
                           (start, start + timedelta(hours=24)))
            summary = cursor.fetchone()
            metrics = []
            for index, (column, label, unit) in enumerate(SENSORS):
                low, average, high, count = summary[index * 4:index * 4 + 4]
                metrics.append(dict(column=column, label=label, unit=unit,
                                    value=latest_row[index + 1] if latest_row else None,
                                    minimum=low, average=average, maximum=high, count=count))
            # sensor is selected from SENSORS by the route, never interpolated from raw input.
            cursor.execute(f"SELECT date_trunc('hour', recorded_at), AVG({sensor}) FROM air_quality_logs "
                           "WHERE recorded_at >= %s AND recorded_at <= %s "
                           "GROUP BY 1 ORDER BY 1", (start, start + timedelta(hours=24)))
            series = cursor.fetchall()
    return dict(total=total, latest=latest, metrics=metrics, series=series, start=start)


def build_trend(series, start):
    """Return SVG coordinates; break the line across missing hourly buckets."""
    valid = [float(value) for _, value in series if value is not None and math.isfinite(float(value))]
    if not valid:
        return dict(segments=[], points=[], low=None, high=None)
    low, high = min(valid), max(valid)
    padding = (high - low) * .1 or 1
    bottom, top = low - padding, high + padding
    segments, points, segment = [], [], []
    previous = None
    for timestamp, value in series:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if value is None or not math.isfinite(float(value)):
            if segment:
                segments.append(" ".join(segment))
            segment, previous = [], None
            continue
        if previous is not None and timestamp - previous > timedelta(hours=1):
            segments.append(" ".join(segment))
            segment = []
        # Put each hourly average at the midpoint of its actual time window.
        midpoint = max(timestamp, start) + (min(timestamp + timedelta(hours=1), start + timedelta(hours=24)) - max(timestamp, start)) / 2
        x = 60 + (midpoint - start).total_seconds() / 86400 * 700
        y = 210 - (float(value) - bottom) / (top - bottom) * 180
        point = dict(x=round(x, 2), y=round(y, 2), time=timestamp.strftime("%Y-%m-%d %H:%M UTC"), value=round(float(value), 2))
        points.append(point)
        segment.append(f"{point['x']},{point['y']}")
        previous = timestamp
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
    data = dict(total=None, latest=None, metrics=[dict(column=c, label=l, unit=u, value=None,
                minimum=None, average=None, maximum=None, count=0) for c, l, u in SENSORS], series=[],
                start=datetime.now(timezone.utc) - timedelta(hours=24))
    database_error = False
    try:
        data = fetch_dashboard_data(sensor)
    except psycopg2.Error:
        app.logger.exception("Unable to load collection summary")
        database_error = True
    return render_template("dashboard.html", **shell_context("Dashboard"),
                           **data, selected=next(m for m in data["metrics"] if m["column"] == sensor),
                           trend=build_trend(data["series"], data["start"]), database_error=database_error,
                           live_sensors=LIVE_SENSORS)


@app.route("/data")
@login_required
def data_page():
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
