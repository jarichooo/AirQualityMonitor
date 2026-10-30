# Simulation guide

Use this guide after the server has been set up and updated.

## Automatic series: Good → Warning → Critical

Stop any previous simulation with **Ctrl+C**, then paste this block into the
server's SSH terminal:

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker compose --profile forecast stop ingestion forecast_worker
  sudo docker compose up -d --wait postgres_db
  sudo docker compose up -d --no-deps web_dashboard
  sudo systemctl start henvironment-ollama.service
  curl --retry 15 --retry-connrefused --retry-delay 2 -fsS \
    http://127.0.0.1:11435/api/tags >/dev/null

  read -rsp 'PostgreSQL password: ' DB_PASS; echo
  export DB_PASS
  sudo --preserve-env=DB_PASS docker compose --profile forecast run \
    --rm --no-deps --name farm_simulation \
    forecast_worker --simulate series
)
```

Keep the terminal open. The series runs **Good → Warning → Critical**, with
**five updates per stage**, then stops. Allow about 15 minutes or longer if Qwen
is slow. Press **Ctrl+C** to stop early; paste the same block to run again.

Open `http://<server-IP>:5000/dashboard` and sign in. **Predictions and Qwen
guidance run automatically during this series. Do not click Predict now.**
The prediction panel updates automatically; use **Refresh** for measured readings
and charts. Forecast colors follow the model's predicted values, so they may
differ from the input stage.

Starting a run removes previous simulation readings and forecasts. Original
readings and accounts stay intact. Do not run the series and manual demo together.

## Manual prediction: seed readings, then click Predict now

### 1. Start

Connect to the server through SSH. Stop any previous simulation with **Ctrl+C**
and wait for it to finish. Keep physical sensor collection paused during the demo.

Paste this whole block into the server terminal:

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

  read -rsp 'PostgreSQL password: ' DB_PASS; echo
  export DB_PASS
  sudo --preserve-env=DB_PASS docker compose --profile forecast up \
    -d --no-deps forecast_worker
  sudo --preserve-env=DB_PASS docker compose --profile forecast run \
    --rm --no-deps --name farm_simulation --entrypoint python \
    forecast_worker -m ai_model.simulate_manual --profile good
)
```

Enter the passwords when prompted. Typed passwords are hidden.
Keep this terminal open. Qwen and the prediction worker run in the background;
you only need one terminal.

The script inserts **120 readings immediately**, then **one new reading each
minute for five minutes**: **125 readings total**. It does not run predictions.
For warning or critical inputs, change `--profile good` to `--profile mid` or
`--profile bad` before starting another run.

**Starting a run removes earlier simulation readings and forecasts.**
The original dataset, real sensor readings, and user accounts stay intact.

### 2. View the results

Open `http://<server-IP>:5000/dashboard` and sign in.

- Once the terminal shows `seeded: 120`, click **Predict now** on the dashboard.
- **Predicted values in 2 hours** shows the model's numbers first.
- **Reasoning AI guidance** below shows Qwen's explanation. Its first response
  can take around two minutes; it appears automatically when ready.
- Green means Normal, yellow means Warning, and red means Critical.
- Click **Refresh** to update the measured readings and charts.

You can request another prediction when a new reading arrives, at least
60 seconds after the previous request, and the previous prediction has finished.
The button disables itself while waiting. The server enforces these rules even
if the page is reloaded or someone clicks from another browser.

You do not need to wait two hours. The model predicts two hours ahead now.

### 3. Stop or run again

The seeder stops automatically after the five new readings. Press **Ctrl+C** to
stop it early. Qwen, the prediction worker, and the dashboard stay running.

To run again, paste the same startup block. Do not run two simulations at once.
Results from the current run remain stored until the next run starts.
Wait for any active prediction to finish before restarting. After readings stop,
you have about three minutes to request a prediction before the data is too old.

## Remove simulation data without starting another run

Stop the simulation with **Ctrl+C** first, then paste this block into the server
terminal. It removes only simulation readings and forecasts. The original dataset,
real sensor readings, and accounts stay intact; retained IDs do not change.

```bash
(
  set -e
  cd ~/AirQualityMonitor
  sudo docker compose --profile forecast stop ingestion forecast_worker
  sudo docker compose up -d --wait postgres_db
  read -rsp 'PostgreSQL password: ' DB_PASS; echo
  export DB_PASS
  sudo --preserve-env=DB_PASS docker compose --profile forecast run \
    --rm --no-deps -T --entrypoint python forecast_worker - <<'PY'
from contextlib import closing
from ai_model.forecast_worker import database_connection, acquire_worker_lock
from ai_model.simulation import clear_history

with closing(database_connection()) as conn:
    if not acquire_worker_lock(conn):
        raise SystemExit("Stop other simulations or forecast workers first.")
    with conn:
        removed = clear_history(conn)
    print(removed)
PY
)
```

The command prints deleted counts and does not start a simulation. Refresh the
webpage afterward. Collection stays stopped until you enable it again.

If a command fails or Qwen reports an error, save the terminal message and ask
the person maintaining the server. Technical setup and cleanup instructions are
in [SERVER_COMMANDS.md](../SERVER_COMMANDS.md).
