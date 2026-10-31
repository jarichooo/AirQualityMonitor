import csv
import copy
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


def raw_history(total=122):
    rows = history()
    start = rows[0]["recorded_at"]
    result = []
    for index in range(total):
        row = dict(rows[0])
        row.update(
            id=index + 1,
            recorded_at=start + timedelta(minutes=index, seconds=36),
            temperature_c=20.0 + index,
            humidity_perc=50.0 + index / 100,
            co2_ppm=800.0 + index,
            pm1_ugm3=10.0 + index / 100,
            pm25_ugm3=20.0 + index / 100,
            pm10_ugm3=30.0 + index / 100,
            mq4_ppm=2.0 + index / 100,
            mq137_ppm=3.0 + index / 100,
        )
        result.append(row)
    return result


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

    def test_prepare_history_aligns_jittered_readings_without_changing_values(self):
        rows = raw_history(120)
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 0)
        self.assertEqual(len(prepared), 120)
        self.assertTrue(all(row["recorded_at"].second == 0 for row in prepared))
        self.assertEqual(prepared[-1]["temperature_c"], rows[-1]["temperature_c"])

    def test_prepare_history_interpolates_two_missing_minutes(self):
        rows = [row for index, row in enumerate(raw_history()) if index not in {60, 61}]
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 2)
        self.assertEqual(len(prepared), 120)
        self.assertIsNone(prepared[58]["id"])
        self.assertIsNone(prepared[59]["id"])
        for column in predictor.FEATURE_COLUMNS:
            self.assertAlmostEqual(prepared[58][column], raw_history()[60][column])
            self.assertAlmostEqual(prepared[59][column], raw_history()[61][column])

    def test_prepare_history_rejects_three_consecutive_missing_minutes(self):
        rows = [row for index, row in enumerate(raw_history(123)) if index not in {60, 61, 62}]
        with self.assertRaisesRegex(predictor.ModelUnavailable, "longer than 2"):
            predictor.prepare_history(rows)

    def test_prepare_history_rejects_more_than_six_total_missing_minutes(self):
        missing = {20, 35, 50, 65, 80, 95, 110}
        rows = [row for index, row in enumerate(raw_history(127)) if index not in missing]
        with self.assertRaisesRegex(predictor.ModelUnavailable, "more than 6"):
            predictor.prepare_history(rows)

    def test_prepare_history_accepts_six_missing_minutes_in_short_runs(self):
        missing = {30, 31, 60, 61, 90, 91}
        rows = [row for index, row in enumerate(raw_history(126)) if index not in missing]
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 6)
        self.assertEqual(len(prepared), 120)

    def test_prepare_history_rejects_an_unmeasured_window_start(self):
        rows = raw_history(120)[1:]
        with self.assertRaisesRegex(predictor.ModelUnavailable, "start and end"):
            predictor.prepare_history(rows)

    def test_prepare_history_uses_the_highest_id_for_duplicate_timestamp(self):
        rows = raw_history(120)
        duplicate = dict(rows[50], id=999, temperature_c=44.0)
        rows.insert(51, duplicate)
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 0)
        self.assertEqual(prepared[50]["id"], 999)
        self.assertEqual(prepared[50]["temperature_c"], 44.0)

    def test_prepare_history_uses_the_latest_reading_within_a_minute(self):
        rows = raw_history(120)
        later = dict(rows[50], id=1, recorded_at=rows[50]["recorded_at"] + timedelta(seconds=15),
                     temperature_c=43.0)
        rows.insert(51, later)
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 0)
        self.assertEqual(prepared[50]["id"], 1)
        self.assertEqual(prepared[50]["temperature_c"], 43.0)

    def test_prepare_history_normalizes_offset_timestamps_to_utc(self):
        philippine_time = timezone(timedelta(hours=8))
        rows = raw_history(120)
        for row in rows:
            row["recorded_at"] = row["recorded_at"].astimezone(philippine_time)
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 0)
        self.assertEqual(prepared[0]["recorded_at"].tzinfo, timezone.utc)
        self.assertEqual(prepared[0]["recorded_at"].hour, 1)

    def test_prepare_history_rejects_mixed_devices_and_invalid_values(self):
        mixed = raw_history(120)
        mixed[30]["device_id"] = "node-2"
        with self.assertRaisesRegex(predictor.ModelUnavailable, "one identified device"):
            predictor.prepare_history(mixed)
        invalid = raw_history(120)
        invalid[30]["co2_ppm"] = float("nan")
        with self.assertRaisesRegex(predictor.ModelUnavailable, "finite"):
            predictor.prepare_history(invalid)

    def test_prepare_history_rejects_naive_and_out_of_order_timestamps(self):
        naive = raw_history(120)
        naive[30]["recorded_at"] = naive[30]["recorded_at"].replace(tzinfo=None)
        with self.assertRaisesRegex(predictor.ModelUnavailable, "timezone"):
            predictor.prepare_history(naive)
        unsorted = raw_history(120)
        unsorted[30], unsorted[31] = unsorted[31], unsorted[30]
        with self.assertRaisesRegex(predictor.ModelUnavailable, "chronological"):
            predictor.prepare_history(unsorted)

    def test_prepare_history_does_not_mutate_database_rows(self):
        rows = [row for index, row in enumerate(raw_history()) if index not in {60, 61}]
        original = copy.deepcopy(rows)
        predictor.prepare_history(rows)
        self.assertEqual(rows, original)

    def test_prepare_history_leaves_an_exact_model_window_unchanged(self):
        rows = history()
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 0)
        self.assertEqual(prepared, rows)


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

    def test_prepared_jittered_history_with_two_missing_minutes_produces_finite_outputs(self):
        import math

        rows = [row for index, row in enumerate(raw_history()) if index not in {60, 61}]
        for index, row in enumerate(rows):
            row.update(
                temperature_c=28.0 + index / 1000,
                humidity_perc=65.0 + index / 1000,
                co2_ppm=900.0 + index / 10,
                pm1_ugm3=10.0 + index / 100,
                pm25_ugm3=20.0 + index / 100,
                pm10_ugm3=30.0 + index / 100,
                mq4_ppm=2.0 + index / 100,
                mq137_ppm=3.0 + index / 100,
            )
        prepared, filled = predictor.prepare_history(rows)
        values = predictor.predict(prepared)
        self.assertEqual(filled, 2)
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
        prepared, filled = predictor.prepare_history(rows)
        self.assertEqual(filled, 0)
        actual = predictor.predict(prepared)

        self.assertEqual(set(actual), set(predictor.TARGET_COLUMNS))
        for column in predictor.TARGET_COLUMNS:
            self.assertIs(type(actual[column]), float)
            self.assertAlmostEqual(actual[column], expected[column], delta=0.001)


if __name__ == "__main__":
    unittest.main()
