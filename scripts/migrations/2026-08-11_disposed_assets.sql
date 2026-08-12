-- Create the disposed_assets archive table for the Dashboard "Disposed" tool.
--
-- One row per disposal event, so a dispose -> restore -> dispose cycle keeps its
-- full history. "Disposed" is defined as active membership in this table
-- (status = 'disposed'); the partial unique index enforces at most one active
-- disposal per QR. The review apps, capture app and AI interpreters subtract
-- that set instead of reading a flag on QR_codes, mirroring how the review
-- dashboards already hide sdi_print_out_arch members.
--
-- The snapshot columns hold JSON text captured at disposal time (the deleted
-- sdi_dataset / sdi_dataset_EL row, the QR_codes row, its QR_code_assets rows,
-- the Output_jason_api payloads and the photo filenames) so Restore can rebuild
-- the curated row without touching any file on disk.
--
-- There is deliberately NO foreign key to QR_codes: the C3 model cascades
-- QR_codes deletes to every child table, and the disposal archive must survive
-- scripts/purge_qr_from_db.py.
--
-- Run as the PostgreSQL table-owner role after taking and verifying a pg_dump:
--
-- Local:
--   psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
--     -U postgres -d qr_code_db \
--     -f scripts/migrations/2026-08-11_disposed_assets.sql
--
-- VM:
--   psql -X -v ON_ERROR_STOP=1 -h /tmp -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-08-11_disposed_assets.sql
--
-- The migration is transactional and idempotent. If the table already exists
-- with an incompatible definition it aborts without changing anything.
-- Pure-DDL migrations write no audit_trail rows (chk_audit_optype allows only
-- INSERT/UPDATE/DELETE; same convention as
-- 2026-08-07_sdi_dataset_el_nameplate_columns.sql): the schema history lives in
-- this script and the table/column comments.

\set ON_ERROR_STOP on

BEGIN;

DO $migrate$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'public'
           AND table_name = 'disposed_assets'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'disposed_assets'
               AND column_name = 'qr_code'
        ) THEN
            RAISE EXCEPTION
                'disposed_assets already exists with an incompatible definition (no qr_code column)';
        END IF;
        RETURN;
    END IF;

    CREATE TABLE "disposed_assets" (
        "id"                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        "qr_code"             text NOT NULL,
        "building_code"       text NOT NULL DEFAULT '',
        "asset_type"          text NOT NULL DEFAULT '',
        "reason"              text NOT NULL,
        "notes"               text NOT NULL DEFAULT '',
        "disposed_by"         text NOT NULL,
        "disposed_at"         text NOT NULL,
        "sdi_table"           text NOT NULL DEFAULT '',
        "sdi_row_json"        text NOT NULL DEFAULT '',
        "qr_codes_row_json"   text NOT NULL DEFAULT '',
        "qr_code_assets_json" text NOT NULL DEFAULT '',
        "structured_json"     text NOT NULL DEFAULT '',
        "et_json"             text NOT NULL DEFAULT '',
        "photos_json"         text NOT NULL DEFAULT '',
        "status"              text NOT NULL DEFAULT 'disposed',
        "restored_by"         text NOT NULL DEFAULT '',
        "restored_at"         text NOT NULL DEFAULT '',
        CONSTRAINT chk_disposed_reason
            CHECK ("reason" IN ('Decommissioned', 'Duplicated', 'User Request')),
        CONSTRAINT chk_disposed_status
            CHECK ("status" IN ('disposed', 'restored')),
        CONSTRAINT chk_disposed_sdi_table
            CHECK ("sdi_table" IN ('sdi_dataset', 'sdi_dataset_EL', ''))
    );

    CREATE UNIQUE INDEX ux_disposed_assets_active
        ON "disposed_assets" ("qr_code")
     WHERE "status" = 'disposed';

    CREATE INDEX ix_disposed_assets_qr
        ON "disposed_assets" ("qr_code");
END
$migrate$;

COMMENT ON TABLE "disposed_assets" IS
    'Disposal archive for the Dashboard Disposed tool (2026-08-11). One row per disposal event; status=disposed marks the QR as hidden from the review apps, the capture app and the AI cron. Snapshots preserve the deleted curated row and companion JSON so Restore can reinstate it. No FK to QR_codes by design so the archive survives scripts/purge_qr_from_db.py.';

COMMENT ON COLUMN "disposed_assets"."asset_type" IS
    'Normalized discipline of the disposed asset (ME, BF or EL); blank when QR_codes.asset_type was unset.';

COMMENT ON COLUMN "disposed_assets"."sdi_table" IS
    'Curated table the deleted row came from: sdi_dataset (ME/BF), sdi_dataset_EL (EL), or blank when the QR was never approved and therefore had no curated row.';

COMMENT ON COLUMN "disposed_assets"."sdi_row_json" IS
    'JSON snapshot of the deleted sdi_dataset / sdi_dataset_EL row (blank when the QR never had one). Restore rebuilds the row from this payload, excluding the generated ID_check column.';

COMMENT ON COLUMN "disposed_assets"."qr_codes_row_json" IS
    'JSON snapshot of the QR_codes row at disposal time, including the pre-disposal ai_status that Restore writes back.';

COMMENT ON COLUMN "disposed_assets"."photos_json" IS
    'JSON array of Capture_photos_upload filenames present at disposal time. Photos are never moved or deleted; the register re-probes the disk when displaying them.';

COMMENT ON COLUMN "disposed_assets"."status" IS
    'disposed = the QR is currently withdrawn from the pipeline; restored = the disposal was reversed and the row is kept for history.';

-- App-role privileges. c4_grants.sql sets ALTER DEFAULT PRIVILEGES granting
-- SELECT/INSERT/UPDATE/DELETE on new tables to assetcap_app; the disposal
-- archive is append-only for the application, so DELETE is revoked explicitly.
-- The grants are also stated explicitly so this migration is complete on a
-- database where c4_grants.sql was never run. They are skipped when the role
-- is absent: local developer databases run as postgres and have no
-- assetcap_app role, and the grants would otherwise abort the migration.
DO $grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assetcap_app') THEN
        GRANT SELECT, INSERT, UPDATE ON "disposed_assets" TO assetcap_app;
        REVOKE DELETE ON "disposed_assets" FROM assetcap_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO assetcap_app;
    ELSE
        RAISE NOTICE 'Role assetcap_app not present; skipping application grants.';
    END IF;
END
$grants$;

COMMIT;

-- Verify:
--   \d "disposed_assets"
--
--   SELECT indexname, indexdef
--   FROM pg_indexes
--   WHERE tablename = 'disposed_assets'
--   ORDER BY indexname;
--
--   SELECT has_table_privilege('assetcap_app', 'disposed_assets', 'SELECT') AS can_select,
--          has_table_privilege('assetcap_app', 'disposed_assets', 'INSERT') AS can_insert,
--          has_table_privilege('assetcap_app', 'disposed_assets', 'UPDATE') AS can_update,
--          has_table_privilege('assetcap_app', 'disposed_assets', 'DELETE') AS can_delete;
--   -- expected: t, t, t, f
--
-- Rollback: DROP TABLE "disposed_assets"; -- only while the table is still
-- empty. Once disposals have been recorded, restore the verified
-- pre-migration pg_dump instead so the disposal history is not lost.
