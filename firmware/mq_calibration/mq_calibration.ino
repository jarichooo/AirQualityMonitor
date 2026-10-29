#include <Arduino.h>
#include <Preferences.h>
#include <math.h>
#include <string.h>

// ESP32-S3: each sensor AO -> verified voltage divider -> its GPIO; common GND.
constexpr int MQ4_PIN = 1;
constexpr int MQ137_PIN = 2;
constexpr float V_CC = 5.0f;
constexpr float DIVIDER_FACTOR = 1.5f;
constexpr float ADC_MAX_MEASURABLE_V = 3.1f;
constexpr int SAMPLE_COUNT = 10;
constexpr float RL_MQ4 = 10000.0f;      // Provisional value borrowed from the previous MQ135.
constexpr float R0_MQ4 = 10947.0f;      // Provisional MQ135 R0, not an MQ4 calibration.
constexpr float MQ4_A = 1012.7f;
constexpr float MQ4_B = -2.786f;
constexpr float RL_MQ137 = 1000.0f;     // Restored starting value; verify actual hardware.
constexpr float R0_MQ137 = 310.0f;      // Starting value, not a measured calibration.
constexpr float MQ137_A = 102.2f;
constexpr float MQ137_B = -2.473f;
// Keep calibration values from discarded curves/load-resistor settings separate.
const char* const MQ4_R0_KEY = "r0-4-borrowed";
const char* const MQ137_R0_KEY = "r0-137-power";

Preferences preferences;
bool storageReady = false;
float r0Methane = R0_MQ4;
float r0Ammonia = R0_MQ137;

bool sensorResistance(int pin, float rl, float& adcV, float& sensorV, float& rs) {
  uint32_t millivolts = 0;
  for (int i = 0; i < SAMPLE_COUNT; ++i) {
    millivolts += analogReadMilliVolts(pin);
    delay(5);
  }
  adcV = millivolts / (1000.0f * SAMPLE_COUNT);
  sensorV = adcV * DIVIDER_FACTOR;
  if (!isfinite(adcV) || adcV <= 0.0f || adcV >= ADC_MAX_MEASURABLE_V ||
      !isfinite(sensorV) || sensorV <= 0.01f || sensorV >= V_CC - 0.05f ||
      !isfinite(rl) || rl <= 0.0f) return false;
  rs = (V_CC * rl) / sensorV - rl;
  return isfinite(rs) && rs > 0.0f;
}

void printSettings() {
  Serial.printf("R0_MQ4 = %.6ff; // kOhm, RL_MQ4 = %.3f kOhm\n", r0Methane / 1000, RL_MQ4 / 1000);
  Serial.printf("R0_MQ137 = %.6ff; // kOhm, RL_MQ137 = %.3f kOhm\n", r0Ammonia / 1000, RL_MQ137 / 1000);
  Serial.println("Copy these R0 values into the collector; it does not load this diagnostic's flash settings.");
}

void printReading(int pin, const char* label, float rl, float r0, bool ammonia) {
  float adcV, sensorV, rs;
  if (!sensorResistance(pin, rl, adcV, sensorV, rs)) {
    Serial.printf("%s: invalid/saturated ADC=%.3f V, AO=%.3f V; check wiring/divider.\n", label, adcV, sensorV);
    return;
  }
  float ratio = rs / r0;
  float ppm = ammonia ? MQ137_A * powf(ratio, MQ137_B)
                      : MQ4_A * powf(ratio, MQ4_B);
  if (!isfinite(ppm) || ppm <= 0) {
    Serial.printf("%s: conversion invalid, Rs=%.2f Ohm\n", label, rs);
    return;
  }
  Serial.printf("%s: ADC=%.3f V | AO=%.3f V | Rs=%.2f Ohm | R0=%.2f Ohm | ppm=%.3f (estimate)\n",
                label, adcV, sensorV, rs, r0, ppm);
}

void calibrate(bool ammonia, float reference) {
  if (!isfinite(reference) || reference <= 0) {
    Serial.println("Enter a finite, positive KNOWN gas concentration in ppm.");
    return;
  }
  float adcV, sensorV, rs;
  if (!sensorResistance(ammonia ? MQ137_PIN : MQ4_PIN,
                        ammonia ? RL_MQ137 : RL_MQ4, adcV, sensorV, rs)) {
    Serial.println("Calibration rejected: invalid/saturated voltage.");
    return;
  }
  float a = ammonia ? MQ137_A : MQ4_A;
  float b = ammonia ? MQ137_B : MQ4_B;
  float r0 = rs / powf(reference / a, 1.0f / b);
  if (!isfinite(r0) || r0 <= 0) {
    Serial.println("Calibration rejected: invalid R0.");
    return;
  }
  if (!storageReady || preferences.putFloat(ammonia ? MQ137_R0_KEY : MQ4_R0_KEY, r0) != sizeof(float)) {
    Serial.println("Calibration NOT applied: flash storage failed.");
    return;
  }
  if (ammonia) r0Ammonia = r0;
  else r0Methane = r0;
  Serial.println("R0 saved in ESP32 NVS flash. Keep RL, divider and curve unchanged or recalibrate.");
  printSettings();
}

void handleCommand(const char* line) {
  if (strcmp(line, "show") == 0) { printSettings(); return; }
  char name[8], extra;
  float reference;
  if (sscanf(line, "%7s %f %c", name, &reference, &extra) == 2) {
    if (strcmp(name, "c4") == 0) { calibrate(false, reference); return; }
    if (strcmp(name, "c137") == 0) { calibrate(true, reference); return; }
  }
  Serial.println("Commands: show | c4 <known methane ppm> | c137 <known NH3 ppm>");
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  storageReady = preferences.begin("mq-diag-v3", false);
  if (storageReady) {
    r0Methane = preferences.getFloat(MQ4_R0_KEY, R0_MQ4);
    r0Ammonia = preferences.getFloat(MQ137_R0_KEY, R0_MQ137);
  }
  if (!isfinite(r0Methane) || r0Methane <= 0) r0Methane = R0_MQ4;
  if (!isfinite(r0Ammonia) || r0Ammonia <= 0) r0Ammonia = R0_MQ137;
  delay(1000);
  Serial.println("Combined MQ diagnostic: no MQTT uploads. Stabilize heaters before calibration.");
  if (!storageReady) Serial.println("Flash unavailable: readings work, calibration saving is disabled.");
  handleCommand("help");
  printSettings();
}

void loop() {
  static char line[48];
  static size_t length = 0;
  static bool overflow = false;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      line[length] = '\0';
      if (overflow) Serial.println("Command too long; ignored.");
      else if (length) handleCommand(line);
      length = 0;
      overflow = false;
    } else if (length < sizeof(line) - 1) line[length++] = ch;
    else overflow = true;
  }
  static unsigned long last = millis() - 2000;
  if (millis() - last >= 2000) {
    last = millis();
    printReading(MQ4_PIN, "MQ4 methane", RL_MQ4, r0Methane, false);
    printReading(MQ137_PIN, "MQ137 ammonia", RL_MQ137, r0Ammonia, true);
  }
}
