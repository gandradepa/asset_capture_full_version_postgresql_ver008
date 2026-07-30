-- Add and populate Buildings property metadata from:
--   UBC - All Properties List with GPS Coordinates.xlsx
--   Sheet 1, range A1:S2015
--   SHA-256: 678c6e472a6f0d37bd81e1e0bdddc8e69e33570140ded97877ae9cfee6b4334f
--
-- The validated staging CSV must be present on the VM at:
--   /tmp/buildings_metadata_678c6e472a6f.csv
--
-- Run as the PostgreSQL table owner after taking and verifying a pg_dump:
--   psql -X -v ON_ERROR_STOP=1 -h /tmp -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-07-27_buildings_property_metadata.sql
--
-- Expected first-run result:
--   2,014 staged workbook rows
--   327 matched/updated Buildings rows
--   1,687 workbook-only rows ignored
--   2,644 field-level audit_trail rows
--
-- The migration is transactional and idempotent. Existing equal values are
-- retained. Any non-null conflicting Buildings value aborts the transaction.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE "Buildings" IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE buildings_metadata_stage (
    "Code"                TEXT PRIMARY KEY,
    "Alternative Name(s)" TEXT,
    "Address"             TEXT,
    "Postal Code"         TEXT,
    "Zone"                TEXT,
    "FM"                  TEXT,
    "Geo Zone"            TEXT,
    "GPS Coordinates"     TEXT,
    "Area (Gross)"        TEXT,
    "Year"                TEXT
) ON COMMIT DROP;

\copy buildings_metadata_stage ("Code", "Alternative Name(s)", "Address", "Postal Code", "Zone", "FM", "Geo Zone", "GPS Coordinates", "Area (Gross)", "Year") FROM '/tmp/buildings_metadata_678c6e472a6f.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8', NULL '')

DO $preflight$
DECLARE
    staged_count bigint;
    building_count bigint;
    normalized_building_count bigint;
    matched_count bigint;
    workbook_only_count bigint;
    invalid_existing_column_count bigint;
BEGIN
    SELECT COUNT(*)
      INTO staged_count
      FROM buildings_metadata_stage;

    IF staged_count <> 2014 THEN
        RAISE EXCEPTION
            'Buildings metadata preflight expected 2014 staged rows; found %',
            staged_count;
    END IF;

    SELECT COUNT(*), COUNT(DISTINCT BTRIM("Code"))
      INTO building_count, normalized_building_count
      FROM "Buildings";

    IF building_count <> 327 OR normalized_building_count <> 327 THEN
        RAISE EXCEPTION
            'Buildings metadata preflight expected 327 rows / 327 normalized Codes; found % / %',
            building_count, normalized_building_count;
    END IF;

    SELECT COUNT(*)
      INTO matched_count
      FROM "Buildings" b
      JOIN buildings_metadata_stage s
        ON BTRIM(b."Code") = s."Code";

    IF matched_count <> building_count THEN
        RAISE EXCEPTION
            'Buildings metadata preflight expected all % Buildings rows to match; found %',
            building_count, matched_count;
    END IF;

    SELECT COUNT(*)
      INTO workbook_only_count
      FROM buildings_metadata_stage s
     WHERE NOT EXISTS (
         SELECT 1
           FROM "Buildings" b
          WHERE BTRIM(b."Code") = s."Code"
     );

    IF workbook_only_count <> 1687 THEN
        RAISE EXCEPTION
            'Buildings metadata preflight expected 1687 workbook-only rows; found %',
            workbook_only_count;
    END IF;

    SELECT COUNT(*)
      INTO invalid_existing_column_count
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'Buildings'
       AND column_name IN (
           'Alternative Name(s)',
           'Address',
           'Postal Code',
           'Zone',
           'FM',
           'Geo Zone',
           'GPS Coordinates',
           'Area (Gross)',
           'Year'
       )
       AND (data_type <> 'text' OR is_nullable <> 'YES');

    IF invalid_existing_column_count <> 0 THEN
        RAISE EXCEPTION
            'Buildings metadata preflight found % requested columns with incompatible definitions',
            invalid_existing_column_count;
    END IF;
END
$preflight$;

ALTER TABLE "Buildings"
    ADD COLUMN IF NOT EXISTS "Alternative Name(s)" TEXT,
    ADD COLUMN IF NOT EXISTS "Address" TEXT,
    ADD COLUMN IF NOT EXISTS "Postal Code" TEXT,
    ADD COLUMN IF NOT EXISTS "Zone" TEXT,
    ADD COLUMN IF NOT EXISTS "FM" TEXT,
    ADD COLUMN IF NOT EXISTS "Geo Zone" TEXT,
    ADD COLUMN IF NOT EXISTS "GPS Coordinates" TEXT,
    ADD COLUMN IF NOT EXISTS "Area (Gross)" TEXT,
    ADD COLUMN IF NOT EXISTS "Year" TEXT;

DO $conflict_check$
DECLARE
    conflict_count bigint;
BEGIN
    SELECT COUNT(*)
      INTO conflict_count
      FROM "Buildings" b
      JOIN buildings_metadata_stage s
        ON BTRIM(b."Code") = s."Code"
     WHERE (b."Alternative Name(s)" IS NOT NULL AND b."Alternative Name(s)" IS DISTINCT FROM s."Alternative Name(s)")
        OR (b."Address"             IS NOT NULL AND b."Address"             IS DISTINCT FROM s."Address")
        OR (b."Postal Code"         IS NOT NULL AND b."Postal Code"         IS DISTINCT FROM s."Postal Code")
        OR (b."Zone"                IS NOT NULL AND b."Zone"                IS DISTINCT FROM s."Zone")
        OR (b."FM"                  IS NOT NULL AND b."FM"                  IS DISTINCT FROM s."FM")
        OR (b."Geo Zone"            IS NOT NULL AND b."Geo Zone"            IS DISTINCT FROM s."Geo Zone")
        OR (b."GPS Coordinates"     IS NOT NULL AND b."GPS Coordinates"     IS DISTINCT FROM s."GPS Coordinates")
        OR (b."Area (Gross)"        IS NOT NULL AND b."Area (Gross)"        IS DISTINCT FROM s."Area (Gross)")
        OR (b."Year"                IS NOT NULL AND b."Year"                IS DISTINCT FROM s."Year");

    IF conflict_count <> 0 THEN
        RAISE EXCEPTION
            'Buildings metadata preflight found % rows with conflicting existing values; no data was changed',
            conflict_count;
    END IF;
END
$conflict_check$;

WITH candidates AS (
    SELECT
        b."Code",
        CASE WHEN b."Alternative Name(s)" IS NULL THEN s."Alternative Name(s)" END AS alternative_name,
        CASE WHEN b."Address"             IS NULL THEN s."Address"             END AS address,
        CASE WHEN b."Postal Code"         IS NULL THEN s."Postal Code"         END AS postal_code,
        CASE WHEN b."Zone"                IS NULL THEN s."Zone"                END AS zone,
        CASE WHEN b."FM"                  IS NULL THEN s."FM"                  END AS fm,
        CASE WHEN b."Geo Zone"            IS NULL THEN s."Geo Zone"            END AS geo_zone,
        CASE WHEN b."GPS Coordinates"     IS NULL THEN s."GPS Coordinates"     END AS gps_coordinates,
        CASE WHEN b."Area (Gross)"        IS NULL THEN s."Area (Gross)"        END AS area_gross,
        CASE WHEN b."Year"                IS NULL THEN s."Year"                END AS year_value
      FROM "Buildings" b
      JOIN buildings_metadata_stage s
        ON BTRIM(b."Code") = s."Code"
     WHERE (b."Alternative Name(s)" IS NULL AND s."Alternative Name(s)" IS NOT NULL)
        OR (b."Address"             IS NULL AND s."Address"             IS NOT NULL)
        OR (b."Postal Code"         IS NULL AND s."Postal Code"         IS NOT NULL)
        OR (b."Zone"                IS NULL AND s."Zone"                IS NOT NULL)
        OR (b."FM"                  IS NULL AND s."FM"                  IS NOT NULL)
        OR (b."Geo Zone"            IS NULL AND s."Geo Zone"            IS NOT NULL)
        OR (b."GPS Coordinates"     IS NULL AND s."GPS Coordinates"     IS NOT NULL)
        OR (b."Area (Gross)"        IS NULL AND s."Area (Gross)"        IS NOT NULL)
        OR (b."Year"                IS NULL AND s."Year"                IS NOT NULL)
),
updated AS (
    UPDATE "Buildings" b
       SET "Alternative Name(s)" = COALESCE(b."Alternative Name(s)", c.alternative_name),
           "Address"             = COALESCE(b."Address",             c.address),
           "Postal Code"         = COALESCE(b."Postal Code",         c.postal_code),
           "Zone"                = COALESCE(b."Zone",                c.zone),
           "FM"                  = COALESCE(b."FM",                  c.fm),
           "Geo Zone"            = COALESCE(b."Geo Zone",            c.geo_zone),
           "GPS Coordinates"     = COALESCE(b."GPS Coordinates",     c.gps_coordinates),
           "Area (Gross)"        = COALESCE(b."Area (Gross)",        c.area_gross),
           "Year"                = COALESCE(b."Year",                c.year_value)
      FROM candidates c
     WHERE b."Code" = c."Code"
    RETURNING
        b."Code",
        c.alternative_name,
        c.address,
        c.postal_code,
        c.zone,
        c.fm,
        c.geo_zone,
        c.gps_coordinates,
        c.area_gross,
        c.year_value
),
audit_stamp AS (
    SELECT timezone('America/Vancouver', clock_timestamp()) AS local_now
),
audited AS (
    INSERT INTO "audit_trail" (
        qr_code,
        description,
        modification_date,
        modification_time,
        modified_by,
        source,
        app_name,
        table_name,
        record_pk,
        op_type,
        field_name,
        old_value,
        new_value
    )
    SELECT
        NULL,
        'Populate Buildings metadata from workbook SHA-256 678c6e472a6f0d37bd81e1e0bdddc8e69e33570140ded97877ae9cfee6b4334f',
        to_char(audit_stamp.local_now, 'YYYY-MM-DD'),
        to_char(audit_stamp.local_now, 'HH24:MI:SS'),
        'gandrade',
        'system',
        'database-migration',
        'Buildings',
        updated."Code",
        'UPDATE',
        field_change.field_name,
        NULL,
        field_change.new_value
      FROM updated
      CROSS JOIN audit_stamp
      CROSS JOIN LATERAL (
          VALUES
              ('Alternative Name(s)', updated.alternative_name),
              ('Address',             updated.address),
              ('Postal Code',         updated.postal_code),
              ('Zone',                updated.zone),
              ('FM',                  updated.fm),
              ('Geo Zone',            updated.geo_zone),
              ('GPS Coordinates',     updated.gps_coordinates),
              ('Area (Gross)',        updated.area_gross),
              ('Year',                updated.year_value)
      ) AS field_change(field_name, new_value)
     WHERE field_change.new_value IS NOT NULL
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM updated) AS updated_buildings,
    (SELECT COUNT(*) FROM audited) AS inserted_audit_rows;

DO $validate$
DECLARE
    mismatch_count bigint;
    building_count bigint;
    alternative_name_count bigint;
    address_count bigint;
    postal_code_count bigint;
    zone_count bigint;
    fm_count bigint;
    geo_zone_count bigint;
    gps_count bigint;
    area_count bigint;
    year_count bigint;
BEGIN
    SELECT COUNT(*)
      INTO mismatch_count
      FROM "Buildings" b
      JOIN buildings_metadata_stage s
        ON BTRIM(b."Code") = s."Code"
     WHERE b."Alternative Name(s)" IS DISTINCT FROM s."Alternative Name(s)"
        OR b."Address"             IS DISTINCT FROM s."Address"
        OR b."Postal Code"         IS DISTINCT FROM s."Postal Code"
        OR b."Zone"                IS DISTINCT FROM s."Zone"
        OR b."FM"                  IS DISTINCT FROM s."FM"
        OR b."Geo Zone"            IS DISTINCT FROM s."Geo Zone"
        OR b."GPS Coordinates"     IS DISTINCT FROM s."GPS Coordinates"
        OR b."Area (Gross)"        IS DISTINCT FROM s."Area (Gross)"
        OR b."Year"                IS DISTINCT FROM s."Year";

    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Buildings metadata validation found % workbook mismatches',
            mismatch_count;
    END IF;

    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE "Alternative Name(s)" IS NOT NULL),
        COUNT(*) FILTER (WHERE "Address"             IS NOT NULL),
        COUNT(*) FILTER (WHERE "Postal Code"         IS NOT NULL),
        COUNT(*) FILTER (WHERE "Zone"                IS NOT NULL),
        COUNT(*) FILTER (WHERE "FM"                  IS NOT NULL),
        COUNT(*) FILTER (WHERE "Geo Zone"            IS NOT NULL),
        COUNT(*) FILTER (WHERE "GPS Coordinates"     IS NOT NULL),
        COUNT(*) FILTER (WHERE "Area (Gross)"        IS NOT NULL),
        COUNT(*) FILTER (WHERE "Year"                IS NOT NULL)
      INTO
        building_count,
        alternative_name_count,
        address_count,
        postal_code_count,
        zone_count,
        fm_count,
        geo_zone_count,
        gps_count,
        area_count,
        year_count
      FROM "Buildings";

    IF building_count <> 327
       OR alternative_name_count <> 231
       OR address_count <> 321
       OR postal_code_count <> 321
       OR zone_count <> 282
       OR fm_count <> 282
       OR geo_zone_count <> 270
       OR gps_count <> 283
       OR area_count <> 327
       OR year_count <> 327 THEN
        RAISE EXCEPTION
            'Buildings metadata validation counts were unexpected: rows %, alternative %, address %, postal %, zone %, fm %, geo %, gps %, area %, year %',
            building_count,
            alternative_name_count,
            address_count,
            postal_code_count,
            zone_count,
            fm_count,
            geo_zone_count,
            gps_count,
            area_count,
            year_count;
    END IF;
END
$validate$;

COMMIT;

-- Verify:
--   SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name = 'Buildings'
--   ORDER BY ordinal_position;
--
--   SELECT "Code", "Alternative Name(s)", "Address", "Postal Code", "Zone",
--          "FM", "Geo Zone", "GPS Coordinates", "Area (Gross)", "Year"
--   FROM "Buildings"
--   WHERE "Code" IN ('017', '020', '4036', '874')
--   ORDER BY "Code";
--
-- Guarded rollback (run only after confirming no consumer depends on the new
-- columns): restore the pre-migration pg_dump. Dropping the columns would lose
-- populated metadata and is intentionally not automated here.
