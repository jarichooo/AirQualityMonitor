import json
import time
import psycopg2
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# Database & MQTT Configuration (matching your docker-compose setup)
DB_HOST = "postgres_db"
DB_NAME = "air_quality"
DB_USER = "admin"
DB_PASS = "notsosecretpass"

MQTT_BROKER = "mqtt_broker"
MQTT_PORT = 1883
MQTT_TOPIC = "poultry/sensors"

def get_db_connection():
    while True:
        try:
            conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
            print("Connected to PostgreSQL successfully!")
            return conn
        except Exception as e:
            print(f"Waiting for database... {e}")
            time.sleep(5)

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("Connected to MQTT broker successfully!")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"Failed to connect, return code {reason_code}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        print(f"Received: {payload}")
        
        # 1. Prioritize ESP32 RTC timestamp, fallback to system UTC if missing
        if "ts" in payload:
            recorded_at = payload["ts"]
        else:
            recorded_at = datetime.now(timezone.utc).isoformat()
            
        # 2. Extract sensor readings safely (returns None if key is missing)
        t = payload.get("t")
        h = payload.get("h")
        co2 = payload.get("co2")
        nh3 = payload.get("nh3")
        pm25 = payload.get("pm25")
        mq135 = payload.get("mq135")
        mq137 = payload.get("mq137")
        
        # 3. Insert directly into Postgres
        insert_query = """
        INSERT INTO air_quality_logs (recorded_at, temperature_c, humidity_perc, co2_ppm, nh3_ppm, pm25_ugm3, mq135_ppm, mq137_ppm)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor = conn.cursor()
        cursor.execute(insert_query, (recorded_at, t, h, co2, nh3, pm25, mq135, mq137))
        conn.commit()
        cursor.close()
        print(f"-> Successfully saved reading for {recorded_at} to database\n")
        
    except Exception as e:
        print(f"Error processing message: {e}\n")

# 1. Connect to DB first
conn = get_db_connection()

# 2. Initialize MQTT Client (Using the new v2 API standard)
mqttc = mqtt.Client(CallbackAPIVersion.VERSION2, "ingestion_service")
mqttc.on_connect = on_connect
mqttc.on_message = on_message

# 3. Connect to MQTT with retry logic
while True:
    try:
        mqttc.connect(MQTT_BROKER, MQTT_PORT, 60)
        break
    except Exception as e:
        print(f"Waiting for MQTT broker... {e}")
        time.sleep(5)

# 4. Keep listening forever
mqttc.loop_forever()