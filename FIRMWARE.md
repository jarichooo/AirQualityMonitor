# ESP32-S3 sensor firmware

`airqualitymonitor.ino` is the hardware collector. It samples every 60 seconds,
publishes JSON to Mosquitto on the Ubuntu server, topic `poultry/sensors`, and
broadcasts the same live sensor readings on a WebSocket at port 81. The dashboard
can pause or resume both MQTT uploads and live broadcasts.
`simulate_esp32.py` is the separate simulator; stop it before real collection.

## Wiring and stored fields

| Sensor | ESP32 GPIO | MQTT fields | Database fields |
| --- | --- | --- | --- |
| MQ-4 Flying Fish methane module AO | 1 | `mq4_ppm` | `mq4_ppm` |
| MQ137 ammonia module AO | 2 | `mq137_ppm` | `mq137_ppm` |
| SHT31 I2C | SDA 4, SCL 11 | `t`, `h` | `temperature_c`, `humidity_perc` |
| DS3231 I2C (second bus) | SDA 13, SCL 12 | `ts` | `recorded_at` |
| MH-Z19E UART1 | RX 7, TX 8 | `co2` | `co2_ppm` |
| Plantower PMS UART2 | RX 9, TX 10 | `pm1`, `pm25`, `pm10` | `pm1_ugm3`, `pm25_ugm3`, `pm10_ugm3` |

Connect UART sensor TX to ESP32 RX and sensor RX to ESP32 TX. Keep the tested
power and voltage-divider wiring: GPIO1/2 must receive ESP32-safe analog levels.
MQ concentration estimates use ten voltage samples per sensor and the
configured divider, load resistor, reference resistance, and gas-specific curve.
A disconnected analog wire can still yield a voltage; a plausible ppm value
alone does not prove that the sensor or its calibration is working.

PMS fields use `pm10_env`, `pm25_env`, and `pm100_env`, respectively: atmospheric
mass concentrations in µg/m³, not particle counts. The firmware continuously
drains UART frames and omits PM readings if no valid frame arrived within 15 seconds.
SHT31 uses address 0x44 or 0x45. Failed SHT/CO2 reads are omitted. Missing keys
become database NULLs; -1, -999, and NaN are never used as error readings.
MQ-4 reports estimated methane ppm and MQ137 reports estimated ammonia ppm.
Invalid voltage or conversion results are omitted, never sent as -1 ppm.

The collector prints each published gas estimate with ADC voltage, reconstructed
AO voltage, Rs, and the active R0 in Serial Monitor at 115200 baud. Out-of-range
estimates are reported without clamping or replacing small values with zero.
Invalid electrical readings are omitted, rather than stored as zero.

## MQ conversion and calibration

The collector in **`airqualitymonitor/airqualitymonitor.ino`** includes calibration
commands directly; no separate calibration firmware is required. The tracked root
`airqualitymonitor.ino` contains the same code.

```text
sensor_voltage = adc_voltage * DIVIDER_FACTOR
Rs = RL * (V_CC - sensor_voltage) / sensor_voltage
MQ4_ppm = 1000 * (Rs / R0)^(-2.95)
MQ137_ppm = 10^((log10(Rs / R0) - 0.42) / -0.263)
```

| Sensor | RL (kOhm) | Default R0 (kOhm) | Curve |
| --- | --- | --- | --- |
| MQ-4 Flying Fish | 1.0 | 0.945 | `1000 * pow(Rs/R0, -2.95)` |
| MQ137 | 47.0 | 20.0 | `pow(10, (log10(Rs/R0) - 0.42) / -0.263)` |

These are the latest local sketch's starting settings, not verified measurements
of your modules. Check the actual load resistor before using them. Changing a
constant does not change the resistor on a module. V_CC is 5.0 V and the divider
factor is 1.5; verify both against your circuit and compare voltages with a meter.
Never connect 5 V AO directly to the ESP32 GPIO.

MQ137 now uses the requested log-log equation with slope `MQ137_M = -0.263`
and intercept `MQ137_B = 0.42`. These are user-supplied tutorial coefficients,
not a verified fit to your module. The 47 kOhm RL must match the physical load
resistor; default R0 is 20 kOhm (20000 Ohm), pending calibration. The ESP32 keeps
its calibrated millivolt readings and divider correction rather than using the
Arduino Uno example's `analogRead(A0) * 5 / 1023` scaling.

`analogReadMilliVolts()` supplies calibrated ADC voltage. Ten samples are averaged
per sensor. Each sample must be valid: ADC voltage above zero and below 3.1 V
(the ESP32-S3 `ADC_11db` measurable limit), reconstructed AO above 0.01 V and below
4.95 V, and finite positive resistance. One invalid sample rejects that sensor's
reading. Conversion results must also be finite and positive. No methane floor
or ammonia zeroing is applied before MQTT uploads or live WebSocket broadcasts.

### Calibration in the collector

1. Verify RL, divider, supply and curve settings. Condition/preheat the sensors
   according to their manufacturer and wait for stable readings. A short sample
   average does not establish heater or gas stabilization. See the manufacturer
   [MQ-4 specifications](https://www.winsen-sensor.com/product/mq-4.html) and
   [MQ137 specifications](https://www.winsen-sensor.com/product/mq137.html).
2. Keep collection **paused** while calibrating. If the ESP32 has queued readings,
   first Resume to deliver those readings, then Pause. The calibration command
   refuses to run while collecting or while the offline queue is nonempty.
3. Use a controlled **known reference gas concentration**, with stable readings.
   Room air is not a known calibration reference. Calibration accepts methane
   references within 300-10000 ppm or ammonia references within 5-500 ppm.
4. Open Serial Monitor at **115200 baud**, with **Newline** enabled. Run the command
   for the sensor you are calibrating. The word `stable` acknowledges that heater
   conditioning, reference-gas stabilization and circuit verification are complete;
   the firmware cannot independently confirm them.

| Command | Action |
| --- | --- |
| `show` | Print active R0/calibration status and a current voltage/resistance/ppm sample; no upload. |
| `c4 <known ppm> stable` | Calculate and save MQ4 R0 from a known methane reference. |
| `c137 <known ppm> stable` | Calculate and save MQ137 R0 from a known ammonia reference. |

For example, `c137 50 stable` is appropriate only when the sensor is actually
stable in a known 50 ppm ammonia reference. Do not use it just to make the output
read 50. Repeat `show` while paused to inspect whether voltage/resistance is
settling without uploading the reference-gas samples. The inverse of the selected
curve determines R0:

```text
MQ4 R0 = measured_Rs / pow(reference_ppm / 1000, 1 / -2.95)
MQ137 R0 = measured_Rs / pow(10, -0.263 * log10(reference_ppm) + 0.42)
```

A successful command saves only that sensor's calibration to ESP32 NVS flash and
then applies it. Invalid references, invalid voltage, invalid R0 or failed flash
writes retain the previous R0. On reboot the collector loads saved values; it
never recalibrates automatically from surrounding air. NVS survives restarts and
power loss ([Espressif Preferences documentation](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/preferences.html)).
The stored record includes R0, RL, curve coefficients (MQ4 A/B or MQ137 m/b),
supply voltage, divider and ADC range.
Saved records with a different circuit/curve configuration are ignored. Missing
or invalid records use the explicit defaults and print **PROVISIONAL, not
calibrated**. Provisional estimates can still be collected, so record calibration
changes when preparing training data. Ordinary sampling never writes flash.

The collector uses a separate `mq-collector-v1` namespace. It does not import
settings from the older optional `firmware/mq_calibration/mq_calibration.ino`
diagnostic: that sketch uses different gas settings and is unnecessary for this
workflow. Do not copy its older R0 into the current collector. Saved MQ137
calibration from the old `102.2 * pow(Rs/R0, -2.473)` configuration is automatically
ignored because the load resistor and curve no longer match; the new default is
20 kOhm until recalibrated. Compatible saved MQ4 calibration remains usable.

After calibration, remove the reference gas, allow readings to stabilize in the
measurement environment, and Resume from the dashboard. The ESP32 still starts
paused and samples once per minute. Check accuracy at additional known reference
points. Recalibrate after changing sensors, RL, divider, supply or curve; firmware
cannot detect replacement of a physical module with identical settings.

MQ4's specified range is 300-10000 ppm; MQ137's is 5-500 ppm. Serial diagnostics
flag estimates outside those ranges, but retain the calculated values unchanged.
A small number is not proof of true zero or accurate sub-ppm measurement. This
code adds no temperature/humidity compensation and does not verify the physical
load resistors or curve accuracy.

No database migration or Docker rebuild is needed. Upload the new firmware to
the ESP32. Existing database rows retain their original values; record the
firmware/calibration transition when selecting data for model training.

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
The current defaults assume server `192.168.11.50`, ESP32 `192.168.11.55`, and
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
sudo docker exec -it postgres_db psql -U admin -d air_quality -c "SELECT recorded_at, device_id, temperature_c, humidity_perc, co2_ppm, pm1_ugm3, pm25_ugm3, pm10_ugm3, mq4_ppm, mq137_ppm FROM air_quality_logs WHERE device_id LIKE 'esp32-%' ORDER BY recorded_at DESC LIMIT 10;"
```

Match values and timestamps against Serial Monitor, then log in to the server's
dashboard and export CSV. NULL means the sensor value was omitted; check the
device diagnostics and wiring. `[CACHED]` means the device has not published
that reading yet. Database write failures appear in the ingestion logs.
