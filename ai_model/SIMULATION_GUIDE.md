# Farm simulation demonstration guide

Use this guide on the Ubuntu server in `~/AirQualityMonitor`. It assumes the
application has already been updated, PostgreSQL and dashboard accounts exist,
and the project Qwen model has been installed. The current server uses the enabled
`henvironment-ollama.service` background service. No physical sensor is needed.

The demonstration follows this flow:

**Simulated readings -> PostgreSQL -> trained GRU -> saved predictions -> Qwen
guidance -> dashboard.**

At startup, the farm scripts remove previous readings and forecasts for
`sim-farm-good`, `sim-farm-mid`, `sim-farm-bad`, and `python-simulator`. Imported
and physical-device readings, their IDs, and accounts stay unchanged. Cleanup
runs once per launch, including `--once`; the series keeps its good/mid/critical
rows until the next launch. The `_cleanup` log reports how many rows were removed.
Cleanup and the first seeded history commit together; a database failure rolls
them back. After deleting old demo rows, new IDs start at the highest retained
ID plus one. Stop earlier simulators before starting another run.

The simulator immediately supplies 120 consecutive one-minute samples ending at
the current UTC minute. The GRU uses them to forecast two hours beyond the latest
sample. The worker classifies the predictions using `reasoning ai/thresholds.md`
and asks Qwen to explain them. You do not need to wait two hours.

## Quick start for an already configured server

Use **one SSH terminal** for the simulation. Qwen already runs in the background
through `henvironment-ollama.service`, starts at boot, and stays available after
SSH disconnects. Do not run `python3 setup_reasoning_ai.py --serve` manually
alongside it. The service itself does not start the simulator or add readings.
For another server that has not been configured, follow the
[one-time background service setup](../SERVER_COMMANDS.md#keep-project-qwen-running-in-the-background).

You do **not** need to pull, rebuild images, apply the schema, or reinstall Qwen
every time. Those are preparation tasks after an update or a missing installation.
The older simulator generated readings; these farm scripts also run the trained
predictor and request Qwen guidance. Docker runs the Python script with its own
installed dependencies, so you do not need to activate a Python environment.

If PostgreSQL and the dashboard are running and other simulators/forecast workers
are stopped, check whether the project's Ollama endpoint is already listening:

```bash
curl --retry 15 --retry-connrefused --retry-delay 2 -fsS http://127.0.0.1:11435/api/tags
```

If the response lists `qwen2.5:3b`, use that running instance. The worker connects
to port **11435**. Ollama listening only
on port 11434 does not satisfy this project's configuration. A successful tags
response confirms the endpoint and model listing; it does not test generation.
If the endpoint is unavailable, use section 2 to start/check the service. An initial
connection-refused line followed by a successful JSON response means a retry
succeeded during startup. Model discovery does not verify actual guidance quality.

Then start the complete good -> warning -> critical sequence in your SSH terminal:

```bash
cd ~/AirQualityMonitor
read -rsp 'PostgreSQL password: ' DB_PASS; echo
export DB_PASS
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps --name farm_simulation forecast_worker --simulate series
```

The sequence runs five updates for each stage, then exits after the critical
stage. Expect roughly five minutes per stage and about 15 minutes overall; slow
prediction or Qwen requests can take longer. It does not skip stages to catch up
with the clock. Continue at section 3 for what to show on the dashboard. If
PostgreSQL or the webpage is stopped, use section 2 to start them first.

## 1. Prepare after pulling an update

Skip this section if you already rebuilt the latest images and applied the
forecast schema. It is not needed every time you demonstrate the application.
For the guidance-retention update, reapply the schema even if the forecast table
already exists: it adds the `last_guidance` column without resetting readings.
For the backup and pull procedure, see [server maintenance](../SERVER_COMMANDS.md#updating-an-existing-server).
New local files must be committed and pushed before a server pull can fetch them.

Stop any foreground simulator with Ctrl+C first. Then:

```bash
cd ~/AirQualityMonitor
sudo docker compose --profile forecast stop ingestion forecast_worker
sudo docker compose up -d --wait postgres_db
sudo docker compose --profile forecast build web_dashboard forecast_worker
sudo docker exec -i postgres_db \
  psql -X -v ON_ERROR_STOP=1 -U admin -d air_quality \
  < ai_model/schema.sql
sudo docker compose up -d --no-deps web_dashboard
```

Run each command only after the previous one succeeds. The forecast schema is
additive and repeatable. These commands do not replace the imported dataset or
remove dashboard accounts. No dataset import, full reset, or root sensor-schema
migration is needed for this demonstration.

## 2. Start a demonstration session

Open **one SSH terminal**. Stop the old `simulate_esp32.py`, any native forecast
worker, and any previous farm simulation before proceeding. Keep physical
publishers paused. Keep this terminal open while the simulation runs; Qwen's
background service does not need its own terminal.

Start the database, webpage, and project Qwen service. Stop regular collection
and forecasting so the demo owns the worker lock:

```bash
cd ~/AirQualityMonitor
sudo docker compose --profile forecast stop ingestion forecast_worker
sudo docker compose up -d --wait postgres_db
sudo docker compose up -d --no-deps web_dashboard
sudo docker compose ps
sudo systemctl start henvironment-ollama.service
sudo systemctl --no-pager status henvironment-ollama.service
curl --retry 15 --retry-connrefused --retry-delay 2 -fsS http://127.0.0.1:11435/api/tags
```

Expect `active (running)` and a model list containing `qwen2.5:3b`. If either check
fails, inspect the service logs before starting the simulation:

```bash
sudo journalctl -u henvironment-ollama.service -n 50 --no-pager
```

The service uses the existing project model store at
`ai_model/reasoning ai/models/` and listens on `http://127.0.0.1:11435`. No model
download or service reinstall is required for each demonstration. For missing
components, follow the setup/repair instructions in
[SERVER_COMMANDS.md](../SERVER_COMMANDS.md#optional-ollama--qwen25-reasoning-engine).

In the same SSH terminal, enter the actual PostgreSQL password. It will not be shown:

```bash
cd ~/AirQualityMonitor
read -rsp 'PostgreSQL password: ' DB_PASS; echo
export DB_PASS
```

`DB_PASS` exists in this terminal's environment. `--preserve-env=DB_PASS` in the
commands below passes it through sudo to Docker Compose. Repeat the password
step when you open a new simulation terminal.

## 3. Run the complete good -> warning -> critical sequence

In the same simulation terminal:

```bash
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps --name farm_simulation forecast_worker --simulate series
```

Leave the command running. `--name farm_simulation` gives the demo a predictable
name for checking logs or stopping it. `--rm` removes the temporary container
after it stops; it does not remove database records. `--no-deps` uses the database
you already started. Terminal progress identifies the stage and update number.

| Stage | Input conditions | Updates | Stored device ID |
| --- | --- | --- | --- |
| 1 | Good / normal | 5 | `sim-farm-good` |
| 2 | Mid / warning | 5 | `sim-farm-mid` |
| 3 | Critical | 5 | `sim-farm-bad` |

Each stage reuses the existing scenario generator and supplies its own fresh
120-minute input history. This is a sequence of three separately labelled demo
scenarios, not an accelerated real flock history. Original imported rows and
earlier stage rows stay unchanged. The command finishes automatically after the
last stage; Ctrl+C stops it early. Restarting clears the previous demo run and
starts with good again.

In an already configured native Python environment, the single script is:

```bash
python -m ai_model.simulate_series
```

`python ai_model/simulate_series.py` also works. `--once` processes only the first
good-stage update and exits; omit it for the complete sequence.

Open `http://<server-IP>:5000/dashboard` in your browser and sign in using an
existing account. Replace `<server-IP>` with the server's LAN address.

For each stage, show:

1. **Stored readings and trends:** click Refresh to include the new demo rows.
   The hourly trends cover all stored dates, including the imported dataset.
   The individual-reading graph shows the latest recorded hour.
2. **2-hour forecast:** show the predicted values first. The target is two hours
   after the input cutoff. Values use **green for Normal, amber/yellow for Warning,
   and red for Critical**, with written category labels. Device details, timestamps,
   completed-stage messages, and empty cards are hidden from this panel.
3. **Reasoning AI guidance:** show Qwen's explanation below the predicted values.
   Numbers are saved before guidance, so the first response can initially be pending.
   After Qwen completes a response, that guidance stays visible across page reloads
   while the next response is being prepared, or if Qwen's update fails. A short
   status identifies it as previous guidance. When readings stop,
   it remains labelled historical; it is not a fresh forecast. New completed
   guidance replaces it automatically. Changing the model or thresholds, deleting
   its device's readings, or completing a simulation stage clears retained guidance.
   Apply `ai_model/schema.sql` after pulling this update (section 1); its new
   `last_guidance` column preserves existing sensor readings and accounts.

The forecast panel refreshes every 60 seconds while the tab is visible. The
measured charts require Refresh. Qwen's default request timeout is 90 seconds;
slow generation can make a worker cycle take longer than a minute. The terminal
cycle summary appears after that cycle's reasoning attempt finishes.

The terminal's input scenario and the displayed forecast categories describe different
things. A good input scenario does not force the model to predict Normal. Each predicted channel
keeps the category computed from its actual forecast value and the policy.

The Live ESP32 panel can remain disconnected throughout this demonstration.
The new simulator writes to PostgreSQL directly, bypassing MQTT and the device.

## 4. Optional: run an individual scenario instead

The complete sequence switches automatically; no command change or second terminal
is needed. The existing individual scripts remain available if you want one stage
to run continuously until Ctrl+C. Stop the sequence before running any of these:

```bash
# Good inputs only
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps --name farm_simulation forecast_worker --simulate good
```

Press **Ctrl+C in your simulation terminal** and wait for the command to exit.
Leave the Qwen background service running. Then choose the next scenario:

```bash
# Warning inputs
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps --name farm_simulation forecast_worker --simulate mid
```

Repeat the dashboard observations for `sim-farm-mid`. Stop it with Ctrl+C before
starting the final scenario:

```bash
# Critical inputs
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps --name farm_simulation forecast_worker --simulate bad
```

| Scenario | Input category under the project policy | Device shown on the page |
| --- | --- | --- |
| Good | Normal | `sim-farm-good` |
| Mid | Warning | `sim-farm-mid` |
| Bad | Critical | `sim-farm-bad` |

These categories describe the **generated inputs**. The trained model chooses
the future values, which can belong to another category. Do not describe a
scenario name as a guaranteed prediction or evidence of field accuracy.
The project policy uses general temperature limits without age/week groups;
MQ-4 is methane and MQ-137 is ammonia.

Each scenario has a separate device ID and receives its own immediate starting
history. The automatic sequence keeps all three stages' readings for its current
run and marks the previous forecast unavailable at stage changes. Starting a
standalone script clears all earlier simulation readings and forecasts first.
All-stored averages include the import and the current simulation run; they are
not isolated scenario averages. No manual deletion is needed before a new run.

## 5. Verify and record what you demonstrated

The simulation prints its cycle results in your one terminal. The following
additional checks are optional: run them after stopping the simulation, or in
another SSH terminal if you want to inspect it while it continues running.
Qwen does not require that extra terminal.

```bash
cd ~/AirQualityMonitor
sudo docker logs --since=5m --tail=30 farm_simulation

sudo docker exec postgres_db psql -U admin -d air_quality -c \
  "SELECT device_id, COUNT(*) AS readings, MIN(recorded_at), MAX(recorded_at) FROM public.air_quality_logs GROUP BY device_id ORDER BY device_id;"

sudo docker exec postgres_db psql -U admin -d air_quality -c \
  "SELECT device_id, status, input_cutoff_at, target_at, explanation_status, reason FROM public.air_quality_forecasts ORDER BY device_id;"
```

Expect the active `sim-farm-*` group to have at least 120 rows and its latest
timestamp to advance as cycles complete. For the restored October 20-26 dataset,
the `original-import` group should still have **9,860 rows**, spanning
`2026-10-20 03:40:25 UTC` through `2026-10-26 23:59:00 UTC`.
Simulation groups are additional rows; their presence increases the table total.
The forecast should be `available`; successful Qwen guidance has
`explanation_status = available`. A terminal result of `created` indicates a
saved forecast with guidance; `explanation_error` means the reasoning attempt
failed even though numerical predictions may already be saved.

The automatic sequence stops early on a model, input, lock, policy, or database
failure rather than claiming that every stage ran. A Qwen guidance error can be
nonfatal because the numerical forecast was already saved; check each stage's
`explanation_status` and the final `guidance_errors` count instead of treating
sequence completion as proof that all guidance succeeded.

For each scenario, record a screenshot or the following observations:

| Scenario/device | Input cutoff (UTC) | Forecast target (UTC) | Predicted values/categories | Qwen guidance |
| --- | --- | --- | --- | --- |
| `sim-farm-good` | | | | |
| `sim-farm-mid` | | | | |
| `sim-farm-bad` | | | | |

An example presentation sentence is: "These inputs are simulated. The trained
model estimates conditions two hours after this input timestamp. Qwen explains
the predicted threshold categories and suggests checks based on our policy."
Read the actual displayed guidance rather than promising a fixed response.

## 6. Stop and restart later

1. Let the sequence finish, or press Ctrl+C and wait for the simulation to stop.
2. Leave `henvironment-ollama.service` running. Ctrl+C in the simulation terminal
   does not stop Qwen, and no extra terminal needs to be closed.
3. Leave PostgreSQL and the dashboard running if you still need the recorded
   data on the webpage. Ingestion and the regular forecast worker remain stopped.

New demo inputs and forecasts stop. Saved readings remain; the final forecast
eventually becomes stale. The forecast table holds only the latest result per
device, so capture screenshots during the demonstration if you need a record.

For the next session, use **Quick start** if the services are already running;
otherwise repeat **sections 2 and 3**, choosing whichever scenario you need.
No pull, rebuild, reinstall, or dataset replacement is needed unless
the code, dependencies, or installation changed. After an SSH disconnect, check
for a leftover container before restarting; disconnecting is not a reliable way
to stop a Docker command. Qwen remains available independently of the SSH session.

To inspect or restart Qwen without opening a dedicated serving terminal:

```bash
sudo systemctl --no-pager status henvironment-ollama.service
sudo journalctl -u henvironment-ollama.service -n 50 --no-pager
sudo systemctl restart henvironment-ollama.service
```

Restart only when troubleshooting. The service is already enabled to start at
boot. If you deliberately want to stop Qwen now, use
`sudo systemctl stop henvironment-ollama.service`; start it again before the next
demo. This is independent of stopping the simulator.

## 7. Remove simulation readings after the demo

Each farm-script launch automatically removes earlier simulation rows. The
current run remains after it stops, so its rows still contribute to the trends
until the next launch or manual cleanup. Imported CSV rows stay unchanged.
To return the charts to the retained dataset immediately after a demo:

1. Stop the simulation with Ctrl+C. Pause physical devices and stop the regular
   database writers:

   ```bash
   cd ~/AirQualityMonitor
   sudo docker compose --profile forecast stop ingestion forecast_worker
   ```

   Check for leftover one-off simulators using the troubleshooting commands below.
   Keep PostgreSQL and the webpage running.
2. Back up the database using [the maintenance instructions](../SERVER_COMMANDS.md#back-up-before-maintenance).
   That backup procedure stops the webpage; restart it afterward with
   `sudo docker compose up -d --no-deps web_dashboard` before using the Data page.
3. Sign in as an **admin**, open **Data**, and expand **Delete readings**. Choose
   deletion from a timestamp onward and keep the **simulated readings only** scope.
   This scope matches only `sim-farm-good`, `sim-farm-mid`, `sim-farm-bad`, and
   `python-simulator`. Imported data and physical-device rows are protected by
   this scope even if their timestamps overlap.
4. Enter the **earliest simulation timestamp to remove, in UTC**. Deletion includes
   that timestamp. The script initially seeds 119 minutes before its current
   minute, so choosing the command's launch time can leave the seeded rows behind.
   Use the per-device `MIN(recorded_at)` query in section 5; a cutoff earlier than
   all demo rows removes all of them under the simulated-only scope. Manila time
   is UTC+8: for example, 10:00 Manila is 02:00 UTC on the same date.
5. Read the confirmation's timestamp and device scope, then confirm. This is
   permanent and applies independently of the table's search/date filters.
   Selecting all devices also deletes any imported or physical-device readings
   at or after the cutoff. The separate full-wipe mode deletes every reading.
6. Check the reported deleted count and next ID, rerun the per-device count query,
   and refresh the dashboard. Forecasts for devices whose readings were deleted
   are removed too, so old advice is not kept for cleaned-up inputs.

No retained reading ID changes. After restoring the current dataset and removing
all later demo rows, a highest remaining ID of **9,860** gives the next insert
**9,861**. If a higher retained ID exists, the next ID must follow it;
existing gaps are not filled or renumbered. If matching deletion leaves the table
empty, the next ID is 1. If no rows match, the ID sequence is left unchanged.
The database locks the operation and restarts the sequence in the same transaction;
a failure rolls back the deletion and sequence restart together.

Keep simulators stopped after cleanup or they will add demo readings again. This
cleanup changes the server database only when you submit and confirm the form;
pulling the update or rebuilding the dashboard does not delete anything.

## Troubleshooting

| Symptom | Check or action |
| --- | --- |
| `another forecast worker is running` | Stop the regular worker with `sudo docker compose --profile forecast stop forecast_worker`. Stop any other native or one-off worker too. Only one worker can hold the lock. |
| Container name `farm_simulation` is already in use | Check `sudo docker ps -a --filter name=farm_simulation`. If it is your previous demo, use `sudo docker stop farm_simulation`; if a stopped container remains, use `sudo docker rm farm_simulation`, then retry. |
| An older, unnamed demo may still be running | List workers with `sudo docker ps --filter label=com.docker.compose.service=forecast_worker`. Stop the specific old demo using `sudo docker stop CONTAINER_ID`, replacing `CONTAINER_ID` with its listed ID. |
| Password missing or authentication failed | Repeat the password prompt in your simulation terminal, use the actual database password, and retain `--preserve-env=DB_PASS`. |
| Connection to PostgreSQL fails | Check `sudo docker compose ps` and `sudo docker compose logs --tail=40 postgres_db`. Start PostgreSQL as in section 2. |
| Forecast table does not exist | Apply `ai_model/schema.sql` using the command in section 1. Do not reset the database. |
| Forecasts are saved, but no Qwen guidance appears | Check `sudo systemctl --no-pager status henvironment-ollama.service`, `sudo journalctl -u henvironment-ollama.service -n 50 --no-pager`, and `sudo docker logs --tail=40 farm_simulation`. The worker logs reasoning errors and retries on later cycles. |
| Port 11435 is already in use | The background service normally owns this port. Check its status and `/api/tags`; do not start manual `--serve` alongside it. If the service itself cannot bind, stop the earlier manually started project server, then restart the service. |
| A curl connection error appears before a successful model list | The retry reached the endpoint after startup. A final successful response listing `qwen2.5:3b` confirms discovery. |
| Old-looking webpage or empty historical charts | Confirm the dashboard was rebuilt from the updated source, then refresh the browser and check the row-count query in section 5. A Git pull alone does not rebuild an image. |
| Previous scenario still appears | Check the device IDs. Old forecast entries can remain listed as unavailable, and old measurements remain in trends. |
| No terminal output immediately after startup | The cycle summary waits for the prediction and Qwen attempt to finish. Check the saved forecast query or webpage while waiting; inspect logs if it does not progress. |

The server output confirmed that `henvironment-ollama.service` is enabled and
running and that port 11435 lists `qwen2.5:3b`. The commands have also been checked
against the repository. That confirms service startup and model discovery; a
complete simulation run is still needed to verify guidance generation and quality.
