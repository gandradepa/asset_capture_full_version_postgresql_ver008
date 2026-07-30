-- One-time migration: merged review-display GPS coordinate field on QR_codes.
--
-- Active backend is PostgreSQL after the C4 cutover. Run this as the table
-- owner/developer role, not the application role:
--
--   pg_dump -h 127.0.0.1 -p 5433 -U developer -d qr_code_db \
--     -Fc -f /home/developer/QR_database/backups/qr_code_db_pre_gps_coordinates_$(date +%Y%m%d_%H%M%S).dump
--   psql "$QR_PG_DSN_AS_DEVELOPER" -v ON_ERROR_STOP=1 \
--     -f 2026-06-23_qr_codes_gps_coordinates.sql
--
-- The app role has DML grants only; keep all DDL in owner-run migrations.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'QR_codes'
          AND column_name = 'capture_latitude'
    ) OR NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'QR_codes'
          AND column_name = 'capture_longitude'
    ) THEN
        RAISE EXCEPTION 'QR_codes.capture_latitude and capture_longitude must exist before adding GPS Coordinates (lat,long)';
    END IF;
END $$;

ALTER TABLE "QR_codes"
    ADD COLUMN IF NOT EXISTS "GPS Coordinates (lat,long)" TEXT;

UPDATE "QR_codes"
SET "GPS Coordinates (lat,long)" =
    CASE
        WHEN NULLIF(BTRIM(capture_latitude), '') IS NOT NULL
         AND NULLIF(BTRIM(capture_longitude), '') IS NOT NULL
        THEN BTRIM(capture_latitude) || ',' || BTRIM(capture_longitude)
        ELSE ''
    END
WHERE COALESCE("GPS Coordinates (lat,long)", '') IS DISTINCT FROM
    CASE
        WHEN NULLIF(BTRIM(capture_latitude), '') IS NOT NULL
         AND NULLIF(BTRIM(capture_longitude), '') IS NOT NULL
        THEN BTRIM(capture_latitude) || ',' || BTRIM(capture_longitude)
        ELSE ''
    END;

-- Verify:
--   \d "QR_codes"
--   SELECT "QR_code_ID", capture_latitude, capture_longitude, "GPS Coordinates (lat,long)"
--   FROM "QR_codes"
--   WHERE NULLIF(BTRIM(capture_latitude), '') IS NOT NULL
--     AND NULLIF(BTRIM(capture_longitude), '') IS NOT NULL
--   LIMIT 10;
