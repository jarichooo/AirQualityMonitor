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


def gas_ratio(adc_millivolts: float, rl: float, r0: float, vcc: float,
              divider: float, adc_max: float) -> float | None:
    adc_voltage = adc_millivolts / 1000.0
    if (not math.isfinite(adc_voltage) or adc_voltage <= 0 or adc_voltage >= adc_max
            or not all(map(math.isfinite, (rl, r0))) or rl <= 0 or r0 <= 0):
        return None
    sensor_voltage = adc_voltage * divider
    if (not math.isfinite(sensor_voltage) or sensor_voltage <= 0.01
            or sensor_voltage >= vcc - 0.05):
        return None
    rs = rl * (vcc - sensor_voltage) / sensor_voltage
    ratio = rs / r0
    return ratio if math.isfinite(ratio) and ratio > 0 else None


def gas_ppm(adc_millivolts: float, rl: float, r0: float, a: float, b: float,
            vcc: float, divider: float, adc_max: float) -> float | None:
    ratio = gas_ratio(adc_millivolts, rl, r0, vcc, divider, adc_max)
    if (ratio is None or not all(map(math.isfinite, (a, b))) or a <= 0):
        return None
    value = a * ratio ** b
    return value if math.isfinite(value) and value > 0 else None


def ammonia_ppm(adc_millivolts: float, rl: float, r0: float, m: float, b: float,
                vcc: float, divider: float, adc_max: float) -> float | None:
    ratio = gas_ratio(adc_millivolts, rl, r0, vcc, divider, adc_max)
    if ratio is None or not all(map(math.isfinite, (m, b))) or m == 0:
        return None
    try:
        value = 10 ** ((math.log10(ratio) - b) / m)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def power_calibration_r0(rs: float, reference: float, a: float, b: float) -> float | None:
    if (not all(map(math.isfinite, (rs, reference, a, b)))
            or rs <= 0 or reference <= 0 or a <= 0 or b == 0):
        return None
    r0 = rs / (reference / a) ** (1 / b)
    return r0 if math.isfinite(r0) and r0 > 0 else None


def ammonia_calibration_r0(rs: float, reference: float, m: float, b: float) -> float | None:
    if (not all(map(math.isfinite, (rs, reference, m, b)))
            or rs <= 0 or reference <= 0 or m == 0):
        return None
    try:
        ratio = 10 ** (m * math.log10(reference) + b)
        r0 = rs / ratio
    except (OverflowError, ValueError):
        return None
    return r0 if math.isfinite(r0) and r0 > 0 else None


def valid_reference(reference: float, minimum: float, maximum: float) -> bool:
    return math.isfinite(reference) and minimum <= reference <= maximum


source = ROOT_SKETCH.read_text(encoding="utf-8")
if FLASH_SKETCH.is_file():  # The local Arduino flash folder is excluded from Git.
    assert source == FLASH_SKETCH.read_text(encoding="utf-8"), "sketch copies differ"

vcc = constant(source, "V_CC")
divider = constant(source, "DIVIDER_FACTOR")
adc_max = constant(source, "ADC_MAX_MEASURABLE_V")

expected_constants = {
    "MQ4": (1.0, 0.945, 1000.0, -2.95),
    "MQ137": (47.0, 20.0, -0.263, 0.42),
}
mq4_rl, mq4_r0, mq4_a, mq4_b = expected_constants["MQ4"]
mq137_rl, mq137_r0, mq137_m, mq137_b = expected_constants["MQ137"]
assert (constant(source, "RL_MQ4"), constant(source, "R0_MQ4"),
        constant(source, "MQ4_A"), constant(source, "MQ4_B")) == expected_constants["MQ4"]
assert (constant(source, "RL_MQ137"), constant(source, "R0_MQ137"),
        constant(source, "MQ137_M"), constant(source, "MQ137_B")) == expected_constants["MQ137"]

# Generate valid ADC inputs from known concentrations, then recover ppm and R0.
for reference in (300.0, 1000.0, 10000.0):
    ratio = (reference / mq4_a) ** (1 / mq4_b)
    rs = mq4_r0 * ratio
    sensor_voltage = vcc * mq4_rl / (mq4_rl + rs)
    adc_millivolts = sensor_voltage / divider * 1000
    assert 0 < adc_millivolts / 1000 < adc_max
    measured = gas_ppm(adc_millivolts, mq4_rl, mq4_r0, mq4_a, mq4_b,
                       vcc, divider, adc_max)
    recovered = power_calibration_r0(rs, reference, mq4_a, mq4_b)
    assert measured is not None and math.isclose(measured, reference, rel_tol=1e-6)
    assert recovered is not None and math.isclose(recovered, mq4_r0, rel_tol=1e-6)

for reference in (5.0, 50.0, 500.0):
    ratio = 10 ** (mq137_m * math.log10(reference) + mq137_b)
    rs = mq137_r0 * ratio
    sensor_voltage = vcc * mq137_rl / (mq137_rl + rs)
    adc_millivolts = sensor_voltage / divider * 1000
    assert 0 < adc_millivolts / 1000 < adc_max
    measured = ammonia_ppm(adc_millivolts, mq137_rl, mq137_r0, mq137_m, mq137_b,
                           vcc, divider, adc_max)
    recovered = ammonia_calibration_r0(rs, reference, mq137_m, mq137_b)
    assert measured is not None and math.isclose(measured, reference, rel_tol=1e-6)
    assert recovered is not None and math.isclose(recovered, mq137_r0, rel_tol=1e-6)

assert valid_reference(300, 300, 10000) and valid_reference(10000, 300, 10000)
assert not valid_reference(299.9, 300, 10000) and not valid_reference(10000.1, 300, 10000)
assert valid_reference(5, 5, 500) and valid_reference(500, 5, 500)
assert not valid_reference(4.9, 5, 500) and not valid_reference(500.1, 5, 500)
assert not valid_reference(math.nan, 5, 500)
assert power_calibration_r0(1, 0, 100, -2) is None
assert power_calibration_r0(1, math.nan, 100, -2) is None
assert ammonia_calibration_r0(1, 0, mq137_m, mq137_b) is None
assert ammonia_calibration_r0(1, math.nan, mq137_m, mq137_b) is None
assert ammonia_calibration_r0(1, 5, 0, mq137_b) is None

assert gas_ppm(0, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(adc_max * 1000, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(0.01 / divider * 1000, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm((vcc - 0.05) / divider * 1000, 10, 10, 100, -2,
               vcc, divider, adc_max) is None
assert gas_ppm(math.nan, 10, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(1000, 0, 10, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(1000, 10, 0, 100, -2, vcc, divider, adc_max) is None
assert gas_ppm(1000, math.inf, 10, 100, -2, vcc, divider, adc_max) is None
assert ammonia_ppm(0, mq137_rl, mq137_r0, mq137_m, mq137_b,
                   vcc, divider, adc_max) is None
assert ammonia_ppm(adc_max * 1000, mq137_rl, mq137_r0, mq137_m, mq137_b,
                   vcc, divider, adc_max) is None
assert ammonia_ppm(1000, mq137_rl, mq137_r0, 0, mq137_b,
                   vcc, divider, adc_max) is None
assert ammonia_ppm(math.nan, mq137_rl, mq137_r0, mq137_m, mq137_b,
                   vcc, divider, adc_max) is None

# Collector calibration must be deliberate, configuration-bound, and non-destructive on failure.
assert '#include <Preferences.h>' in source
assert 'gasPreferences.begin("mq-collector-v1", false)' in source
assert 'float (&record)[7]' in source
for field in range(7):
    assert f'record[{field}] =' in source
assert 'gasPreferences.getBytesLength(key) != sizeof(record)' in source
assert 'gasPreferences.getBytes(key, record, sizeof(record)) != sizeof(record)' in source
assert 'for (size_t i = 1; i < 7; ++i)' in source
assert 'if (record[i] != expected[i]) return;' in source
assert 'record[2] = ammonia ? MQ137_M : MQ4_A;' in source
assert 'record[3] = ammonia ? MQ137_B : MQ4_B;' in source
assert 'if (!readingsPaused || !offlineCache.empty())' in source
assert 'strcmp(confirmation, "stable") == 0' in source
assert 'referencePpm < minimum || referencePpm > maximum' in source
assert 'if (!gasResistance(millivolts, rlKOhm, rsKOhm)) return false;' in source
save_position = source.index('gasPreferences.putBytes')
apply_position = source.index('if (ammonia) { r0Ammonia = r0;', save_position)
assert save_position < apply_position, "R0 must be applied only after its full record is saved"

# The unchanged MQ4 record remains usable, while the old MQ137 curve/circuit record is rejected.
def calibration_record(ammonia: bool, r0: float) -> list[float]:
    return [r0,
            mq137_rl if ammonia else mq4_rl,
            mq137_m if ammonia else mq4_a,
            mq137_b if ammonia else mq4_b,
            vcc, divider, adc_max]


def record_compatible(record: list[float], ammonia: bool) -> bool:
    if len(record) != 7 or not math.isfinite(record[0]) or record[0] <= 0:
        return False
    expected = calibration_record(ammonia, record[0])
    return record[1:] == expected[1:]


assert record_compatible([0.945, 1.0, 1000.0, -2.95, vcc, divider, adc_max], False)
assert not record_compatible([0.310, 1.0, 102.2, -2.473, vcc, divider, adc_max], True)
assert not record_compatible([], True)
assert not record_compatible([math.nan, mq137_rl, mq137_m, mq137_b,
                              vcc, divider, adc_max], True)

# Calibration commands remain available while paused, without publishing reference-gas samples.
assert source.index('pollGasCommands();') < source.index('if (readingsPaused) return;')
assert 'CLEAN_AIR_RATIO' not in source and 'calibrateMQ' not in source
assert not re.search(r'if\s*\(ppm\s*<[^)]*\)\s*ppm\s*=', source)
assert 'value retained without clamping' in source
assert 'doc["mq4_ppm"]' in source and 'doc["mq137_ppm"]' in source
assert "mq135_raw" not in source and "MQ135_PIN" not in source
assert 'ppm = powf(10.0f, (log10f(ratio) - MQ137_B) / MQ137_M);' in source
assert 'powf(10.0f, MQ137_M * log10f(referencePpm) + MQ137_B)' in source
assert '(ammonia ? ammoniaPpm(millivolts, ppm)' in source
assert source.index('Serial.println("[DIAGNOSTIC] Current samples below are not uploaded.");') < source.index('printGasSample(false);')

print("Gas math, NH3 log fit, calibration record binding, built-in diagnostics, and sketch sync passed")
