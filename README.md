# Henvironment

Phase one of a poultry air quality monitoring application: prepare containers,
sensor data collection, and a usable web interface. Real collection has not
started; there is no predictive AI.

## Hardware

| Hardware | Specifications / role |
| --- | --- |
| Router (1) | Connects the mini server, ESP32, and dashboard devices on the same LAN. |
| Dell OptiPlex 9020 mini server | 1,120 GB storage, 8 GB RAM, Intel Core i7 (4th generation); Ubuntu and Docker run Mosquitto, ingestion, PostgreSQL, and the web app. |
| ESP32 | Collects sensor readings and publishes them to the server; hosts the dashboard WebSocket connection. |

The ESP32 has these connected sensors and clock:

| Component | Purpose / stored measurements |
| --- | --- |
| MQ137 | Ammonia concentration estimate, stored as `mq137_ppm`. |
| MQ-4 Flying Fish module | Methane concentration estimate, stored as `mq4_ppm`; GPIO1. |
| SHT31 | Temperature and relative humidity. |
| PM2.5 particulate sensor | PM1.0, PM2.5, and PM10 mass concentrations. |
| MH-Z19E | CO2 concentration in ppm. |
| DS3231 | Real-time clock for measurement timestamps. |

MQ readings are model-derived ppm estimates. MQ-4 temporarily uses the previous
sensor's RL = 10 kOhm and R0 = 10.947 kOhm at the owner's request; this is a
provisional calibration, not a validated methane measurement. See
[FIRMWARE.md](FIRMWARE.md) for conversion constants, wiring, and Arduino libraries.

## Data flow

ESP32 or an optional Python simulator publishes JSON to `poultry/sensors`.
Mosquitto receives it; Python ingestion validates and stores readings in
PostgreSQL. The authenticated Flask website shows a collection summary and
a searchable, paginated Data page with date filters and CSV export. The logged-in
dashboard can connect directly to the ESP32 over WebSocket for live sensor
values and pause/resume collection; MQTT remains the path used to store readings
in PostgreSQL. The ESP32 samples once per minute.
The dashboard plots individual stored readings for the selected sensor over
the last hour, alongside hourly averages for the last 24 hours. Select a sensor
card to change both graphs and use Refresh to load newly recorded data. Gaps
longer than two minutes break the individual-reading line; devices have separate
lines. Point tooltips and the expandable values table show measurement times
and device IDs.

Sensors: SHT31 temperature/humidity, MH-Z19E CO2, MQ-4 methane and MQ137 ammonia ppm estimates,
and PM1.0, PM2.5, PM10 mass concentrations in µg/m³.
The simulator generates test values; they are not real measurements.

## ESP32 dashboard controls

The ESP32 **starts paused on every boot**, regardless of its previous state.
WiFi, MQTT connectivity, and WebSocket controls stay available while paused.
Click **Resume readings** to begin sampling and sending measurements once per
minute. **Pause readings** stops new samples, live broadcasts, and MQTT uploads,
including queued readings; queued readings wait until collection resumes.

1. Configure the sketch's WiFi credentials, server address, static ESP32 IP,
   gateway, subnet, and DNS for your router. Reserve the ESP32 address to avoid
   conflicts, then compile and upload the sketch using Arduino IDE.
2. Open `http://<server-IP>:5000`, log in, and find **Live ESP32 readings**.
3. Enter `ws://<ESP32-IP>:81/` and click **Connect**. The default
   `ws://airqualitymonitor.local:81/` also works where mDNS resolves. The ESP32 IP
   is printed in Serial Monitor; the dashboard remembers the entered address.
4. Click **Resume readings** or **Pause readings**. The button shows
   Resuming/Pausing while waiting, then a confirmed **Running/Paused** indicator.
   An eight-second timeout means the command was not confirmed; reconnect and
   check that the updated sketch was uploaded.

The WebSocket controls are unauthenticated and intended for a trusted LAN.
The button controls the connected ESP32; stop other publishers, including the
simulator, separately. Previously stored readings remain in the database.
Docker rebuilds update the web app; they do not upload firmware to the ESP32.

## Local setup

Docker Desktop must be running.

1. Copy `.env.example` to `.env`. Generate a session secret with
   `python -c "import secrets; print(secrets.token_hex(32))"` and set
   `FLASK_SECRET_KEY` in `.env`.
2. For an existing deployment, stop writers and the dashboard first:
   `docker compose stop ingestion web_dashboard`. Start just the database with
   `docker compose up -d --wait postgres_db`.
3. For an existing database directory, back up first using
   [SERVER_COMMANDS.md](SERVER_COMMANDS.md#updating-an-existing-server), then apply
   the repeatable schema before starting the updated application. The MQ migration
   preserves historical raw values separately and creates new ppm columns:
   `Get-Content schema.sql | docker compose exec -T postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality`
4. Run `docker compose up -d --build`, then create an account with
   `docker compose exec web_dashboard python add_users.py admin`.
   The command prompts for a password. Existing accounts are left unchanged.
   Add `--role viewer` for a viewer account; both roles have read/export access.
   Admin accounts can also delete all sensor readings from the Data page.
5. Open http://localhost:5000 and log in.

A fresh PostgreSQL directory runs `schema.sql` automatically. The website and
ingestion wait for PostgreSQL readiness. Stored data remains in `db_data/`.
Do not delete that directory to apply schema changes.

To clear collected readings, an admin can click **Delete all readings** on the
Data page and accept the confirmation popup. This clears the entire sensor
table, including rows hidden by filters, and resets the next reading ID to 1.
Accounts are kept. Export anything you need first, and pause the ESP32 and stop
simulators if you want the table to remain empty. No container teardown or
`db_data/` deletion is needed.

Without a configured session secret, Flask generates an ephemeral secret and
sessions become invalid after a restart. Database credentials still default to
the existing local development values; the MQTT broker allows anonymous access.
This setup is for a trusted local development network.

## Collection contract

Use one JSON object per MQTT message:

```json
{"device_id":"esp32-1","ts":"2026-10-01T14:00:00+08:00","t":30.5,"h":68,"co2":420,"pm1":8.0,"pm25":15.2,"pm10":22.0,"mq4_ppm":1200,"mq137_ppm":32}
```

- At least one sensor value is required. Missing values remain NULL.
- `pm1`, `pm25`, `pm10` map to `pm1_ugm3`, `pm25_ugm3`, `pm10_ugm3`.
  These are mass concentrations, not particle counts.
- `mq4_ppm` and `mq137_ppm` carry methane and ammonia concentration estimates,
  respectively. Publishers must use these explicit ppm keys. Old raw or
  ambiguous MQ keys are not converted into ppm; update the firmware and simulator
  together with ingestion. Invalid voltage/conversion results are omitted so
  PostgreSQL stores NULL, rather than a misleading negative concentration.
- Firmware estimates sensor resistance from the measured ADC voltage and the
  configured divider, load resistance, R0, and gas-specific curve. MQ-4's borrowed
  calibration must be replaced after calibration of the installed module.
- Values must be finite numbers. Humidity is 0–100; gas and particulate
  readings cannot be negative. Booleans and numeric strings are rejected.
- `device_id` is optional and at most 100 characters.
- Prefer timestamps with an explicit timezone. Ingestion converts them to UTC.
  Legacy timestamps without an offset are interpreted as Philippine time (UTC+8).
  Missing timestamps use the ingestion server's current UTC time.
- The website displays UTC; Today and custom date filters also use UTC.

Fresh databases use TIMESTAMPTZ. Existing timestamp columns are not automatically
converted: inspect the original timestamp convention before migrating historical
rows, particularly if Philippine local time was previously stored as UTC.
Ingestion sets its database session to UTC for future writes.

Applying `schema.sql` creates nullable `mq4_ppm` and `mq137_ppm` columns.
Historical raw readings are retained under `legacy_` names, including
`legacy_mq135_raw` and `legacy_mq137_raw`; they are never renamed into methane or
ammonia ppm values. Old rows therefore have NULL in the new ppm columns. The
migration also supports the previously mislabeled raw columns and adds missing
PM1.0/PM10 fields. Back up before migrating and inspect the resulting columns.
The earlier `nh3_ppm` retirement still removes that obsolete column if present;
export it first if its historical values are needed.
See [SERVER_COMMANDS.md](SERVER_COMMANDS.md) for updates, backups, truncation,
and a separate full database reset procedure.

`airqualitymonitor.ino` reads the connected hardware; it generates no simulated
values. See [FIRMWARE.md](FIRMWARE.md) for pins, Arduino libraries, flashing, and
verification on Ubuntu. `simulate_esp32.py` remains the optional test simulator;
stop it before collecting real readings.

## Planned predictive AI integration

The custom regression model will be saved as `.h5` in `ai_model/`. Its inference
script will prepare collected readings as model inputs and retrieve predictions
for readings **two hours ahead**. Input features, lookback window, preprocessing,
target sensors, and model runtime must be confirmed from the trained model before
inference is implemented. Measured data and predictions must remain separate.
The dashboard already provides descriptive analytics and an unavailable forecast
panel; it does not generate predictions or substitute simulated values.

The root URL `/` opens the login page. Successful login redirects to `/dashboard`.
Dashboard, Data, and export require authentication; existing sessions remain valid
until logout or expiry.

## Optional Ollama + Qwen2.5 setup (Windows laptop)

`setup_reasoning_ai.py` uses Python's standard library to check or install
**Ollama** and the exact model **`qwen2.5:3b`**. No pip dependencies or virtual
environment are required. The model comes from the
[Ollama Qwen2.5:3b library](https://ollama.com/library/qwen2.5:3b), rather than a
separate Hugging Face GGUF download. The model download is approximately 1.9 GB;
allow additional disk space for the Ollama application and download files.

Run in PowerShell:

```powershell
cd C:\Users\Admin\Documents\AirQualityMonitor
python setup_reasoning_ai.py
python setup_reasoning_ai.py --install
python setup_reasoning_ai.py --smoke-test
```

The initial check returns code 1 if the engine or project model is missing.
`--install` downloads and runs the official Windows Ollama installer only when
Ollama is missing, then pulls `qwen2.5:3b` if it is absent or incomplete in the
project model folder. Installation needs internet; repeating it reuses existing
components. A broken installed engine is reported for repair. If the CLI is not
found after installation, reopen PowerShell and retry. Ollama requires Windows
10 22H2 or newer.

Model manifests and blobs are stored under `ai_model/reasoning ai/models/` using
`OLLAMA_MODELS`. Models installed elsewhere remain there; the script checks this
project's store. `ai_model/reasoning ai/thresholds.md` starts empty and is preserved
on subsequent runs. Add thresholds, units, poultry context, and response rules
later. Downloaded model files are ignored by Git; the script and Markdown can be
committed.

The default check is offline and makes no changes. It checks the CLI and local
manifest/blob sizes, without rehashing every blob or testing inference. Ollama
verifies downloads; `--smoke-test` actually loads the model and generates a short
generic answer. Setup and smoke tests start a temporary local Ollama server and
stop it afterward. They do not reconfigure another Ollama instance's model store.

To keep the project's model available for manual testing:

```powershell
python setup_reasoning_ai.py --serve
```

Keep that terminal open; Ctrl+C stops it. The API is at `http://127.0.0.1:11435`,
separate from Ollama's normal port 11434. If this port is occupied, stop the earlier
project server first. In another PowerShell terminal, chat with the project model:

```powershell
$env:OLLAMA_HOST = '127.0.0.1:11435'
ollama run qwen2.5:3b
```

The intended later flow is collected readings -> custom `.h5` regression model ->
readings predicted two hours ahead -> thresholds and Qwen -> explanatory sentences.
This change installs and checks the runtime only. It does not yet interpret the
thresholds, generate sensor assessments, or connect Qwen to the dashboard or
regression model. Missing thresholds must not be replaced with invented limits.

See [SERVER_COMMANDS.md](SERVER_COMMANDS.md#optional-ollama--qwen25-reasoning-engine)
for Ubuntu, SCP, and troubleshooting instructions. No Docker rebuild or database
migration is required. Test speed and available memory on the 8 GB server before
connecting inference to the application.

## Reliability boundaries

Malformed messages are rejected with a log entry. Each database write uses its
own connection and transaction; a failed write cannot poison subsequent inserts.
MQTT reconnects with bounded backoff.

Failed database writes are logged with the original message for verification and
manual replay. There is no durable retry queue or duplicate suppression yet.
QoS 1 alone does not guarantee a database commit; do not claim lossless collection.
Offline device buffering and retry/deduplication need end-to-end testing before
unattended collection.

Dashboard analytics summarize stored rows and refresh manually. The separate
WebSocket panel shows live ESP32 readings and confirmed collection status;
a live broadcast does not prove that PostgreSQL stored the reading.
There are no AI predictions. MQ concentration conversions are estimates and
have not been validated against reference gas measurements; do not use them as
certified gas alarms or mix the retired raw signals with ppm training features.
The Flask development server is used locally; deployment hardening comes later.

## Checks

Install dependencies:
`python -m pip install -r web_app/requirements.txt -r ingestion/requirements.txt`

Run:
```powershell
python -m unittest discover -s web_app -p "test_*.py"
python -m unittest discover -s ingestion -p "test_*.py"
python firmware_gas_math_test.py
python -m unittest test_setup_reasoning_ai.py
docker compose config --quiet
```

Tests cover authenticated routes, empty and unavailable databases, filters,
CSV export, timestamp handling, malformed readings, and recovery for subsequent
messages after a database failure. They mock database access; they do not replace
a live MQTT/PostgreSQL check.

Before deployment, `git ls-files -- db_data .env` must print nothing. Runtime
database files and secrets belong on each machine, outside Git tracking.
