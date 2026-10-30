"""Seed a Good to Warning to Critical demo for manual predictions."""

import argparse
import json
import sys
from pathlib import Path

from psycopg2 import Error as DatabaseError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_model.simulation import run_simulation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = run_simulation(report=lambda message: print(json.dumps(message), flush=True))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        parser.exit(1, f"Simulation failed: {exc}\n")
    print(json.dumps(result), flush=True)
