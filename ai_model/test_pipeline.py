"""Cross-check the worker's saved record against the webpage contract."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from ai_model import forecast_worker as worker
from ai_model import predictor
from ai_model.policy import load_policy
from web_app.forecast import FORECAST_COLUMNS, serialize_forecast


class PipelineTest(unittest.TestCase):
    def test_numeric_forecast_and_qwen_guidance_reach_webpage_record(self):
        now = datetime(2026, 10, 28, 14, 0, 50, tzinfo=timezone.utc)
        cutoff = now.replace(second=0)
        model = SimpleNamespace(
            MODEL_VERSION="fixture-v1", LOOKBACK_SAMPLES=2,
            FEATURE_COLUMNS=("temperature_c",), TARGET_COLUMNS=("temperature_c",),
            SAMPLE_INTERVAL_SECONDS=60, CADENCE_TOLERANCE_SECONDS=0, HORIZON_MINUTES=120,
            predict=lambda rows: {"temperature_c": 34.25},
        )
        rows = [dict(id=index + 1, device_id="fixture-node", temperature_c=30.0,
                     recorded_at=cutoff - timedelta(minutes=1 - index) + timedelta(seconds=36))
                for index in range(2)]
        connection = MagicMock()

        def explain(values, categories, policy, target):
            self.assertTrue(any(execution.args[0].startswith("INSERT") for execution in
                                connection.cursor.return_value.__enter__.return_value.execute.call_args_list))
            self.assertEqual(connection.mock_calls[-1], call.commit())
            self.assertEqual(values, {"temperature_c": 34.25})
            self.assertEqual(categories, {"temperature_c": "warning"})
            self.assertIn("threshold", policy)
            self.assertEqual(target, cutoff + timedelta(hours=2))
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
        result = serialize_forecast(tuple(saved[key] for key in FORECAST_COLUMNS) + (rows[-1]["recorded_at"],),
                                    [("temperature_c", "Temperature", "°C")], now)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["predictions"][0]["value"], 34.25)
        self.assertEqual(result["predictions"][0]["severity"], "warning")
        self.assertIn("check ventilation", result["explanation"])

    def test_trajectory_series_reaches_and_validates_in_webpage_serializer(self):
        now = datetime(2026, 10, 28, 14, 0, 50, tzinfo=timezone.utc)
        cutoff = now.replace(second=0)
        valid_series = {"temperature_c": [30.0 + i * 0.03 for i in range(120)]}
        invalid_series = {"temperature_c": [30.0] * 119}
        base_row = (
            "ae7e337a-a0af-428d-81ea-908a9927d219", "fixture-node", 1, cutoff,
            cutoff + timedelta(hours=2), now, "fixture-v1", "available", None,
            {"temperature_c": valid_series["temperature_c"][-1]},
            {"temperature_c": "warning"}, "unavailable", None, "policy-hash", None,
            cutoff,
        )
        ok = serialize_forecast(base_row + (valid_series,), [("temperature_c", "Temperature", "°C")], now)
        self.assertEqual(len(ok["series"]["temperature_c"]), 120)
        self.assertAlmostEqual(ok["series"]["temperature_c"][-1], valid_series["temperature_c"][-1])

        rejected = serialize_forecast(base_row + (invalid_series,), [("temperature_c", "Temperature", "°C")], now)
        self.assertEqual(rejected["series"], {})

    def test_predict_trajectory_returns_120_steps_and_rejects_nonfinite_mid_step(self):
        class FakeColumn:
            def __init__(self, values):
                self._values = values

            def tolist(self):
                return list(self._values)

        frame = {
            col: FakeColumn([float(idx + i) for i in range(predictor.HORIZON_MINUTES)])
            for idx, col in enumerate(predictor.MODEL_COLUMNS)
        }
        with patch.object(predictor, "_forecast_frame", return_value=frame):
            trajectory = predictor.predict_trajectory([])
        self.assertEqual(set(trajectory), set(predictor.TARGET_COLUMNS))
        for idx, col in enumerate(predictor.TARGET_COLUMNS):
            self.assertEqual(len(trajectory[col]), predictor.HORIZON_MINUTES)
            self.assertAlmostEqual(trajectory[col][-1], float(idx + predictor.HORIZON_MINUTES - 1))

        bad_vals = [1.0] * predictor.HORIZON_MINUTES
        bad_vals[15] = float("nan")
        frame[predictor.MODEL_COLUMNS[0]] = FakeColumn(bad_vals)
        with patch.object(predictor, "_forecast_frame", return_value=frame):
            with self.assertRaisesRegex(predictor.ModelUnavailable, "nonfinite"):
                predictor.predict_trajectory([])


if __name__ == "__main__":
    unittest.main()
