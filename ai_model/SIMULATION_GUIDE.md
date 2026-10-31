# Simulation guide

Run one condition at a time, then use **Predict now** to create a two-hour forecast.

**Each launch deletes all existing sensor readings, forecasts, and prediction
requests, then seeds the selected condition. Accounts are kept.**

| Condition | Script |
| --- | --- |
| Good | `ai_model/simulate_good.py` |
| Warning | `ai_model/simulate_mid.py` |
| Critical | `ai_model/simulate_bad.py` |

The Python scripts write readings to PostgreSQL. **Predict now** sends a job to
the background worker, which runs the trained forecast model. Qwen then uses
the forecast and `ai_model/reasoning ai/thresholds.md` to produce the advice.
Both models run locally; the simulation does not start predictions automatically.

## What the dashboard shows

The prediction box shows the model's values first and the advice below them.
Its legend is green for **Normal**, yellow for **Warning**, and red for
**Critical**. These colours describe the actual forecast, not the condition
script you started. Methane and ammonia use those simple names in the forecast.
"Loading…" means the advice is still being generated. "Updating…" keeps the
previous output visible while the next response is generated.

Both charts label the horizontal axis **Time (UTC)** and the vertical axis with
the selected sensor and its unit.

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
If [automatic startup](../SERVER_COMMANDS.md#automatic-startup) is configured,
these services already start at boot. Still run this block before a simulation:
it pauses real sensor ingestion so the demo data stays separate. A reboot starts
ingestion again; it does not restart the simulation.

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

All 120 model inputs match the selected condition.

Each new condition replaces the previous data and restarts reading IDs at 1.
If seeding fails, the reset rolls back.

To stop the simulation, press **Ctrl+C**. The dashboard and prediction services
continue in the background. After the readings stop, request a prediction within
about three minutes before they become too old.

## Apply code updates

After the changes have been pushed to the remote repository, wait for the current
prediction to finish, then run:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  git pull --ff-only origin major_updates
  sudo docker compose --profile forecast up -d --build --no-deps \
    web_dashboard forecast_worker
)
```

A Git pull alone does not update the running containers. The command above
rebuilds and restarts them. Refresh the webpage with **Ctrl+Shift+R** afterward.
If a simulation script changed too, stop it and start it again to load that change.

If Git reports diverging branches or local changes, stop and send the error to
the maintainer; do not force the update.

## If a prediction fails

"Wait 25 seconds" is the button's cooldown, not the reason for the failure.
The numbers may have been saved even if the advice failed. Check the actual error:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker exec postgres_db psql -U admin -d air_quality \
    -c "SELECT status, error FROM air_quality_prediction_requests WHERE id = 1;"
  sudo docker compose --profile forecast logs --since=10m --tail=60 forecast_worker
)
```

`done_reason=length` means the advice was cut off at its output token limit.
Tokens are pieces of text; this limit also applies offline. The current worker
allows **384 output tokens**, asks for **at most 50 words**, and has a **120-second
response timeout**. Rebuild the worker after pulling changes to these settings.
An actual timeout is a different error from reaching the token limit.

After checking the error, wait for a new reading and for the button to become
available, then click **Predict now** again. Save the log output if it keeps failing.

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

## Physical device note

For a live device, let it collect about two hours of readings, then click
**Predict now**. You do not need to run a simulation. The prediction worker groups
readings into UTC minutes and uses the latest reading in each minute.

After a short disconnection, it can estimate up to **two consecutive missing
minutes**, with at most **six missing minutes in the 120-minute window**. It needs
actual readings on both sides of each gap and a latest reading no more than
three minutes old. Longer gaps or insufficient history block prediction.
Estimated inputs are used only for prediction; the database and measured charts
keep the original readings. Check accuracy with real device data before relying
on forecasts that use estimated inputs.
