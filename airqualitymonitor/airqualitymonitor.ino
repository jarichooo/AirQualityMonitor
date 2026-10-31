#include <WiFi.h>
#include <PubSubClient.h>
#include <WebSocketsServer.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <RTClib.h>
#include <Adafruit_SHT31.h>
#include <Adafruit_PM25AQI.h>
#include <MHZ19.h>
#include <ArduinoJson.h>
#include <Preferences.h>
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

const int MQ4_PIN = 1;
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

constexpr float V_CC = 5.0f;
constexpr float ADC_REF_V = 3.3f;  // Electrical reference; analogReadMilliVolts already applies ADC calibration.
constexpr float DIVIDER_FACTOR = 1.5f;
constexpr float ADC_MAX_MEASURABLE_V = 3.1f;  // ESP32-S3 at ADC_11db.
constexpr int GAS_SAMPLE_COUNT = 10;
static_assert(ADC_MAX_MEASURABLE_V <= ADC_REF_V, "ADC range must not exceed its reference");

// Provisional user-supplied curve/settings; verify actual RL and calibrate with known gas.
constexpr float RL_MQ4 = 1.0f;
constexpr float R0_MQ4 = 0.945f;
constexpr float MQ4_A = 1000.0f;
constexpr float MQ4_B = -2.95f;

// User-supplied NH3 log-log fit; verify RL and calibrate R0 on the actual module.
constexpr float RL_MQ137 = 47.0f;
constexpr float R0_MQ137 = 20.0f;
constexpr float MQ137_M = -0.263f;
constexpr float MQ137_B = 0.42f;
static_assert(MQ137_M < 0.0f, "NH3 curve slope must be negative");

Preferences gasPreferences;
bool gasStorageReady = false;
float r0Methane = R0_MQ4;
float r0Ammonia = R0_MQ137;
bool methaneCalibrated = false;
bool ammoniaCalibrated = false;

WiFiClient espClient;
PubSubClient client(espClient);
WebSocketsServer webSocket(81);
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
bool readingsPaused = false;  // Every boot requires Resume from the dashboard.
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

bool gasResistance(float adcMillivolts, float rlKOhm, float& rsKOhm) {
  float adcVoltage = adcMillivolts / 1000.0f;
  if (!isfinite(adcVoltage) || adcVoltage <= 0.0f || adcVoltage >= ADC_MAX_MEASURABLE_V ||
      !isfinite(rlKOhm) || rlKOhm <= 0.0f) return false;

  float sensorVoltage = adcVoltage * DIVIDER_FACTOR;
  if (!isfinite(sensorVoltage) || sensorVoltage <= 0.01f || sensorVoltage >= V_CC - 0.05f) return false;

  rsKOhm = rlKOhm * (V_CC - sensorVoltage) / sensorVoltage;
  return isfinite(rsKOhm) && rsKOhm > 0.0f;
}

bool gasRatio(float adcMillivolts, float rlKOhm, float r0KOhm, float& ratio) {
  float rsKOhm;
  if (!isfinite(r0KOhm) || r0KOhm <= 0.0f ||
      !gasResistance(adcMillivolts, rlKOhm, rsKOhm)) return false;
  ratio = rsKOhm / r0KOhm;
  return isfinite(ratio) && ratio > 0.0f;
}

bool gasPpm(float adcMillivolts, float rlKOhm, float r0KOhm, float a, float b, float& ppm) {
  float ratio;
  if (!gasRatio(adcMillivolts, rlKOhm, r0KOhm, ratio) ||
      !isfinite(a) || !isfinite(b) || a <= 0.0f) return false;
  ppm = a * powf(ratio, b);
  return isfinite(ppm) && ppm > 0.0f;
}

bool ammoniaPpm(float adcMillivolts, float& ppm) {
  float ratio;
  if (!gasRatio(adcMillivolts, RL_MQ137, r0Ammonia, ratio)) return false;
  ppm = powf(10.0f, (log10f(ratio) - MQ137_B) / MQ137_M);
  return isfinite(ppm) && ppm > 0.0f;
}

// Called only after a valid conversion; log the same sample that is published.
void logGasReading(const char* sensor, float adcMillivolts, float rlKOhm, float r0KOhm, float ppm) {
  float adcVoltage = adcMillivolts / 1000.0f;
  float sensorVoltage = adcVoltage * DIVIDER_FACTOR;
  float rsOhms = rlKOhm * 1000.0f * (V_CC - sensorVoltage) / sensorVoltage;
  Serial.printf("%s: ADC=%.4f V | AO=%.4f V | Rs=%.2f Ohm | R0=%.2f Ohm | ppm=%.6f (estimate)\n",
                sensor, adcVoltage, sensorVoltage, rsOhms, r0KOhm * 1000.0f, ppm);
}

bool sampleGasVoltage(int pin, float rlKOhm, float& averageMillivolts) {
  float sum = 0.0f;
  for (int i = 0; i < GAS_SAMPLE_COUNT; ++i) {
    float millivolts = analogReadMilliVolts(pin);
    float rsKOhm;
    // Reject individual rail/invalid samples instead of hiding them in an average.
    if (!gasResistance(millivolts, rlKOhm, rsKOhm)) return false;
    sum += millivolts;
    delay(2);
  }
  averageMillivolts = sum / GAS_SAMPLE_COUNT;
  return true;
}

// One NVS record binds R0 to its curve and circuit; older configurations are ignored.
void calibrationRecord(bool ammonia, float r0, float (&record)[7]) {
  record[0] = r0;
  record[1] = ammonia ? RL_MQ137 : RL_MQ4;
  record[2] = ammonia ? MQ137_M : MQ4_A;
  record[3] = ammonia ? MQ137_B : MQ4_B;
  record[4] = V_CC;
  record[5] = DIVIDER_FACTOR;
  record[6] = ADC_MAX_MEASURABLE_V;
}

void loadGasCalibration(bool ammonia) {
  const char* key = ammonia ? "mq137" : "mq4";
  float record[7], expected[7];
  if (!gasStorageReady || gasPreferences.getBytesLength(key) != sizeof(record) ||
      gasPreferences.getBytes(key, record, sizeof(record)) != sizeof(record) ||
      !isfinite(record[0]) || record[0] <= 0.0f) return;
  calibrationRecord(ammonia, record[0], expected);
  for (size_t i = 1; i < 7; ++i) {
    if (record[i] != expected[i]) return;
  }
  if (ammonia) { r0Ammonia = record[0]; ammoniaCalibrated = true; }
  else { r0Methane = record[0]; methaneCalibrated = true; }
}

void printGasSettings() {
  Serial.printf("[MQ4] RL=%.1f Ohm | R0=%.3f Ohm | %s\n", RL_MQ4 * 1000.0f,
                r0Methane * 1000.0f, methaneCalibrated ? "saved reference calibration" : "PROVISIONAL, not calibrated");
  Serial.printf("[MQ137] RL=%.1f Ohm | R0=%.3f Ohm | %s\n", RL_MQ137 * 1000.0f,
                r0Ammonia * 1000.0f, ammoniaCalibrated ? "saved reference calibration" : "PROVISIONAL, not calibrated");
}

void printGasSample(bool ammonia) {
  float millivolts, ppm;
  float rl = ammonia ? RL_MQ137 : RL_MQ4;
  float r0 = ammonia ? r0Ammonia : r0Methane;
  if (sampleGasVoltage(ammonia ? MQ137_PIN : MQ4_PIN, rl, millivolts) &&
      (ammonia ? ammoniaPpm(millivolts, ppm)
               : gasPpm(millivolts, rl, r0, MQ4_A, MQ4_B, ppm))) {
    logGasReading(ammonia ? "MQ137 ammonia" : "MQ4 methane", millivolts, rl, r0, ppm);
  } else Serial.println(ammonia ? "[MQ137] Invalid/saturated sample" : "[MQ4] Invalid/saturated sample");
}

void calibrateGas(bool ammonia, float referencePpm) {
  if (!readingsPaused || !offlineCache.empty()) {
    Serial.println("[CALIBRATION] Pause collection first; queued readings must be delivered before calibration.");
    return;
  }
  float minimum = ammonia ? 5.0f : 300.0f;
  float maximum = ammonia ? 500.0f : 10000.0f;
  if (!isfinite(referencePpm) || referencePpm < minimum || referencePpm > maximum) {
    Serial.printf("[CALIBRATION] Known reference must be within %.0f-%.0f ppm.\n", minimum, maximum);
    return;
  }
  float millivolts, rsKOhm;
  float rl = ammonia ? RL_MQ137 : RL_MQ4;
  if (!sampleGasVoltage(ammonia ? MQ137_PIN : MQ4_PIN, rl, millivolts) ||
      !gasResistance(millivolts, rl, rsKOhm)) {
    Serial.println("[CALIBRATION] Invalid/saturated voltage; previous R0 retained.");
    return;
  }
  float referenceRatio = ammonia
      ? powf(10.0f, MQ137_M * log10f(referencePpm) + MQ137_B)
      : powf(referencePpm / MQ4_A, 1.0f / MQ4_B);
  float r0 = rsKOhm / referenceRatio;
  if (!isfinite(r0) || r0 <= 0.0f) {
    Serial.println("[CALIBRATION] Invalid R0; previous R0 retained.");
    return;
  }
  float record[7];
  calibrationRecord(ammonia, r0, record);
  if (!gasStorageReady || gasPreferences.putBytes(ammonia ? "mq137" : "mq4", record, sizeof(record)) != sizeof(record)) {
    Serial.println("[CALIBRATION] Flash write failed; previous R0 retained.");
    return;
  }
  if (ammonia) { r0Ammonia = r0; ammoniaCalibrated = true; }
  else { r0Methane = r0; methaneCalibrated = true; }
  Serial.println("[CALIBRATION] Saved reference R0. Verify accuracy against additional reference points.");
  printGasSettings();
}

void handleGasCommand(const char* line) {
  if (strcmp(line, "show") == 0) {
    printGasSettings();
    Serial.println("[DIAGNOSTIC] Current samples below are not uploaded.");
    printGasSample(false);
    printGasSample(true);
    return;
  }
  char command[8], confirmation[8], extra;
  float reference;
  if (sscanf(line, "%7s %f %7s %c", command, &reference, confirmation, &extra) == 3 &&
      strcmp(confirmation, "stable") == 0) {
    if (strcmp(command, "c4") == 0) { calibrateGas(false, reference); return; }
    if (strcmp(command, "c137") == 0) { calibrateGas(true, reference); return; }
  }
  Serial.println("Commands: show | c4 <known ppm> stable | c137 <known ppm> stable");
  Serial.println("'stable' confirms conditioned heaters, stable readings in KNOWN reference gas, and verified circuit settings. No ambient auto-calibration.");
}

void pollGasCommands() {
  static char line[64];
  static size_t length = 0;
  static bool overflow = false;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      line[length] = '\0';
      if (overflow) Serial.println("Command too long; ignored.");
      else if (length) handleGasCommand(line);
      length = 0;
      overflow = false;
    } else if (length < sizeof(line) - 1) line[length++] = ch;
    else overflow = true;
  }
}

bool readSensors(JsonDocument& doc) {
  doc["device_id"] = deviceId;
  bool hasTimestamp = addTimestamp(doc);

  float mq4AverageMillivolts, mq137AverageMillivolts;
  float ppm = 0.0f;

  if (sampleGasVoltage(MQ4_PIN, RL_MQ4, mq4AverageMillivolts) &&
      gasPpm(mq4AverageMillivolts, RL_MQ4, r0Methane, MQ4_A, MQ4_B, ppm)) {
    doc["mq4_ppm"] = ppm;
    logGasReading("MQ4 methane", mq4AverageMillivolts, RL_MQ4, r0Methane, ppm);
    if (!methaneCalibrated) Serial.println("[MQ4] PROVISIONAL calibration; estimate only");
    if (ppm < 300.0f || ppm > 10000.0f) Serial.println("[MQ4] Estimate outside specified 300-10000 ppm range; value retained without clamping");
  } else {
    Serial.println("[MQ4] Invalid/saturated reading; omitting mq4_ppm");
  }

  if (sampleGasVoltage(MQ137_PIN, RL_MQ137, mq137AverageMillivolts) &&
      ammoniaPpm(mq137AverageMillivolts, ppm)) {
    doc["mq137_ppm"] = ppm;
    logGasReading("MQ137 ammonia", mq137AverageMillivolts, RL_MQ137, r0Ammonia, ppm);
    if (!ammoniaCalibrated) Serial.println("[MQ137] PROVISIONAL calibration; estimate only");
    if (ppm < 5.0f || ppm > 500.0f) Serial.println("[MQ137] Estimate outside specified 5-500 ppm range; value retained without clamping");
  } else {
    Serial.println("[MQ137] Invalid/saturated reading; omitting mq137_ppm");
  }

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
  Serial.println("[COLLECTION] Boot: Paused; waiting for dashboard Resume");
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  gasStorageReady = gasPreferences.begin("mq-collector-v1", false);
  loadGasCalibration(false);
  loadGasCalibration(true);
  if (!gasStorageReady) Serial.println("[CALIBRATION] Flash unavailable; calibration saving disabled.");
  printGasSettings();
  handleGasCommand("help");

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
  pollGasCommands();
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