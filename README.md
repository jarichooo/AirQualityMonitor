# Henvironment

Phase one of a poultry air quality monitoring application: prepare containers,
sensor data collection, and a usable web interface. Real collection has not
started; there is no predictive AI.

## Data flow

ESP32 or an optional Python simulator publishes JSON to `poultry/sensors`.
Mosquitto receives it; Python ingestion validates and stores readings in
PostgreSQL. The authenticated Flask website shows a collection summary and
a searchable, paginated Data page with date filters and CSV export.

Sensors: temperature, humidity, CO2, ammonia, PM2.5, MQ135 and MQ137.
The simulator generates test values; they are not real measurements.

## Local setup

Docker Desktop must be running.

1. Copy `.env.example` to `.env`. Generate a session secret with
   `python -c "import secrets; print(secrets.token_hex(32))"` and set
   `FLASK_SECRET_KEY` in `.env`.
2. Run `docker compose up -d --build`.
3. For an existing database directory, apply the repeatable schema without
   deleting any data:
   `Get-Content schema.sql | docker compose exec -T postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality`
4. Create an account with `docker compose exec web_dashboard python add_users.py admin`.
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
{"device_id":"esp32-1","ts":"2026-10-01T14:00:00+08:00","t":30.5,"h":68,"co2":420,"nh3":8.5,"pm25":15.2,"mq135":12,"mq137":32}
```

- At least one sensor value is required. Missing values remain NULL.
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

The Python simulator uses `poultry/sensors` and UTC timestamps. ESP32 code kept
outside version control must publish to that same topic and server address.

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
There are no predictions, calibrated sensor drivers, or automatic chart updates.
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

The current repository already tracks database files. Ignore rules prevent new
ones being added, but previously tracked files need a separate repository cleanup.
