"""Read the project's threshold table without executing Markdown expressions."""

import ast
import hashlib
import math
import operator
from dataclasses import dataclass
from pathlib import Path


POLICY_PATH = Path(__file__).parent / "reasoning ai" / "thresholds.md"
CHANNELS = {
    "Ammonia (NH3, MQ-137)": ("mq137_ppm", "ppm"),
    "Methane (CH4, MQ-4)": ("mq4_ppm", "ppm"),
    "Temperature (SHT31)": ("temperature_c", "degrees C"),
    "Relative humidity (SHT31)": ("humidity_perc", "% RH"),
    "Carbon dioxide (CO2, MH-Z19E)": ("co2_ppm", "ppm"),
    "PM1.0": ("pm1_ugm3", "micrograms/m3"),
    "PM2.5": ("pm25_ugm3", "micrograms/m3"),
    "PM10": ("pm10_ugm3", "micrograms/m3"),
}
COMPARISONS = {ast.Lt: operator.lt, ast.LtE: operator.le,
               ast.Gt: operator.gt, ast.GtE: operator.ge}


def _operand(node):
    if isinstance(node, ast.Name) and node.id == "value":
        return None
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        number = float(node.value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        number = _operand(node.operand)
        if number is None:
            raise ValueError("Only numeric constants may be negated.")
        number = -number
    else:
        raise ValueError("Thresholds must compare value with numeric constants.")
    if not math.isfinite(number):
        raise ValueError("Nonfinite threshold.")
    return number


def _parse_band(expression):
    alternatives = []
    for clause in expression.split(" OR "):
        try:
            comparison = ast.parse(clause.strip(), mode="eval").body
        except (SyntaxError, RecursionError) as exc:
            raise ValueError("Invalid threshold expression.") from exc
        if not isinstance(comparison, ast.Compare) or not 1 <= len(comparison.ops) <= 2:
            raise ValueError("Thresholds require one comparison or a bounded range.")
        operands = [_operand(node) for node in [comparison.left, *comparison.comparators]]
        if operands.count(None) != 1 or any(type(op) not in COMPARISONS for op in comparison.ops):
            raise ValueError("Unsupported threshold comparison.")
        alternatives.append((operands, [COMPARISONS[type(op)] for op in comparison.ops]))
    return alternatives


def _matches(alternatives, value):
    for operands, comparisons in alternatives:
        numbers = [value if number is None else number for number in operands]
        if all(compare(left, right) for compare, left, right in zip(comparisons, numbers, numbers[1:])):
            return True
    return False


@dataclass(frozen=True)
class Policy:
    text: str
    sha256: str
    bands: dict

    def classify(self, values):
        result = {}
        for channel, value in values.items():
            if channel not in self.bands or type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Unsupported or invalid prediction for classification.")
            for category in ("critical", "warning", "normal"):
                if _matches(self.bands[channel][category], value):
                    result[channel] = category
                    break
            else:
                raise ValueError("Prediction falls outside the threshold table.")
        return result


def load_policy(path=POLICY_PATH):
    content = Path(path).read_bytes()
    if len(content) > 65536:
        raise ValueError("Threshold policy is too large.")
    text = content.decode("utf-8")
    bands = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] == "Measurement" or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if len(cells) != 5 or cells[0] not in CHANNELS:
            raise ValueError("Unrecognized threshold table row.")
        channel, expected_unit = CHANNELS[cells[0]]
        if cells[1] != expected_unit or channel in bands:
            raise ValueError("Threshold units or channel identity do not match.")
        bands[channel] = dict(zip(("normal", "warning", "critical"), map(_parse_band, cells[2:])))
    if set(bands) != {channel for channel, _ in CHANNELS.values()}:
        raise ValueError("Threshold table is incomplete.")
    return Policy(text, hashlib.sha256(content).hexdigest(), bands)
