-- Repeatable setup; existing tables and readings are preserved.
CREATE TABLE IF NOT EXISTS air_quality_logs (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    device_id VARCHAR(100),
    temperature_c DOUBLE PRECISION,
    humidity_perc DOUBLE PRECISION,
    co2_ppm DOUBLE PRECISION,
    nh3_ppm DOUBLE PRECISION,
    pm25_ugm3 DOUBLE PRECISION,
    mq135_ppm DOUBLE PRECISION,
    mq137_ppm DOUBLE PRECISION
);
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS device_id VARCHAR(100);
CREATE INDEX IF NOT EXISTS air_quality_logs_recorded_at_idx ON air_quality_logs (recorded_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'viewer'))
);
