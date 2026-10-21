-- Repeatable setup and migration; existing readings are preserved except retired NH3.
BEGIN;
CREATE TABLE IF NOT EXISTS air_quality_logs (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    device_id VARCHAR(100),
    temperature_c DOUBLE PRECISION,
    humidity_perc DOUBLE PRECISION,
    co2_ppm DOUBLE PRECISION,
    pm1_ugm3 DOUBLE PRECISION,
    pm25_ugm3 DOUBLE PRECISION,
    pm10_ugm3 DOUBLE PRECISION
);
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS device_id VARCHAR(100);

CREATE TABLE IF NOT EXISTS air_quality_schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Before this migration, mq135_ppm/mq137_ppm and later mq135_raw/mq137_raw
-- all contained ADC readings. Archive them before creating the real ppm columns.
DO $$
DECLARE
    has_mq4_ppm BOOLEAN;
    has_mq135_ppm BOOLEAN;
    has_mq137_ppm BOOLEAN;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM air_quality_schema_migrations
        WHERE name = 'real_mq_ppm_columns_v1'
    ) THEN
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'air_quality_logs'
              AND column_name = 'mq4_ppm'
        ) INTO has_mq4_ppm;
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'air_quality_logs'
              AND column_name = 'mq135_ppm'
        ) INTO has_mq135_ppm;
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'air_quality_logs'
              AND column_name = 'mq137_ppm'
        ) INTO has_mq137_ppm;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                   AND table_name = 'air_quality_logs' AND column_name = 'mq135_raw')
           AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                       AND table_name = 'air_quality_logs' AND column_name = 'legacy_mq135_raw') THEN
            RAISE EXCEPTION 'Both mq135_raw and legacy_mq135_raw exist; resolve without discarding either column';
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                   AND table_name = 'air_quality_logs' AND column_name = 'mq137_raw')
           AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                       AND table_name = 'air_quality_logs' AND column_name = 'legacy_mq137_raw') THEN
            RAISE EXCEPTION 'Both mq137_raw and legacy_mq137_raw exist; resolve without discarding either column';
        END IF;
        IF has_mq135_ppm
           AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                       AND table_name = 'air_quality_logs'
                       AND column_name IN ('mq135_raw', 'legacy_mq135_raw')) THEN
            RAISE EXCEPTION 'Multiple legacy MQ135 columns exist; resolve without discarding either column';
        END IF;
        IF NOT has_mq4_ppm AND has_mq137_ppm AND NOT has_mq135_ppm THEN
            RAISE EXCEPTION 'Ambiguous mq137_ppm column; expected mq135_ppm beside the old raw data';
        END IF;
        IF NOT has_mq4_ppm AND has_mq137_ppm
           AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                       AND table_name = 'air_quality_logs'
                       AND column_name IN ('mq137_raw', 'legacy_mq137_raw')) THEN
            RAISE EXCEPTION 'Multiple legacy MQ137 columns exist; resolve without discarding either column';
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                   AND table_name = 'air_quality_logs' AND column_name = 'mq135_raw') THEN
            ALTER TABLE air_quality_logs RENAME COLUMN mq135_raw TO legacy_mq135_raw;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                   AND table_name = 'air_quality_logs' AND column_name = 'mq137_raw') THEN
            ALTER TABLE air_quality_logs RENAME COLUMN mq137_raw TO legacy_mq137_raw;
        END IF;

        -- The oldest schema created mq135_ppm and mq137_ppm as a pair, but both
        -- held raw ADC values. mq4_ppm marks the new ppm-estimate contract.
        IF has_mq135_ppm THEN
            ALTER TABLE air_quality_logs RENAME COLUMN mq135_ppm TO legacy_mq135_raw;
        END IF;
        IF NOT has_mq4_ppm AND has_mq135_ppm AND has_mq137_ppm THEN
                ALTER TABLE air_quality_logs RENAME COLUMN mq137_ppm TO legacy_mq137_raw;
        END IF;

        INSERT INTO air_quality_schema_migrations (name)
        VALUES ('real_mq_ppm_columns_v1');
    END IF;
END;
$$;
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS pm1_ugm3 DOUBLE PRECISION;
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS pm10_ugm3 DOUBLE PRECISION;
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS mq4_ppm DOUBLE PRECISION;
ALTER TABLE air_quality_logs ADD COLUMN IF NOT EXISTS mq137_ppm DOUBLE PRECISION;
-- This also deletes any stored NH3 values. Export/backup them before applying if needed.
ALTER TABLE air_quality_logs DROP COLUMN IF EXISTS nh3_ppm;
CREATE INDEX IF NOT EXISTS air_quality_logs_recorded_at_idx ON air_quality_logs (recorded_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'viewer'))
);
COMMIT;
