-- Mark the electrical distribution Asset_Group rows as elec_dist_setup = Y.
--
-- This migration expects seven distinct requested names and eight physical
-- rows because Asset_Group currently contains two different Panels records.
-- It is transactional, audited, and idempotent.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE "Asset_Group" IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
    matched_name_count bigint;
    matched_row_count bigint;
BEGIN
    WITH requested(name) AS (
        VALUES
            ('Panels'),
            ('Other Service and Distribution'),
            ('Interior Distribution Transformers'),
            ('Main Transformers'),
            ('Motor Control Centers'),
            ('Enclosed Circuit Breakers'),
            ('Automatic Transfer Switches')
    )
    SELECT COUNT(DISTINCT requested.name), COUNT("Asset_Group".*)
      INTO matched_name_count, matched_row_count
      FROM requested
      JOIN "Asset_Group"
        ON "Asset_Group"."Name" = requested.name;

    IF matched_name_count <> 7 OR matched_row_count <> 8 THEN
        RAISE EXCEPTION
            'Electrical distribution preflight expected 7 names / 8 rows; found % names / % rows',
            matched_name_count,
            matched_row_count;
    END IF;
END
$preflight$;

WITH requested(name) AS (
    VALUES
        ('Panels'),
        ('Other Service and Distribution'),
        ('Interior Distribution Transformers'),
        ('Main Transformers'),
        ('Motor Control Centers'),
        ('Enclosed Circuit Breakers'),
        ('Automatic Transfer Switches')
),
updated AS (
    UPDATE "Asset_Group" AS asset_group
       SET elec_dist_setup = 'Y'
      FROM requested
     WHERE asset_group."Name" = requested.name
       AND asset_group.elec_dist_setup IS DISTINCT FROM 'Y'
    RETURNING asset_group."Code", asset_group."Name"
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
        'Mark electrical distribution Asset_Group entry for setup',
        to_char(audit_stamp.local_now, 'YYYY-MM-DD'),
        to_char(audit_stamp.local_now, 'HH24:MI:SS'),
        'gandrade',
        'human',
        'database-migration',
        'Asset_Group',
        updated."Code",
        'UPDATE',
        'elec_dist_setup',
        'N',
        'Y'
      FROM updated
      CROSS JOIN audit_stamp
    RETURNING 1
)
SELECT
    (SELECT COUNT(*) FROM updated) AS updated_asset_groups,
    (SELECT COUNT(*) FROM audited) AS inserted_audit_rows;

DO $validate$
DECLARE
    matched_row_count bigint;
    y_row_count bigint;
BEGIN
    WITH requested(name) AS (
        VALUES
            ('Panels'),
            ('Other Service and Distribution'),
            ('Interior Distribution Transformers'),
            ('Main Transformers'),
            ('Motor Control Centers'),
            ('Enclosed Circuit Breakers'),
            ('Automatic Transfer Switches')
    )
    SELECT COUNT(*), COUNT(*) FILTER (WHERE asset_group.elec_dist_setup = 'Y')
      INTO matched_row_count, y_row_count
      FROM "Asset_Group" AS asset_group
      JOIN requested
        ON requested.name = asset_group."Name";

    IF matched_row_count <> 8 OR y_row_count <> 8 THEN
        RAISE EXCEPTION
            'Electrical distribution validation expected 8 Y rows; found % Y rows among % matches',
            y_row_count,
            matched_row_count;
    END IF;
END
$validate$;

COMMIT;

-- Verify:
--   SELECT "Code", "Name", elec_dist_setup
--   FROM "Asset_Group"
--   WHERE elec_dist_setup = 'Y'
--   ORDER BY "Name", "Code";
