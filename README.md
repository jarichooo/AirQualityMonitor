# Henvironment

Phase one of a poultry air quality monitoring application: prepare containers,
sensor data collection, and a usable web interface. Real collection has not
started; there is no predictive AI.

## Data flow

ESP32 or an optional Python simulator publishes JSON to `poultry/sensors`.
Mosquitto receives it; Python ingestion validates and stores readings in
PostgreSQL. The authenticated Flask website shows a collection summary and
a searchable, paginated Data page with date filters and CSV export.

Sensors: SHT31 temperature/humidity, MH-Z19E CO2, MQ135/MQ137 raw signals,
and PM1.0, PM2.5, PM10 mass concentrations in µg/m³.
The simulator generates test values; they are not real measurements.

## Local setup

Docker Desktop must be running.

1. Copy `.env.example` to `.env`. Generate a session secret with
   `python -c "import secrets; print(secrets.token_hex(32))"` and set
   `FLASK_SECRET_KEY` in `.env`.
2. For an existing deployment, stop writers and the dashboard first:
   `docker compose stop ingestion web_dashboard`. Start just the database with
   `docker compose up -d --wait postgres_db`.
3. For an existing database directory, apply the repeatable schema before
   starting the updated application, without deleting any data:
   `Get-Content schema.sql | docker compose exec -T postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality`
4. Run `docker compose up -d --build`, then create an account with
   `docker compose exec web_dashboard python add_users.py admin`.
   The command prompts for a password. Existing accounts are left unchanged.
   Add `--role viewer` for a viewer account; both roles currently have read/export access.
5. Open http://localhost:5000 and log in.

A fresh PostgreSQL directory runs `schema.sql` automatically. The website and
ingestion wait for PostgreSQL readiness. Stored data remains in `db_data/`.
Do not delete that directory to apply schema changes.

Without a configured session secret, Flask generates an ephemeral secret and
sessions become invalid after a restart. Database credentials still default to
the existing local development values; the MQTT broker allows anonymous access.
This setup is for a trusted local development network.

## Collection contract

Use one JSON object per MQTT message:

```json
{"device_id":"esp32-1","ts":"2026-10-01T14:00:00+08:00","t":30.5,"h":68,"co2":420,"pm1":8.0,"pm25":15.2,"pm10":22.0,"mq135_raw":1200,"mq137_raw":1600}
```

- At least one sensor value is required. Missing values remain NULL.
- `pm1`, `pm25`, `pm10` map to `pm1_ugm3`, `pm25_ugm3`, `pm10_ugm3`.
  These are mass concentrations, not particle counts.
- `mq135_raw` and `mq137_raw` are unconverted raw readings, not ppm.
  Legacy MQTT keys `mq135` and `mq137` are accepted as raw values; explicit raw
  keys take precedence. MQ137 is the ammonia channel. The intended MQ135 channel
  must be calibrated for the chosen gas before reporting a gas concentration.
  [Winsen lists MQ135 as an air-quality sensor](https://www.winsen-sensor.com/sensors/voc-sensor/mq135.html)
  for ammonia, sulfide, and benzene vapors; do not label its raw signal as a
  methane concentration.
- `nh3` / `nh3_ppm` remains optional for independently calibrated ammonia ppm;
  it is not calculated from MQ137 raw. Simulators leave this value missing.
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

Applying `schema.sql` also renames the old MQ `*_ppm` columns to `*_raw`,
preserving their numeric values, and adds nullable PM1.0/PM10 columns. Historical
MQ values are not converted: verify they were raw readings before using them
for training. Existing rows have NULL for the newly added particle sizes.
See [SERVER_COMMANDS.md](SERVER_COMMANDS.md) for updates, backups, truncation,
and a separate full database reset procedure.

The Python simulator uses `poultry/sensors` and UTC timestamps. ESP32 code kept
outside version control must publish to that same topic and server address.

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

## Reliability boundaries

Malformed messages are rejected with a log entry. Each database write uses its
own connection and transaction; a failed write cannot poison subsequent inserts.
MQTT reconnects with bounded backoff.

Failed database writes are logged with the original message for verification and
manual replay. There is no durable retry queue or duplicate suppression yet.
QoS 1 alone does not guarantee a database commit; do not claim lossless collection.
Offline device buffering and retry/deduplication need end-to-end testing before
unattended collection.

The dashboard summarizes stored rows, not live broker/device connectivity.
There are no predictions or calibrated sensor drivers. Analytics refresh manually.
The Flask development server is used locally; deployment hardening comes later.

## Checks

Install dependencies:
`python -m pip install -r web_app/requirements.txt -r ingestion/requirements.txt`

Run:
```powershell
python -m unittest discover -s web_app -p "test_*.py"
python -m unittest discover -s ingestion -p "test_*.py"
docker compose config --quiet
```

Tests cover authenticated routes, empty and unavailable databases, filters,
CSV export, timestamp handling, malformed readings, and recovery for subsequent
messages after a database failure. They mock database access; they do not replace
a live MQTT/PostgreSQL check.

Before deployment, `git ls-files -- db_data .env` must print nothing. Runtime
database files and secrets belong on each machine, outside Git tracking.
