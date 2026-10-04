#include <WiFi.h>
#include <PubSubClient.h>
#include <WebSocketsServer.h>
#include <Preferences.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <RTClib.h>
#include <Adafruit_SHT31.h>
#include <Adafruit_PM25AQI.h>
#include <MHZ19.h>
#include <ArduinoJson.h>
#include <math.h>
#include <string.h>
#include <time.h>
#include <vector>

// Configure these addresses for your router's LAN; reserve local_IP for this ESP32.
const char* ssid = "Henvironment";
const char* password = "notsosecretpass";
const char* mqtt_server = "192.168.11.50";
IPAddress local_IP(192, 168, 11, 55);
IPAddress gateway(192, 168, 11, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress primaryDNS(192, 168, 11, 1);
const int mqtt_port = 1883;
const char* MQTT_TOPIC = "poultry/sensors";

const int MQ135_PIN = 1;
const int MQ137_PIN = 2;
const int SHT_SDA = 4;
const int SHT_SCL = 11;
const int RTC_SDA = 13;
const int RTC_SCL = 12;
const int MHZ19_RX = 7;  // Sensor TX -> ESP32 RX
const int MHZ19_TX = 8;  // Sensor RX -> ESP32 TX
const int PMS_RX = 9;
const int PMS_TX = 10;

const unsigned long READ_INTERVAL_MS = 60000;
const unsigned long PMS_MAX_AGE_MS = 15000;
const long RTC_OFFSET_SECONDS = 8 * 3600;  // RTC stores Philippine local time.
const size_t MAX_CACHE_SIZE = 120;

WiFiClient espClient;
PubSubClient client(espClient);
WebSocketsServer webSocket(81);
Preferences collectionSettings;
bool collectionSettingsReady = false;
Adafruit_SHT31 sht;
TwoWire I2C_RTC(1);
RTC_DS3231 rtc;
MHZ19 mhz19;
HardwareSerial mhzSerial(1);
HardwareSerial pmsSerial(2);
Adafruit_PM25AQI aqi;
PM25_AQI_Data latestPms;

bool shtOnline = false;
bool rtcOnline = false;
bool rtcSynced = false;
bool pmsInitialized = false;
bool mdnsStarted = false;
bool mdnsAttempted = false;
bool webSocketStarted = false;
bool readingsPaused = false;
bool havePms = false;
unsigned long lastPmsTime = 0;
unsigned long lastReadTime = 0;
unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;
String deviceId;
// ponytail: RAM-only buffer, lost on reset; use persistent storage for longer outages.
std::vector<String> offlineCache;

void webSocketEvent(uint8_t clientNumber, WStype_t type, uint8_t* payload, size_t length) {
  bool wasPaused = readingsPaused;
  if (type == WStype_TEXT && length <= 16) {
    if (length == 5 && memcmp(payload, "PAUSE", 5) == 0) readingsPaused = true;
    else if (length == 6 && memcmp(payload, "RESUME", 6) == 0) readingsPaused = false;
    else if (!(length == 6 && memcmp(payload, "STATUS", 6) == 0)) return;
  } else if (type != WStype_CONNECTED) return;

  String status = readingsPaused
      ? "{\"type\":\"control\",\"paused\":true}"
      : "{\"type\":\"control\",\"paused\":false}";
  if (wasPaused != readingsPaused) {
    if (collectionSettingsReady && collectionSettings.putBool("paused", readingsPaused) == 0) {
      Serial.println("[COLLECTION] Pause state active but could not save it for reboot");
    }
    Serial.printf("[COLLECTION] %s\n", readingsPaused ? "Paused" : "Running");
    webSocket.broadcastTXT(status);
  } else {
    webSocket.sendTXT(clientNumber, status);
  }
}

void pollPms() {
  // Drain complete frames continuously so each minute's sample uses recent data.
  while (pmsInitialized && pmsSerial.available() >= 32) {
    PM25_AQI_Data reading;
    if (aqi.read(&reading)) {
      latestPms = reading;
      havePms = true;
      lastPmsTime = millis();
    }
  }
}

bool addTimestamp(JsonDocument& doc) {
  time_t utc = time(nullptr);
  bool ntpValid = utc >= 1704067200;  // At least 2024-01-01, not the boot epoch.
  if (rtcOnline && ntpValid && !rtcSynced) {
    rtc.adjust(DateTime(static_cast<uint32_t>(utc + RTC_OFFSET_SECONDS)));
    rtcSynced = true;
  }
  if (rtcOnline && !rtc.lostPower()) {
    DateTime now = rtc.now();
    if (now.isValid() && now.year() >= 2024) {
      doc["ts"] = now.timestamp() + "+08:00";
      return true;
    }
  }
  if (ntpValid) {
    struct tm now;
    gmtime_r(&utc, &now);
    char timestamp[25];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &now);
    doc["ts"] = timestamp;
    return true;
  }
  // Without a valid clock, live messages use ingestion's receipt time.
  // Do not cache them: receipt time would misdate an offline measurement.
  return false;
}

bool readSensors(JsonDocument& doc) {
  doc["device_id"] = deviceId;
  bool hasTimestamp = addTimestamp(doc);

  uint32_t adc135 = 0, adc137 = 0;
  for (int i = 0; i < 10; ++i) {
    adc135 += analogRead(MQ135_PIN);
    adc137 += analogRead(MQ137_PIN);
    delay(2);
  }
  // Store averaged 12-bit ADC counts. No gas/ppm conversion or guessed R0.
  doc["mq135_raw"] = adc135 / 10.0;
  doc["mq137_raw"] = adc137 / 10.0;

  if (!shtOnline) shtOnline = sht.begin(0x44) || sht.begin(0x45);
  if (shtOnline) {
    float temperature = sht.readTemperature();
    float humidity = sht.readHumidity();
    if (isfinite(temperature)) doc["t"] = temperature;
    if (isfinite(humidity) && humidity >= 0 && humidity <= 100) doc["h"] = humidity;
    if (!isfinite(temperature) || !isfinite(humidity)) shtOnline = false;
  }
  if (doc["t"].isNull() || doc["h"].isNull()) Serial.println("[SHT31] Missing/invalid reading");

  // MH-Z19E uses the documented 0x86 concentration request.
  int co2 = mhz19.getCO2(false);
  if (mhz19.errorCode == RESULT_OK && co2 > 0) {
    doc["co2"] = co2;
  } else {
    Serial.println("[MH-Z19E] Missing/invalid reading");
  }

  pollPms();
  if (havePms && millis() - lastPmsTime <= PMS_MAX_AGE_MS) {
    // *_env are atmospheric mass concentrations (ug/m3), not particle counts.
    doc["pm1"] = latestPms.pm10_env;
    doc["pm25"] = latestPms.pm25_env;
    doc["pm10"] = latestPms.pm100_env;
  } else {
    Serial.println("[PMS] Missing/stale frame; omitting particle readings");
  }
  return hasTimestamp;
}

void setup() {
  Serial.begin(115200);
  collectionSettingsReady = collectionSettings.begin("collection", false);
  if (collectionSettingsReady) readingsPaused = collectionSettings.getBool("paused", false);
  else Serial.println("[COLLECTION] Cannot persist pause state");
  Serial.printf("[COLLECTION] Boot: %s\n", readingsPaused ? "Paused" : "Running");
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  Wire.begin(SHT_SDA, SHT_SCL);
  shtOnline = sht.begin(0x44) || sht.begin(0x45);
  I2C_RTC.begin(RTC_SDA, RTC_SCL);
  rtcOnline = rtc.begin(&I2C_RTC);
  // Never set RTC to compile time: a later flash would give an old timestamp.
  if (!rtcOnline || rtc.lostPower()) Serial.println("[RTC] Unavailable/unset; awaiting NTP");

  mhzSerial.begin(9600, SERIAL_8N1, MHZ19_RX, MHZ19_TX);
  mhz19.begin(mhzSerial);
  mhz19.autoCalibration(false);  // Preserve the tested device's ABC setting.
  pmsSerial.begin(9600, SERIAL_8N1, PMS_RX, PMS_TX);
  pmsInitialized = aqi.begin_UART(&pmsSerial);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  if (!WiFi.config(local_IP, gateway, subnet, primaryDNS)) {
    Serial.println("[WiFi] Static IP configuration failed");
  }
  WiFi.begin(ssid, password);
  lastReadTime = millis() - READ_INTERVAL_MS;
  lastWifiAttempt = millis();
  deviceId = "esp32-" + WiFi.macAddress();
  deviceId.replace(":", "");
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  client.setServer(mqtt_server, mqtt_port);
  client.setBufferSize(768);
  client.setSocketTimeout(2);
  webSocket.onEvent(webSocketEvent);
  offlineCache.reserve(MAX_CACHE_SIZE);
}

void loop() {
  pollPms();
  unsigned long now = millis();
  if (webSocketStarted) webSocket.loop();
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWifiAttempt >= 10000) {
      lastWifiAttempt = now;
      WiFi.reconnect();
    }
  } else {
    if (!webSocketStarted) {
      webSocket.begin();
      webSocketStarted = true;
    }
    if (!mdnsAttempted) {
      mdnsAttempted = true;
      mdnsStarted = MDNS.begin("airqualitymonitor");
      String address = WiFi.localIP().toString();
      Serial.printf("[DEVICE] IP %s; WebSocket ws://%s:81/\n", address.c_str(),
                    mdnsStarted ? "airqualitymonitor.local" : address.c_str());
    }
    if (!client.connected() && now - lastMqttAttempt >= 5000) {
      lastMqttAttempt = now;
      if (client.connect(deviceId.c_str())) Serial.println("[MQTT] Connected");
      else Serial.printf("[MQTT] Connect failed (%d)\n", client.state());
    }
  }
  if (client.connected()) {
    client.loop();
    // Publish one cached reading per loop; only remove it after a successful write.
    if (!readingsPaused && !offlineCache.empty() && client.publish(MQTT_TOPIC, offlineCache.front().c_str())) {
      offlineCache.erase(offlineCache.begin());
    }
  }
  if (readingsPaused) return;
  if (millis() - lastReadTime < READ_INTERVAL_MS) return;
  lastReadTime = millis();
  JsonDocument doc;
  bool hasTimestamp = readSensors(doc);
  String payload;
  serializeJson(doc, payload);
  webSocket.broadcastTXT(payload);
  if (client.connected() && offlineCache.empty() && client.publish(MQTT_TOPIC, payload.c_str())) {
    Serial.print("[PUBLISHED] ");
    Serial.println(payload);
  } else if (hasTimestamp && offlineCache.size() < MAX_CACHE_SIZE) {
    offlineCache.push_back(payload);
    Serial.printf("[CACHED] %u readings\n", static_cast<unsigned>(offlineCache.size()));
  } else {
    Serial.println("[DROPPED] Buffer full or no valid measurement timestamp");
  }
  // PubSubClient publishes QoS 0; a successful socket write is not a DB acknowledgement.
}
