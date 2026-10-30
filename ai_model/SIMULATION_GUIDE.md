# Simulation guide

Run one condition at a time, then use **Predict now** to create a two-hour forecast.

**Each launch deletes all existing sensor readings, forecasts, and prediction
requests, then seeds the selected condition. Accounts are kept.**

| Condition | Script |
| --- | --- |
| Good | `ai_model/simulate_good.py` |
| Warning | `ai_model/simulate_mid.py` |
| Critical | `ai_model/simulate_bad.py` |

## One-time Python setup

Connect through SSH and paste this once. Skip it on later runs:

```bash
(
  set -e
  sudo apt install -y python3-venv
  python3 -m venv ~/henvironment-simulation-venv
  ~/henvironment-simulation-venv/bin/python3 -m pip install psycopg2-binary==2.9.11
)
```

## Start the background services

Run this once before presenting. The services keep running when
you stop a simulation or change conditions; you only need one SSH terminal.

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker compose stop ingestion
  sudo docker compose up -d --wait postgres_db
  sudo docker compose up -d --no-deps web_dashboard
  sudo systemctl start henvironment-ollama.service
  curl --retry 15 --retry-connrefused --retry-delay 2 -fsS \
    http://127.0.0.1:11435/api/tags >/dev/null

  sudo docker compose --profile forecast up \
    -d --no-deps forecast_worker
)
```

## Every simulation run

Stop the previous simulation with **Ctrl+C** and wait for any active prediction
to finish. This is the only block you need each time:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  source ~/henvironment-simulation-venv/bin/activate
  python3 ai_model/simulate_good.py
)
```

This runs **Good**. For **Warning**, replace `simulate_good.py` with
`simulate_mid.py`. For **Critical**, use `simulate_bad.py`. Do not recreate the
environment or reinstall packages each time.

The database password is already included, so you do not need to enter it.

Keep the terminal open. Each script inserts **120 readings for its condition**,
then keeps adding **one reading per minute until you press Ctrl+C**. There is no
five-minute limit. The scripts only supply readings.

Open `http://<server-IP>:5000/dashboard`, sign in, and click **Predict now**
after the terminal reports `seeded: 120`. The predicted values appear first,
and the advice follows when ready. You can click again after a new reading has
arrived, at least 60 seconds after the previous request, and after the current
prediction finishes. The button disables itself while it cannot run.

All 120 model inputs match the selected condition. Forecast colours still follow
the model's own predicted values; they are not forced to match the input label.

Each new condition replaces the previous data and restarts reading IDs at 1.
If seeding fails, the reset rolls back.

To stop the simulation, press **Ctrl+C**. The dashboard and prediction services
continue in the background. After the readings stop, request a prediction within
about three minutes before they become too old.

## Remove simulation data without starting another run

Stop the simulation first, then paste this block. It deletes simulation data
only and does not start a new simulation:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker compose --profile forecast stop ingestion forecast_worker
  sudo docker compose up -d --wait postgres_db
  sudo docker compose --profile forecast run \
    --rm --no-deps -T --entrypoint python forecast_worker - <<'PY'
from contextlib import closing
from ai_model.forecast_worker import database_connection, acquire_worker_lock
from ai_model.simulation import clear_history

with closing(database_connection()) as conn:
    if not acquire_worker_lock(conn):
        raise SystemExit("Stop other simulations or forecast workers first.")
    with conn:
        print(clear_history(conn))
PY
)
```

Refresh the dashboard afterward. Technical server instructions are in
[SERVER_COMMANDS.md](../SERVER_COMMANDS.md).
Start the background services again before using **Predict now** after cleanup.
