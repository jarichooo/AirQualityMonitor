import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from ai_model import forecast_worker as worker
from ai_model import predictor


class ForecastWorkerTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 12, 2, tzinfo=timezone.utc)
        self.model = SimpleNamespace(
            MODEL_VERSION="test-v1",
            LOOKBACK_SAMPLES=3,
            SAMPLE_INTERVAL_SECONDS=60,
            CADENCE_TOLERANCE_SECONDS=0,
            HORIZON_MINUTES=120,
            FEATURE_COLUMNS=("temperature_c", "humidity_perc"),
            TARGET_COLUMNS=("temperature_c",),
            predict=Mock(return_value={"temperature_c": 34.0}),
        )
        self.rows = [
            {
                "id": index + 1,
                "recorded_at": self.now - timedelta(minutes=2 - index),
                "device_id": "esp32-1",
                "temperature_c": 30.0 + index,
                "humidity_perc": 60.0,
            }
            for index in range(3)
        ]
        self.policy = SimpleNamespace(
            sha256="policy-hash",
            text="trusted policy",
            classify=Mock(return_value={"temperature_c": "warning"}),
        )
        self.conn = Mock()

    def test_valid_forecast_targets_two_hours_after_input_and_commits_before_qwen(self):
        events = []
        with (
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
            patch.object(worker, "save_forecast", side_effect=lambda *_: events.append("saved")) as save,
            patch.object(worker, "update_explanation", side_effect=lambda *_: events.append("explained")),
        ):
            result = worker.process_device(
                self.conn, "esp32-1", self.model, self.now, (self.policy, None),
                lambda *_: events.append("qwen") or "Check ventilation.",
            )
        record = save.call_args.args[1]
        self.assertEqual(result, "created")
        self.assertEqual(record["target_at"], self.rows[-1]["recorded_at"] + timedelta(hours=2))
        self.assertEqual(record["predicted_values"], {"temperature_c": 34.0})
        self.assertEqual(events, ["saved", "qwen", "explained"])

    def test_unconfigured_model_and_missing_history_produce_no_forecast(self):
        with self.assertRaises(predictor.ModelUnavailable):
            worker.validate_model_contract(SimpleNamespace(MODEL_VERSION=None))
        with (
            patch.object(worker, "fetch_history", return_value=[]),
            patch.object(worker, "save_forecast") as save,
        ):
            with self.assertRaises(worker.ContractError):
                worker.process_device(self.conn, "esp32-1", self.model, self.now, (self.policy, None))
            save.assert_not_called()

    def test_invalid_history_and_model_output_are_rejected(self):
        duplicate = [dict(row) for row in self.rows]
        duplicate[1]["recorded_at"] = duplicate[0]["recorded_at"]
        with self.assertRaisesRegex(worker.ContractError, "unique and chronological"):
            worker.validate_history(duplicate, self.model, self.now)
        with self.assertRaisesRegex(worker.ContractError, "finite"):
            worker.validate_prediction({"temperature_c": float("nan")}, self.model)
        with self.assertRaisesRegex(worker.ContractError, "exactly match"):
            worker.validate_prediction({"humidity_perc": 50.0}, self.model)

    def test_qwen_timeout_keeps_committed_numeric_forecast(self):
        events = []
        with (
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
            patch.object(worker, "save_forecast", side_effect=lambda *_: events.append("numeric_committed")),
            patch.object(worker, "update_explanation", side_effect=lambda *args: events.append(args[3])) as update,
        ):
            result = worker.process_device(
                self.conn, "esp32-1", self.model, self.now, (self.policy, None),
                Mock(side_effect=worker.QwenUnavailable("timed out")),
            )
        self.assertEqual(result, "explanation_error")
        self.assertEqual(events, ["numeric_committed", "error"])
        self.assertIsNone(update.call_args.kwargs.get("explanation"))

    def test_malformed_ollama_url_keeps_numeric_forecast(self):
        with (
            patch.dict(os.environ, {"OLLAMA_URL": "::bad"}),
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
            patch.object(worker, "save_forecast") as save,
            patch.object(worker, "update_explanation") as update,
        ):
            result = worker.process_device(
                self.conn, "esp32-1", self.model, self.now, (self.policy, None), worker.call_qwen,
            )
        self.assertEqual(result, "explanation_error")
        self.assertEqual(save.call_args.args[1]["status"], "available")
        self.assertEqual(save.call_args.args[1]["predicted_values"], {"temperature_c": 34.0})
        self.assertEqual(update.call_args.args[3], "error")

    def test_malformed_policy_keeps_numeric_forecast_and_skips_qwen(self):
        qwen = Mock()
        with (
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
            patch.object(worker, "save_forecast") as save,
        ):
            result = worker.process_device(
                self.conn, "esp32-1", self.model, self.now,
                (None, "POLICY_INVALID: bad table"), qwen,
            )
        record = save.call_args.args[1]
        self.assertEqual(result, "policy_error")
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["severity"], {"temperature_c": "unavailable"})
        self.assertEqual(record["explanation_status"], "error")
        qwen.assert_not_called()

    def test_classification_failure_keeps_numeric_forecast(self):
        self.policy.classify.side_effect = ValueError("threshold gap")
        with (
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
            patch.object(worker, "save_forecast") as save,
        ):
            result = worker.process_device(
                self.conn, "esp32-1", self.model, self.now, (self.policy, None), Mock()
            )
        record = save.call_args.args[1]
        self.assertEqual(result, "policy_error")
        self.assertEqual(record["predicted_values"], {"temperature_c": 34.0})
        self.assertEqual(record["severity"], {"temperature_c": "unavailable"})
        self.assertEqual(record["explanation_status"], "error")

    def test_nonfinite_cadence_tolerance_is_rejected(self):
        self.model.CADENCE_TOLERANCE_SECONDS = float("nan")
        with self.assertRaisesRegex(worker.ContractError, "finite"):
            worker.validate_model_contract(self.model)

    def test_readings_arriving_after_cycle_start_use_their_device_processing_time(self):
        connection = MagicMock()
        with (
            patch.object(worker, "utc_now", side_effect=[self.now - timedelta(seconds=20), self.now, self.now]),
            patch.object(worker, "acquire_worker_lock", return_value=True),
            patch.object(worker, "invalidate_stale_forecasts"),
            patch.object(worker, "fetch_active_devices", return_value=["esp32-1"]),
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
            patch.object(worker, "load_policy_result", return_value=(self.policy, None)),
            patch.object(worker, "save_forecast") as save,
            patch.object(worker, "update_explanation"),
        ):
            result = worker.run_once(lambda: connection, self.model, qwen=Mock(return_value="Forecast warning."))
        self.assertEqual(result, {"esp32-1": "created"})
        self.assertEqual(save.call_args.args[1]["generated_at"], self.now)

    def test_unconfigured_worker_cannot_bypass_another_workers_lock(self):
        with (
            patch.object(worker, "acquire_worker_lock", return_value=False),
            patch.object(worker, "invalidate_forecast") as invalidate,
        ):
            result = worker.run_once(lambda: MagicMock(), predictor, self.now)
        self.assertIn("another forecast worker", result["_worker"])
        invalidate.assert_not_called()

    def test_simulation_seeds_under_the_worker_lock_before_forecasting(self):
        events = []
        seeded = dict(device_id="sim-farm-good", inserted=120, latest_at=self.now)
        with (
            patch.object(worker, "acquire_worker_lock", side_effect=lambda conn: events.append("lock") or True),
            patch("ai_model.simulation.write_history", side_effect=lambda *args: events.append("seed") or seeded),
            patch.object(worker, "load_policy_result", return_value=(self.policy, None)),
            patch.object(worker, "invalidate_stale_forecasts"),
            patch.object(worker, "fetch_active_devices") as fetch_devices,
            patch.object(worker, "process_device", side_effect=lambda *args: events.append("forecast") or "created"),
        ):
            result = worker.run_once(lambda: MagicMock(), self.model, self.now, simulation="good")
        self.assertEqual(events, ["lock", "seed", "forecast"])
        self.assertEqual(result["_simulation"]["inserted"], 120)
        self.assertEqual(result["sim-farm-good"], "created")
        fetch_devices.assert_not_called()

    def test_lock_contention_does_not_insert_simulation_history(self):
        with (
            patch.object(worker, "acquire_worker_lock", return_value=False),
            patch("ai_model.simulation.write_history") as seed,
            patch.object(worker, "invalidate_forecast") as invalidate,
        ):
            result = worker.run_once(
                lambda: MagicMock(), self.model, self.now,
                simulation="bad", previous_simulations=("good", "mid"),
            )
        seed.assert_not_called()
        invalidate.assert_not_called()
        self.assertTrue(result["_worker"].startswith("unavailable:"))

    def test_series_stage_invalidates_both_other_forecasts_under_lock(self):
        events = []
        seeded = dict(device_id="sim-farm-good", inserted=120, latest_at=self.now)
        with (
            patch.object(worker, "acquire_worker_lock", side_effect=lambda conn: events.append("lock") or True),
            patch("ai_model.simulation.write_history", side_effect=lambda *args: events.append("seed") or seeded),
            patch.object(worker, "invalidate_forecast", side_effect=lambda *args: events.append("previous")) as invalidate,
            patch.object(worker, "load_policy_result", return_value=(self.policy, None)),
            patch.object(worker, "invalidate_stale_forecasts"),
            patch.object(worker, "process_device", side_effect=lambda *args: events.append("forecast") or "created"),
        ):
            result = worker.run_once(
                lambda: MagicMock(), self.model, self.now,
                simulation="good", previous_simulations=("mid", "bad"),
            )
        self.assertEqual(events, ["lock", "seed", "previous", "previous", "forecast"])
        invalidate.assert_has_calls([
            unittest.mock.call(unittest.mock.ANY, "Simulation stage completed", "sim-farm-mid"),
            unittest.mock.call(unittest.mock.ANY, "Simulation stage completed", "sim-farm-bad"),
        ])
        self.assertEqual(result["sim-farm-good"], "created")

    def test_continuous_simulator_fails_clearly_when_another_worker_is_running(self):
        with (
            patch.object(worker, "run_once", return_value={"_worker": "unavailable: another forecast worker is running"}),
            patch("builtins.print"),
        ):
            self.assertEqual(worker.main(["--simulate", "good"]), 1)

    def test_three_script_entrypoints_select_the_expected_worker_profile(self):
        from ai_model import simulation

        for profile in ("good", "mid", "bad"):
            with patch.object(worker, "main", return_value=0) as main:
                self.assertEqual(simulation.main(profile, ["--once"]), 0)
                main.assert_called_once_with(["--simulate", profile, "--once"])

    def test_single_cycle_unavailable_status_is_an_unsuccessful_check(self):
        with (
            patch.object(worker, "run_once", return_value={"_worker": "unavailable: MODEL_VERSION is not configured"}),
            patch("builtins.print"),
        ):
            self.assertEqual(worker.main(["--once"]), 1)

    def test_series_runs_five_cycles_per_phase_in_order_and_completes(self):
        run_cycle = Mock(return_value={"sim-farm": "created"})
        sleeper = Mock()
        with patch("builtins.print") as output:
            result = worker.run_series(run_cycle=run_cycle, clock=lambda: 0, sleeper=sleeper)
        self.assertEqual(result, 0)
        self.assertEqual(
            [call.kwargs["simulation"] for call in run_cycle.call_args_list],
            ["good"] * 5 + ["mid"] * 5 + ["bad"] * 5,
        )
        self.assertEqual(
            [(index, call.kwargs["previous_simulations"])
             for index, call in enumerate(run_cycle.call_args_list)
             if "previous_simulations" in call.kwargs],
            [(0, ("mid", "bad")), (5, ("good", "bad")), (10, ("good", "mid"))],
        )
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [worker.POLL_SECONDS] * 14)
        progress = [json.loads(call.args[0]) for call in output.call_args_list]
        self.assertEqual(progress[0]["series"], {
            "phase": "good", "phase_index": 1, "phase_total": 3,
            "cycle": 1, "cycles_per_phase": 5,
        })
        self.assertEqual(progress[-1]["status"], "complete")
        self.assertEqual(progress[-1]["guidance_errors"], 0)

    def test_series_once_runs_only_the_first_good_cycle(self):
        run_cycle = Mock(return_value={"sim-farm-good": "created"})
        sleeper = Mock()
        with patch("builtins.print") as output:
            result = worker.run_series(once=True, run_cycle=run_cycle, clock=lambda: 0, sleeper=sleeper)
        self.assertEqual(result, 0)
        run_cycle.assert_called_once_with(simulation="good", previous_simulations=("mid", "bad"))
        sleeper.assert_not_called()
        self.assertEqual(json.loads(output.call_args.args[0])["series"]["cycle"], 1)

    def test_series_stops_on_worker_failure_without_reporting_completion(self):
        run_cycle = Mock(side_effect=[
            {"sim-farm-good": "created"},
            {"_worker": "unavailable: another forecast worker is running"},
        ])
        with patch("builtins.print") as output:
            result = worker.run_series(run_cycle=run_cycle, clock=lambda: 0, sleeper=Mock())
        self.assertEqual(result, 1)
        self.assertEqual(run_cycle.call_count, 2)
        self.assertNotIn("complete", [json.loads(call.args[0]).get("status") for call in output.call_args_list])

    def test_series_stops_when_forecast_processing_returns_an_error(self):
        for failure in ("error: prediction failed", "policy_error"):
            with self.subTest(failure=failure), patch("builtins.print"):
                result = worker.run_series(
                    run_cycle=Mock(return_value={"sim-farm-good": failure}),
                    clock=lambda: 0,
                    sleeper=Mock(),
                )
            self.assertEqual(result, 1)

    def test_series_keeps_numeric_forecast_when_only_qwen_explanation_fails(self):
        run_cycle = Mock(return_value={"sim-farm-good": "explanation_error"})
        with patch("builtins.print"):
            result = worker.run_series(once=True, run_cycle=run_cycle, clock=lambda: 0, sleeper=Mock())
        self.assertEqual(result, 0)

    def test_series_completion_reports_qwen_explanation_failures(self):
        run_cycle = Mock(return_value={"sim-farm": "explanation_error"})
        with patch("builtins.print") as output:
            result = worker.run_series(run_cycle=run_cycle, clock=lambda: 0, sleeper=Mock())
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.call_args.args[0])["guidance_errors"], 15)

    def test_individual_simulation_mode_still_runs_its_selected_profile(self):
        with (
            patch.object(worker, "run_once", return_value={"sim-farm-mid": "created"}) as run_once,
            patch("builtins.print"),
        ):
            self.assertEqual(worker.main(["--simulate", "mid", "--once"]), 0)
        run_once.assert_called_once_with(simulation="mid")

    def test_save_forecast_executes_before_committing(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        record = {
            "device_id": "esp32-1",
            "forecast_id": "ae7e337a-a0af-428d-81ea-908a9927d219",
            "input_row_id": 3,
            "input_cutoff_at": self.rows[-1]["recorded_at"],
            "target_at": self.rows[-1]["recorded_at"] + timedelta(hours=2),
            "generated_at": self.now,
            "model_version": "test-v1",
            "status": "available",
            "reason": None,
            "predicted_values": {"temperature_c": 34.0},
            "severity": {"temperature_c": "warning"},
            "explanation_status": "pending",
            "explanation": None,
            "policy_sha256": "policy-hash",
            "source_hash": "source-hash",
        }
        worker.save_forecast(connection, record)
        cursor.execute.assert_called_once()
        connection.commit.assert_called_once()

    def test_stale_forecast_is_invalidated_without_deleting_history(self):
        connection = MagicMock()
        worker.invalidate_stale_forecasts(connection, self.now)
        query, params = connection.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertIn("status = 'unavailable'", query)
        self.assertIn("input_cutoff_at < %s", query)
        self.assertEqual(params[-1], self.now - timedelta(seconds=worker.FRESHNESS_SECONDS))
        connection.commit.assert_called_once()

    def test_qwen_requires_complete_structured_output(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({
            "done": True,
            "response": json.dumps({"message": "Forecast warning. Check ventilation."}),
        }).encode())
        with patch.object(worker.urllib.request, "build_opener", return_value=opener):
            message = worker.call_qwen(
                {"temperature_c": 34.0}, {"temperature_c": "warning"},
                "trusted policy", self.now + timedelta(hours=2),
            )
        self.assertEqual(message, "Forecast warning. Check ventilation.")

        opener.open.return_value = io.BytesIO(json.dumps({"done": False, "response": ""}).encode())
        with patch.object(worker.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(worker.QwenUnavailable):
                worker.call_qwen({}, {}, "trusted policy", self.now)

    def test_same_source_model_and_policy_is_deduplicated(self):
        current = {
            "forecast_id": "ae7e337a-a0af-428d-81ea-908a9927d219",
            "input_row_id": 3,
            "input_cutoff_at": self.rows[-1]["recorded_at"],
            "target_at": self.rows[-1]["recorded_at"] + timedelta(hours=2),
            "generated_at": self.now,
            "model_version": self.model.MODEL_VERSION,
            "status": "available",
            "reason": None,
            "predicted_values": {"temperature_c": 34.0},
            "severity": {"temperature_c": "warning"},
            "explanation_status": "available",
            "explanation": "Existing explanation.",
            "policy_sha256": self.policy.sha256,
            "source_hash": worker.history_hash(self.rows, self.model),
            "device_id": "esp32-1",
        }
        with (
            patch.object(worker, "fetch_history", return_value=self.rows),
            patch.object(worker, "fetch_current_forecast", return_value=current),
            patch.object(worker, "save_forecast") as save,
        ):
            result = worker.process_device(
                self.conn, "esp32-1", self.model, self.now, (self.policy, None), Mock()
            )
        self.assertEqual(result, "duplicate")
        self.model.predict.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
