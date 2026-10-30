import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg2

import app as application
import prediction


NOW = datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)
KEYS = {
    "configured", "status", "request_id", "device_id", "input_row_id",
    "cooldown_seconds", "message", "can_predict",
}


class FakeCursor:
    def __init__(self, now=NOW, latest=(125, "sensor-1", NOW), queued=None, lock=True):
        self.now = now
        self.latest = latest
        self.queued = queued
        self.lock = lock
        self.result = None
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if "pg_try_advisory_xact_lock" in query:
            self.result = (self.lock,)
        elif query == "SELECT CURRENT_TIMESTAMP":
            self.result = (self.now,)
        elif "FROM public.air_quality_logs" in query:
            self.result = self.latest
        elif "FROM public.air_quality_prediction_requests" in query:
            self.result = self.queued
        else:
            self.result = None

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, **state):
        self.cursor_value = FakeCursor(**state)

    def cursor(self):
        return self.cursor_value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def request_row(status="completed", requested_at=None, row_id=124, device_id="sensor-1", error=None):
    return (
        "11111111-1111-4111-8111-111111111111", device_id, row_id, status,
        requested_at or NOW - timedelta(seconds=61), NOW - timedelta(seconds=60),
        NOW - timedelta(seconds=59), error,
    )


class PredictionDomainTest(unittest.TestCase):
    def test_idle_state_allows_a_fresh_identified_reading(self):
        payload = prediction.get_prediction_state(FakeConnection())
        self.assertEqual(set(payload), KEYS)
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["can_predict"])

    def test_enqueue_uses_global_lock_and_writes_one_queued_request(self):
        connection = FakeConnection(queued=request_row(row_id=124))
        payload = prediction.enqueue_prediction(connection)
        self.assertEqual(set(payload), KEYS)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["input_row_id"], 125)
        calls = connection.cursor_value.executed
        self.assertEqual(calls[0][1], (prediction.PREDICTION_LOCK_ID,))
        insert = next(call for call in calls if call[0].startswith("INSERT INTO"))
        self.assertIn("ON CONFLICT (id) DO UPDATE", insert[0])
        self.assertEqual(insert[1][1:3], ("sensor-1", 125))

    def test_busy_request_or_worker_lock_refuses_a_second_request(self):
        for connection in (
            FakeConnection(queued=request_row(status="queued")),
            FakeConnection(queued=request_row(), lock=False),
        ):
            with self.subTest(lock=connection.cursor_value.lock), self.assertRaises(prediction.PredictionRejected) as caught:
                prediction.enqueue_prediction(connection)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertFalse(any(call[0].startswith("INSERT INTO") for call in connection.cursor_value.executed))

    def test_global_cooldown_uses_database_time(self):
        connection = FakeConnection(queued=request_row(requested_at=NOW - timedelta(seconds=30)))
        with self.assertRaises(prediction.PredictionRejected) as caught:
            prediction.enqueue_prediction(connection)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.payload["cooldown_seconds"], 30)

    def test_same_reading_requires_a_new_row_even_after_cooldown(self):
        connection = FakeConnection(queued=request_row(row_id=125))
        with self.assertRaises(prediction.PredictionRejected) as caught:
            prediction.enqueue_prediction(connection)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("new sensor reading", caught.exception.payload["message"].lower())

    def test_new_reading_can_be_queued_after_cooldown(self):
        connection = FakeConnection(queued=request_row(row_id=124))
        payload = prediction.enqueue_prediction(connection)
        self.assertEqual(payload["input_row_id"], 125)

    def test_worker_error_is_safely_reported_without_changing_availability(self):
        queued = request_row(status="error", row_id=125,
                             error="Prediction failed.\n  Check worker logs.")
        payload = prediction.get_prediction_state(FakeConnection(queued=queued))
        self.assertFalse(payload["can_predict"])
        self.assertIn("The last prediction could not be completed.", payload["message"])
        self.assertNotIn("worker logs", payload["message"])
        self.assertIn("new sensor reading", payload["message"].lower())

        queued = request_row(status="error", row_id=124, error="Qwen could not finish.")
        payload = prediction.get_prediction_state(FakeConnection(queued=queued))
        self.assertTrue(payload["can_predict"])
        self.assertIn("The last prediction could not be completed.", payload["message"])
        self.assertNotIn("Qwen", payload["message"])
        self.assertIn("Ready to predict", payload["message"])

    def test_stored_technical_errors_are_not_shown_in_the_prediction_message(self):
        for error in (None, "x" * 500, "Qwen failed. Check Ollama and forecast worker logs.", "Cannot load model.joblib with NumPy"):
            with self.subTest(error=error):
                queued = request_row(status="error", row_id=125, error=error)
                payload = prediction.get_prediction_state(FakeConnection(queued=queued))
                self.assertLessEqual(len(payload["message"]), 320)
                self.assertTrue(payload["message"])
                self.assertNotRegex(payload["message"], r"Qwen|Ollama|worker|joblib|NumPy")

    def test_missing_stale_and_future_readings_are_rejected(self):
        readings = (None, (1, "sensor-1", NOW - timedelta(seconds=181)),
                    (1, "sensor-1", NOW + timedelta(seconds=1)))
        for latest in readings:
            with self.subTest(latest=latest), self.assertRaises(prediction.PredictionRejected) as caught:
                prediction.enqueue_prediction(FakeConnection(latest=latest))
            self.assertEqual(caught.exception.status_code, 409)


class PredictionApiTest(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = application.app.test_client()

    def login(self, token="test-token"):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username="admin", role="admin",
                           prediction_token=token)

    def test_api_requires_login_and_post_requires_csrf(self):
        self.assertEqual(self.client.get("/api/predictions").status_code, 302)
        self.assertEqual(self.client.post("/api/predictions", json={}).status_code, 302)
        self.login()
        with patch.object(application, "get_db_connection") as connect:
            for body in ({}, [], {"csrf_token": "wrong"}, {"csrf_token": "é"}):
                response = self.client.post("/api/predictions", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.get_json()), KEYS)
            connect.assert_not_called()

    def test_dashboard_creates_and_reuses_prediction_token(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username="viewer", role="viewer")
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (0, None, None)
            cursor.fetchall.return_value = []
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
            with self.client.session_transaction() as session:
                first_token = session["prediction_token"]
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["prediction_token"], first_token)
        self.assertEqual(len(first_token), 64)

    def test_get_and_accepted_post_have_stable_shape_and_no_store(self):
        self.login()
        ready = {
            "configured": True, "status": "idle", "request_id": None,
            "device_id": None, "input_row_id": None, "cooldown_seconds": 0,
            "message": "Ready.", "can_predict": True,
        }
        queued = {**ready, "status": "queued", "request_id": "request-1",
                  "device_id": "sensor-1", "input_row_id": 125,
                  "cooldown_seconds": 60, "message": "Prediction queued.",
                  "can_predict": False}
        with patch.object(application, "get_prediction_state", return_value=ready), \
             patch.object(application, "enqueue_prediction", return_value=queued), \
             patch.object(application, "get_db_connection"):
            get_response = self.client.get("/api/predictions")
            post_response = self.client.post("/api/predictions", json={"csrf_token": "test-token"})
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 202)
        self.assertEqual(set(get_response.get_json()), KEYS)
        self.assertEqual(set(post_response.get_json()), KEYS)
        self.assertEqual(get_response.headers["Cache-Control"], "no-store")
        self.assertEqual(post_response.headers["Cache-Control"], "no-store")

    def test_missing_schema_and_database_failure_are_sanitized(self):
        self.login()
        cases = (
            (psycopg2.errors.UndefinedTable(), 200, False, "not configured"),
            (psycopg2.OperationalError("password and host leaked"), 503, True, "temporarily"),
        )
        for error, code, configured, message in cases:
            with self.subTest(error=type(error).__name__), \
                 patch.object(application, "get_db_connection", side_effect=error):
                response = self.client.get("/api/predictions")
            payload = response.get_json()
            self.assertEqual(response.status_code, code)
            self.assertEqual(set(payload), KEYS)
            self.assertEqual(payload["configured"], configured)
            self.assertIn(message, payload["message"].lower())
            self.assertNotIn("password", response.get_data(as_text=True))

        with patch.object(application, "get_db_connection", side_effect=psycopg2.errors.UndefinedTable()):
            response = self.client.post("/api/predictions", json={"csrf_token": "test-token"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()["configured"])


if __name__ == "__main__":
    unittest.main()
