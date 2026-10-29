"""Adapter between database readings and the supplied GRU forecast bundle."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


class ModelUnavailable(RuntimeError):
    pass


MODEL_SHA256 = "eaef59203a1654e4c51c626420f0a558e3aab939c30b881bcaeaad370763b959"
MODEL_PATH = Path(__file__).with_name("microclimate_forecaster.joblib")
MODEL_VERSION = f"gru-v1-{MODEL_SHA256[:12]}"
LOOKBACK_SAMPLES = 120
FEATURE_COLUMNS = (
    "temperature_c", "humidity_perc", "co2_ppm", "pm1_ugm3",
    "pm25_ugm3", "pm10_ugm3", "mq4_ppm", "mq137_ppm",
)
TARGET_COLUMNS = FEATURE_COLUMNS
MODEL_COLUMNS = (
    "Temp (°C)", "Humidity (%)", "CO2 (ppm)", "PM1.0 (µg/m³)",
    "PM2.5 (µg/m³)", "PM10 (µg/m³)", "Methane (ppm)", "Ammonia (ppm)",
)
SAMPLE_INTERVAL_SECONDS = 60
CADENCE_TOLERANCE_SECONDS = 0
HORIZON_MINUTES = 120


def _invalid(message):
    raise ModelUnavailable(message)


def _validate_rows(history_rows):
    if not isinstance(history_rows, (list, tuple)) or len(history_rows) != LOOKBACK_SAMPLES:
        _invalid(f"Exactly {LOOKBACK_SAMPLES} history rows are required")
    devices = {row.get("device_id") for row in history_rows}
    device = next(iter(devices), None)
    if len(devices) != 1 or not isinstance(device, str) or not device.strip():
        _invalid("History must belong to one identified device")

    times = []
    values = []
    previous = None
    for row in history_rows:
        timestamp = row.get("recorded_at")
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            _invalid("Reading timestamps must include a timezone")
        utc_timestamp = timestamp.astimezone(timezone.utc)
        if utc_timestamp.second or utc_timestamp.microsecond:
            _invalid("Reading timestamps must be aligned to UTC minute boundaries")
        if previous is not None and (timestamp - previous).total_seconds() != SAMPLE_INTERVAL_SECONDS:
            _invalid("Input readings must be consecutive one-minute samples")
        previous = timestamp
        times.append(timestamp)

        sample = []
        for column in FEATURE_COLUMNS:
            value = row.get(column)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                _invalid(f"{column} must be a finite number")
            sample.append(float(value))
        if not 0 <= sample[1] <= 100:
            _invalid("Humidity must be between 0 and 100")
        if any(value < 0 for value in sample[2:]):
            _invalid("Concentrations must be nonnegative")
        if not sample[3] <= sample[4] <= sample[5]:
            _invalid("Particulate readings must satisfy PM1 <= PM2.5 <= PM10")
        values.append(sample)
    return times, values


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _load_forecaster():
    if not MODEL_PATH.is_file():
        raise ModelUnavailable(f"Model artifact is missing: {MODEL_PATH.name}")
    try:
        checksum = _sha256(MODEL_PATH)
    except OSError as exc:
        raise ModelUnavailable("Model artifact could not be read") from exc
    if checksum != MODEL_SHA256:
        raise ModelUnavailable("Model artifact checksum does not match the supplied GRU")

    try:
        from ai_model import predict as runtime
    except ImportError as exc:
        raise ModelUnavailable("Model dependencies are missing; install ai_model/requirements.txt") from exc
    try:
        forecaster = runtime.load_forecaster(MODEL_PATH, device="cpu")
    except Exception as exc:
        raise ModelUnavailable(f"Model artifact could not be loaded ({type(exc).__name__})") from exc
    if tuple(forecaster["bundle"].get("sensors", ())) != MODEL_COLUMNS:
        raise ModelUnavailable("Model sensor order does not match the application contract")
    return runtime, forecaster


def predict(history_rows):
    """Return all eight sensor values exactly 120 sampling steps ahead."""
    times, values = _validate_rows(history_rows)
    runtime, forecaster = _load_forecaster()
    try:
        import pandas as pd
    except ImportError as exc:
        raise ModelUnavailable("Model dependencies are missing; install ai_model/requirements.txt") from exc

    index = pd.DatetimeIndex(times).tz_convert("UTC")
    frame = pd.DataFrame(values, index=index, columns=MODEL_COLUMNS)
    try:
        forecast = runtime.forecast_next_120_minutes(frame, forecaster)
    except (FloatingPointError, TypeError, ValueError) as exc:
        raise ModelUnavailable(f"Model rejected the input ({exc})") from exc
    expected_target = index[-1] + pd.Timedelta(minutes=HORIZON_MINUTES)
    if (list(forecast.columns) != list(MODEL_COLUMNS) or len(forecast) != HORIZON_MINUTES
            or forecast.index[-1] != expected_target):
        raise ModelUnavailable("Model returned an unexpected forecast shape or horizon")

    endpoint = [float(value) for value in forecast.iloc[-1].tolist()]
    if not all(math.isfinite(value) for value in endpoint):
        raise ModelUnavailable("Model returned a nonfinite forecast")
    return dict(zip(TARGET_COLUMNS, endpoint))
