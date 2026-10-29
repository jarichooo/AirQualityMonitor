import tempfile
import unittest
from pathlib import Path

from ai_model.policy import POLICY_PATH, load_policy


class PolicyTests(unittest.TestCase):
    def test_project_policy_boundaries_and_channel_identity(self):
        policy = load_policy()
        cases = {
            "mq137_ppm": [(10, "normal"), (10.5, "warning"), (20, "warning"), (20.1, "critical")],
            "mq4_ppm": [(1000, "normal"), (1000.1, "warning"), (5000, "warning"), (5001, "critical")],
            "temperature_c": [(17.9, "critical"), (18, "warning"), (21, "normal"),
                              (32, "normal"), (35, "warning"), (35.1, "critical")],
            "humidity_perc": [(39.9, "critical"), (40, "warning"), (50, "normal"),
                              (70, "normal"), (80, "warning"), (80.1, "critical")],
            "co2_ppm": [(2500, "normal"), (2500.5, "warning"), (3000, "warning"), (3001, "critical")],
            "pm1_ugm3": [(10, "normal"), (10.1, "warning"), (35, "warning"), (36, "critical")],
            "pm25_ugm3": [(35, "normal"), (35.1, "warning"), (150, "warning"), (151, "critical")],
            "pm10_ugm3": [(150, "normal"), (150.1, "warning"), (1000, "warning"), (1001, "critical")],
        }
        for channel, boundaries in cases.items():
            for value, expected in boundaries:
                with self.subTest(channel=channel, value=value):
                    self.assertEqual(policy.classify({channel: value}), {channel: expected})

    def test_invalid_or_executable_policy_fails_closed(self):
        source = POLICY_PATH.read_text(encoding="utf-8")
        for replacement in ("__import__('os').system('echo invalid')", "value == 10", "value < float('inf')"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "thresholds.md"
                path.write_text(source.replace("0 <= value <= 10", replacement, 1), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_policy(path)
        policy = load_policy()
        for values in ({"unknown": 1}, {"temperature_c": float("nan")}, {"temperature_c": True}):
            with self.assertRaises(ValueError):
                policy.classify(values)


if __name__ == "__main__":
    unittest.main()
