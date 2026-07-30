-- One-time migration: stage capture GPS in SDI package tables for Planon export.
--
-- Active backend is PostgreSQL after the C4 cutover. Run this as the table
-- owner/developer role, not the application role:
--
--   pg_dump -h 127.0.0.1 -p 5433 -U developer -d qr_code_db \
--     -Fc \
--     --table='public.sdi_print_out' \
--     --table='public.sdi_print_out_arch' \
--     -f /home/developer/QR_database/backups/sdi_package_tables_pre_gps_$(date +%Y%m%d_%H%M%S).dump
--   psql -h 127.0.0.1 -p 5433 -U developer -d qr_code_db -v ON_ERROR_STOP=1 \
--     -f 2026-06-23_sdi_package_gps_coordinates.sql
--
-- The application role has DML grants only; keep this DDL in owner-run migrations.

ALTER TABLE public.sdi_print_out
    ADD COLUMN IF NOT EXISTS "GPS Coordinates (lat,long)" TEXT;

ALTER TABLE public.sdi_print_out_arch
    ADD COLUMN IF NOT EXISTS "GPS Coordinates (lat,long)" TEXT;

-- The package column is only staging. QR_codes remains the source of truth,
-- and SDI_process/app.py refreshes the exported value from QR_codes at read/export time.

-- Verify:
--   SELECT table_name, column_name, data_type
--   FROM information_schema.columns
--   WHERE table_schema='public'
--     AND table_name IN ('sdi_print_out','sdi_print_out_arch')
--     AND column_name = 'GPS Coordinates (lat,long)'
--   ORDER BY table_name;
