"""Insert clearly labelled, minute-aligned demo readings for the forecast worker."""

import argparse
import json
import math
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

from psycopg2 import Error as DatabaseError, sql
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

MANUAL_SEEDER_LOCK = 739_783_247_071_623_202


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


def _insert_rows(conn, rows):
    columns = ("recorded_at", "device_id", *FEATURE_COLUMNS)
    values = [tuple(row[column] for column in columns) for row in rows]
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
            page_size=len(rows),
        )
        return cursor.rowcount


def clear_history(conn, *, all_readings=False):
    """Clear demo history or all readings; caller holds the worker lock and commits."""
    devices = [device_id(profile) for profile in PROFILES] + ["sim-farm-series", "python-simulator"]
    scope = "" if all_readings else " WHERE device_id = ANY(%s)"
    params = None if all_readings else (devices,)
    with conn.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("LOCK TABLE public.air_quality_forecasts IN ACCESS EXCLUSIVE MODE")
        cursor.execute("LOCK TABLE public.air_quality_logs IN ACCESS EXCLUSIVE MODE")
        cursor.execute("SELECT to_regclass('public.air_quality_prediction_requests')")
        if cursor.fetchone()[0] is not None:
            cursor.execute(
                f"DELETE FROM public.air_quality_prediction_requests{scope}", params
            )
        cursor.execute(f"DELETE FROM public.air_quality_forecasts{scope}", params)
        forecasts_deleted = cursor.rowcount
        cursor.execute(f"DELETE FROM public.air_quality_logs{scope}", params)
        readings_deleted = cursor.rowcount
        if readings_deleted or all_readings:
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
    try:
        inserted = _insert_rows(conn, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "device_id": device_id(profile),
        "inserted": inserted,
        "latest_at": rows[-1]["recorded_at"],
    }


def run_simulation(profile, connection_factory=None, clock=None, sleeper=time.sleep, report=print):
    """Seed one condition, then append a reading each minute until interrupted."""
    from ai_model.forecast_worker import WORKER_LOCK_ID, acquire_worker_lock, database_connection

    current_device = device_id(profile)
    connection_factory = connection_factory or database_connection
    clock = clock or (lambda: datetime.now(timezone.utc))
    with closing(connection_factory()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (MANUAL_SEEDER_LOCK,))
            if not cursor.fetchone()[0]:
                raise RuntimeError("Another manual simulation seeder is already running")

        if not acquire_worker_lock(conn):
            raise RuntimeError("A prediction is running; wait for it to finish and try again")
        started_at = _utc_minute(clock())
        rows = generate_readings(profile, started_at)
        try:
            cleanup = clear_history(conn, all_readings=True)
            inserted = _insert_rows(conn, rows)
            if inserted != LOOKBACK_SAMPLES:
                raise RuntimeError(f"Expected to seed {LOOKBACK_SAMPLES} readings, inserted {inserted}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (WORKER_LOCK_ID,))
            conn.commit()

        report({
            "profile": profile, "status": "started", "device_id": current_device,
            "seeded": inserted, **cleanup,
        })
        expected_at = started_at
        while True:
            expected_at += timedelta(minutes=1)
            remaining = (expected_at - clock()).total_seconds()
            if remaining > 0:
                sleeper(remaining)
            observed_at = clock()
            if _utc_minute(observed_at) != expected_at:
                raise RuntimeError(
                    f"Missed the {expected_at.isoformat()} reading; stopping instead of backfilling fake data"
                )
            row = generate_readings(profile, expected_at)[-1]
            try:
                inserted = _insert_rows(conn, [row])
                if inserted != 1:
                    raise RuntimeError(f"The {expected_at.isoformat()} reading already exists")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            report({
                "profile": profile, "device_id": current_device,
                "recorded_at": expected_at.isoformat(), "inserted": 1,
            })


def main(profile, argv=None):
    parser = argparse.ArgumentParser(description=f"Replace all sensor readings with {profile} conditions for manual predictions.")
    parser.parse_args(argv)
    try:
        run_simulation(profile, report=lambda message: print(json.dumps(message), flush=True))
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        parser.exit(1, f"Simulation failed: {exc}\n")
    return 0

