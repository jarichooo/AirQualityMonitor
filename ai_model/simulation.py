"""Insert clearly labelled, minute-aligned demo readings for the forecast worker."""

import math
import sys
from datetime import datetime, timedelta, timezone

from psycopg2 import sql
from psycopg2.extras import execute_values

from ai_model.predictor import FEATURE_COLUMNS, LOOKBACK_SAMPLES


PROFILES = {
    "good": {
        "temperature_c": (26.0, 0.8),
        "humidity_perc": (60.0, 3.0),
        "co2_ppm": (900.0, 100.0),
        "pm1_ugm3": (4.0, 0.5),
        "pm25_ugm3": (14.0, 1.0),
        "pm10_ugm3": (30.0, 2.0),
        "mq4_ppm": (400.0, 50.0),
        "mq137_ppm": (5.0, 1.0),
    },
    "mid": {
        "temperature_c": (33.5, 0.5),
        "humidity_perc": (75.0, 2.0),
        "co2_ppm": (2750.0, 100.0),
        "pm1_ugm3": (20.0, 2.0),
        "pm25_ugm3": (80.0, 5.0),
        "pm10_ugm3": (500.0, 20.0),
        "mq4_ppm": (3000.0, 200.0),
        "mq137_ppm": (15.0, 1.0),
    },
    "bad": {
        "temperature_c": (37.0, 0.5),
        "humidity_perc": (85.0, 2.0),
        "co2_ppm": (3500.0, 100.0),
        "pm1_ugm3": (45.0, 2.0),
        "pm25_ugm3": (180.0, 5.0),
        "pm10_ugm3": (1100.0, 20.0),
        "mq4_ppm": (6000.0, 200.0),
        "mq137_ppm": (25.0, 1.0),
    },
}


def device_id(profile):
    if profile not in PROFILES:
        raise ValueError(f"Unknown simulation profile: {profile}")
    return f"sim-farm-{profile}"


def _utc_minute(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Simulation time must include a timezone")
    return now.astimezone(timezone.utc).replace(second=0, microsecond=0)


def generate_readings(profile, now):
    """Return the latest 120 deterministic samples, ending at the current UTC minute."""
    profile_values = PROFILES.get(profile)
    if profile_values is None:
        raise ValueError(f"Unknown simulation profile: {profile}")
    end = _utc_minute(now)
    start = end - timedelta(minutes=LOOKBACK_SAMPLES - 1)
    readings = []
    for offset in range(LOOKBACK_SAMPLES):
        recorded_at = start + timedelta(minutes=offset)
        phase = 2 * math.pi * ((int(recorded_at.timestamp()) // 60) % LOOKBACK_SAMPLES) / LOOKBACK_SAMPLES
        row = {"recorded_at": recorded_at, "device_id": device_id(profile)}
        for column in FEATURE_COLUMNS:
            center, amplitude = profile_values[column]
            row[column] = round(center + amplitude * math.sin(phase), 3)
        readings.append(row)
    return readings


def clear_history(conn):
    """Remove earlier demo runs; caller holds the worker lock and commits with seeding."""
    devices = [device_id(profile) for profile in PROFILES] + ["python-simulator"]
    with conn.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("LOCK TABLE public.air_quality_forecasts IN ACCESS EXCLUSIVE MODE")
        cursor.execute("LOCK TABLE public.air_quality_logs IN ACCESS EXCLUSIVE MODE")
        cursor.execute("DELETE FROM public.air_quality_forecasts WHERE device_id = ANY(%s)", (devices,))
        forecasts_deleted = cursor.rowcount
        cursor.execute("DELETE FROM public.air_quality_logs WHERE device_id = ANY(%s)", (devices,))
        readings_deleted = cursor.rowcount
        if readings_deleted:
            cursor.execute(
                "SELECT namespace.nspname, sequence.relname, "
                "(SELECT COALESCE(MAX(id), 0) + 1 FROM public.air_quality_logs) "
                "FROM pg_class AS sequence JOIN pg_namespace AS namespace "
                "ON namespace.oid = sequence.relnamespace "
                "WHERE sequence.oid = pg_get_serial_sequence('public.air_quality_logs', 'id')::regclass"
            )
            sequence = cursor.fetchone()
            if sequence is None:
                raise RuntimeError("The readings ID sequence is missing")
            schema, name, next_id = sequence
            cursor.execute(sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                sql.Identifier(schema, name), sql.Literal(int(next_id)),
            ))
    return {"readings_deleted": readings_deleted, "forecasts_deleted": forecasts_deleted}


def write_history(conn, profile, now):
    """Insert missing demo minutes; caller holds the worker's advisory lock."""
    rows = generate_readings(profile, now)
    columns = ("recorded_at", "device_id", *FEATURE_COLUMNS)
    values = [tuple(row[column] for column in columns) for row in rows]
    try:
        with conn.cursor() as cursor:
            execute_values(
                cursor,
                f"""INSERT INTO public.air_quality_logs ({', '.join(columns)})
                    SELECT {', '.join(f'candidate.{column}' for column in columns)}
                    FROM (VALUES %s) AS candidate ({', '.join(columns)})
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.air_quality_logs existing
                        WHERE existing.device_id = candidate.device_id
                          AND existing.recorded_at = candidate.recorded_at
                    )""",
                values,
                page_size=LOOKBACK_SAMPLES,
            )
            inserted = cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "device_id": device_id(profile),
        "inserted": inserted,
        "latest_at": rows[-1]["recorded_at"],
    }


def main(profile, argv=None):
    """Run simulation and forecasting together through the normal worker loop."""
    from ai_model.forecast_worker import main as worker_main

    return worker_main(["--simulate", profile, *(sys.argv[1:] if argv is None else argv)])

