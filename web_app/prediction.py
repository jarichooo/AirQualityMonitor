import math
import uuid
from datetime import timezone


PREDICTION_LOCK_ID = 739_783_247_071_623_201
PREDICTION_COOLDOWN_SECONDS = 60
READING_FRESHNESS_SECONDS = 180


class PredictionRejected(Exception):
    def __init__(self, status_code, payload):
        super().__init__(payload["message"])
        self.status_code = status_code
        self.payload = payload


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _read_state(cursor, lock_request=False):
    cursor.execute("SELECT CURRENT_TIMESTAMP")
    now = _utc(cursor.fetchone()[0])
    cursor.execute(
        "SELECT id, device_id, recorded_at FROM public.air_quality_logs "
        "WHERE device_id IS NOT NULL AND BTRIM(device_id) <> '' "
        "ORDER BY recorded_at DESC, id DESC LIMIT 1"
    )
    latest = cursor.fetchone()
    cursor.execute(
        "SELECT request_id::text, device_id, input_row_id, status, requested_at, "
        "started_at, completed_at, error FROM public.air_quality_prediction_requests "
        "WHERE id = 1" + (" FOR UPDATE" if lock_request else "")
    )
    queued = cursor.fetchone()
    return now, latest, queued


def _base_payload(queued):
    return {
        "configured": True,
        "status": queued[3] if queued else "idle",
        "request_id": queued[0] if queued else None,
        "device_id": queued[1] if queued else None,
        "input_row_id": int(queued[2]) if queued else None,
        "cooldown_seconds": 0,
        "message": "",
        "can_predict": False,
    }


def _worker_error(queued):
    if not queued or queued[3] != "error":
        return None
    return "The last prediction could not be completed."


def _availability(now, latest, queued):
    payload = _base_payload(queued)
    if queued:
        elapsed = (now - _utc(queued[4])).total_seconds()
        payload["cooldown_seconds"] = max(
            0, math.ceil(PREDICTION_COOLDOWN_SECONDS - elapsed)
        )

    if queued and queued[3] in {"queued", "running"}:
        payload["message"] = (
            "Prediction is queued." if queued[3] == "queued" else "Prediction is running."
        )
        return payload, "busy"
    if payload["cooldown_seconds"]:
        payload["message"] = (
            f"Wait {payload['cooldown_seconds']} seconds before requesting another prediction."
        )
        reason = "cooldown"
    elif latest is None:
        payload["message"] = "No identified sensor readings are available yet."
        reason = "no_reading"
    else:
        row_id, device_id, recorded_at = latest
        recorded_at = _utc(recorded_at)
        age = (now - recorded_at).total_seconds()
        if age < 0:
            payload["message"] = "The latest sensor reading has an invalid future timestamp."
            reason = "invalid_reading"
        elif age > READING_FRESHNESS_SECONDS:
            payload["message"] = "The latest sensor reading is too old. Wait for a new reading."
            reason = "stale_reading"
        elif queued and queued[1] == device_id and int(queued[2]) == int(row_id):
            payload["message"] = "A new sensor reading is required before predicting again."
            reason = "same_reading"
        else:
            payload["can_predict"] = True
            payload["message"] = "Ready to predict the latest sensor reading."
            reason = "ready"

    worker_error = _worker_error(queued)
    if worker_error:
        payload["message"] = f"{worker_error} {payload['message']}"
    return payload, reason


def get_prediction_state(connection):
    with connection.cursor() as cursor:
        now, latest, queued = _read_state(cursor)
    return _availability(now, latest, queued)[0]


def enqueue_prediction(connection):
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", (PREDICTION_LOCK_ID,))
            lock_acquired = cursor.fetchone()[0]
            now, latest, queued = _read_state(cursor, lock_request=lock_acquired)
            payload, reason = _availability(now, latest, queued)

            if not lock_acquired or reason == "busy":
                payload["can_predict"] = False
                payload["message"] = "A prediction request is already being processed."
                raise PredictionRejected(409, payload)
            if reason == "cooldown":
                raise PredictionRejected(429, payload)
            if reason == "same_reading":
                raise PredictionRejected(409, payload)
            if reason != "ready":
                raise PredictionRejected(409, payload)

            request_id = str(uuid.uuid4())
            row_id, device_id, _ = latest
            cursor.execute(
                "INSERT INTO public.air_quality_prediction_requests "
                "(id, request_id, device_id, input_row_id, status, requested_at, "
                "started_at, completed_at, error) "
                "VALUES (1, %s::uuid, %s, %s, 'queued', %s, NULL, NULL, NULL) "
                "ON CONFLICT (id) DO UPDATE SET request_id = EXCLUDED.request_id, "
                "device_id = EXCLUDED.device_id, input_row_id = EXCLUDED.input_row_id, "
                "status = EXCLUDED.status, requested_at = EXCLUDED.requested_at, "
                "started_at = NULL, completed_at = NULL, error = NULL",
                (request_id, device_id, row_id, now),
            )

    return {
        "configured": True,
        "status": "queued",
        "request_id": request_id,
        "device_id": device_id,
        "input_row_id": int(row_id),
        "cooldown_seconds": PREDICTION_COOLDOWN_SECONDS,
        "message": "Prediction queued.",
        "can_predict": False,
    }
