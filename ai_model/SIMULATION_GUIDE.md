# Simulation guide

Use this for the final dashboard demonstration. The simulator starts with good
conditions, then changes to warning conditions, then critical conditions. You
use **Predict now** whenever you want to create a two-hour forecast.

Stop any previous simulation with **Ctrl+C**, then paste this into the server's
SSH terminal:

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
  sudo --preserve-env=DB_PASS docker compose --profile forecast up \
    -d --no-deps forecast_worker
  sudo --preserve-env=DB_PASS docker compose --profile forecast run \
    --rm --no-deps --name farm_simulation --entrypoint python \
    forecast_worker -m ai_model.simulate
)
```

Keep the terminal open. It immediately inserts **120 good-condition readings**,
then inserts one reading per minute for **15 minutes**: five good, five warning,
and five critical. It then stops. The simulator does not run the predictive
model or Qwen by itself.

Open `http://<server-IP>:5000/dashboard`, sign in, and click **Predict now**
after the terminal reports `seeded: 120`. The predicted values appear first;
Qwen guidance follows when ready. You can click again after a new reading has
arrived, at least 60 seconds after the previous request, and after the current
prediction finishes. The button disables itself while it cannot run.

The stage describes the inserted readings. The forecast can change more
gradually because the model uses the most recent 120 readings; its colours are
based on its own predicted values.

Starting the simulation removes only earlier simulator readings, forecasts, and
queued simulator requests. Imported readings, physical sensor readings, accounts,
and their existing IDs stay intact. Do not run two simulations together.

To stop early, press **Ctrl+C**. The dashboard, forecast worker, and Qwen service
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
        print(clear_history(conn))
PY
)
```

Refresh the dashboard afterward. Technical server instructions are in
[SERVER_COMMANDS.md](../SERVER_COMMANDS.md).
