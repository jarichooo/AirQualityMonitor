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

    def test_cleanup_targets_only_known_simulators_and_leaves_commit_to_seeding(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 8
        cursor.fetchone.side_effect = [("air_quality_prediction_requests",), ("public", "renamed_id_sequence", 32322)]
        result = simulation.clear_history(connection)
        calls = cursor.execute.call_args_list
        self.assertEqual(calls[1].args[0], "LOCK TABLE public.air_quality_forecasts IN ACCESS EXCLUSIVE MODE")
        self.assertEqual(calls[2].args[0], "LOCK TABLE public.air_quality_logs IN ACCESS EXCLUSIVE MODE")
        self.assertIn("to_regclass", calls[3].args[0])
        for execution in calls[4:7]:
            self.assertIn("WHERE device_id = ANY(%s)", execution.args[0])
            self.assertEqual(execution.args[1], ([
                "sim-farm-good", "sim-farm-mid", "sim-farm-bad", "sim-farm-series", "python-simulator",
            ],))
        self.assertIn("MAX(id)", calls[7].args[0])
        self.assertEqual(result, {"readings_deleted": 8, "forecasts_deleted": 8})
        connection.commit.assert_not_called()

    def test_cleanup_without_readings_does_not_reset_the_sequence(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 0
        cursor.fetchone.return_value = (None,)
        self.assertEqual(simulation.clear_history(connection)["readings_deleted"], 0)
        self.assertEqual(cursor.execute.call_count, 6)
        cursor.fetchone.assert_called_once_with()

    def test_cleanup_fails_if_the_readings_id_sequence_is_missing(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        cursor.fetchone.side_effect = [(None,), None]
        with self.assertRaisesRegex(RuntimeError, "sequence is missing"):
            simulation.clear_history(connection)
        connection.commit.assert_not_called()

    def test_full_reset_deletes_all_readings_forecasts_and_requests_but_not_accounts(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 8
        cursor.fetchone.side_effect = [("air_quality_prediction_requests",), ("public", "air_quality_logs_id_seq", 1)]
        result = simulation.clear_history(connection, all_readings=True)
        deletes = [call for call in cursor.execute.call_args_list if str(call.args[0]).startswith("DELETE")]
        self.assertEqual([call.args for call in deletes], [
            ("DELETE FROM public.air_quality_prediction_requests", None),
            ("DELETE FROM public.air_quality_forecasts", None),
            ("DELETE FROM public.air_quality_logs", None),
        ])
        self.assertIn("Literal(1)", repr(cursor.execute.call_args.args[0]))
        self.assertEqual(result, {"readings_deleted": 8, "forecasts_deleted": 8})
        connection.commit.assert_not_called()

    def test_full_reset_restarts_ids_even_if_the_readings_table_is_already_empty(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 0
        cursor.fetchone.side_effect = [(None,), ("public", "air_quality_logs_id_seq", 1)]
        simulation.clear_history(connection, all_readings=True)
        self.assertIn("Literal(1)", repr(cursor.execute.call_args.args[0]))

    def test_each_simulation_seeds_and_appends_only_its_condition_without_inference(self):
        for profile, category in {"good": "normal", "mid": "warning", "bad": "critical"}.items():
            with self.subTest(profile=profile):
                connection = MagicMock()
                cursor = connection.cursor.return_value.__enter__.return_value
                cursor.fetchone.return_value = (True,)
                current = [self.now]
                sleeps, batches, reports = [], [], []

                def sleeper(seconds):
                    sleeps.append(seconds)
                    current[0] += timedelta(seconds=seconds)

                def insert_rows(_connection, rows):
                    batches.append(rows)
                    return len(rows)

                with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=True), \
                        patch.object(simulation, "clear_history", return_value={}) as clear, \
                        patch.object(simulation, "_insert_rows", side_effect=insert_rows), \
                        patch("ai_model.predictor.predict") as predict, \
                        patch("ai_model.forecast_worker.call_qwen") as qwen:
                    result = simulation.run_simulation(
                        profile, lambda: connection, clock=lambda: current[0],
                        sleeper=sleeper, report=reports.append,
                    )

                clear.assert_called_once_with(connection, all_readings=True)
                predict.assert_not_called()
                qwen.assert_not_called()
                lock_calls = [call for call in cursor.execute.call_args_list if "advisory" in call.args[0]]
                self.assertEqual(lock_calls[0].args[1], (simulation.MANUAL_SEEDER_LOCK,))
                self.assertEqual(lock_calls[-1].args[1], (739_783_247_071_623_201,))
                self.assertEqual([len(rows) for rows in batches], [120] + [1] * 5)
                rows = [row for batch in batches for row in batch]
                self.assertEqual({row["device_id"] for row in rows}, {f"sim-farm-{profile}"})
                policy = load_policy()
                for row in rows:
                    self.assertEqual(set(policy.classify({key: row[key] for key in FEATURE_COLUMNS}).values()), {category})
                self.assertTrue(all(
                    later["recorded_at"] - earlier["recorded_at"] == timedelta(minutes=1)
                    for earlier, later in zip(rows, rows[1:])
                ))
                self.assertEqual(reports[0]["seeded"], 120)
                self.assertEqual(sleeps, [22.999877] + [60.0] * 4)
                self.assertEqual(result, {"device_id": f"sim-farm-{profile}", "total_inserted": 125})

    def test_invalid_condition_is_rejected_before_connecting_or_clearing(self):
        connect = MagicMock()
        with self.assertRaisesRegex(ValueError, "Unknown simulation profile"):
            simulation.run_simulation("unknown", connect)
        connect.assert_not_called()

    def test_final_simulation_rejects_a_second_seeder_before_cleanup(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (False,)
        with patch("ai_model.forecast_worker.acquire_worker_lock") as worker_lock, \
                patch.object(simulation, "clear_history") as clear:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                simulation.run_simulation("good", lambda: connection, clock=lambda: self.now)
        worker_lock.assert_not_called()
        clear.assert_not_called()

    def test_final_simulation_does_not_clear_data_when_prediction_lock_is_busy(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=False), \
                patch.object(simulation, "clear_history") as clear:
            with self.assertRaisesRegex(RuntimeError, "prediction is running"):
                simulation.run_simulation("good", lambda: connection, clock=lambda: self.now)
        clear.assert_not_called()

    def test_final_simulation_rolls_back_cleanup_when_initial_seed_fails(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=True), \
                patch.object(simulation, "clear_history"), \
                patch.object(simulation, "_insert_rows", side_effect=RuntimeError("insert failed")):
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                simulation.run_simulation("good", lambda: connection, clock=lambda: self.now)
        connection.rollback.assert_called_once_with()

    def test_final_simulation_rolls_back_a_failed_live_insert_after_committing_the_seed(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        current = [self.now]

        def sleeper(seconds):
            current[0] += timedelta(seconds=seconds)

        with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=True), \
                patch.object(simulation, "clear_history", return_value={}), \
                patch.object(simulation, "_insert_rows", side_effect=[LOOKBACK_SAMPLES, RuntimeError("live failed")]):
            with self.assertRaisesRegex(RuntimeError, "live failed"):
                simulation.run_simulation(
                    "good", lambda: connection, clock=lambda: current[0], sleeper=sleeper, report=lambda _: None,
                )
        self.assertEqual(connection.commit.call_count, 2)
        connection.rollback.assert_called_once_with()

    def test_final_simulation_stops_when_a_minute_was_missed(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        current = [self.now]

        def sleeper(seconds):
            current[0] += timedelta(seconds=seconds + 60)

        with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=True), \
                patch.object(simulation, "clear_history", return_value={}), \
                patch.object(simulation, "_insert_rows", side_effect=lambda _conn, rows: len(rows)):
            with self.assertRaisesRegex(RuntimeError, "stopping instead of backfilling"):
                simulation.run_simulation(
                    "good", lambda: connection, clock=lambda: current[0], sleeper=sleeper, report=lambda _: None,
                )


if __name__ == "__main__":
    unittest.main()
