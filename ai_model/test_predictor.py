import csv
import importlib.util
import tempfile
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ai_model import predictor


def history():
    start = datetime(2026, 10, 29, 1, 2, tzinfo=timezone.utc)
    return [
        {
            "id": index + 1,
            "recorded_at": start + timedelta(minutes=index),
            "device_id": "node-1",
            "temperature_c": 28.0,
            "humidity_perc": 70.0,
            "co2_ppm": 1000.0,
            "pm1_ugm3": 10.0,
            "pm25_ugm3": 20.0,
            "pm10_ugm3": 30.0,
            "mq4_ppm": 2.0,
            "mq137_ppm": 3.0,
        }
        for index in range(120)
    ]


class PredictorContractTests(unittest.TestCase):
    def tearDown(self):
        predictor._load_forecaster.cache_clear()

    def test_rejects_non_aligned_timestamp_before_loading_model(self):
        rows = history()
        rows[0]["recorded_at"] += timedelta(seconds=1)
        with self.assertRaisesRegex(predictor.ModelUnavailable, "minute boundaries"):
            predictor.predict(rows)

    def test_rejects_gap_before_loading_model(self):
        rows = history()
        rows[60]["recorded_at"] += timedelta(minutes=1)
        with self.assertRaisesRegex(predictor.ModelUnavailable, "consecutive"):
            predictor.predict(rows)

    def test_rejects_invalid_particulate_order_before_loading_model(self):
        rows = history()
        rows[0]["pm1_ugm3"] = 40.0
        with self.assertRaisesRegex(predictor.ModelUnavailable, "PM1"):
            predictor.predict(rows)

    def test_missing_artifact_is_unavailable(self):
        with patch.object(predictor, "MODEL_PATH", Path("missing-model.joblib")):
            with self.assertRaisesRegex(predictor.ModelUnavailable, "missing"):
                predictor.predict(history())

    def test_changed_artifact_is_rejected_before_deserialization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "model.joblib")
            path.write_bytes(b"not the supplied model")
            with patch.object(predictor, "MODEL_PATH", path):
                with self.assertRaisesRegex(predictor.ModelUnavailable, "checksum"):
                    predictor.predict(history())


@unittest.skipUnless(
    all(importlib.util.find_spec(name) for name in ("numpy", "pandas", "joblib")),
    "model runtime dependencies are not installed",
)
class PredictorArtifactTests(unittest.TestCase):
    def tearDown(self):
        predictor._load_forecaster.cache_clear()

    def test_all_demo_profiles_produce_finite_model_outputs_without_overflow(self):
        import math
        from ai_model.simulation import generate_readings

        now = datetime(2026, 10, 29, 15, 42, tzinfo=timezone.utc)
        for profile in ("good", "mid", "bad"):
            rows = generate_readings(profile, now)
            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                values = predictor.predict(rows)
            self.assertEqual(set(values), set(predictor.TARGET_COLUMNS))
            self.assertTrue(all(math.isfinite(value) for value in values.values()))

    def test_supplied_example_matches_endpoint(self):
        root = Path(predictor.__file__).parent
        mapping = dict(zip(predictor.MODEL_COLUMNS, predictor.FEATURE_COLUMNS))
        rows = []
        with (root / "example_input.csv").open(encoding="utf-8", newline="") as source:
            for index, row in enumerate(csv.DictReader(source)):
                sample = {
                    "id": index + 1,
                    "recorded_at": datetime.fromisoformat(row["Timestamp (UTC)"]),
                    "device_id": "example-node",
                }
                sample.update({database: float(row[model]) for model, database in mapping.items()})
                rows.append(sample)

        with (root / "example_predictions.csv").open(encoding="utf-8", newline="") as expected_file:
            expected_row = list(csv.DictReader(expected_file))[-1]
        expected = {database: float(expected_row[model]) for model, database in mapping.items()}
        actual = predictor.predict(rows)

        self.assertEqual(set(actual), set(predictor.TARGET_COLUMNS))
        for column in predictor.TARGET_COLUMNS:
            self.assertIs(type(actual[column]), float)
            self.assertAlmostEqual(actual[column], expected[column], delta=0.001)


if __name__ == "__main__":
    unittest.main()
