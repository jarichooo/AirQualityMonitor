"""Standalone LAN simulator and cleanup runner for external Henvironment testers."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import venv
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

VALID_ACTIONS = ("good", "mid", "bad", "random", "delete")
PSYCOPG2_REQUIREMENT = "psycopg2-binary==2.9.11"
DEFAULT_HOST = "192.168.11.50"
DEFAULT_PORT = "5432"
DEFAULT_DB = "air_quality"
DEFAULT_USER = "admin"
DEFAULT_PASS = "notsosecretpass"

LOOKBACK_SAMPLES = 120
FEATURE_COLUMNS = (
    "temperature_c",
    "humidity_perc",
    "co2_ppm",
    "pm1_ugm3",
    "pm25_ugm3",
    "pm10_ugm3",
    "mq4_ppm",
    "mq137_ppm",
)
PROFILES = {
    "good": {
        "temperature_c": (26.0, 0.8),
        "humidity_perc": (60.0, 3.0),
        "co2_ppm": (900.0, 100.0),
        "pm1_ugm3": (4.0, 0.5),
        "pm25_ugm3": (14.0, 1.0),
        "pm10_ugm3": (30.0, 2.0),
        "mq4_ppm": (5.0, 1.0),
        "mq137_ppm": (400.0, 50.0),
    },
    "mid": {
        "temperature_c": (33.5, 0.5),
        "humidity_perc": (75.0, 2.0),
        "co2_ppm": (2750.0, 100.0),
        "pm1_ugm3": (20.0, 2.0),
        "pm25_ugm3": (80.0, 5.0),
        "pm10_ugm3": (500.0, 20.0),
        "mq4_ppm": (15.0, 1.0),
        "mq137_ppm": (3000.0, 200.0),
    },
    "bad": {
        "temperature_c": (37.0, 0.5),
        "humidity_perc": (85.0, 2.0),
        "co2_ppm": (3500.0, 100.0),
        "pm1_ugm3": (45.0, 2.0),
        "pm25_ugm3": (180.0, 5.0),
        "pm10_ugm3": (1100.0, 20.0),
        "mq4_ppm": (25.0, 1.0),
        "mq137_ppm": (6000.0, 200.0),
    },
}
RANDOM_BANDS = {
    "temperature_c": {
        "good": ((21.2, 31.8),),
        "mid": ((18.1, 20.9), (32.1, 34.9)),
        "bad": ((15.5, 17.9), (35.1, 39.0)),
    },
    "humidity_perc": {
        "good": ((50.5, 69.5),),
        "mid": ((40.5, 49.8), (70.2, 79.8)),
        "bad": ((28.0, 39.8), (80.2, 92.0)),
    },
    "co2_ppm": {
        "good": ((450.0, 2450.0),),
        "mid": ((2510.0, 2990.0),),
        "bad": ((3020.0, 4200.0),),
    },
    "pm1_ugm3": {
        "good": ((1.0, 9.8),),
        "mid": ((10.2, 34.5),),
        "bad": ((35.5, 60.0),),
    },
    "pm25_ugm3": {
        "good": ((10.2, 34.5),),
        "mid": ((35.5, 148.0),),
        "bad": ((152.0, 240.0),),
    },
    "pm10_ugm3": {
        "good": ((35.0, 148.0),),
        "mid": ((152.0, 980.0),),
        "bad": ((1010.0, 1400.0),),
    },
    "mq4_ppm": {
        "good": ((1.0, 9.8),),
        "mid": ((10.2, 19.8),),
        "bad": ((20.2, 32.0),),
    },
    "mq137_ppm": {
        "good": ((60.0, 980.0),),
        "mid": ((1020.0, 4950.0),),
        "bad": ((5050.0, 7000.0),),
    },
}
CONDITION_TIERS = ("good", "mid", "bad")
PARTICULATE_COLUMNS = ("pm1_ugm3", "pm25_ugm3", "pm10_ugm3")
INDEPENDENT_COLUMNS = tuple(col for col in FEATURE_COLUMNS if col not in PARTICULATE_COLUMNS)
VALID_PROFILES = (*PROFILES, "random")
SIMULATION_DEVICES = [f"sim-farm-{profile}" for profile in VALID_PROFILES] + [
    "sim-farm-series",
    "python-simulator",
]

WORKER_LOCK_ID = 739_783_247_071_623_201
MANUAL_SEEDER_LOCK = 739_783_247_071_623_202


def _venv_python(venv_dir):
    win_python = venv_dir / "Scripts" / "python.exe"
    if win_python.exists() or sys.platform == "win32":
        return win_python
    return venv_dir / "bin" / "python"


def _cache_venv_dir():
    base = Path.home() / ".cache"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base / "airqualitymonitor-sim-venv"
    except OSError:
        return Path(tempfile.gettempdir()) / "airqualitymonitor-sim-venv"


def resolve_host(cli_host=None, env=None):
    """Determine PostgreSQL host from CLI argument, DB_HOST, or built-in server IP."""
    env = os.environ if env is None else env
    if cli_host and cli_host.strip():
        return cli_host.strip()
    env_host = env.get("DB_HOST", "").strip()
    if env_host:
        return env_host
    return DEFAULT_HOST


def configure_db_env(host, *, port=None, user=None, dbname=None, env=None):
    """Populate PostgreSQL connection environment variables with LAN defaults."""
    target = os.environ if env is None else env
    target["DB_HOST"] = host
    target["DB_PORT"] = (port or target.get("DB_PORT") or DEFAULT_PORT).strip()
    target["DB_NAME"] = (dbname or target.get("DB_NAME") or DEFAULT_DB).strip()
    target["DB_USER"] = (user or target.get("DB_USER") or DEFAULT_USER).strip()
    target["DB_PASS"] = target.get("DB_PASS") or DEFAULT_PASS
    return target


def check_db_reachable(host, port, *, timeout=4.0, opener=socket.create_connection):
    """Verify TCP connectivity to the remote/local PostgreSQL port before connecting."""
    try:
        port_num = int(port)
        with opener((host, port_num), timeout=timeout):
            return
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot reach server at {host}:{port} ({exc}). "
            f"Check that your device is on the same network as the server, "
            f"the server is powered on, and port {port} is open on the server."
        ) from exc


def ensure_psycopg2(host, action, *, port=None, user=None, dbname=None):
    """Run inside the bootstrapped venv if the current Python lacks psycopg2."""
    if importlib.util.find_spec("psycopg2") is not None:
        return None
    venv_dir = _cache_venv_dir()
    venv_python = _venv_python(venv_dir)
    if not venv_python.exists():
        print(f"Setting up helper environment in {venv_dir} (one-time)...", file=sys.stderr)
        venv.create(venv_dir, with_pip=True)
    probe = subprocess.run(
        [str(venv_python), "-c", "import psycopg2"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        print(f"Installing {PSYCOPG2_REQUIREMENT} into {venv_dir}...", file=sys.stderr)
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", PSYCOPG2_REQUIREMENT],
            check=True,
        )
    script_path = Path(__file__).resolve()
    cmd = [str(venv_python), str(script_path), "--bootstrapped", action, host]
    if port:
        cmd.extend(["--port", port])
    if user:
        cmd.extend(["--user", user])
    if dbname:
        cmd.extend(["--db", dbname])
    return subprocess.run(cmd, cwd=str(script_path.parent), env=os.environ.copy(), check=False).returncode


def database_connection():
    import psycopg2

    password = os.environ.get("DB_PASS") or DEFAULT_PASS
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", DEFAULT_HOST),
        port=os.environ.get("DB_PORT", DEFAULT_PORT),
        database=os.environ.get("DB_NAME", DEFAULT_DB),
        user=os.environ.get("DB_USER", DEFAULT_USER),
        password=password,
        connect_timeout=5,
        options="-c timezone=UTC",
    )


def device_id(profile):
    if profile not in VALID_PROFILES:
        raise ValueError(f"Unknown simulation profile: {profile}")
    return f"sim-farm-{profile}"


def _utc_minute(now):
    return now.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _sample_band(rng, column, tier):
    low, high = rng.choice(RANDOM_BANDS[column][tier])
    return round(rng.uniform(low, high), 3)


def _random_reading(recorded_at, rng):
    row = {"recorded_at": recorded_at, "device_id": device_id("random")}
    pm_indices = sorted(rng.randrange(len(CONDITION_TIERS)) for _ in PARTICULATE_COLUMNS)
    tiers = {
        column: CONDITION_TIERS[index]
        for column, index in zip(PARTICULATE_COLUMNS, pm_indices)
    }
    independent = list(INDEPENDENT_COLUMNS)
    rng.shuffle(independent)
    for required_tier, column in zip(CONDITION_TIERS, independent):
        tiers[column] = required_tier
    for column in independent[len(CONDITION_TIERS):]:
        tiers[column] = rng.choice(CONDITION_TIERS)
    for column in FEATURE_COLUMNS:
        row[column] = _sample_band(rng, column, tiers[column])
    return row


def generate_readings(profile, now):
    """Return 120 minute-aligned readings ending at the current UTC minute."""
    if profile not in VALID_PROFILES:
        raise ValueError(f"Unknown simulation profile: {profile}")
    end = _utc_minute(now)
    start = end - timedelta(minutes=LOOKBACK_SAMPLES - 1)
    if profile == "random":
        rng = random.SystemRandom()
        return [
            _random_reading(start + timedelta(minutes=offset), rng)
            for offset in range(LOOKBACK_SAMPLES)
        ]
    profile_values = PROFILES[profile]
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


def _acquire_worker_lock(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (WORKER_LOCK_ID,))
        return bool(cursor.fetchone()[0])


def _insert_rows(conn, rows):
    from psycopg2.extras import execute_values

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


def clear_simulated_data(connection_factory=database_connection, report=print, *, retries=5, retry_delay=1.0, sleeper=time.sleep):
    """Clear any seeded simulated readings and forecasts while keeping real data intact."""
    from psycopg2 import sql

    with closing(connection_factory()) as conn:
        for attempt in range(retries):
            if _acquire_worker_lock(conn):
                break
            if attempt + 1 < retries and retry_delay > 0:
                sleeper(retry_delay)
        else:
            raise RuntimeError("A prediction is running; wait for it to finish and try again")
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("LOCK TABLE public.air_quality_forecasts IN ACCESS EXCLUSIVE MODE")
                cursor.execute("LOCK TABLE public.air_quality_logs IN ACCESS EXCLUSIVE MODE")
                cursor.execute("SELECT to_regclass('public.air_quality_prediction_requests')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute(
                        "DELETE FROM public.air_quality_prediction_requests WHERE device_id = ANY(%s)",
                        (SIMULATION_DEVICES,),
                    )
                cursor.execute(
                    "DELETE FROM public.air_quality_forecasts WHERE device_id = ANY(%s)",
                    (SIMULATION_DEVICES,),
                )
                forecasts_deleted = cursor.rowcount
                cursor.execute(
                    "DELETE FROM public.air_quality_logs WHERE device_id = ANY(%s)",
                    (SIMULATION_DEVICES,),
                )
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
                    cursor.execute(
                        sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                            sql.Identifier(schema, name), sql.Literal(int(next_id))
                        )
                    )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (WORKER_LOCK_ID,))
            conn.commit()
    summary = {"status": "cleared", "readings_deleted": readings_deleted, "forecasts_deleted": forecasts_deleted}
    report(json.dumps(summary), flush=True)
    return summary


def run_simulation(profile, connection_factory=database_connection, clock=None, sleeper=time.sleep, report=print):
    """Seed one condition, then append a reading each minute until interrupted."""
    current_device = device_id(profile)
    clock = clock or (lambda: datetime.now(timezone.utc))
    with closing(connection_factory()) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (MANUAL_SEEDER_LOCK,))
            if not cursor.fetchone()[0]:
                raise RuntimeError("Another simulation script is already running")
        if not _acquire_worker_lock(conn):
            raise RuntimeError("A prediction is running; wait for it to finish and try again")

        started_at = _utc_minute(clock())
        rows = generate_readings(profile, started_at)
        try:
            inserted = _insert_rows(conn, rows)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (WORKER_LOCK_ID,))
            conn.commit()

        report(
            json.dumps({"profile": profile, "status": "started", "device_id": current_device, "seeded": inserted}),
            flush=True,
        )
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
            report(
                json.dumps(
                    {
                        "profile": profile,
                        "device_id": current_device,
                        "recorded_at": expected_at.isoformat(),
                        "inserted": 1,
                    }
                ),
                flush=True,
            )


def run_action(action):
    """Execute the requested simulation profile or clear all seeded simulated records."""
    from psycopg2 import Error as DatabaseError

    try:
        if action == "delete":
            clear_simulated_data()
            return 0
        run_simulation(action)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        print(f"Operation failed: {exc}", file=sys.stderr)
        return 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run standalone LAN air quality simulations or delete simulated DB rows."
    )
    parser.add_argument("action", choices=VALID_ACTIONS, help="Simulation profile or 'delete'")
    parser.add_argument("host", nargs="?", help=f"Server LAN IP or hostname (default: DB_HOST or {DEFAULT_HOST})")
    parser.add_argument("--port", help=f"PostgreSQL port (default: DB_PORT or {DEFAULT_PORT})")
    parser.add_argument("--user", help=f"PostgreSQL user (default: DB_USER or {DEFAULT_USER})")
    parser.add_argument("--db", help=f"PostgreSQL database (default: DB_NAME or {DEFAULT_DB})")
    parser.add_argument("--bootstrapped", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    host = resolve_host(args.host)
    env = configure_db_env(host, port=args.port, user=args.user, dbname=args.db)
    try:
        check_db_reachable(host, env["DB_PORT"])
        if not args.bootstrapped:
            delegated = ensure_psycopg2(host, args.action, port=args.port, user=args.user, dbname=args.db)
            if delegated is not None:
                return delegated
        return run_action(args.action)
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
