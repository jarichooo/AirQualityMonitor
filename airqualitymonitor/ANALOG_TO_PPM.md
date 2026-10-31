# How the gas readings become ppm

The ESP32 measures an analog voltage from each gas module. The firmware converts
that voltage into sensor resistance, compares it with a calibrated reference,
then applies a gas-specific curve to estimate concentration in parts per million
(ppm). MQ-4 measures methane; MQ137 measures ammonia.

```text
Module AO -> voltage divider -> ESP32 ADC -> measured voltage
          -> recover AO voltage -> Rs -> Rs/R0 -> gas curve -> estimated ppm
```

## What the names mean

| Name | Meaning |
| --- | --- |
| ADC | The ESP32 analog-to-digital converter. It measures the voltage at its input pin. |
| AO or Vout | The module's analog output voltage, before the external voltage divider. |
| Vc | Supply voltage of the sensor measurement circuit; configured as 5 V. |
| RL | The physical load resistor between AO and GND in the standard circuit. |
| Rs | The sensing element's resistance at the time of measurement. |
| R0 | A reference resistance used with the chosen sensitivity curve. Calibration determines the appropriate value for the individual sensor. |
| Rs/R0 | A dimensionless resistance ratio used to calculate concentration. |
| A, B, m, b | Coefficients describing the selected gas sensitivity curve. |

A resistor marked **102** means `10 × 10² = 1000 ohms = 1 kilohm`.
If it is the resistor from AO to GND, RL is 1 kΩ. This does not determine R0.
The firmware represents RL and R0 in **kΩ**, although diagnostic logs print Ω.

## 1. Measure voltage

`sampleGasVoltage()` takes ten readings using `analogReadMilliVolts()` and averages
the valid measurements. The API already returns millivolts; the code does not
convert raw ADC counts using an assumed 4095-count scale.

```text
Vadc = average millivolts / 1000
Vout = Vadc × DIVIDER_FACTOR
```

The configured divider factor is **1.5**. This assumes the actual external
resistors reduce AO to two-thirds of its original voltage. It must match the
wiring; it is separate from the sensor module's RL.

For example, `Vadc = 0.6297 V` gives `Vout = 0.94455 V`.

## 2. Calculate sensor resistance

The standard measurement circuit is:

```text
Vc ---- sensing element (Rs) ----+---- AO
                                |
                                RL
                                |
                               GND
```

`gasResistance()` calculates:

```text
Rs = RL × (Vc / Vout - 1)
```

For `RL = 1 kΩ`, `Vc = 5 V`, and `Vout = 0.94455 V`, Rs is approximately
**4.294 kΩ**. The result is a resistance, not yet a gas concentration.

## 3. Compare with the reference

`gasRatio()` calculates:

```text
ratio = Rs / R0
```

R0 accounts for the sensor's reference response. A placeholder copied from
another sensor is not proof of calibration. R0 must use the same reference
definition as the curve coefficients; do not mix values from different curves.

## 4. Apply the gas curve

For **methane**, `gasPpm()` uses a power curve:

```text
ppm = A × ratio^B
```

For **ammonia**, `ammoniaPpm()` uses a log-log curve:

```text
log10(ratio) = m × log10(ppm) + b
ppm = 10^((log10(ratio) - b) / m)
```

These are configured curve approximations, not universal coefficients for every
MQ module. Firmware calibration adjusts R0 while retaining the selected curve.

## Check which sketch you flash

At the time this document was written, the two local copies differ:

| Setting | Root `../airqualitymonitor.ino` | Folder `airqualitymonitor.ino` |
| --- | --- | --- |
| MQ-4 RL | 1 kΩ | 1 kΩ |
| MQ-4 default R0 | 0.945 kΩ | 0.945 kΩ |
| Methane A | 1012.7 | 1000 |
| Methane B | -2.786 | -2.95 |
| MQ137 RL | 1 kΩ | 47 kΩ |
| MQ137 default R0 | 3.6 kΩ | 20 kΩ |
| Ammonia m | -0.263 | -0.263 |
| Ammonia b | 0.42 | 0.42 |

**If both actual load resistors are 102 resistors from AO to GND, the folder
copy's 47 kΩ MQ137 setting does not match that hardware.** Check the file Arduino
IDE has open before flashing. This document does not change either sketch.
Matching saved calibration can override the default R0; startup diagnostics show
the active value and whether it is provisional or loaded from saved calibration.

## Calibration and invalid readings

`calibrateGas()` uses a known reference concentration to calculate the expected
ratio, then sets `R0 = measured Rs / expected ratio`. It requires collection to
be paused and the offline queue to be empty. Saved calibration is tied to the
configured circuit and curve. A successful save still needs verification at
additional reference concentrations.

The firmware rejects invalid or saturated voltages and nonfinite conversions.
It omits an invalid gas field instead of substituting zero. It also warns when
estimates are outside its configured ranges: 300–10000 ppm for methane and
5–500 ppm for ammonia. Such estimates are retained without clamping; they are
not evidence that the sensor measures accurately outside those ranges.

Gas conversion happens on the ESP32 before JSON is sent to the server. The
fields are `mq4_ppm` and `mq137_ppm`. Database storage and the predictive model do
not independently calibrate these readings or correct an incorrect RL/R0.

## Limits and references

Actual RL, supply voltage, divider wiring, sensor stabilization, calibration,
temperature, humidity, and interfering gases affect the result. Until hardware
and reference measurements are verified, describe the output as **estimated
ppm**, not validated gas concentration.

- [Winsen MQ-4 specifications](https://www.winsen-sensor.com/sensors/combustible-sensor/mq4.html)
- [Winsen MQ137 manual](https://www.winsen-sensor.com/d/files/PDF/Semiconductor%20Gas%20Sensor/MQ137%20%28Ver1.4%29%20-%20Manual.pdf)

The manufacturer's reference definitions must be checked against the particular
curve used in the sketch. The settings table above documents local code, not a
manufacturer-verified calibration.
