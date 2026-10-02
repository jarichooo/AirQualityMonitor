# Ubuntu server command list

Run these in an SSH terminal on the mini PC. The commands using `docker compose`
start in the application folder:

```bash
cd ~/AirQualityMonitor
```

Use `sudo docker` as shown if your account is not in the Docker group. Docker
already contains the Python packages needed by the web app and ingestion service.

## Containers

```bash
sudo docker compose ps                       # See all four services
sudo docker compose logs --tail=50 ingestion # Check received or failed readings
sudo docker compose logs --tail=50 postgres_db
sudo docker compose logs -f ingestion        # Follow logs; Ctrl+C leaves it running
sudo docker compose up -d                    # Start existing containers
sudo docker compose up -d --build            # Rebuild after copying changed code
sudo docker compose restart web_dashboard    # Restart one existing container
```

The services are `postgres_db`, `mqtt_broker`, `ingestion`, and `web_dashboard`.
Compose already sets `restart: unless-stopped` for each service.

## Dashboard accounts

The account script runs **inside the web container**. Do not activate the host
virtual environment to use it. The password prompts do not echo what you type.

```bash
sudo docker compose exec web_dashboard python add_users.py admin
sudo docker compose exec web_dashboard python add_users.py viewer --role viewer
```

The first command creates an administrator; the second creates a viewer. Use
another username if desired. Both roles currently have the same read and CSV
export access. An existing username is left unchanged, including its password.
Creating an account writes to PostgreSQL immediately; it needs no rebuild.

Equivalent direct Docker command (works from any directory):

```bash
sudo docker exec -it web_dashboard python /app/add_users.py admin
```

## Inspect PostgreSQL

Open an interactive SQL prompt:

```bash
sudo docker compose exec postgres_db psql -U admin -d air_quality
```

At the `air_quality=#` prompt, try:

```sql
\dt
\d air_quality_logs
SELECT COUNT(*) FROM air_quality_logs;
SELECT recorded_at, device_id, temperature_c, humidity_perc, co2_ppm,
       nh3_ppm, pm25_ugm3
FROM air_quality_logs ORDER BY recorded_at DESC LIMIT 10;
SELECT username, role FROM dashboard_users ORDER BY username;
SELECT COUNT(*) FROM air_quality_logs
WHERE recorded_at >= now() - interval '24 hours';
\q
```

Timestamps in the database are stored as UTC. To display recent times in the
Philippines:

```sql
SELECT recorded_at AT TIME ZONE 'Asia/Manila' AS manila_time, device_id,
       temperature_c FROM air_quality_logs ORDER BY recorded_at DESC LIMIT 10;
```

For a single query from the normal shell, use `-c`:

```bash
sudo docker compose exec -T postgres_db psql -U admin -d air_quality -c \
  "SELECT COUNT(*) FROM air_quality_logs;"
```

Equivalent direct Docker command:

```bash
sudo docker exec -it postgres_db psql -U admin -d air_quality
```

### Direct, one-command database queries

Run these from the Ubuntu shell, from any directory. Each command connects,
prints a result, and exits; there is no `psql` prompt. These examples only
read data.

```bash
# List tables and inspect their columns
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\dt"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\d air_quality_logs"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\d dashboard_users"

# Count all readings and list the 10 most recent
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT COUNT(*) AS total_readings FROM air_quality_logs;"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT recorded_at, device_id, temperature_c, humidity_perc, co2_ppm, nh3_ppm, pm25_ugm3 FROM air_quality_logs ORDER BY recorded_at DESC LIMIT 10;"

# Recent activity and a 24-hour temperature summary
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT MAX(recorded_at) AS latest_reading FROM air_quality_logs;"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT COUNT(*) AS readings_24h, ROUND(AVG(temperature_c)::numeric, 2) AS avg_temp_c FROM air_quality_logs WHERE recorded_at >= now() - interval '24 hours';"

# Readings grouped by device; usernames and roles (no password hashes)
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT COALESCE(device_id, '(unspecified)') AS device, COUNT(*) AS readings FROM air_quality_logs GROUP BY device_id ORDER BY readings DESC;"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT username, role FROM dashboard_users ORDER BY username;"

# Show the latest timestamp in Philippine local time
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT recorded_at AT TIME ZONE 'Asia/Manila' AS manila_time, device_id, temperature_c FROM air_quality_logs ORDER BY recorded_at DESC LIMIT 10;"
```

## Host Python simulator

The simulator is the only Python command here that uses the **Ubuntu host's
virtual environment**. It generates fake readings every five seconds.

```bash
cd ~/AirQualityMonitor
~/henvironment-venv/bin/python simulate_esp32.py
```

Press Ctrl+C to stop it. If the environment has not been created yet:

```bash
python3 -m venv ~/henvironment-venv
~/henvironment-venv/bin/pip install 'paho-mqtt>=2,<3'
```

The simulator stops when its terminal closes or the server loses power. It is
separate from the four Compose services.

## Shut down or reboot the server

If the host Python simulator is running, stop it with Ctrl+C. Leave the Compose
containers running so their `unless-stopped` policy can start them again after
the next boot. From the SSH session, shut down Ubuntu normally:

```bash
sudo systemctl poweroff
```

The SSH connection will close. Wait until the mini PC has fully powered off
before unplugging it. For a planned restart instead, run `sudo systemctl reboot`.

After powering it on and reconnecting with SSH, check the services:

```bash
cd ~/AirQualityMonitor
sudo docker compose ps
```

If a service did not start, run `sudo docker compose up -d`. Do not run
`docker compose stop` before a normal server shutdown: manually stopped
containers using `unless-stopped` stay stopped on the next boot.
