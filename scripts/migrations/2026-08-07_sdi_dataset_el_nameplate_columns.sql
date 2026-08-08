-- Add the EL General nameplate columns to sdi_dataset_EL.
--
-- The four TEXT columns hold ME-style nameplate values (Manufacturer, Model,
-- Serial Number, Year) for General (non-distribution) electrical assets
-- captured by the EL review form's General variant. Distribution rows are
-- intentionally left blank by the application sync. Column names match the
-- structured JSON keys, following the sdi_dataset_EL convention (do not copy
-- ME's "Serial Number" -> "Serial" rename; the SDI process renames at
-- packaging time instead).
--
-- Run as the PostgreSQL table-owner role after taking and verifying a pg_dump:
--
-- Local:
--   psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
--     -U postgres -d qr_code_db \
--     -f scripts/migrations/2026-08-07_sdi_dataset_el_nameplate_columns.sql
--
-- VM:
--   psql -X -v ON_ERROR_STOP=1 -h /tmp -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-08-07_sdi_dataset_el_nameplate_columns.sql
--
-- The migration is transactional and idempotent. If a pre-existing column has
-- an incompatible type it aborts without changing the table. Pure-DDL
-- migrations write no audit_trail rows (chk_audit_optype allows only
-- INSERT/UPDATE/DELETE; same convention as 2026-08-04_asset_group_elec_dist_setup.sql):
-- the schema history lives in this script and the column comments.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE "sdi_dataset_EL" IN SHARE ROW EXCLUSIVE MODE;

DO $migrate$
DECLARE
    col text;
    existing_data_type text;
BEGIN
    FOREACH col IN ARRAY ARRAY['Manufacturer', 'Model', 'Serial Number', 'Year']
    LOOP
        SELECT data_type
          INTO existing_data_type
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'sdi_dataset_EL'
           AND column_name = col;

        IF FOUND THEN
            IF existing_data_type <> 'text' THEN
                RAISE EXCEPTION
                    'sdi_dataset_EL.% has incompatible definition: type %',
                    col,
                    existing_data_type;
            END IF;
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE "sdi_dataset_EL" ADD COLUMN %I TEXT', col);
    END LOOP;
END
$migrate$;

COMMENT ON COLUMN "sdi_dataset_EL"."Manufacturer" IS
    'EL General nameplate field from the -0 Asset Plate photo; Distribution rows intentionally blank. Exported to Planon as Make (2026-08-07).';

COMMENT ON COLUMN "sdi_dataset_EL"."Model" IS
    'EL General nameplate field from the -0 Asset Plate photo; Distribution rows intentionally blank. Exported to Planon as Model (2026-08-07).';

COMMENT ON COLUMN "sdi_dataset_EL"."Serial Number" IS
    'EL General nameplate field from the -0 Asset Plate photo; Distribution rows intentionally blank. Renamed to Serial in the SDI package, exported to Planon as Serial Number (2026-08-07).';

COMMENT ON COLUMN "sdi_dataset_EL"."Year" IS
    'EL General nameplate field from the -0 Asset Plate photo; Distribution rows intentionally blank. Exported to Planon as Date Of Manufacture Or Construction (2026-08-07).';

COMMIT;

-- Verify:
--   SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name = 'sdi_dataset_EL'
--     AND column_name IN ('Manufacturer', 'Model', 'Serial Number', 'Year')
--   ORDER BY column_name;
--
--   SELECT COUNT(*) AS total_rows,
--          COUNT(*) FILTER (WHERE COALESCE("Manufacturer", '') <> '') AS with_manufacturer
--   FROM "sdi_dataset_EL";
--
-- Rollback: restore the verified pre-migration pg_dump. Dropping the columns
-- is intentionally not automated because reviewer-entered nameplate values
-- would be lost.
