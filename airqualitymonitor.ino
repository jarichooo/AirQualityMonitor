#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <RTClib.h>
#include <ArduinoJson.h>
#include <vector>

// 1. Network & MQTT Configuration
const char* ssid = "Henvironment";
const char* password = "notsosecretpass";
const char* mqtt_server = "192.168.11.50"; // Update to your Server/PC IP
const int mqtt_port = 1883;

WiFiClient espClient;
PubSubClient client(espClient);
RTC_DS3231 rtc;

// 2. Offline Caching Mechanism
std::vector<String> offlineCache;
const size_t MAX_CACHE_SIZE = 120; // Stores 120 readings (10 mins at 5s intervals)

// 3. Non-Blocking Timers
unsigned long lastReadTime = 0;
unsigned long lastReconnectAttempt = 0;

void setup_wifi() {
  WiFi.begin(ssid, password);
  Serial.print("[WIFI] Connecting");
  // Brief blocking wait on boot, but loop handles drops later
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 10) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setBufferSize(512);

  Wire.begin(); 
  if (!rtc.begin()) {
    Serial.println("[ERROR] Couldn't find DS3231 RTC module! Check I2C wiring.");
    Serial.flush();
  }
  
  if (rtc.lostPower()) {
    Serial.println("[RTC] RTC lost power, setting to compile time!");
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
  }
}

void flushCache() {
  if (offlineCache.empty()) return;
  
  Serial.print("[CACHE] Flushing ");
  Serial.print(offlineCache.size());
  Serial.println(" saved readings to server...");

  for (String &payload : offlineCache) {
    client.publish("poultry/sensors", payload.c_str());
    delay(50); // Small delay to prevent flooding the broker
  }
  offlineCache.clear();
  Serial.println("[CACHE] Flush complete.");
}

void loop() {
  // Non-blocking WiFi & MQTT Reconnection
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
  } else if (!client.connected()) {
    unsigned long now = millis();
    if (now - lastReconnectAttempt > 5000) {
      lastReconnectAttempt = now;
      Serial.println("[MQTT] Attempting connection...");
      if (client.connect("ESP32_Farm_Prod")) {
        Serial.println("[MQTT] Connected!");
        flushCache(); // Push all offline data immediately upon reconnect
      }
    }
  } else {
    client.loop();
  }

  // Trigger sensor reading every 5 seconds regardless of network status
  if (millis() - lastReadTime > 5000) {
    lastReadTime = millis();

    // 1. Grab hardware timestamp
    DateTime now = rtc.now();
    char timestamp[25];
    snprintf(timestamp, sizeof(timestamp), "%04d-%02d-%02d %02d:%02d:%02d",
             now.year(), now.month(), now.day(),
             now.hour(), now.minute(), now.second());

    // 2. Read Sensors (Still using simulation math so you can test it right now)
    float temp = random(280, 350) / 10.0;
    float hum = random(600, 850) / 10.0;
    float co2 = random(4000, 6000) / 10.0;
    float pm1 = random(50, 100) / 10.0;
    float pm10 = random(250, 400) / 10.0;
    float pm25 = random(100, 250) / 10.0;
    int mq135 = random(10, 50);
    int mq137 = random(20, 60);

    // 3. Build JSON payload
    StaticJsonDocument<512> doc;
    doc["ts"] = timestamp;
    doc["t"] = temp;
    doc["h"] = hum;
    doc["co2"] = co2;
    doc["pm1"] = pm1;
    doc["pm10"] = pm10;
    doc["pm25"] = pm25;
    doc["mq135_raw"] = mq135;
    doc["mq137_raw"] = mq137;

    String jsonString;
    serializeJson(doc, jsonString);

    // 4. Publish or Cache
    if (client.connected()) {
      client.publish("poultry/sensors", jsonString.c_str());
      Serial.print("[PUBLISHED] ");
      Serial.println(jsonString);
    } else {
      // Network is down, save to cache
      if (offlineCache.size() < MAX_CACHE_SIZE) {
        offlineCache.push_back(jsonString);
        Serial.print("[CACHED] Offline reading saved. Cache size: ");
        Serial.println(offlineCache.size());
      } else {
        Serial.println("[ERROR] Cache full! Oldest readings will be dropped if not handled.");
        // Optional: offlineCache.erase(offlineCache.begin()); to make room for newest data
      }
    }
  }
}