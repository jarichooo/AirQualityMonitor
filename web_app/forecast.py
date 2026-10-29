import math
from datetime import datetime, timedelta, timezone


FORECAST_COLUMNS = (
    "forecast_id", "device_id", "input_row_id", "input_cutoff_at", "target_at",
    "generated_at", "model_version", "status", "reason", "predicted_values",
    "severity", "explanation_status", "explanation", "policy_sha256",
)
VALID_SEVERITIES = {"normal", "warning", "critical", "unavailable"}
VALID_EXPLANATION_STATUSES = {"pending", "available", "unavailable", "error"}


def _utc_timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _iso(value):
    value = _utc_timestamp(value)
    return value.isoformat() if value else None


def _valid_sensor_value(key, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return False
    if key == "humidity_perc":
        return 0 <= value <= 100
    if key in {"co2_ppm", "pm1_ugm3", "pm25_ugm3", "pm10_ugm3", "mq4_ppm", "mq137_ppm"}:
        return value >= 0
    return True


def _unavailable(row, reason, explanation_status, explanation):
    device = row[1].strip() if isinstance(row[1], str) else None
    scenario = {"sim-farm-good": "Good", "sim-farm-mid": "Warning", "sim-farm-bad": "Critical"}.get(device)
    return {
        "forecast_id": str(row[0]) if row[0] is not None else None,
        "device_id": device,
        "data_class": "SIMULATED" if scenario or device == "python-simulator" else "RECORDED",
        "scenario": scenario,
        "input_row_id": row[2],
        "input_cutoff_at": _iso(row[3]),
        "target_at": _iso(row[4]),
        "generated_at": _iso(row[5]),
        "model_version": row[6] if isinstance(row[6], str) else None,
        "status": "unavailable",
        "reason": reason,
        "predictions": [],
        "explanation_status": explanation_status,
        "explanation": explanation,
        "policy_sha256": row[13] if isinstance(row[13], str) else None,
    }


def serialize_forecast(row, sensors, now=None):
    """Validate one saved forecast before it crosses the web trust boundary."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    explanation_status = row[11] if row[11] in VALID_EXPLANATION_STATUSES else "unavailable"
    explanation = row[12].strip() if explanation_status == "available" and isinstance(row[12], str) and row[12].strip() else None
    device_id = row[1]
    if not isinstance(device_id, str) or not device_id.strip():
        return None

    reason = row[8].strip() if isinstance(row[8], str) and row[8].strip() else None
    if row[7] != "available":
        return _unavailable(row, reason or "The forecast is not available.", "unavailable", None)

    cutoff = _utc_timestamp(row[3])
    target = _utc_timestamp(row[4])
    generated = _utc_timestamp(row[5])
    if cutoff is None or target is None or cutoff > now or now - cutoff > timedelta(seconds=180):
        return _unavailable(row, "The saved forecast is stale or has an invalid input time.", "unavailable", None)
    if target != cutoff + timedelta(hours=2) or target <= now:
        return _unavailable(row, "The saved forecast has an invalid two-hour target time.", "unavailable", None)
    if generated is None or generated < cutoff or generated > now:
        return _unavailable(row, "The saved forecast has an invalid generation time.", "unavailable", None)

    values = row[9] if isinstance(row[9], dict) else {}
    severities = row[10] if isinstance(row[10], dict) else {}
    predictions = []
    for key, label, unit in sensors:
        value = values.get(key)
        severity = severities.get(key)
        if not _valid_sensor_value(key, value):
            continue
        if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
            continue
        predictions.append({"key": key, "label": label, "unit": unit,
                            "value": float(value), "severity": severity})
    if not predictions:
        return _unavailable(row, reason or "The forecast contains no valid sensor predictions.", "unavailable", None)

    result = _unavailable(row, reason, explanation_status, explanation)
    if any(prediction["severity"] == "unavailable" for prediction in predictions):
        result.update(explanation_status="unavailable", explanation=None)
    result.update(status="available", predictions=predictions)
    return result


def fetch_latest_forecasts(connection, sensors, now=None):
    columns = ", ".join(f"forecast.{column}" for column in FORECAST_COLUMNS)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {columns} FROM public.air_quality_forecasts AS forecast "
            "WHERE EXISTS (SELECT 1 FROM public.air_quality_logs AS source "
            "WHERE source.id = forecast.input_row_id "
            "AND source.device_id = forecast.device_id "
            "AND source.recorded_at = forecast.input_cutoff_at) "
            "ORDER BY forecast.device_id"
        )
        rows = cursor.fetchall()
    return [forecast for row in rows if (forecast := serialize_forecast(row, sensors, now)) is not None]
