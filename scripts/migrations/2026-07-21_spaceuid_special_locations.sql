-- Seed three synthetic SpaceUID locations for every property code currently
-- represented in the live PostgreSQL qr_code_db SpaceUID table.
--
-- Expected production snapshot (2026-07-21):
--   765 distinct nonblank property codes
--   2,295 inserted SpaceUID rows (765 x 3)
--   11,475 audit_trail rows (2,295 x 5 populated fields)
--
-- Run as the PostgreSQL table-owner role after taking and verifying a pg_dump:
--   psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5433 \
--     -U developer -d qr_code_db \
--     -f scripts/migrations/2026-07-21_spaceuid_special_locations.sql
--
-- The script is idempotent. Existing exact rows are retained. If an existing
-- (Property.Property code, Space number) key has different values, the whole
-- transaction aborts instead of overwriting reference data.

\set ON_ERROR_STOP on

BEGIN;

-- Keep the property snapshot, conflict check, inserts, and validation stable
-- while allowing application reads to continue.
LOCK TABLE "SpaceUID" IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
    property_count bigint;
    conflict_count bigint;
    pipe_code_count bigint;
BEGIN
    SELECT COUNT(DISTINCT "Property.Property code")
      INTO property_count
      FROM "SpaceUID"
     WHERE btrim(COALESCE("Property.Property code", '')) <> '';

    IF property_count <> 765 THEN
        RAISE EXCEPTION
            'SpaceUID preflight expected 765 distinct nonblank property codes; found %',
            property_count;
    END IF;

    SELECT COUNT(*)
      INTO pipe_code_count
      FROM "SpaceUID"
     WHERE "Property.Property code" LIKE '%|%';

    IF pipe_code_count <> 0 THEN
        RAISE EXCEPTION
            'SpaceUID preflight found % property-code rows containing |; audit record keys would be ambiguous',
            pipe_code_count;
    END IF;

    WITH desired("Space number", "Floor Code", "Space Name", "Floor Name") AS (
        VALUES
            ('Z01Rooftop',          'RT', 'No Room Identification', 'Floor: -'),
            ('Z02Notfound_Room',    'NF', 'No Room Identification', 'Floor: -'),
            ('Z03External_building','EB', 'No Room Identification', 'Floor: -')
    )
    SELECT COUNT(*)
      INTO conflict_count
      FROM "SpaceUID" existing
      JOIN desired
        ON desired."Space number" = existing."Space number"
     WHERE existing."Floor Code" IS DISTINCT FROM desired."Floor Code"
        OR existing."Space Name" IS DISTINCT FROM desired."Space Name"
        OR existing."Floor Name" IS DISTINCT FROM desired."Floor Name";

    IF conflict_count <> 0 THEN
        RAISE EXCEPTION
            'SpaceUID preflight found % conflicting synthetic-location rows; no data was changed',
            conflict_count;
    END IF;
END
$preflight$;

WITH properties AS (
    SELECT DISTINCT "Property.Property code" AS property_code
      FROM "SpaceUID"
     WHERE btrim(COALESCE("Property.Property code", '')) <> ''
),
desired("Space number", "Floor Code", "Space Name", "Floor Name") AS (
    VALUES
        ('Z01Rooftop',          'RT', 'No Room Identification', 'Floor: -'),
        ('Z02Notfound_Room',    'NF', 'No Room Identification', 'Floor: -'),
        ('Z03External_building','EB', 'No Room Identification', 'Floor: -')
),
inserted AS (
    INSERT INTO "SpaceUID" (
        "Space number",
        "Property.Property code",
        "Floor Code",
        "Space Name",
        "Floor Name"
    )
    SELECT
        desired."Space number",
        properties.property_code,
        desired."Floor Code",
        desired."Space Name",
        desired."Floor Name"
      FROM properties
      CROSS JOIN desired
     WHERE NOT EXISTS (
        SELECT 1
          FROM "SpaceUID" existing
         WHERE existing."Property.Property code" = properties.property_code
           AND existing."Space number" = desired."Space number"
     )
    RETURNING
        "Space number",
        "Property.Property code",
        "Floor Code",
        "Space Name",
        "Floor Name"
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
        'Seed synthetic SpaceUID fallback locations (2026-07-21)',
        to_char(audit_stamp.local_now, 'YYYY-MM-DD'),
        to_char(audit_stamp.local_now, 'HH24:MI:SS'),
        'gandrade',
        'system',
        'database-migration',
        'SpaceUID',
        inserted."Property.Property code" || '|' || inserted."Space number",
        'INSERT',
        field_change.field_name,
        NULL,
        field_change.new_value
      FROM inserted
      CROSS JOIN audit_stamp
      CROSS JOIN LATERAL (
        VALUES
            ('Space number',              inserted."Space number"),
            ('Property.Property code',    inserted."Property.Property code"),
            ('Floor Code',                inserted."Floor Code"),
            ('Space Name',                inserted."Space Name"),
            ('Floor Name',                inserted."Floor Name")
      ) AS field_change(field_name, new_value)
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM inserted) AS inserted_spaceuid_rows,
    (SELECT COUNT(*) FROM audited) AS inserted_audit_rows;

DO $validate$
DECLARE
    invalid_key_count bigint;
    special_row_count bigint;
    property_count bigint;
BEGIN
    WITH properties AS (
        SELECT DISTINCT "Property.Property code" AS property_code
          FROM "SpaceUID"
         WHERE btrim(COALESCE("Property.Property code", '')) <> ''
    ),
    desired("Space number", "Floor Code", "Space Name", "Floor Name") AS (
        VALUES
            ('Z01Rooftop',          'RT', 'No Room Identification', 'Floor: -'),
            ('Z02Notfound_Room',    'NF', 'No Room Identification', 'Floor: -'),
            ('Z03External_building','EB', 'No Room Identification', 'Floor: -')
    )
    SELECT COUNT(*)
      INTO invalid_key_count
      FROM (
        SELECT properties.property_code, desired."Space number"
          FROM properties
          CROSS JOIN desired
          LEFT JOIN "SpaceUID" actual
            ON actual."Property.Property code" = properties.property_code
           AND actual."Space number" = desired."Space number"
         GROUP BY properties.property_code, desired."Space number",
                  desired."Floor Code", desired."Space Name", desired."Floor Name"
        HAVING COUNT(actual.*) <> 1
            OR COUNT(actual.*) FILTER (
                WHERE actual."Floor Code" = desired."Floor Code"
                  AND actual."Space Name" = desired."Space Name"
                  AND actual."Floor Name" = desired."Floor Name"
            ) <> 1
      ) invalid_keys;

    IF invalid_key_count <> 0 THEN
        RAISE EXCEPTION
            'SpaceUID post-insert validation found % invalid synthetic keys',
            invalid_key_count;
    END IF;

    SELECT COUNT(DISTINCT "Property.Property code"), COUNT(*)
      INTO property_count, special_row_count
      FROM "SpaceUID"
     WHERE "Space number" IN (
        'Z01Rooftop', 'Z02Notfound_Room', 'Z03External_building'
     );

    IF property_count <> 765 OR special_row_count <> 2295 THEN
        RAISE EXCEPTION
            'SpaceUID post-insert expected 765 properties / 2295 rows; found % properties / % rows',
            property_count, special_row_count;
    END IF;
END
$validate$;

COMMIT;

-- Verification:
--   SELECT "Space number", COUNT(*), COUNT(DISTINCT "Property.Property code")
--   FROM "SpaceUID"
--   WHERE "Space number" IN
--     ('Z01Rooftop','Z02Notfound_Room','Z03External_building')
--   GROUP BY "Space number" ORDER BY "Space number";
--
-- Narrow rollback (removes only rows carrying this migration's audit key):
--   BEGIN;
--   WITH migration_keys AS (
--       SELECT DISTINCT record_pk
--       FROM "audit_trail"
--       WHERE table_name = 'SpaceUID'
--         AND description = 'Seed synthetic SpaceUID fallback locations (2026-07-21)'
--   )
--   DELETE FROM "SpaceUID" target
--   USING migration_keys
--   WHERE migration_keys.record_pk =
--         target."Property.Property code" || '|' || target."Space number"
--     AND target."Space number" IN
--         ('Z01Rooftop','Z02Notfound_Room','Z03External_building')
--     AND target."Space Name" = 'No Room Identification'
--     AND target."Floor Name" = 'Floor: -';
--
--   DELETE FROM "audit_trail"
--   WHERE table_name = 'SpaceUID'
--     AND description = 'Seed synthetic SpaceUID fallback locations (2026-07-21)';
--   COMMIT;
