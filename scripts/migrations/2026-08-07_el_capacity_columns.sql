-- Add the EL General "Capacity" / "Capacity (UoM)" columns to sdi_dataset_EL
-- and to the SDI package tables (sdi_print_out, sdi_print_out_arch).
--
-- Capacity is an OPTIONAL nameplate field on the EL General review form
-- (2026-08-07 follow-up): captured by AI from the -0 Asset Plate, editable in
-- review, stored bare-value + unit per the EL rating storage convention, and
-- carried through SDI packaging to the Planon export columns "Capacity" /
-- "Capacity UoM". Distribution rows are intentionally left blank by the
-- application sync. It does NOT participate in completeness scoring or the
-- review traffic light.
--
-- The package tables need the columns because package creation INSERTs every
-- PRINT_OUT_COLS column explicitly. If a package table does not exist yet the
-- migration skips it: the SDI app's CREATE TABLE IF NOT EXISTS builds new
-- tables from PRINT_OUT_COLS, which includes the new columns.
--
-- Run as the PostgreSQL table-owner role after taking and verifying a pg_dump:
--
-- Local:
--   psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
--     -U postgres -d qr_code_db \
--     -f scripts/migrations/2026-08-07_el_capacity_columns.sql
--
-- VM:
--   psql -X -v ON_ERROR_STOP=1 -h /tmp -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-08-07_el_capacity_columns.sql
--
-- The migration is transactional and idempotent. If a pre-existing column has
-- an incompatible type it aborts without changing the tables. Pure-DDL
-- migrations write no audit_trail rows (chk_audit_optype allows only
-- INSERT/UPDATE/DELETE; same convention as 2026-08-07_sdi_dataset_el_nameplate_columns.sql):
-- the schema history lives in this script and the column comments.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE "sdi_dataset_EL" IN SHARE ROW EXCLUSIVE MODE;

DO $migrate$
DECLARE
    tbl text;
    col text;
    existing_data_type text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['sdi_dataset_EL', 'sdi_print_out', 'sdi_print_out_arch']
    LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            RAISE NOTICE '% does not exist; skipping (created on demand with the new columns)', tbl;
            CONTINUE;
        END IF;

        FOREACH col IN ARRAY ARRAY['Capacity', 'Capacity (UoM)']
        LOOP
            SELECT data_type
              INTO existing_data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = col;

            IF FOUND THEN
                IF existing_data_type <> 'text' THEN
                    RAISE EXCEPTION
                        '%.% has incompatible definition: type %',
                        tbl,
                        col,
                        existing_data_type;
                END IF;
                CONTINUE;
            END IF;

            EXECUTE format('ALTER TABLE %I ADD COLUMN %I TEXT', tbl, col);
        END LOOP;

        EXECUTE format(
            'COMMENT ON COLUMN %I."Capacity" IS %L',
            tbl,
            'EL General optional nameplate field (bare value; unit in "Capacity (UoM)") from the -0 Asset Plate photo; Distribution rows intentionally blank; not part of completeness scoring. Exported to Planon column "Capacity" (2026-08-07).'
        );
        EXECUTE format(
            'COMMENT ON COLUMN %I."Capacity (UoM)" IS %L',
            tbl,
            'Unit for the EL General Capacity value, stored as printed on the nameplate (kVA, kW, HP, A, BTU, ...); no fixed code list unlike AMP/VLT. Exported to Planon column "Capacity UoM" (2026-08-07).'
        );
    END LOOP;
END
$migrate$;

COMMIT;

-- Verify:
--   SELECT table_name, column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name IN ('sdi_dataset_EL', 'sdi_print_out', 'sdi_print_out_arch')
--     AND column_name IN ('Capacity', 'Capacity (UoM)')
--   ORDER BY table_name, column_name;
--
--   SELECT COUNT(*) AS total_rows,
--          COUNT(*) FILTER (WHERE COALESCE("Capacity", '') <> '') AS with_capacity
--   FROM "sdi_dataset_EL";
--
-- Rollback: restore the verified pre-migration pg_dump. Dropping the columns
-- is intentionally not automated because reviewer-entered capacity values
-- would be lost.
