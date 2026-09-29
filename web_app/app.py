import csv
import io
import math
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


def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)


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
    return render_template("dashboard.html", **shell_context("Dashboard"))


@app.route("/data")
@login_required
def data_page():
    filters = parse_filters(request.args)
    context = build_data_context(filters)
    if filters["page"] > context["total_pages"] and context["total"]:
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
