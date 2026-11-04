# Simulation guide

Run one condition at a time, then use **Predict now** to create a two-hour forecast.

**Each launch preserves existing database readings (such as `henvironment-data.csv`), seeds 120 minute-aligned readings for the selected condition ending at the current UTC minute, and appends one reading per minute until stopped. Accounts are kept.**

| Condition | Script |
| --- | --- |
| Good | `ai_model/simulate_good.py` |
| Warning | `ai_model/simulate_mid.py` |
| Critical | `ai_model/simulate_bad.py` |
| Random (Good + Warning + Critical) | `ai_model/simulate_random.py` |

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

## Import `henvironment-data.csv` baseline dataset (optional)

To load the 9,860 historical rows from `ai_model/henvironment-data.csv` (`2026-10-20` to `2026-10-26`, IDs `1–9860`) into PostgreSQL before running simulations:

```bash
(
  set -eu
  cd "$HOME/AirQualityMonitor"
  sudo docker compose stop ingestion
  sudo docker compose up -d --wait postgres_db web_dashboard

  sudo docker cp ai_model/henvironment-data.csv postgres_db:/tmp/henvironment-data.csv

  sudo docker exec -i postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality <<'SQL'
BEGIN;
SET LOCAL TIME ZONE 'UTC';

DO $$
BEGIN
    IF to_regclass('public.air_quality_prediction_requests') IS NOT NULL THEN
        DELETE FROM public.air_quality_prediction_requests;
    END IF;
    IF to_regclass('public.air_quality_forecasts') IS NOT NULL THEN
        DELETE FROM public.air_quality_forecasts;
    END IF;
END $$;

TRUNCATE TABLE public.air_quality_logs RESTART IDENTITY;

\copy public.air_quality_logs (recorded_at, temperature_c, humidity_perc, co2_ppm, pm1_ugm3, pm25_ugm3, pm10_ugm3, mq4_ppm, mq137_ppm) FROM '/tmp/henvironment-data.csv' WITH (FORMAT csv, HEADER true)

UPDATE public.air_quality_logs
SET device_id = 'henvironment-sensor'
WHERE device_id IS NULL;

COMMIT;

SELECT COUNT(*) AS total_readings,
       MIN(recorded_at) AS earliest_utc,
       MAX(recorded_at) AS latest_utc
FROM public.air_quality_logs;
SQL

  sudo docker exec postgres_db rm -f /tmp/henvironment-data.csv
)
```

## Every simulation run

The server's configured background services start automatically after boot.
Wait for the dashboard to become available; you do not need to start them manually.
Simulation scripts still need to be started in your SSH terminal.

Stop the previous simulation with **Ctrl+C** and wait for any active prediction
to finish. Paste this block each time. It stops real-device ingestion so physical
readings cannot mix with the simulation:

```bash
(
  set -e
  cd "$HOME/AirQualityMonitor"
  sudo docker compose stop ingestion
  source ~/henvironment-simulation-venv/bin/activate
  python3 ai_model/simulate_good.py
)
```

This runs **Good**. For **Warning**, replace `simulate_good.py` with
`simulate_mid.py`. For **Critical**, use `simulate_bad.py`. For **Random**
(mixing Good, Warning, and Critical sensor values within threshold bands), use
`simulate_random.py`:

```bash
# Warning condition
(
  set -e
  cd "$HOME/AirQualityMonitor"
  sudo docker compose stop ingestion
  source ~/henvironment-simulation-venv/bin/activate
  python3 ai_model/simulate_mid.py
)

# Critical condition
(
  set -e
  cd "$HOME/AirQualityMonitor"
  sudo docker compose stop ingestion
  source ~/henvironment-simulation-venv/bin/activate
  python3 ai_model/simulate_bad.py
)

# Random mixed condition
(
  set -e
  cd "$HOME/AirQualityMonitor"
  sudo docker compose stop ingestion
  source ~/henvironment-simulation-venv/bin/activate
  python3 ai_model/simulate_random.py
)
```

Do not recreate the environment or reinstall packages each time.
The database password is already included, so you do not need to enter it.

Keep the terminal open. Each script inserts **120 readings for its condition**
(`now - 119 minutes` through `now` in UTC), then keeps adding **one reading per
minute until you press Ctrl+C**. Existing database rows are preserved.

Open `http://<server-IP>:5000/dashboard`, sign in, and click **Predict now**
after the terminal reports `seeded: 120`. The predicted values appear first,
and the advice follows when ready. You can click again after a new reading has
arrived, at least 60 seconds after the previous request, and after the current
prediction finishes. The button disables itself while it cannot run.

All 120 model inputs match the selected condition.

To stop the simulation, press **Ctrl+C**. The dashboard and prediction services
continue in the background. After the readings stop, request a prediction within
about three minutes before they become too old.

When finished with the demo, stop the simulation with **Ctrl+C** and restore
real-device ingestion:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker compose up -d ingestion
)
```

A server reboot also starts ingestion again. It does not restart a simulation.

## Apply code updates

After the changes have been pushed to the remote repository (`git push origin major_updates`), wait for the current
prediction to finish, then run:

```bash
(
  set -eu
  cd "$HOME/AirQualityMonitor"
  git pull --ff-only origin major_updates
  sudo docker compose --profile forecast up -d --build --no-deps \
    web_dashboard forecast_worker
  sudo docker compose --profile forecast ps
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

### Option A: From the webpage (recommended)

1. Stop the running simulation script in SSH with **Ctrl+C** (and wait for any active prediction to finish).
2. Open the **Data** page in the dashboard (logged in as `admin`) and click **Delete readings**.
3. In the **Time range** dropdown, select **Delete all simulation readings** (no timestamp needed).
4. Click **Delete selected readings** and confirm.

This removes all seeded and appended simulation rows (`sim-farm-good`, `sim-farm-mid`, `sim-farm-bad`, etc.) and their saved forecasts, leaves your imported dataset untouched, and resets the next reading ID right after the highest retained row (e.g. `9861`).

### Option B: From the Ubuntu SSH terminal

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
  sudo docker compose --profile forecast up -d --no-deps forecast_worker
)
```

Refresh the dashboard afterward. Technical server instructions are in
[SERVER_COMMANDS.md](../SERVER_COMMANDS.md).
The cleanup block restarts the prediction worker and leaves real-device ingestion
stopped. Start a simulation, or restore ingestion using the command above.

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
