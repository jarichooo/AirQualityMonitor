import time
import json
import random
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

# Configuration
BROKER = "localhost" # Connects directly to the Docker container on your PC
PORT = 1883
TOPIC = "poultry/sensors"

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("[MQTT] Successfully connected to local broker!")
    else:
        print(f"[MQTT] Connection failed with code {reason_code}")

# Setup MQTT Client
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.connect(BROKER, PORT, 60)
client.loop_start()

print("=======================================")
print("   PYTHON ESP32 SIMULATOR RUNNING      ")
print("=======================================")
print("Press Ctrl+C to stop.\n")

try:
    while True:
        # 1. Generate Timestamp
        timestamp = datetime.now(timezone.utc).isoformat()

        # 2. Generate Random Farm Data
        payload = {
            "device_id": "python-simulator",
            "ts": timestamp,
            "t": round(random.uniform(28.0, 35.0), 1),
            "h": round(random.uniform(60.0, 85.0), 1),
            "co2": round(random.uniform(400.0, 600.0), 1),
            "pm1": round(random.uniform(5.0, 10.0), 1),
            "pm25": round(random.uniform(10.0, 25.0), 1),
            "pm10": round(random.uniform(25.0, 40.0), 1),
            "mq135_raw": random.randint(10, 50),
            "mq137_raw": random.randint(20, 60)
        }

        # 3. Publish to Mosquitto
        json_string = json.dumps(payload)
        client.publish(TOPIC, json_string, qos=1).wait_for_publish(timeout=5)
        print(f"[PUBLISHED] {json_string}")
        
        time.sleep(5)

except KeyboardInterrupt:
    print("\nSimulator stopped.")
    client.loop_stop()
    client.disconnect()
