BEGIN;

CREATE TABLE IF NOT EXISTS public.air_quality_forecasts (
    device_id TEXT PRIMARY KEY CHECK (btrim(device_id) <> ''),
    forecast_id UUID NOT NULL,
    input_row_id BIGINT NOT NULL,
    input_cutoff_at TIMESTAMPTZ NOT NULL,
    target_at TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('available', 'unavailable', 'error')),
    reason TEXT,
    predicted_values JSONB,
    severity JSONB,
    explanation_status TEXT NOT NULL CHECK (
        explanation_status IN ('pending', 'available', 'unavailable', 'error')
    ),
    explanation TEXT,
    policy_sha256 TEXT,
    source_hash TEXT NOT NULL
);

ALTER TABLE public.air_quality_forecasts ADD COLUMN IF NOT EXISTS last_guidance JSONB;

UPDATE public.air_quality_forecasts
SET last_guidance = jsonb_build_object(
    'text', explanation, 'input_cutoff_at', input_cutoff_at,
    'target_at', target_at, 'model_version', model_version,
    'policy_sha256', policy_sha256
)
WHERE last_guidance IS NULL AND status = 'available'
    AND explanation_status = 'available' AND btrim(explanation) <> '';

CREATE INDEX IF NOT EXISTS air_quality_logs_device_recorded_id_idx
    ON public.air_quality_logs (device_id, recorded_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS public.air_quality_prediction_requests (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    request_id UUID NOT NULL,
    device_id TEXT NOT NULL CHECK (btrim(device_id) <> ''),
    input_row_id BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'error')),
    requested_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error TEXT
);

COMMIT;
