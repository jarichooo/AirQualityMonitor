"""Cross-check the worker's saved record against the webpage contract."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from ai_model import forecast_worker as worker
from ai_model.policy import load_policy
from web_app.forecast import FORECAST_COLUMNS, serialize_forecast


class PipelineTest(unittest.TestCase):
    def test_numeric_forecast_and_qwen_guidance_reach_webpage_record(self):
        now = datetime(2026, 10, 28, 14, tzinfo=timezone.utc)
        model = SimpleNamespace(
            MODEL_VERSION="fixture-v1", LOOKBACK_SAMPLES=2,
            FEATURE_COLUMNS=("temperature_c",), TARGET_COLUMNS=("temperature_c",),
            SAMPLE_INTERVAL_SECONDS=60, CADENCE_TOLERANCE_SECONDS=0, HORIZON_MINUTES=120,
            predict=lambda rows: {"temperature_c": 34.25},
        )
        rows = [dict(id=index + 1, device_id="fixture-node", temperature_c=30.0,
                     recorded_at=now - timedelta(minutes=1 - index)) for index in range(2)]
        connection = MagicMock()

        def explain(values, categories, policy, target):
            self.assertTrue(any(execution.args[0].startswith("INSERT") for execution in
                                connection.cursor.return_value.__enter__.return_value.execute.call_args_list))
            self.assertEqual(connection.mock_calls[-1], call.commit())
            self.assertEqual(values, {"temperature_c": 34.25})
            self.assertEqual(categories, {"temperature_c": "warning"})
            self.assertIn("threshold", policy)
            self.assertEqual(target, now + timedelta(hours=2))
            return "Temperature is forecast to reach the warning range; check ventilation."

        with (
            patch.object(worker, "fetch_history", return_value=rows),
            patch.object(worker, "fetch_current_forecast", return_value=None),
        ):
            self.assertEqual(worker.process_device(
                connection, "fixture-node", model, now, (load_policy(), None), explain,
            ), "created")

        calls = connection.cursor.return_value.__enter__.return_value.execute.call_args_list
        insert_params = next(call.args[1] for call in calls if call.args[0].startswith("INSERT"))
        columns = ("device_id", "forecast_id", "input_row_id", "input_cutoff_at", "target_at",
                   "generated_at", "model_version", "status", "reason", "predicted_values",
                   "severity", "explanation_status", "explanation", "policy_sha256", "source_hash")
        saved = dict(zip(columns, insert_params))
        for key in ("predicted_values", "severity"):
            saved[key] = saved[key].adapted
        explanation_params = calls[-1].args[1]
        saved.update(explanation_status=explanation_params[0], explanation=explanation_params[1], last_guidance=None)
        result = serialize_forecast(tuple(saved[key] for key in FORECAST_COLUMNS),
                                    [("temperature_c", "Temperature", "°C")], now)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["predictions"][0]["value"], 34.25)
        self.assertEqual(result["predictions"][0]["severity"], "warning")
        self.assertIn("check ventilation", result["explanation"])


if __name__ == "__main__":
    unittest.main()
