"""Small host-side check for the gas conversion used by airqualitymonitor.ino."""

import math
import re
from pathlib import Path


ROOT_SKETCH = Path(__file__).with_name("airqualitymonitor.ino")
FLASH_SKETCH = Path(__file__).with_name("airqualitymonitor") / "airqualitymonitor.ino"


def constant(source: str, name: str) -> float:
    match = re.search(rf"constexpr float {name} = (-?[0-9.]+)f;", source)
    assert match, f"missing {name}"
    return float(match.group(1))


def gas_ppm(adc_millivolts: float, rl: float, r0: float, a: float, b: float,
            vcc: float, divider: float, adc_max: float) -> float | None:
    adc_voltage = adc_millivolts / 1000.0
    if (not math.isfinite(adc_voltage) or adc_voltage <= 0 or adc_voltage >= adc_max
            or not all(map(math.isfinite, (rl, r0, a, b)))
            or rl <= 0 or r0 <= 0 or a <= 0):
        return None
    sensor_voltage = adc_voltage * divider
    if (not math.isfinite(sensor_voltage) or sensor_voltage <= 0.01
            or sensor_voltage >= vcc - 0.05):
        return None
    rs = rl * (vcc - sensor_voltage) / sensor_voltage
    ratio = rs / r0
    if not math.isfinite(ratio) or ratio <= 0:
        return None
    value = a * ratio ** b
    return value if math.isfinite(value) and value > 0 else None


source = ROOT_SKETCH.read_text(encoding="utf-8")
assert source == FLASH_SKETCH.read_text(encoding="utf-8"), "sketch copies differ"

vcc = constant(source, "V_CC")
divider = constant(source, "DIVIDER_FACTOR")
adc_max = constant(source, "ADC_MAX_MEASURABLE_V")

for prefix in ("MQ4", "MQ137"):
    rl = constant(source, f"RL_{prefix}")
    r0 = constant(source, f"R0_{prefix}")
    a = constant(source, f"{prefix}_A")
    b = constant(source, f"{prefix}_B")
    sensor_voltage_at_ratio_one = vcc * rl / (rl + r0)
    result = gas_ppm(sensor_voltage_at_ratio_one / divider * 1000, rl, r0, a, b,
                     vcc, divider, adc_max)
    assert result is not None and math.isclose(result, a, rel_tol=1e-6), prefix

assert gas_ppm(0, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(adc_max * 1000, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(0.01 / divider * 1000, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm((vcc - 0.05) / divider * 1000, 10, 10, 100, -2,
               vcc, divider, adc_max) is None
assert gas_ppm(math.nan, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(1000, 0, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(1000, 10, 0, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(1000, math.inf, 10, 100, -2, vcc, divider, adc_max) is None
assert 'doc["mq4_ppm"]' in source and 'doc["mq137_ppm"]' in source
assert "mq135_raw" not in source and "MQ135_PIN" not in source
print("Gas conversion math, invalid-input guards, keys, and sketch sync passed")
