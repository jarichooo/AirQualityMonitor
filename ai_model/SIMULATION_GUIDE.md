# Simulation guide

Run one condition at a time, then use **Predict now** to create a two-hour forecast.

**Each launch deletes all existing sensor readings, forecasts, and prediction
requests, then seeds the selected condition. Accounts are kept.**

| Condition | Script/module |
| --- | --- |
| Good | `ai_model.simulate_good` |
| Warning | `ai_model.simulate_mid` |
| Critical | `ai_model.simulate_bad` |

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
    forecast_worker -m ai_model.simulate_good
)
```

This block runs **Good**. To run **Warning** or **Critical**, change only the last
module name to `ai_model.simulate_mid` or `ai_model.simulate_bad`, then paste the
whole block again after stopping the previous run.

Keep the terminal open. Each script inserts **120 readings for its condition**,
then keeps adding **one reading per minute until you press Ctrl+C**. There is no
five-minute limit. The scripts do not run the predictive model or Qwen themselves.

Open `http://<server-IP>:5000/dashboard`, sign in, and click **Predict now**
after the terminal reports `seeded: 120`. The predicted values appear first;
Qwen guidance follows when ready. You can click again after a new reading has
arrived, at least 60 seconds after the previous request, and after the current
prediction finishes. The button disables itself while it cannot run.

All 120 model inputs match the selected condition. Forecast colours still follow
the model's own predicted values; they are not forced to match the input label.

Each new condition replaces the previous data and restarts reading IDs at 1.
If seeding fails, the reset rolls back. Stop other simulations and wait for any
active prediction to finish before starting another condition.

To stop the simulation, press **Ctrl+C**. The dashboard, forecast worker, and Qwen service
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
