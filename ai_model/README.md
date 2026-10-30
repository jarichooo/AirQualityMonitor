# Two-hour environmental forecasts

For the server demonstration procedure, including switching scenarios and stopping
or restarting a session, follow [SIMULATION_GUIDE.md](SIMULATION_GUIDE.md).

The worker reads committed PostgreSQL sensor history every 60 seconds, separately
for each device. A trained predictor returns the sensor values expected **120
minutes after the newest input timestamp**. It saves those values before asking
Qwen2.5 for an explanation based on `reasoning ai/thresholds.md`.

The dashboard reads the saved values through authenticated `GET /api/forecasts`
and refreshes its forecast panel every minute. It shows device, input time,
forecast target, values, units, threshold classifications and Qwen guidance.
Measured readings remain in their existing panels. This is an endpoint forecast,
not a graph of every intervening minute, and does not predict FCR or mortality.

The supplied GRU is connected through `predictor.py`. The artifact is
`microclimate_forecaster.joblib`, **not an HDF5 file**; keep its original extension.
`predict.py` is the supplied inference implementation, with its normalization,
time features and NumPy CPU evaluation. No TensorFlow or PyTorch is needed.
The artifact contains its trained weights and normalization; there is no separate
scaler to install. Missing, changed or incompatible artifacts report unavailable.

## Delivered model and adapter

The handoff was copied from `client_handoff`, without its environment, training
dataset, notebook or generated scratch predictions. The guide, example input and
expected output, evaluation metrics and graphs are included for verification.
See [MODEL_GUIDE.md](MODEL_GUIDE.md) for the delivery's evaluation limitations.

The adapter uses this delivered contract:

| Metadata | Required meaning |
| --- | --- |
| `MODEL_VERSION` | `gru-v2-eaef59203a16`, identifying this artifact and stable CPU inference implementation. |
| `LOOKBACK_SAMPLES` | 120 consecutive one-minute rows. |
| `FEATURE_COLUMNS` | All eight database channels, in the order below. |
| `TARGET_COLUMNS` | The same eight channels, from the final forecast row. |
| `SAMPLE_INTERVAL_SECONDS` | 60, matching the collection interval. |
| `CADENCE_TOLERANCE_SECONDS` | 0: exact one-minute intervals. |
| `HORIZON_MINUTES` | 120; the model must actually have been trained for this horizon. |

`predict(history_rows)` receives the full lookback ordered oldest to newest.
Each row contains `id`, timezone-aware `recorded_at`, `device_id` and the declared
features. It must return exactly `{target_column: native_python_number}` for
`TARGET_COLUMNS`. Convert NumPy scalars/arrays inside the adapter. Never return
an array of unknown shape directly to the worker.

Supported names and units are:

| Column | Meaning | Unit |
| --- | --- | --- |
| `temperature_c` | Temperature | degrees C |
| `humidity_perc` | Relative humidity | % RH |
| `co2_ppm` | Carbon dioxide | ppm |
| `pm1_ugm3`, `pm25_ugm3`, `pm10_ugm3` | PM1.0, PM2.5, PM10 | micrograms/m3 |
| `mq4_ppm` | Methane estimate, MQ-4 | ppm |
| `mq137_ppm` | Ammonia estimate, MQ-137 | ppm |

The artifact is loaded once and cached. Its SHA-256 is checked before Joblib
deserialization:

```text
eaef59203a1654e4c51c626420f0a558e3aab939c30b881bcaeaad370763b959
```

Joblib is pickle-based: load only the trusted supplied artifact. To replace it,
review the new model contract, update the expected checksum/version and rebuild
the worker. Do not rename another file to match this artifact.

The worker rejects insufficient history, missing required features, duplicate
timestamps, unacceptable cadence, future/stale input, nonfinite values, negative
gas/PM values, humidity outside 0–100, and output keys that differ from the
declared targets. It does not fill missing values, resample or substitute zeros.
Particulate input must also satisfy PM1 <= PM2.5 <= PM10, matching the delivered
loader. After a collection gap, wait for a complete fresh lookback. Devices
without a nonempty `device_id` are skipped.

**Live input prerequisite:** every timestamp must fall on a UTC minute boundary
(seconds and microseconds zero). Current firmware schedules from an arbitrary
boot-time phase, so existing readings may fail this check even when 60 seconds
apart. The adapter reports that reason; it does not floor timestamps, interpolate,
fill gaps or change collection timing. Collection alignment needs to be addressed
before claiming live forecasts work with that firmware. Resume ESP32 collection
after restarting it.

## Ubuntu server: Docker worker

This is the recommended dependency setup on the Linux server. The optional
`forecast` profile builds a separate Python 3.12 CPU image with exact direct and
transitive versions from `requirements.txt`. The build checks the dependencies
and loads the checksum-verified artifact before producing the image. It leaves the web/ingestion Python
environments alone. Its build context excludes the large Ollama cache and all
training/evaluation files.

Commit and push the new runtime files, model artifact, requirements and Compose
changes before pulling on the server; Git does not transfer uncommitted files.
Keep `.venv-forecast`, model caches, database volumes and secrets out of Git.
After pulling the project, from the repository root:

```bash
read -rsp 'PostgreSQL password: ' DB_PASS; echo
export DB_PASS
docker compose --profile forecast build forecast_worker
docker compose --profile forecast run --rm forecast_worker --init-db
docker compose --profile forecast run --rm forecast_worker --once
docker compose --profile forecast up -d forecast_worker
docker compose --profile forecast logs --tail=50 -f forecast_worker
```

Use the password actually configured for the PostgreSQL container. `DB_PASS` can
also be stored in the server's ignored `.env`; do not commit it. Existing Postgres
must have its sensor schema. `--init-db` creates only the separate forecast table
and source index; it does not reset readings. Apply the index outside active
collection when practical because a large table can briefly block writes.

The worker uses **Linux host networking** to reach the database's published 5432
port and the project's host-local Ollama server on 11435. This profile targets
the Ubuntu server; use the native environment below for Windows development.
The profile is optional and does not run with a normal `docker compose up -d`.
After enabling it, its restart policy handles worker restarts; Ollama still needs
its own running host process. Restart/recreate the worker if the database password
or built model changes.

For the Ubuntu server, install the
[project Qwen background service](../SERVER_COMMANDS.md#keep-project-qwen-running-in-the-background)
once to start it at boot. The simulation then needs only one SSH terminal.
For a temporary foreground session instead, start the existing helper in another
server terminal:

```bash
python3 setup_reasoning_ai.py --serve
```

Numerical forecasts remain available if Ollama is offline. Rebuild the webpage
after pulling its integration:

```bash
docker compose up -d --build web_dashboard
```

## Alternative: native Python

Run from this repository's root. These instructions use a host Python worker
alongside the existing Docker database. Use a fresh **Python 3.12** environment,
the version used for local verification and the worker image. Never copy the
handoff's Linux `.venv` to another machine or install these packages into the
dashboard's Python 3.9 image. The native worker does not start automatically.

```bash
cd ~/AirQualityMonitor
python3.12 -m venv .venv-forecast
. .venv-forecast/bin/activate
python -m pip install --only-binary=:all: -r ai_model/requirements.txt
python -m pip check

export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=air_quality DB_USER=admin
read -rsp 'PostgreSQL password: ' DB_PASS; echo
export DB_PASS
```

Use the password configured for this installation's PostgreSQL container.
`DB_PASS` is required; the worker does not embed a database password. Database
connection defaults otherwise match the published port of this project's
`postgres_db` service. The database and existing sensor schema must already exist.

Apply the **separate additive forecast schema** once:

```bash
python -m ai_model.forecast_worker --init-db
```

This creates `public.air_quality_forecasts` and a source query index. It does not
reset or reseed readings. Do not rerun the root `schema.sql` to enable forecasting.
The index creation can briefly block writes on a large existing sensor table;
apply this setup step outside active collection when practical.

If the project's Ollama endpoint is not already provided by the Ubuntu background
service, start it in another terminal using the existing helper:

```bash
python3 setup_reasoning_ai.py --serve
```

The worker defaults to `OLLAMA_URL=http://127.0.0.1:11435` and
`OLLAMA_MODEL=qwen2.5:3b`, matching this project's separate model store/server.
Ollama's usual port 11434 is a different instance. Neither the worker nor webpage
installs or starts Ollama. Numerical forecasts still work when it is offline.

Run a single cycle, then run continuously:

```bash
python -m ai_model.forecast_worker --once
python -m ai_model.forecast_worker
```

Insufficient, nonaligned or invalid readings report unavailable. Ctrl+C stops the
continuous worker. It prints status to its terminal; do not run the native worker
alongside the Docker worker.

Rebuild the dashboard to serve its new API and forecast panel:

```bash
docker compose up -d --build web_dashboard
```

Sign in and open `/dashboard`. `/api/forecasts` uses the same login. Until the
forecast schema exists it returns a clear not-configured state. Sensor panels
remain independent of forecasting.

## Automatic good -> warning -> critical sequence

For one complete demonstration, use `simulate_series.py`. It runs five updates
each for the existing good, mid, and bad scenarios, in that order, then exits.
Each update uses the real GRU and Qwen pipeline. Updates normally run about a
minute apart; slow inference can extend the roughly 15-minute demonstration.
The stages do not skip ahead based on wall-clock time. At launch, previous demo
readings and forecasts are removed for `sim-farm-good`, `sim-farm-mid`,
`sim-farm-bad`, and `python-simulator`. Imported/physical readings and their IDs
remain unchanged. Cleanup runs once before seeding, including with `--once`,
and never between stages. New IDs follow the highest retained ID; the first
cycle logs `_cleanup` counts. Cleanup and the first seed share one transaction.

After pulling/rebuilding the worker and dashboard, with PostgreSQL and the Qwen
background service running and other workers stopped:

```bash
cd ~/AirQualityMonitor
read -rsp 'PostgreSQL password: ' DB_PASS; echo
export DB_PASS
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps --name farm_simulation forecast_worker --simulate series
```

In the configured native Python environment, use
`python -m ai_model.simulate_series` or `python ai_model/simulate_series.py`.
`--once` runs only the first good-stage update. The existing standalone scenarios
below are still available for continuous single-condition demonstrations.

The forecast panel uses green Normal, amber Warning, and red Critical labels.
Input-scenario colors and predicted-value colors are separate: model results are
not forced to match a scenario name. Stale/unavailable forecasts remain neutral.
At an automatic stage transition the previous stage's forecast is marked
unavailable immediately, while its measured history is kept. Model/input/lock/
policy failures stop the series early; Qwen failures keep saved numbers and are
reported in each cycle and the final `guidance_errors` count.
See [the demonstration guide](SIMULATION_GUIDE.md) for the stage order, checks,
background service and cleanup instructions.

## Three farm demonstrations without a device

These scripts write labelled simulated inputs directly to PostgreSQL and run the
real predictor and Qwen flow. They do not need MQTT or a physical ESP32:

| Script | Input conditions | Stored device ID |
| --- | --- | --- |
| `simulate_good.py` | Normal for every channel | `sim-farm-good` |
| `simulate_mid.py` | Warning for every channel | `sim-farm-mid` |
| `simulate_bad.py` | Critical for every channel | `sim-farm-bad` |

The generated ranges follow the existing general `thresholds.md` policy, without
week-specific temperatures. MQ-4 is methane and MQ-137 is ammonia, correcting the
swapped gas labels in the source chart. Each script inserts a 120-minute starting
history ending at the current UTC minute, then adds missing demo minutes on each
worker cycle. You do not wait two hours to start the demonstration. All samples
have exact minute timestamps and valid particulate ordering.

Imported dataset and physical-device rows are never deleted or modified. Each
script clears the four known simulator device IDs once at startup, then adds
missing demo minutes on subsequent cycles under the worker lock. Stop earlier
simulators before starting another run; cleanup exits on a busy worker lock or
database failure. Earlier simulation forecasts are removed with their readings.

To remove demo readings afterward, stop all writers and use the Data page's
**Delete readings** controls. Select an inclusive start time in UTC and keep
the default simulated-only scope to preserve imported/physical readings. Retained
IDs stay unchanged; after a matching deletion the next ID follows the highest
remaining ID. See [cleanup steps](SIMULATION_GUIDE.md#7-remove-simulation-readings-after-the-demo).

The scenario describes **input conditions**, not a forced model result. The GRU
owns the future numbers and can predict another category. The CPU sigmoid uses a
numerically stable equivalent to handle extreme demo concentrations without
overflow; the supplied example still matches within the verification tolerance.
Extreme scenarios are not a validation of the model's field accuracy.

After committing/pushing and pulling these changes, rebuild on the server:

```bash
cd ~/AirQualityMonitor
sudo docker compose --profile forecast stop forecast_worker
sudo docker compose --profile forecast build forecast_worker
sudo docker compose up -d --build --no-deps web_dashboard
```

Keep the project's Qwen endpoint available. With the background service installed,
check `sudo systemctl --no-pager status henvironment-ollama.service`; no second
terminal is needed. Without that service, use another terminal:

```bash
cd ~/AirQualityMonitor
python3 setup_reasoning_ai.py --serve
```

Back in the simulation terminal, enter the real PostgreSQL password without
echoing it. Preserving `DB_PASS` through sudo matters for Compose interpolation:

```bash
cd ~/AirQualityMonitor
read -rsp 'PostgreSQL password: ' DB_PASS; echo
export DB_PASS

# Choose ONE command. Ctrl+C stops it; then choose another scenario.
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps forecast_worker --simulate good
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps forecast_worker --simulate mid
sudo --preserve-env=DB_PASS docker compose --profile forecast run --rm --no-deps forecast_worker --simulate bad
```

In an already-configured native Python 3.12 environment with the same database
environment variables, the three script entrypoints are:

```bash
python -m ai_model.simulate_good
python -m ai_model.simulate_mid
python -m ai_model.simulate_bad
```

Direct `python ai_model/simulate_good.py` also works. Append `--once` for one
seed/prediction cycle. Do not run the old five-second `simulate_esp32.py` under
these device IDs; it does not satisfy the trained model's one-minute contract.

Open `/dashboard`: the panel starts with **Predicted values in 2 hours**, showing
all eight model values and threshold categories, followed by **Reasoning AI
guidance**. The redundant outer heading and device metadata are hidden.
Numerical values are committed first;
they can appear while Qwen is still working. The panel checks for pending guidance
every 3 seconds, returning to 60 seconds once generation finishes. Ollama requests have a 120-second
default timeout for CPU generation, configurable with `OLLAMA_TIMEOUT` for native
runs. Guidance failure is logged and saved as `QWEN_TIMEOUT` or `QWEN_UNAVAILABLE`
in the existing forecast `reason` field, then retried on later worker updates.
Numerical results and the previous completed guidance stay visible. Requests use
an explicit 4096-token context and keep the model loaded for 15 minutes, using
[Ollama's generation options](https://docs.ollama.com/api/generate). This avoids
reloading it between normal demonstration cycles; it does not eliminate first-load latency.

The trends now cover the **whole stored date range**, including October data.
Hourly averages represent all stored readings; the individual graph shows the
latest recorded hour even when that hour is historical. The Data page retains
pagination/export for the complete original records. Refresh the page to reload
the measured charts; forecasts refresh every minute automatically. A disconnected
Live ESP32 panel is expected during a database-only demonstration.

To check that the imported rows remain intact, compare their per-device counts
and timestamps before and after the demonstration:

```bash
sudo docker exec postgres_db psql -U admin -d air_quality -c "SELECT device_id, COUNT(*), MIN(recorded_at), MAX(recorded_at) FROM air_quality_logs GROUP BY device_id ORDER BY device_id;"
```

For the current restored `henvironment-data.csv`, the `original-import` group
should contain 9,860 rows from October 20-26, 2026 UTC. An older import under
`dataset-import` may instead contain 32,320 rows from the earlier augmented CSV.
Additional `sim-farm-*` groups are separate simulated inputs, not replacements.

## Results and failure behavior

- `public.air_quality_forecasts` holds the latest result per device. Original
  measurements stay in `air_quality_logs`. It does not retain forecast history.
- Repeated identical input windows and model versions reuse their numeric result;
  changed threshold policy is reclassified and its explanation regenerated.
- Forecasts based on input older than 180 seconds, invalid target times, or deleted
  source rows are hidden as current results. This freshness limit is separate
  from the model's two-hour prediction horizon.
- The Markdown table supplies deterministic classifications. Qwen receives the
  same policy, predictions and classifications; it does not choose the numeric
  values or warning categories. Malformed policy leaves valid forecast numbers
  available with classification/guidance unavailable.
- Ollama errors leave numeric forecasts available. Failed guidance is retried on
  a later cycle while the input remains fresh. The worker bounds each Ollama
  request, runs cycles serially, and does not queue overlapping minute jobs.
- The supplied model produces 120 future rows, but the existing stored/dashboard
  contract uses only the final +120-minute row. Full trajectory display and
  confidence intervals are not implemented. Qwen prose still needs acceptance
  testing; structured output and grounded
  prompts do not prove its advice correct.

Do not train on raw MQ ADC values and then treat them as ppm features. Keep the
same units, measurement method and calibration assumptions in training and
inference. Check real inference latency and memory on the server with both models
loaded; slower-than-minute work will delay the next cycle.

## Checks

From the repository root, with the existing web and worker dependencies installed:

```bash
python -m unittest discover -s ai_model -t . -p 'test_*.py'
python -m unittest discover -s web_app -p 'test_*.py'
```

`test_predictor.py` loads the actual artifact and compares all eight final values
against `example_predictions.csv` with absolute tolerance 0.001. The other checks
use fixtures and mocked database/Ollama calls for contracts, threshold boundaries,
saved forecast delivery and failure behavior. They do not validate accuracy on
new field measurements, the live PostgreSQL deployment, Ollama response quality
or server memory capacity.
