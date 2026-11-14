"""Zero-dependency LAN launcher for condition simulations and cleanup."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

VALID_ACTIONS = ("good", "mid", "bad", "random", "delete")
PSYCOPG2_REQUIREMENT = "psycopg2-binary==2.9.11"
DEFAULT_HOST = "192.168.11.50"
DEFAULT_PORT = "5432"
DEFAULT_DB = "air_quality"
DEFAULT_USER = "admin"
DEFAULT_PASS = "notsosecretpass"


def _repo_root():
    return Path(__file__).resolve().parent.parent


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
            f"Cannot reach PostgreSQL at {host}:{port} ({exc}). "
            f"Check that both devices are on the same network, the server IP is right, "
            f"postgres_db is running, and TCP port {port} is open on the server."
        ) from exc


def ensure_psycopg2(repo_root, host, action, *, port=None, user=None, dbname=None):
    """Run inside the bootstrapped venv if the current Python lacks psycopg2."""
    if importlib.util.find_spec("psycopg2") is not None:
        return None
    venv_dir = _cache_venv_dir()
    venv_python = _venv_python(venv_dir)
    if not venv_python.exists():
        print(f"Setting up isolated simulation environment in {venv_dir}...", file=sys.stderr)
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
    cmd = [str(venv_python), "-m", "ai_model.lan_runner", "--bootstrapped", action, host]
    if port:
        cmd.extend(["--port", port])
    if user:
        cmd.extend(["--user", user])
    if dbname:
        cmd.extend(["--db", dbname])
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(repo_root)
    return subprocess.run(cmd, cwd=str(repo_root), env=env, check=False).returncode


def run_action(action):
    """Execute the requested simulation profile or clear all seeded simulated records."""
    from psycopg2 import Error as DatabaseError
    from ai_model.simulation import clear_simulated_data, main as simulation_main

    if action == "delete":
        try:
            clear_simulated_data(report=lambda message: print(json.dumps(message), flush=True))
            return 0
        except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
            print(f"Cleanup failed: {exc}", file=sys.stderr)
            return 1
    return simulation_main(action, [])


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run AirQualityMonitor LAN condition simulations or delete simulated DB rows."
    )
    parser.add_argument("action", choices=VALID_ACTIONS, help="Simulation profile or 'delete'")
    parser.add_argument("host", nargs="?", help="Server LAN IP or hostname (defaults to DB_HOST or prompt)")
    parser.add_argument("--port", help="PostgreSQL port (default: DB_PORT or 5432)")
    parser.add_argument("--user", help="PostgreSQL user (default: DB_USER or admin)")
    parser.add_argument("--db", help="PostgreSQL database (default: DB_NAME or air_quality)")
    parser.add_argument("--bootstrapped", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    host = resolve_host(args.host)
    env = configure_db_env(host, port=args.port, user=args.user, dbname=args.db)
    try:
        check_db_reachable(host, env["DB_PORT"])
        if not args.bootstrapped:
            delegated = ensure_psycopg2(
                _repo_root(), host, args.action, port=args.port, user=args.user, dbname=args.db
            )
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

