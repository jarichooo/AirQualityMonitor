import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import psycopg2

import app as application
from forecast import serialize_forecast


class ForecastTestCase(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = application.app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username="admin", role="admin")

    @staticmethod
    def row(now, **changes):
        cutoff = now - timedelta(seconds=30)
        values = {
            "forecast_id": "8d8e98c3-e574-4d21-a388-7d38ee99f0a5",
            "device_id": "house-1",
            "input_row_id": 42,
            "input_cutoff_at": cutoff,
            "target_at": cutoff + timedelta(hours=2),
            "generated_at": now - timedelta(seconds=20),
            "model_version": "test-model",
            "status": "available",
            "reason": None,
            "predicted_values": {"temperature_c": 34.25, "humidity_perc": 78},
            "severity": {"temperature_c": "warning", "humidity_perc": "critical"},
            "explanation_status": "available",
            "explanation": "Check ventilation before the forecast target.",
            "policy_sha256": "abc123",
        }
        values.update(changes)
        return tuple(values[column] for column in (
            "forecast_id", "device_id", "input_row_id", "input_cutoff_at", "target_at",
            "generated_at", "model_version", "status", "reason", "predicted_values",
            "severity", "explanation_status", "explanation", "policy_sha256",
        ))

    def test_forecast_api_requires_login_and_returns_saved_output(self):
        self.assertEqual(self.client.get("/api/forecasts").status_code, 302)
        self.login()
        now = datetime.now(timezone.utc)
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [self.row(now)]
            response = self.client.get("/api/forecasts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        forecast = response.get_json()["forecasts"][0]
        self.assertEqual(forecast["device_id"], "house-1")
        self.assertEqual(forecast["predictions"][0], {
            "key": "temperature_c", "label": "Temperature", "unit": "°C",
            "value": 34.25, "severity": "warning",
        })
        self.assertEqual(forecast["explanation"], "Check ventilation before the forecast target.")
        self.assertTrue(forecast["target_at"].endswith("+00:00"))
        query = cursor.execute.call_args.args[0]
        self.assertIn("FROM public.air_quality_forecasts", query)
        self.assertIn("source.id = forecast.input_row_id", query)
        self.assertIn("source.device_id = forecast.device_id", query)
        self.assertIn("source.recorded_at = forecast.input_cutoff_at", query)

    def test_stale_unavailable_and_untrusted_values_hide_numbers(self):
        now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        stale_cutoff = now - timedelta(seconds=181)
        stale = serialize_forecast(
            self.row(now, input_cutoff_at=stale_cutoff, target_at=stale_cutoff + timedelta(hours=2)),
            application.SENSORS, now,
        )
        self.assertEqual(stale["status"], "unavailable")
        self.assertEqual(stale["predictions"], [])
        self.assertIsNone(stale["explanation"])

        malformed_target = serialize_forecast(
            self.row(now, target_at=now + timedelta(hours=3)), application.SENSORS, now,
        )
        self.assertEqual(malformed_target["status"], "unavailable")
        self.assertIn("target time", malformed_target["reason"])

        unavailable = serialize_forecast(
            self.row(now, status="unavailable", reason="Training model is not installed.",
                     predicted_values={"temperature_c": 99}),
            application.SENSORS, now,
        )
        self.assertEqual(unavailable["reason"], "Training model is not installed.")
        self.assertEqual(unavailable["predictions"], [])

        malicious = serialize_forecast(
            self.row(now, predicted_values={"temperature_c": "34", "humidity_perc": float("inf"),
                                             "co2_ppm": 2800, "unknown": 1},
                     severity={"temperature_c": "normal", "humidity_perc": "normal",
                               "co2_ppm": "made-up", "unknown": "normal"}),
            application.SENSORS, now,
        )
        self.assertEqual(malicious["status"], "unavailable")
        self.assertEqual(malicious["predictions"], [])
        self.assertIsNone(malicious["explanation"])

        reasoning_error = serialize_forecast(
            self.row(now, explanation_status="error", explanation="old guidance"),
            application.SENSORS, now,
        )
        self.assertEqual(reasoning_error["status"], "available")
        self.assertTrue(reasoning_error["predictions"])
        self.assertEqual(reasoning_error["explanation_status"], "error")
        self.assertIsNone(reasoning_error["explanation"])

        malformed_policy = serialize_forecast(
            self.row(now, predicted_values={"temperature_c": 34.5},
                     severity={"temperature_c": "unavailable"}),
            application.SENSORS, now,
        )
        self.assertEqual(malformed_policy["status"], "available")
        self.assertEqual(malformed_policy["predictions"][0]["value"], 34.5)
        self.assertEqual(malformed_policy["predictions"][0]["severity"], "unavailable")
        self.assertEqual(malformed_policy["explanation_status"], "unavailable")
        self.assertIsNone(malformed_policy["explanation"])

        invalid_domain = serialize_forecast(
            self.row(now, predicted_values={"humidity_perc": 101, "co2_ppm": -1},
                     severity={"humidity_perc": "critical", "co2_ppm": "normal"}),
            application.SENSORS, now,
        )
        self.assertEqual(invalid_domain["status"], "unavailable")
        self.assertIsNone(invalid_domain["explanation"])

        invalid_generated = serialize_forecast(
            self.row(now, generated_at=now + timedelta(seconds=1)), application.SENSORS, now,
        )
        self.assertEqual(invalid_generated["status"], "unavailable")
        self.assertIn("generation time", invalid_generated["reason"])

    def test_missing_forecast_table_is_expected_and_dashboard_stays_independent(self):
        self.login()
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.execute.side_effect = psycopg2.errors.UndefinedTable("missing")
            response = self.client.get("/api/forecasts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "configured": False, "forecasts": [], "message": "Forecasting is not configured yet."
        })

        dashboard_data = dict(
            total=0, latest=None,
            metrics=[dict(column=key, label=label, unit=unit, value=None,
                          minimum=None, average=None, maximum=None, count=0)
                     for key, label, unit in application.SENSORS],
            series=[], start=datetime.now(timezone.utc) - timedelta(hours=24),
            recorded_series=[], recorded_start=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        with patch.object(application, "fetch_dashboard_data", return_value=dashboard_data):
            page = self.client.get("/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="forecast-panel"', page.data)

    def test_browser_renderer_uses_text_only_for_model_output(self):
        source = (Path(__file__).parent / "static" / "forecasts.js").read_text(encoding="utf-8")
        self.assertIn("element.textContent = text", source)
        self.assertNotIn("innerHTML", source)
        self.assertIn("new AbortController()", source)
        self.assertIn("controller.abort(), 10000", source)
        self.assertIn("fingerprint !== renderedFingerprint", source)


if __name__ == "__main__":
    unittest.main()
