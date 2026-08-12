-- Add "Wrong Asset" to the disposal reason codes.
--
-- chk_disposed_reason (created by 2026-08-11_disposed_assets.sql) whitelists the
-- reasons the Disposed tool offers. The dropdown is served from
-- DISPOSAL_REASONS in Dashboard/disposed_assets_service.py, so the constraint
-- and that tuple must be widened together: a reason the constraint rejects
-- aborts the whole disposal transaction at COMMIT.
--
-- Run as the PostgreSQL table-owner role after taking and verifying a pg_dump:
--
-- Local:
--   psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
--     -U postgres -d qr_code_db \
--     -f scripts/migrations/2026-08-12_disposed_reason_wrong_asset.sql
--
-- VM:
--   psql -X -v ON_ERROR_STOP=1 -h /tmp -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-08-12_disposed_reason_wrong_asset.sql
--
-- The migration is transactional and idempotent: widening a CHECK constraint
-- only ever accepts more rows, so re-running it is a no-op and no existing row
-- can be invalidated. Pure-DDL migrations write no audit_trail rows
-- (chk_audit_optype allows only INSERT/UPDATE/DELETE; same convention as
-- 2026-08-11_disposed_assets.sql).

\set ON_ERROR_STOP on

BEGIN;

DO $migrate$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'public'
           AND table_name = 'disposed_assets'
    ) THEN
        RAISE EXCEPTION
            'disposed_assets does not exist; run 2026-08-11_disposed_assets.sql first';
    END IF;

    -- Already widened (constraint text carries the new value): nothing to do.
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = '"disposed_assets"'::regclass
           AND conname = 'chk_disposed_reason'
           AND pg_get_constraintdef(oid) LIKE '%Wrong Asset%'
    ) THEN
        RETURN;
    END IF;

    ALTER TABLE "disposed_assets" DROP CONSTRAINT IF EXISTS chk_disposed_reason;
    ALTER TABLE "disposed_assets" ADD CONSTRAINT chk_disposed_reason
        CHECK ("reason" IN ('Decommissioned', 'Duplicated', 'Wrong Asset', 'User Request'));
END
$migrate$;

COMMIT;

-- Verify:
--   SELECT pg_get_constraintdef(oid)
--   FROM pg_constraint
--   WHERE conrelid = '"disposed_assets"'::regclass
--     AND conname = 'chk_disposed_reason';
--   -- expected: CHECK ((reason = ANY (ARRAY['Decommissioned'::text,
--   --           'Duplicated'::text, 'Wrong Asset'::text, 'User Request'::text])))
--
-- Rollback (only while no row uses the new reason):
--   ALTER TABLE "disposed_assets" DROP CONSTRAINT chk_disposed_reason;
--   ALTER TABLE "disposed_assets" ADD CONSTRAINT chk_disposed_reason
--       CHECK ("reason" IN ('Decommissioned', 'Duplicated', 'User Request'));
