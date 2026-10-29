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

CREATE INDEX IF NOT EXISTS air_quality_logs_device_recorded_id_idx
    ON public.air_quality_logs (device_id, recorded_at DESC, id DESC);

COMMIT;
