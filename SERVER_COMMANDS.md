# Ubuntu server command list

Run these in an SSH terminal on the mini PC. The commands using `docker compose`
start in the application folder:

```bash
cd ~/AirQualityMonitor
```

Use `sudo docker` as shown if your account is not in the Docker group. Docker
already contains the Python packages needed by the web app and ingestion service.

## Initial setup: choose GitHub or SCP

These steps assume Ubuntu, Docker Engine, the Compose plugin, and SSH are already
installed. Examples use `airqualitymonitor@192.168.xxx.xxx`; change that if the
server's username or address changes. For a fresh setup, use a destination folder
that has no existing database. An existing running server should use the update
instructions below instead.

**Do not transfer the laptop's `db_data/`.** PostgreSQL creates a new server-local
directory and runs `schema.sql` automatically when it starts with an empty data
directory. A fresh database has no readings and no dashboard accounts. Docker
builds the application images on Ubuntu; you do not need to copy images, `.git/`,
`.test-deps/`, or Python virtual environments.

### Option A: clone from GitHub on Ubuntu

Before using GitHub, check the laptop repository:

```powershell
cd C:\Users\Admin\Documents\AirQualityMonitor
git ls-files -- db_data .env
```

There should be no output. If a checkout still tracks `db_data/`, clean its
tracking before deployment. `.gitignore` does not stop tracking files
that were already committed. See the [Git ignore documentation](https://git-scm.com/docs/gitignore).

If `db_data/` is tracked, prepare a commit on the **laptop**, review it, then push:

```powershell
git rm -r --cached -- db_data
git add .gitignore SERVER_COMMANDS.md
git diff --cached --stat
git commit -m "Stop tracking PostgreSQL runtime data and document server setup"
git push
```

`--cached` removes files from Git's index while keeping the laptop's data on disk.
This does not remove them from previous commits. See [Git rm documentation](https://git-scm.com/docs/git-rm).
If `.env` is listed by the check, stop and remove it from tracking separately
before deployment. Do not run these cleanup commands in a server's live database
folder. Pulling a commit that deletes tracked database files can remove those
files from another checkout.

Once the GitHub branch is prepared, run on **Ubuntu**:

```bash
cd ~
git clone https://github.com/jarichooo/AirQualityMonitor.git AirQualityMonitor
cd ~/AirQualityMonitor
git ls-files -- db_data .env
```

Do not start Docker if that last command lists files. Clone into a new directory;
`git clone` will refuse an existing nonempty destination. For a private repo,
configure GitHub SSH access or authenticate HTTPS with a token when prompted.
Do not put a token directly into the repository URL.

### Option B: copy source files with SCP from Windows

Run in **PowerShell on the laptop**:

```powershell
cd C:\Users\Admin\Documents\AirQualityMonitor
ssh airqualitymonitor@192.168.xxx.xxx "mkdir -p ~/AirQualityMonitor"
scp -r .\ingestion .\web_app .\airqualitymonitor .\airqualitymonitor.ino `
  .\docker-compose.yml .\schema.sql .\mosquitto.conf .\add_users.py `
  .\simulate_esp32.py .\README.md .\SERVER_COMMANDS.md .\.gitignore `
  airqualitymonitor@192.168.xxx.xxx:~/AirQualityMonitor/
```

This explicit list excludes `db_data/` and `.env`. SCP does not read `.gitignore`,
so do not copy the entire laptop folder with `scp -r .`.

Then connect to **Ubuntu**:

```powershell
ssh airqualitymonitor@192.168.xxx.xxx
```

### Finish either setup method on Ubuntu

Create a private session key on the server if `.env` does not already exist:

```bash
cd ~/AirQualityMonitor
if [ ! -e .env ]; then
  (umask 077; python3 -c "import secrets; print('FLASK_SECRET_KEY=' + secrets.token_hex(32))" > .env)
fi
chmod 600 .env
grep -qE '^FLASK_SECRET_KEY=.+$' .env && echo "Session key configured"
```

The check displays no secret. If it prints nothing, configure a nonempty
`FLASK_SECRET_KEY` before startup. Preserve an existing server key during updates.

```bash
sudo systemctl enable --now docker.service containerd.service
sudo docker compose config --quiet
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose exec web_dashboard python add_users.py admin
```

Enter and confirm the account password. Open `http://192.168.xxx.xxx:5000` on the
laptop and log in. Account creation needs no rebuild. No host virtual environment
is needed for Docker or account creation; it is only used for the simulator below.
For the ESP32, configure the server's IP, port `1883`, and topic `poultry/sensors`.

## Updating an existing server

Keep the server's `.env` and `db_data/`. If it was copied by SCP, copy only changed
source/configuration files, then rebuild. For a Git checkout whose database and
secrets were never tracked, run:

```bash
cd ~/AirQualityMonitor
git status --short
git ls-files -- db_data .env
```

Resolve source changes first; the second command must list no files. Only then:

```bash
git pull --ff-only
sudo docker compose stop ingestion web_dashboard
sudo docker compose up -d --wait postgres_db
```

Do not use this pull recipe on a live checkout that tracks `db_data/`; migrating
that checkout needs a database backup and a plan to preserve the data first.
Stop the host simulator and pause ESP32 publishers during maintenance. For
existing databases, initialization scripts do not rerun on rebuild. Back up
first using the next section, then apply the repeatable schema **before**
starting the updated ingestion/dashboard containers. Run each step only after
the previous step succeeds:

```bash
sudo docker compose exec -T postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality < schema.sql
sudo docker compose up -d --build
sudo docker compose ps
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\d air_quality_logs"
sudo docker compose logs --tail=50 ingestion web_dashboard
```

For moving real data to another server, use a PostgreSQL dump and restore, not a
copy of a running `db_data/` directory.

The sensor migration renames `mq135_ppm`/`mq137_ppm` to `mq135_raw`/`mq137_raw`
without converting historical values. It keeps `pm25_ugm3` and adds nullable
`pm1_ugm3` and `pm10_ugm3`. Existing readings and login accounts remain intact;
older rows have NULL for the new particle sizes. Verify historical MQ readings
were raw values before training a model with them. Updated publishers should
send `mq135_raw`, `mq137_raw`, `pm1`, `pm25`, and `pm10`. Older MQTT keys
`mq135`/`mq137` still work as raw values. Resume publishers after verification.

## Back up before maintenance

Stop the simulator (Ctrl+C), pause devices, and stop database writers. Keep
PostgreSQL running. Store backups outside the repository, with private permissions:

```bash
cd ~/AirQualityMonitor
sudo docker compose stop ingestion web_dashboard
mkdir -p ~/airquality-backups
chmod 700 ~/airquality-backups
backup_file="$HOME/airquality-backups/air_quality-$(date +%Y%m%d-%H%M%S).dump"
(umask 077; sudo docker compose exec -T postgres_db pg_dump -U admin -d air_quality -Fc > "$backup_file")
```

Continue only if `pg_dump` exits successfully. Validate the dump archive:

```bash
sudo docker compose exec -T postgres_db pg_restore --list < "$backup_file" > /dev/null
ls -lh "$backup_file"
```

The dump includes readings and account password hashes. Keep it private. These
commands do not back up `.env`; preserve the server's existing `.env` separately.

## Clear readings only (TRUNCATE)

This permanently deletes sensor rows and resets their IDs, while keeping the
schema and dashboard accounts. Back up first using the preceding section.
**No container teardown or `db_data/` deletion is needed.** PostgreSQL's
[TRUNCATE documentation](https://www.postgresql.org/docs/15/sql-truncate.html)
describes `RESTART IDENTITY` and immediate space reclamation.

```bash
cd ~/AirQualityMonitor
sudo docker compose stop ingestion web_dashboard
sudo docker exec -it postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality -c "TRUNCATE TABLE air_quality_logs RESTART IDENTITY;"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT COUNT(*) AS readings FROM air_quality_logs;"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT username, role FROM dashboard_users ORDER BY username;"
sudo docker compose up -d
```

The count should be zero while publishers are paused. Log in with your existing
account; the dashboard shows its empty state and CSV export contains headers
only. Resume devices or the simulator when ready; new readings start at ID 1.
If you also pulled code changes, apply `schema.sql` and use `up -d --build` as
described above before resuming publishers.

## Full database reset (delete db_data)

Use this only when you want a completely fresh database. **All readings and
dashboard accounts will be lost.** Truncating first is redundant. Back up before
resetting, pause publishers, and finish/review any Git source changes first.
Ensure `git ls-files -- db_data .env` prints nothing before pulling.

```bash
cd ~/AirQualityMonitor
git status --short
git ls-files -- db_data .env
git pull --ff-only
```

After a successful pull and backup, this block stops containers, checks the
exact database directory, and removes only that directory. `.env` stays in place:

```bash
(
  set -eu
  cd "$HOME/AirQualityMonitor"
  db_target="$(realpath -- db_data)"
  expected_target="$(pwd -P)/db_data"
  [ "$db_target" = "$expected_target" ] || { echo "Unexpected database path; stopping"; exit 1; }
  [ -d "$db_target" ] || { echo "Database directory missing; stopping"; exit 1; }
  sudo docker compose down
  sudo rm -rf --one-file-system -- "$db_target"
)
```

Never remove `db_data/` while PostgreSQL is running. Start the database first;
its initialization runs the current `schema.sql` on the empty directory:

```bash
sudo docker compose up -d --wait postgres_db
sudo docker compose logs --tail=50 postgres_db
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\dt"
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\d air_quality_logs"
sudo docker compose up -d --build
sudo docker compose exec web_dashboard python add_users.py admin
sudo docker compose ps
sudo docker compose logs --tail=50 ingestion web_dashboard
```

Both `air_quality_logs` and `dashboard_users` should exist. Recreate any viewer
accounts too. Log out of old browser sessions and log in with the new account;
keeping `.env` means existing session cookies can still be valid. Check empty
analytics/CSV export, then resume publishers and verify new rows with the direct
queries below. No host venv is needed for account creation.

### Restore a backup if needed

Keep publishers paused and stop ingestion/dashboard. Replace the example dump
filename with the backup you actually saved. Restoring replaces current tables,
including accounts; a dump from before this change contains the old column names:

```bash
cd ~/AirQualityMonitor
sudo docker compose stop ingestion web_dashboard
sudo docker compose up -d --wait postgres_db
sudo docker compose exec -T postgres_db pg_restore -U admin -d air_quality --clean --if-exists --single-transaction < ~/airquality-backups/air_quality-YYYYMMDD-HHMMSS.dump
sudo docker compose exec -T postgres_db psql -v ON_ERROR_STOP=1 -U admin -d air_quality < schema.sql
sudo docker compose up -d --build
sudo docker compose ps
```

Continue only on success; inspect columns and logs before resuming publishers.

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
       nh3_ppm, pm1_ugm3, pm25_ugm3, pm10_ugm3, mq135_raw, mq137_raw
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
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT recorded_at, device_id, temperature_c, humidity_perc, co2_ppm, nh3_ppm, pm1_ugm3, pm25_ugm3, pm10_ugm3, mq135_raw, mq137_raw FROM air_quality_logs ORDER BY recorded_at DESC LIMIT 10;"

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
