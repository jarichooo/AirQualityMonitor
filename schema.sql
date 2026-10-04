-- Repeatable setup and migration; existing tables and readings are preserved.
BEGIN;
CREATE TABLE IF NOT EXISTS air_quality_logs (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    device_id VARCHAR(100),
    temperature_c DOUBLE PRECISION,
    humidity_perc DOUBLE PRECISION,
    co2_ppm DOUBLE PRECISION,
    nh3_ppm DOUBLE PRECISION,
    pm1_ugm3 DOUBLE PRECISION,
    pm25_ugm3 DOUBLE PRECISION,
    pm10_ugm3 DOUBLE PRECISION,
    mq135_raw DOUBLE PRECISION,
    mq137_raw DOUBLE PRECISION
);
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS device_id VARCHAR(100);
-- Earlier MQ columns were mislabeled ppm. Rename without converting values.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
               AND table_name = 'air_quality_logs' AND column_name = 'mq135_ppm') THEN
        ALTER TABLE air_quality_logs RENAME COLUMN mq135_ppm TO mq135_raw;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
               AND table_name = 'air_quality_logs' AND column_name = 'mq137_ppm') THEN
        ALTER TABLE air_quality_logs RENAME COLUMN mq137_ppm TO mq137_raw;
    END IF;
END;
$$;
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS pm1_ugm3 DOUBLE PRECISION;
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS pm10_ugm3 DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS air_quality_logs_recorded_at_idx ON air_quality_logs (recorded_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'viewer'))
);
COMMIT;
