-- Add the electrical-distribution setup flag to the Asset_Group lookup.
--
-- The business values are the one-character flags Y and N (not PostgreSQL
-- TRUE/FALSE). Existing rows and future inserts default to N.
--
-- Run as the PostgreSQL table-owner role after taking and verifying a pg_dump:
--
-- Local:
--   psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
--     -U postgres -d qr_code_db \
--     -f scripts/migrations/2026-08-04_asset_group_elec_dist_setup.sql
--
-- VM:
--   psql -X -v ON_ERROR_STOP=1 -h /tmp -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-08-04_asset_group_elec_dist_setup.sql
--
-- The migration is transactional and idempotent. If a pre-existing column has
-- an incompatible type or contains a value other than Y/N, it aborts without
-- changing the table.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE "Asset_Group" IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
    existing_data_type text;
    existing_length integer;
BEGIN
    SELECT data_type, character_maximum_length
      INTO existing_data_type, existing_length
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'Asset_Group'
       AND column_name = 'elec_dist_setup';

    IF FOUND
       AND (existing_data_type <> 'character' OR existing_length <> 1) THEN
        RAISE EXCEPTION
            'Asset_Group.elec_dist_setup has incompatible definition: type %, length %',
            existing_data_type,
            existing_length;
    END IF;
END
$preflight$;

ALTER TABLE "Asset_Group"
    ADD COLUMN IF NOT EXISTS elec_dist_setup CHAR(1);

UPDATE "Asset_Group"
   SET elec_dist_setup = 'N'
 WHERE elec_dist_setup IS NULL;

DO $value_check$
DECLARE
    invalid_value_count bigint;
BEGIN
    SELECT COUNT(*)
      INTO invalid_value_count
      FROM "Asset_Group"
     WHERE elec_dist_setup NOT IN ('Y', 'N');

    IF invalid_value_count <> 0 THEN
        RAISE EXCEPTION
            'Asset_Group.elec_dist_setup contains % values outside Y/N',
            invalid_value_count;
    END IF;
END
$value_check$;

ALTER TABLE "Asset_Group"
    ALTER COLUMN elec_dist_setup SET DEFAULT 'N',
    ALTER COLUMN elec_dist_setup SET NOT NULL;

DO $constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public."Asset_Group"'::regclass
           AND conname = 'ck_asset_group_elec_dist_setup'
    ) THEN
        ALTER TABLE "Asset_Group"
            ADD CONSTRAINT ck_asset_group_elec_dist_setup
            CHECK (elec_dist_setup IN ('Y', 'N')) NOT VALID;
    END IF;
END
$constraint$;

ALTER TABLE "Asset_Group"
    VALIDATE CONSTRAINT ck_asset_group_elec_dist_setup;

COMMENT ON COLUMN "Asset_Group".elec_dist_setup IS
    'Y when electrical distribution setup applies; otherwise N.';

COMMIT;

-- Verify:
--   SELECT column_name, data_type, character_maximum_length, is_nullable,
--          column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name = 'Asset_Group'
--     AND column_name = 'elec_dist_setup';
--
--   SELECT elec_dist_setup, COUNT(*)
--   FROM "Asset_Group"
--   GROUP BY elec_dist_setup
--   ORDER BY elec_dist_setup;
--
-- Rollback: restore the verified pre-migration pg_dump. Dropping the column is
-- intentionally not automated because any later Y assignments would be lost.
