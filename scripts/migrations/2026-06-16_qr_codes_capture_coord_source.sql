-- One-time migration: provenance of the per-capture GPS coordinates on QR_codes.
--
-- Adds capture_coord_source so a building-centroid fallback is never mistaken
-- for a precise device fix. Values written by app.py POST /submit:
--   'device'   - precise GPS from the phone's Geolocation API
--   'building' - approximate centroid from "UBC - All Properties List with GPS Coordinates"
--   '' / NULL  - no coordinate captured
--
-- Pairs with 2026-06-16_qr_codes_capture_coords.sql (capture_latitude/longitude).
-- Run ONCE on the VM as the table owner ('developer'):
--   psql "$QR_PG_DSN_AS_DEVELOPER" -f 2026-06-16_qr_codes_capture_coord_source.sql

ALTER TABLE "QR_codes" ADD COLUMN IF NOT EXISTS capture_coord_source TEXT;

-- Verify:
--   \d "QR_codes"   -> should list capture_coord_source (text)
--
-- SQLite frozen-rollback equivalent (no IF NOT EXISTS in SQLite ALTER; run once
-- and ignore "duplicate column name" if already present):
--   ALTER TABLE "QR_codes" ADD COLUMN capture_coord_source TEXT;
