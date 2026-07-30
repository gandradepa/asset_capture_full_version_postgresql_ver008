-- One-time migration: unique index backing the /submit dedupe semantics.
--
-- Background (incident 2026-06-11): asset_capture_app_dev/app.py used to run
-- this CREATE INDEX inside every POST /submit transaction. On PostgreSQL the
-- app connects as 'assetcap_app' (DML grants only); CREATE INDEX requires
-- table ownership, so the statement raised insufficient_privilege and aborted
-- the whole transaction — every mobile capture since the 2026-06-08 cutover
-- silently lost its DB rows. The DDL was removed from the request path; this
-- file replaces it.
--
-- Run ONCE on the VM as the table owner:
--   psql "$QR_PG_DSN_AS_DEVELOPER" -f 2026-06-11_ux_qr_code_assets.sql
-- (or paste interactively as the 'developer' role, which owns the tables).
--
-- Pre-check: must return 0 rows before creating the index.
SELECT "code_assets", COUNT(*)
FROM "QR_code_assets"
GROUP BY "code_assets"
HAVING COUNT(*) > 1;

CREATE UNIQUE INDEX IF NOT EXISTS "ux_QR_code_assets_code_assets"
    ON "QR_code_assets" ("code_assets");

-- Verify:
-- \d "QR_code_assets"  → should list ux_QR_code_assets_code_assets UNIQUE
