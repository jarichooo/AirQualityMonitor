import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg2
from paho.mqtt.enums import CallbackAPIVersion


DB_HOST = "postgres_db"
DB_NAME = "air_quality"
DB_USER = "admin"
DB_PASS = "notsosecretpass"

MQTT_BROKER = "mqtt_broker"
MQTT_PORT = 1883
MQTT_TOPIC = "poultry/sensors"

INSERT_READING = """
    INSERT INTO air_quality_logs
        (recorded_at, temperature_c, humidity_perc, co2_ppm, nh3_ppm, pm25_ugm3, mq135_ppm, mq137_ppm)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
pending_readings = queue.Queue()


def get_db_connection():
    return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)


def parse_reading(raw_payload):
    payload = json.loads(raw_payload.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    recorded_at = payload.get("ts") or datetime.now(timezone.utc).isoformat()
    if payload.get("ts"):
        datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))

    values = [payload.get(key) for key in ("t", "h", "co2", "nh3", "pm25", "mq135", "mq137")]
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))) for value in values):
        raise ValueError("sensor values must be numbers or null")

    return (recorded_at, *values)


def database_worker():
    connection = None
    while True:
        reading = pending_readings.get()
        retry_delay = 1
        while True:
            try:
                if connection is None or connection.closed:
                    connection = get_db_connection()
                    log.info("Connected to PostgreSQL")
                with connection.cursor() as cursor:
                    cursor.execute(INSERT_READING, reading)
                connection.commit()
                log.info("Saved sensor reading for %s", reading[0])
                break
            except psycopg2.Error as error:
                log.warning("Database write failed; retrying in %s seconds: %s", retry_delay, error)
                if connection is not None:
                    try:
                        connection.rollback()
                        connection.close()
                    except psycopg2.Error:
                        pass
                connection = None
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
        pending_readings.task_done()


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info("Connected to MQTT broker")
        client.subscribe(MQTT_TOPIC)
    else:
        log.warning("MQTT connection failed with code %s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if reason_code != 0:
        log.warning("MQTT connection lost; reconnecting automatically")


def on_message(client, userdata, message):
    try:
        pending_readings.put(parse_reading(message.payload))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        log.warning("Ignoring malformed sensor payload: %s", error)


def connect_mqtt(client):
    retry_delay = 1
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            return
        except OSError as error:
            log.warning("MQTT broker unavailable; retrying in %s seconds: %s", retry_delay, error)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


def main():
    threading.Thread(target=database_worker, name="database-writer", daemon=True).start()

    client = mqtt.Client(CallbackAPIVersion.VERSION2, "ingestion_service")
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    connect_mqtt(client)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
