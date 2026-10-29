"""Create two-hour forecasts from committed PostgreSQL sensor readings."""

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from ai_model import predictor


ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema.sql"
SENSOR_COLUMNS = {
    "temperature_c",
    "humidity_perc",
    "co2_ppm",
    "pm1_ugm3",
    "pm25_ugm3",
    "pm10_ugm3",
    "mq4_ppm",
    "mq137_ppm",
}
NONNEGATIVE_COLUMNS = SENSOR_COLUMNS - {"temperature_c", "humidity_perc"}
FRESHNESS_SECONDS = 180
POLL_SECONDS = 60


class ContractError(ValueError):
    pass


class QwenUnavailable(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def database_connection():
    password = os.environ.get("DB_PASS")
    if not password:
        raise RuntimeError("DB_PASS is required")
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=os.environ.get("DB_PORT", "5432"),
        database=os.environ.get("DB_NAME", "air_quality"),
        user=os.environ.get("DB_USER", "admin"),
        password=password,
        connect_timeout=5,
        options="-c timezone=UTC",
    )


def validate_model_contract(model=predictor):
    if not isinstance(model.MODEL_VERSION, str) or not model.MODEL_VERSION.strip():
        raise predictor.ModelUnavailable("MODEL_VERSION is not configured")
    if isinstance(model.LOOKBACK_SAMPLES, bool) or not isinstance(model.LOOKBACK_SAMPLES, int) or model.LOOKBACK_SAMPLES < 2:
        raise ContractError("LOOKBACK_SAMPLES must be an integer of at least 2")
    if type(model.SAMPLE_INTERVAL_SECONDS) is not int or model.SAMPLE_INTERVAL_SECONDS != 60:
        raise ContractError("SAMPLE_INTERVAL_SECONDS must be 60")
    tolerance = model.CADENCE_TOLERANCE_SECONDS
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or not 0 <= tolerance < 60):
        raise ContractError("CADENCE_TOLERANCE_SECONDS must be finite and between 0 and 60")
    if type(model.HORIZON_MINUTES) is not int or model.HORIZON_MINUTES != 120:
        raise ContractError("HORIZON_MINUTES must be 120")
    for name in ("FEATURE_COLUMNS", "TARGET_COLUMNS"):
        columns = getattr(model, name)
        if not isinstance(columns, (tuple, list)) or not columns or len(columns) != len(set(columns)):
            raise ContractError(f"{name} must contain unique sensor columns")
        unknown = set(columns) - SENSOR_COLUMNS
        if unknown:
            raise ContractError(f"{name} contains unsupported columns: {sorted(unknown)}")
    if not callable(getattr(model, "predict", None)):
        raise ContractError("predict must be callable")


def validate_value(column, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{column} must be a finite number")
    if column == "humidity_perc" and not 0 <= value <= 100:
        raise ContractError("humidity_perc must be between 0 and 100")
    if column in NONNEGATIVE_COLUMNS and value < 0:
        raise ContractError(f"{column} cannot be negative")
    return float(value)


def validate_history(rows, model=predictor, now=None):
    now = now or utc_now()
    if now.tzinfo is None:
        raise ContractError("Current time must include a timezone")
    if len(rows) != model.LOOKBACK_SAMPLES:
        raise ContractError(f"Expected {model.LOOKBACK_SAMPLES} history rows, got {len(rows)}")
    devices = {row["device_id"] for row in rows}
    if len(devices) != 1 or not next(iter(devices), "").strip():
        raise ContractError("History must belong to one identified device")
    previous = None
    for row in rows:
        timestamp = row["recorded_at"]
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ContractError("Reading timestamps must include a timezone")
        if timestamp > now:
            raise ContractError("History contains a future reading")
        if previous is not None:
            delta = (timestamp - previous).total_seconds()
            if delta <= 0:
                raise ContractError("History timestamps must be unique and chronological")
            if abs(delta - model.SAMPLE_INTERVAL_SECONDS) > model.CADENCE_TOLERANCE_SECONDS:
                raise ContractError("History cadence does not match the model contract")
        for column in model.FEATURE_COLUMNS:
            validate_value(column, row.get(column))
        previous = timestamp
    if (now - rows[-1]["recorded_at"]).total_seconds() > FRESHNESS_SECONDS:
        raise ContractError("Latest reading is stale")
    return rows


def validate_prediction(values, model=predictor):
    if not isinstance(values, dict) or set(values) != set(model.TARGET_COLUMNS):
        raise ContractError("Prediction keys must exactly match TARGET_COLUMNS")
    return {column: validate_value(column, values[column]) for column in model.TARGET_COLUMNS}


def history_hash(rows, model=predictor):
    source = [
        {
            "id": row["id"],
            "recorded_at": row["recorded_at"].isoformat(),
            "device_id": row["device_id"],
            **{column: row[column] for column in model.FEATURE_COLUMNS},
        }
        for row in rows
    ]
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fetch_active_devices(conn, now):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT device_id FROM public.air_quality_logs "
            "WHERE device_id IS NOT NULL AND btrim(device_id) <> '' AND recorded_at >= %s",
            (now - timedelta(seconds=FRESHNESS_SECONDS),),
        )
        return [row[0] for row in cursor.fetchall()]


def fetch_history(conn, device_id, model=predictor):
    columns = ", ".join(model.FEATURE_COLUMNS)
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            f"SELECT id, recorded_at, device_id, {columns} FROM public.air_quality_logs "
            "WHERE device_id = %s ORDER BY recorded_at DESC, id DESC LIMIT %s",
            (device_id, model.LOOKBACK_SAMPLES),
        )
        return list(reversed(cursor.fetchall()))


def fetch_current_forecast(conn, device_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM public.air_quality_forecasts WHERE device_id = %s", (device_id,))
        return cursor.fetchone()


def load_policy_result():
    try:
        from ai_model.policy import load_policy

        return load_policy(), None
    except (OSError, ValueError) as exc:
        return None, f"POLICY_INVALID: {exc}"


def unavailable_severity(targets):
    return {column: "unavailable" for column in targets}


def classify(policy, predictions):
    try:
        severity = policy.classify(predictions)
        if not isinstance(severity, dict) or set(severity) != set(predictions):
            raise ValueError("Classification keys do not match predictions")
        return severity, None
    except (TypeError, ValueError) as exc:
        return unavailable_severity(predictions), f"POLICY_INVALID: {exc}"


def save_forecast(conn, record):
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO public.air_quality_forecasts AS current_forecast
               (device_id, forecast_id, input_row_id, input_cutoff_at, target_at,
                generated_at, model_version, status, reason, predicted_values,
                severity, explanation_status, explanation, policy_sha256, source_hash)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (device_id) DO UPDATE SET
                 forecast_id = EXCLUDED.forecast_id,
                 input_row_id = EXCLUDED.input_row_id,
                 input_cutoff_at = EXCLUDED.input_cutoff_at,
                 target_at = EXCLUDED.target_at,
                 generated_at = EXCLUDED.generated_at,
                 model_version = EXCLUDED.model_version,
                 status = EXCLUDED.status,
                 reason = EXCLUDED.reason,
                 predicted_values = EXCLUDED.predicted_values,
                 severity = EXCLUDED.severity,
                 explanation_status = EXCLUDED.explanation_status,
                 explanation = EXCLUDED.explanation,
                 policy_sha256 = EXCLUDED.policy_sha256,
                 source_hash = EXCLUDED.source_hash
               WHERE current_forecast.input_cutoff_at < EXCLUDED.input_cutoff_at
                  OR (current_forecast.input_cutoff_at = EXCLUDED.input_cutoff_at
                      AND current_forecast.input_row_id <= EXCLUDED.input_row_id)""",
            (
                record["device_id"], record["forecast_id"], record["input_row_id"],
                record["input_cutoff_at"], record["target_at"], record["generated_at"],
                record["model_version"], record["status"], record["reason"],
                Json(record["predicted_values"]), Json(record["severity"]),
                record["explanation_status"], record["explanation"],
                record["policy_sha256"], record["source_hash"],
            ),
        )
    conn.commit()


def set_policy_pending(conn, device_id, forecast_id, severity, policy_sha256):
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE public.air_quality_forecasts SET severity = %s, policy_sha256 = %s, "
            "explanation_status = 'pending', explanation = NULL, reason = NULL "
            "WHERE device_id = %s AND forecast_id = %s::uuid",
            (Json(severity), policy_sha256, device_id, forecast_id),
        )
    conn.commit()


def update_explanation(conn, device_id, forecast_id, status, explanation=None):
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE public.air_quality_forecasts SET explanation_status = %s, explanation = %s "
            "WHERE device_id = %s AND forecast_id = %s::uuid",
            (status, explanation, device_id, forecast_id),
        )
    conn.commit()


def invalidate_forecast(conn, reason, device_id=None, status="unavailable"):
    where = "device_id = %s" if device_id is not None else "TRUE"
    params = [status, reason, Json({}), Json({}), "unavailable"]
    if device_id is not None:
        params.append(device_id)
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE public.air_quality_forecasts SET status = %s, reason = %s, "
            "predicted_values = %s, severity = %s, explanation_status = %s, "
            f"explanation = NULL WHERE {where}", params,
        )
    conn.commit()


def invalidate_stale_forecasts(conn, now):
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE public.air_quality_forecasts SET status = 'unavailable', "
            "reason = 'Latest reading is stale', predicted_values = %s, severity = %s, "
            "explanation_status = 'unavailable', explanation = NULL "
            "WHERE status = 'available' AND input_cutoff_at < %s",
            (Json({}), Json({}), now - timedelta(seconds=FRESHNESS_SECONDS)),
        )
    conn.commit()


def acquire_worker_lock(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (739_783_247_071_623_201,))
        return bool(cursor.fetchone()[0])


def call_qwen(predictions, severity, policy_text, target_at):
    prompt = (
        "Use the trusted policy below to explain this two-hour model forecast in 1-3 concise sentences. "
        "Use at most 70 words and summarize the most concerning channels; all numeric forecasts are displayed separately. "
        "Keep every supplied value and category unchanged. Clearly call it a forecast, include the target time, "
        "do not claim any action occurred, and only recommend checks supported by the policy.\n\n"
        f"Trusted policy:\n{policy_text}\n\n"
        f"Target time (UTC): {target_at.astimezone(timezone.utc).isoformat()}\n"
        f"Forecast: {json.dumps(predictions, sort_keys=True)}\n"
        f"Categories: {json.dumps(severity, sort_keys=True)}"
    )
    payload = json.dumps({
        "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"),
        "prompt": prompt + "\nReturn JSON only as {\"message\": \"your explanation\"}.",
        "stream": False,
        "format": {"type": "object", "properties": {"message": {"type": "string"}},
                   "required": ["message"], "additionalProperties": False},
        "options": {"temperature": 0, "num_predict": 160},
    }).encode()
    try:
        base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11435").rstrip("/")
        request = urllib.request.Request(
            f"{base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        timeout = float(os.environ.get("OLLAMA_TIMEOUT", "90"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("OLLAMA_TIMEOUT must be a finite positive number")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise QwenUnavailable(str(exc)) from exc
    if not isinstance(result, dict) or result.get("error") or result.get("done") is not True:
        raise QwenUnavailable("Ollama returned an invalid response")
    try:
        structured = json.loads(result["response"])
        explanation = structured["message"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise QwenUnavailable("Ollama returned an invalid explanation") from exc
    if not isinstance(explanation, str) or not explanation.strip():
        raise QwenUnavailable("Ollama returned no explanation")
    explanation = explanation.strip()
    if len(explanation) > 2000:
        raise QwenUnavailable("Ollama explanation exceeds 2000 characters")
    return explanation


def process_device(conn, device_id, model=predictor, now=None, policy_result=None, qwen=call_qwen):
    fixed_now = now
    rows = fetch_history(conn, device_id, model)
    now = now or utc_now()
    validate_history(rows, model, now)
    source_hash = history_hash(rows, model)
    current = fetch_current_forecast(conn, device_id)
    conn.commit()
    policy, policy_error = policy_result if policy_result is not None else load_policy_result()

    same_numeric = bool(
        current and current["status"] == "available"
        and current["source_hash"] == source_hash
        and current["model_version"] == model.MODEL_VERSION
    )
    if same_numeric:
        predictions = validate_prediction(dict(current["predicted_values"]), model)
        forecast_id = str(current["forecast_id"])
        target_at = current["target_at"]
        if not policy_error and current["policy_sha256"] == policy.sha256 and current["explanation_status"] == "available":
            return "duplicate"
        if not policy_error:
            severity, policy_error = classify(policy, predictions)
        if policy_error:
            if current["explanation_status"] != "error" or current["reason"] != policy_error:
                record = dict(current)
                record.update(
                    forecast_id=forecast_id, reason=policy_error,
                    severity=unavailable_severity(model.TARGET_COLUMNS),
                    explanation_status="error", explanation=None,
                    policy_sha256=policy.sha256 if policy else None, predicted_values=predictions,
                )
                save_forecast(conn, record)
            return "policy_error"
        set_policy_pending(conn, device_id, forecast_id, severity, policy.sha256)
    else:
        predictions = validate_prediction(model.predict(rows), model)
        target_at = rows[-1]["recorded_at"] + timedelta(minutes=model.HORIZON_MINUTES)
        forecast_id = str(uuid.uuid4())
        if policy_error:
            severity = unavailable_severity(model.TARGET_COLUMNS)
            explanation_status = "error"
        else:
            severity, policy_error = classify(policy, predictions)
            explanation_status = "error" if policy_error else "pending"
        save_forecast(conn, {
            "device_id": device_id,
            "forecast_id": forecast_id,
            "input_row_id": rows[-1]["id"],
            "input_cutoff_at": rows[-1]["recorded_at"],
            "target_at": target_at,
            "generated_at": fixed_now or utc_now(),
            "model_version": model.MODEL_VERSION,
            "status": "available",
            "reason": policy_error,
            "predicted_values": predictions,
            "severity": severity,
            "explanation_status": explanation_status,
            "explanation": None,
            "policy_sha256": policy.sha256 if policy else None,
            "source_hash": source_hash,
        })
        if policy_error:
            return "policy_error"

    try:
        policy_text = policy.text
        if device_id.startswith("sim-farm-"):
            policy_text = "Input source: SIMULATED farm readings, not physical sensor measurements. State that this is a simulation.\n\n" + policy_text
        explanation = qwen(predictions, severity, policy_text, target_at)
    except QwenUnavailable as exc:
        print(json.dumps({"device_id": device_id, "reasoning": "unavailable", "reason": str(exc)}),
              file=sys.stderr, flush=True)
        update_explanation(conn, device_id, forecast_id, "error")
        return "explanation_error"
    update_explanation(conn, device_id, forecast_id, "available", explanation)
    return "created" if not same_numeric else "explanation_retried"


def run_once(connection_factory=database_connection, model=predictor, now=None, qwen=call_qwen, simulation=None):
    cycle_now = now or utc_now()
    results = {}
    with closing(connection_factory()) as conn:
        if not acquire_worker_lock(conn):
            status = "unavailable" if simulation else "skipped"
            return {"_worker": f"{status}: another forecast worker is running"}
        try:
            validate_model_contract(model)
        except (ContractError, predictor.ModelUnavailable) as exc:
            invalidate_forecast(conn, str(exc))
            return {"_worker": f"unavailable: {exc}"}
        if simulation is not None:
            from ai_model.simulation import write_history

            seeded = write_history(conn, simulation, cycle_now)
            results["_simulation"] = {
                **seeded, "latest_at": seeded["latest_at"].isoformat(), "profile": simulation,
            }
        policy_result = load_policy_result()
        invalidate_stale_forecasts(conn, cycle_now)
        devices = [seeded["device_id"]] if simulation is not None else fetch_active_devices(conn, cycle_now)
        conn.commit()
        for device_id in devices:
            try:
                results[device_id] = process_device(conn, device_id, model, now, policy_result, qwen)
            except (ContractError, predictor.ModelUnavailable) as exc:
                conn.rollback()
                invalidate_forecast(conn, str(exc), device_id)
                results[device_id] = f"unavailable: {exc}"
            except Exception as exc:
                conn.rollback()
                try:
                    invalidate_forecast(conn, "Prediction failed", device_id, "error")
                except Exception:
                    conn.rollback()
                results[device_id] = f"error: {exc}"
    return results


def initialize_database(connection_factory=database_connection):
    with closing(connection_factory()) as conn:
        with conn.cursor() as cursor:
            cursor.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="process available devices once and exit")
    parser.add_argument("--init-db", action="store_true", help="create the forecast table and query index")
    parser.add_argument("--simulate", choices=("good", "mid", "bad"),
                        help="insert labelled demo history and forecast it without a sensor device")
    args = parser.parse_args(argv)
    try:
        if args.init_db:
            try:
                initialize_database()
            except Exception as exc:
                print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
                return 1
            else:
                print(json.dumps({"status": "initialized"}))
                return 0
        while True:
            started = time.monotonic()
            try:
                results = run_once(simulation=args.simulate) if args.simulate else run_once()
                print(json.dumps({"at": utc_now().isoformat(), "devices": results}), flush=True)
                if args.simulate and results.get("_worker", "").startswith("unavailable:"):
                    return 1
            except (ContractError, predictor.ModelUnavailable, RuntimeError) as exc:
                print(json.dumps({"status": "unavailable", "reason": str(exc)}), file=sys.stderr, flush=True)
                if args.once:
                    return 1
            except Exception as exc:
                print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr, flush=True)
                if args.once:
                    return 1
            if args.once:
                return int(any(isinstance(result, str) and result.startswith(("unavailable:", "error:"))
                               for result in results.values()))
            time.sleep(max(0, POLL_SECONDS - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
