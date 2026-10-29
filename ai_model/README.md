# Two-hour environmental forecasts

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
| `MODEL_VERSION` | `gru-v1-eaef59203a16`, identifying this artifact and adapter contract. |
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

In another server terminal, start the existing project Ollama helper:

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

Start the project's Ollama server in another terminal using the existing helper:

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
