-- One-time migration: per-capture device GPS coordinates on QR_codes.
--
-- Adds two nullable columns that the mobile Capture App (asset_capture_app_dev)
-- populates at POST /submit from the browser Geolocation API. They are
-- best-effort capture metadata (latitude/longitude in decimal degrees, stored
-- as TEXT to match the existing "UBC - All Properties List with GPS Coordinates"
-- reference table and the all-TEXT QR_codes convention).
--
-- Why a migration and not in-app DDL (incident 2026-06-11): the app connects as
-- 'assetcap_app' (DML grants only). ALTER TABLE requires table ownership and a
-- failed DDL aborts the whole /submit transaction. upsert_qr_codes() in app.py
-- feature-detects these columns, so it safely no-ops until they exist — meaning
-- coordinates are SILENTLY DROPPED if the app ships before this runs. Apply this
-- FIRST, then deploy the application change.
--
-- Run ONCE on the VM as the table owner ('developer'):
--   psql "$QR_PG_DSN_AS_DEVELOPER" -f 2026-06-16_qr_codes_capture_coords.sql
-- (or interactively as the 'developer' role, which owns the tables).
--
-- Lowercase, unquoted-safe identifiers are used deliberately to avoid the
-- mixed-case identifier-folding trap that broke columns post-cutover.

ALTER TABLE "QR_codes" ADD COLUMN IF NOT EXISTS capture_latitude  TEXT;
ALTER TABLE "QR_codes" ADD COLUMN IF NOT EXISTS capture_longitude TEXT;

-- Verify:
--   \d "QR_codes"   -> should list capture_latitude / capture_longitude (text)
--
-- SQLite frozen-rollback equivalent (only if reverting to the SQLite backend;
-- SQLite ALTER TABLE has no "IF NOT EXISTS" — run once and ignore a
-- "duplicate column name" error if the column is already present):
--   ALTER TABLE "QR_codes" ADD COLUMN capture_latitude TEXT;
--   ALTER TABLE "QR_codes" ADD COLUMN capture_longitude TEXT;
