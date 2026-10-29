import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from ai_model import simulation
from ai_model.policy import load_policy
from ai_model.predictor import FEATURE_COLUMNS, LOOKBACK_SAMPLES


class SimulationTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 29, 15, 42, 37, 123, tzinfo=timezone.utc)

    def test_profiles_match_the_threshold_categories(self):
        policy = load_policy()
        expected = {"good": "normal", "mid": "warning", "bad": "critical"}
        for profile, category in expected.items():
            for reading in simulation.generate_readings(profile, self.now):
                values = {column: reading[column] for column in FEATURE_COLUMNS}
                self.assertEqual(set(policy.classify(values).values()), {category})
                self.assertLessEqual(reading["pm1_ugm3"], reading["pm25_ugm3"])
                self.assertLessEqual(reading["pm25_ugm3"], reading["pm10_ugm3"])

    def test_history_has_exact_minute_cadence_and_ends_at_current_minute(self):
        readings = simulation.generate_readings("good", self.now)
        self.assertEqual(len(readings), LOOKBACK_SAMPLES)
        self.assertEqual(readings[-1]["recorded_at"], self.now.replace(second=0, microsecond=0))
        self.assertTrue(all(
            later["recorded_at"] - earlier["recorded_at"] == timedelta(minutes=1)
            for earlier, later in zip(readings, readings[1:])
        ))

    def test_write_history_inserts_only_missing_device_minutes_and_commits(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = LOOKBACK_SAMPLES
        with patch.object(simulation, "execute_values") as execute:
            result = simulation.write_history(connection, "mid", self.now)
        query = execute.call_args.args[1]
        self.assertIn("WHERE NOT EXISTS", query)
        self.assertIn("existing.device_id = candidate.device_id", query)
        self.assertIn("existing.recorded_at = candidate.recorded_at", query)
        self.assertEqual(result["device_id"], "sim-farm-mid")
        self.assertEqual(result["inserted"], LOOKBACK_SAMPLES)
        connection.commit.assert_called_once_with()

    def test_invalid_profile_and_naive_time_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown simulation profile"):
            simulation.generate_readings("unknown", self.now)
        with self.assertRaisesRegex(ValueError, "timezone"):
            simulation.generate_readings("good", self.now.replace(tzinfo=None))

    def test_failed_insert_rolls_back(self):
        connection = MagicMock()
        with patch.object(simulation, "execute_values", side_effect=RuntimeError("database failed")):
            with self.assertRaisesRegex(RuntimeError, "database failed"):
                simulation.write_history(connection, "bad", self.now)
        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
