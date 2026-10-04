# ESP32-S3 sensor firmware

`airqualitymonitor.ino` is the hardware collector. It samples every 60 seconds,
publishes JSON to Mosquitto on the Ubuntu server, topic `poultry/sensors`, and
broadcasts the same live sensor readings on a WebSocket at port 81. The dashboard
can pause or resume both MQTT uploads and live broadcasts.
`simulate_esp32.py` is the separate simulator; stop it before real collection.

## Wiring and stored fields

| Sensor | ESP32 GPIO | MQTT fields | Database fields |
| --- | --- | --- | --- |
| MQ135 analog output | 1 | `mq135_raw` | `mq135_raw` |
| MQ137 analog output | 2 | `mq137_raw` | `mq137_raw` |
| SHT31 I2C | SDA 4, SCL 11 | `t`, `h` | `temperature_c`, `humidity_perc` |
| DS3231 I2C (second bus) | SDA 13, SCL 12 | `ts` | `recorded_at` |
| MH-Z19E UART1 | RX 7, TX 8 | `co2` | `co2_ppm` |
| Plantower PMS UART2 | RX 9, TX 10 | `pm1`, `pm25`, `pm10` | `pm1_ugm3`, `pm25_ugm3`, `pm10_ugm3` |

Connect UART sensor TX to ESP32 RX and sensor RX to ESP32 TX. Keep the tested
power and voltage-divider wiring: GPIO1/2 must receive ESP32-safe analog levels.
MQ values are averages of ten 12-bit ADC reads (0–4095 counts), without voltage,
resistance, or ppm conversion. A disconnected analog wire can still yield a
number; a raw value alone does not prove that the MQ sensor is working.

PMS fields use `pm10_env`, `pm25_env`, and `pm100_env`, respectively: atmospheric
mass concentrations in µg/m³, not particle counts. The firmware continuously
drains UART frames and omits PM readings if no valid frame arrived within 15 seconds.
SHT31 uses address 0x44 or 0x45. Failed SHT/CO2 reads are omitted. Missing keys
become database NULLs; -1, -999, and NaN are never used as error readings.
MQ137 is reported as raw ADC counts; no calibrated ammonia concentration is
stored.

The MH-Z19E is read with the library's `getCO2(false)` (0x86 request); a
successful response and positive concentration are required. Automatic baseline
correction is disabled, as in the tested device code. Allow the sensor's
[one-minute preheat](https://www.winsen-sensor.com/sensors/co2-sensor/mh-z19e.html)
before treating readings as stable; MQ heater conditioning and calibration
remain part of the hardware setup.

## Compile and flash

Install the Espressif ESP32 board package in Arduino IDE. Select the board
matching your ESP32-S3 and the connected port. Install these Library Manager
packages (including dependencies):

- PubSubClient by Nick O'Leary
- ArduinoJson **7.x**
- Adafruit SHT31 Library
- Adafruit PM25 AQI Sensor
- RTClib by Adafruit
- MH-Z19 by Jonathan Dempsey (WifWaf)
- WebSockets by Markus Sattler (Links2004 / arduinoWebSockets)

Set `ssid`, `password`, `mqtt_server`, and the `local_IP`, `gateway`, `subnet`,
and `primaryDNS` addresses at the top of the sketch to match your router's LAN.
The current defaults assume server `192.168.11.50`, ESP32 `192.168.11.51`, and
router/DNS `192.168.11.1` on a `/24` subnet. Confirm the gateway and subnet in
your router, and reserve the ESP32 address there (or confirm it is outside the
DHCP pool) to prevent an address conflict. MQTT uses port 1883. WiFi station
mode connects to your router; this collector does not create an access point.

Arduino requires a sketch folder matching the `.ino` name. From PowerShell:

```powershell
New-Item -ItemType Directory -Force .\airqualitymonitor | Out-Null
Copy-Item .\airqualitymonitor.ino .\airqualitymonitor\airqualitymonitor.ino
```

This copy replaces the local sketch, including its network settings. Set the
network values in the copy you will flash, then open
`airqualitymonitor/airqualitymonitor.ino` in Arduino IDE, Verify, and Upload.
The local folder is ignored by Git. During this update its existing network
settings were preserved while its sensor code was updated.

Open Serial Monitor at 115200 baud. A successful connection shows
`[MQTT] Connected`; each live reading shows `[PUBLISHED]` and its JSON. Missing
sensors print diagnostics. The device ID comes from the ESP32 MAC address. The
WebSocket server is at `ws://<ESP32-IP>:81/`; the IP is printed in the
Serial Monitor after WiFi connects. Log in to the dashboard and use the Live
ESP32 readings panel. The dashboard and ESP32 must be reachable on the same LAN;
enter the printed IP as `ws://<ESP32-IP>:81/` if `.local` name resolution is
unavailable. The dashboard remembers the address. The device socket broadcasts
readings and accepts pause/resume commands without authentication, so keep it
on a trusted LAN. Pausing holds queued MQTT readings on the ESP32 until resumed.
The dashboard shows Pausing/Resuming while waiting, then Running/Paused only
after the device acknowledges. An eight-second timeout means control was not
confirmed; reconnect and check that this sketch was uploaded. A container
rebuild does not update ESP32 firmware. Every boot starts paused, regardless of
the previous state; click Resume readings to start collection. Serial Monitor
logs `[COLLECTION] Paused` or `Running`
when commands change the state. The button controls this device; other MQTT
publishers (including `simulate_esp32.py`) must be stopped separately.

## Time and buffering

The DS3231 stores Philippine local time. MQTT timestamps include the full date
and `+08:00`; ingestion converts them to UTC. NTP sets the RTC once per boot when
internet access is available. A previously valid RTC works without internet.
If your RTC stores UTC instead, change its stored time to Philippine local time
before using this firmware offline. An unset RTC is never assigned compile time.

Without a valid RTC/NTP clock, live packets omit `ts` and ingestion assigns its
receipt time. Such packets cannot be cached. With a valid clock, up to 120
unsent readings are buffered in RAM; a full queue drops new readings with a
diagnostic. Power loss clears this queue. Failed MQTT publish writes leave the
queued reading intact. PubSubClient sends QoS 0, so a successful publish write
does not prove a PostgreSQL commit. End-to-end lossless delivery is not provided.

## Verify on Ubuntu

Before flashing, apply the schema/rebuild steps in
[SERVER_COMMANDS.md](SERVER_COMMANDS.md#updating-an-existing-server). No database
deletion or truncation is required. Confirm the laptop simulator is also stopped.
Then run these on the server while the ESP32 is connected:

```bash
cd ~/AirQualityMonitor
sudo docker compose ps
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "\d air_quality_logs"
sudo docker compose logs -f ingestion
```

Use Ctrl+C to exit log viewing. In another SSH terminal, inspect real-device rows:

```bash
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT recorded_at, device_id, temperature_c, humidity_perc, co2_ppm, pm1_ugm3, pm25_ugm3, pm10_ugm3, mq135_raw, mq137_raw FROM air_quality_logs WHERE device_id LIKE 'esp32-%' ORDER BY recorded_at DESC LIMIT 10;"
```

Match values and timestamps against Serial Monitor, then log in to the server's
dashboard and export CSV. NULL means the sensor value was omitted; check the
device diagnostics and wiring. `[CACHED]` means the device has not published
that reading yet. Database write failures appear in the ingestion logs.
