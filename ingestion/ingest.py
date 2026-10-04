import json
import math
import os
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import psycopg2
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

DB_HOST = os.environ.get("DB_HOST", "postgres_db")
DB_NAME = os.environ.get("DB_NAME", "air_quality")
DB_USER = os.environ.get("DB_USER", "admin")
DB_PASS = os.environ.get("DB_PASS", "notsosecretpass")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "mqtt_broker")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "poultry/sensors")
SENSOR_KEYS = ("t", "h", "co2", "nh3", "pm1", "pm25", "pm10", "mq135_raw", "mq137_raw")
LEGACY_TIMEZONE = timezone(timedelta(hours=8))


def parse_reading(raw):
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")
    timestamp = payload.get("ts")
    if timestamp is None:
        recorded_at = datetime.now(timezone.utc)
    else:
        if not isinstance(timestamp, str):
            raise ValueError("ts must be an ISO 8601 timestamp")
        recorded_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        # Existing ESP32 sketches send Philippine local time without an offset.
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=LEGACY_TIMEZONE)
        recorded_at = recorded_at.astimezone(timezone.utc)
    # Older firmware uses mq135/mq137 for the same raw values.
    values = [payload.get(key, payload.get(key.removesuffix("_raw"))) for key in SENSOR_KEYS]
    if all(value is None for value in values):
        raise ValueError("At least one sensor reading is required")
    for key, value in zip(SENSOR_KEYS, values):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number or null")
        if key == "h" and not 0 <= value <= 100:
            raise ValueError("Humidity must be between 0 and 100")
        if key != "t" and value < 0:
            raise ValueError(f"{key} cannot be negative")
    device_id = payload.get("device_id")
    if device_id is not None and (not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 100):
        raise ValueError("device_id must be a nonempty string of at most 100 characters")
    return (recorded_at, device_id, *values)


def save_reading(reading):
    # A fresh transaction per message prevents one failure poisoning later inserts.
    with closing(psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER,
                                password=DB_PASS, connect_timeout=5, options="-c timezone=UTC")) as conn:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO air_quality_logs
                    (recorded_at, device_id, temperature_c, humidity_perc, co2_ppm,
                     nh3_ppm, pm1_ugm3, pm25_ugm3, pm10_ugm3, mq135_raw, mq137_raw)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, reading)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        result, _ = client.subscribe(MQTT_TOPIC, qos=1)
        print(f"Connected to MQTT; subscribing to {MQTT_TOPIC} (result {result})", flush=True)
    else:
        print(f"MQTT connection failed: {reason_code}", flush=True)


def on_message(client, userdata, msg):
    try:
        reading = parse_reading(msg.payload)
    except (ValueError, UnicodeError, OverflowError) as error:
        print(f"Rejected invalid reading: {error}", flush=True)
        return
    try:
        save_reading(reading)
        print(f"Saved reading for {reading[0].isoformat()}", flush=True)
    except psycopg2.Error as error:
        # No blind retry: a connection failure during commit can mean it was saved.
        print(f"DATABASE WRITE FAILED; verify/replay this reading: {msg.payload!r}; {error}", flush=True)


def main():
    client = mqtt.Client(CallbackAPIVersion.VERSION2, "ingestion_service")
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            break
        except OSError as error:
            print(f"Waiting for MQTT broker: {error}", flush=True)
            time.sleep(5)
    client.loop_forever()


if __name__ == "__main__":
    main()
