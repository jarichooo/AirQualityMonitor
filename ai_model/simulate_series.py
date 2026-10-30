"""Run the good, warning, and critical farm simulations in sequence."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_model.forecast_worker import main


if __name__ == "__main__":
    raise SystemExit(main(["--simulate", "series", *sys.argv[1:]]))
