"""Seed 120 demo readings, then add one reading per minute for five minutes."""

import argparse
import json
import sys
from pathlib import Path

from psycopg2 import Error as DatabaseError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_model.simulation import PROFILES, run_manual_seed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="good")
    args = parser.parse_args()
    try:
        result = run_manual_seed(args.profile, report=lambda message: print(json.dumps(message), flush=True))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, RuntimeError, ValueError, DatabaseError) as exc:
        parser.exit(1, f"Simulation failed: {exc}\n")
    print(json.dumps(result), flush=True)
