-- C4 (cont.): port matching_check triggers + placeholder CHECK + 2 views to qr_code_db.
-- matching_check (trg_sdi_ai/ad/au) maintains electrical_building_schema.matching_check from
-- sdi_dataset_EL (EL SLD reconciliation). matching_check is text in the mirror -> '1'/'0'.
-- Idempotent; one transaction (psql -1).

-- placeholder-reject (was trg_qr_codes_reject_placeholder_insert/update) -> CHECK
ALTER TABLE "QR_codes" DROP CONSTRAINT IF EXISTS chk_qr_not_placeholder;
ALTER TABLE "QR_codes" ADD CONSTRAINT chk_qr_not_placeholder
  CHECK (lower(btrim(coalesce("QR_code_ID",''))) NOT IN ('','none','nan','null'));

-- views (faithful ports, identifiers quoted for PG)
CREATE OR REPLACE VIEW "asset_group_view" AS
SELECT CASE
         WHEN "Name" IS NOT NULL AND "Full Classification" IS NOT NULL THEN "Name" || ' | ' || "Full Classification"
         WHEN "Name" IS NULL THEN "Full Classification"
         ELSE "Name"
       END AS "Asset Group"
FROM "fls_asset_group";

CREATE OR REPLACE VIEW "Sorted by name" AS SELECT * FROM "Buildings";

-- matching_check: AFTER INSERT
CREATE OR REPLACE FUNCTION ebs_matching_ai() RETURNS trigger AS $fn$
BEGIN
  UPDATE "electrical_building_schema" e
  SET "matching_check" = '1'
  WHERE e."new_draw" = 'TRUE'
    AND btrim(coalesce(NEW."UBC Asset Tag",'')) <> ''
    AND upper(btrim(coalesce(e."Building",'')))     = upper(btrim(coalesce(NEW."Building",'')))
    AND upper(btrim(coalesce(e."Equipment ID",''))) = upper(btrim(coalesce(NEW."UBC Asset Tag",'')));
  RETURN NULL;
END; $fn$ LANGUAGE plpgsql;

-- matching_check: AFTER DELETE (recompute affected diagram rows)
CREATE OR REPLACE FUNCTION ebs_matching_ad() RETURNS trigger AS $fn$
BEGIN
  UPDATE "electrical_building_schema" e
  SET "matching_check" = CASE WHEN EXISTS (
        SELECT 1 FROM "sdi_dataset_EL" s
        WHERE btrim(coalesce(s."UBC Asset Tag",'')) <> ''
          AND upper(btrim(coalesce(s."Building",'')))     = upper(btrim(coalesce(e."Building",'')))
          AND upper(btrim(coalesce(s."UBC Asset Tag",''))) = upper(btrim(coalesce(e."Equipment ID",'')))
      ) THEN '1' ELSE '0' END
  WHERE e."new_draw" = 'TRUE'
    AND btrim(coalesce(OLD."UBC Asset Tag",'')) <> ''
    AND upper(btrim(coalesce(e."Building",'')))     = upper(btrim(coalesce(OLD."Building",'')))
    AND upper(btrim(coalesce(e."Equipment ID",''))) = upper(btrim(coalesce(OLD."UBC Asset Tag",'')));
  RETURN NULL;
END; $fn$ LANGUAGE plpgsql;

-- matching_check: AFTER UPDATE OF Building, "UBC Asset Tag" (recompute old+new targets)
CREATE OR REPLACE FUNCTION ebs_matching_au() RETURNS trigger AS $fn$
BEGIN
  UPDATE "electrical_building_schema" e
  SET "matching_check" = CASE WHEN EXISTS (
        SELECT 1 FROM "sdi_dataset_EL" s
        WHERE btrim(coalesce(s."UBC Asset Tag",'')) <> ''
          AND upper(btrim(coalesce(s."Building",'')))     = upper(btrim(coalesce(e."Building",'')))
          AND upper(btrim(coalesce(s."UBC Asset Tag",''))) = upper(btrim(coalesce(e."Equipment ID",'')))
      ) THEN '1' ELSE '0' END
  WHERE e."new_draw" = 'TRUE'
    AND (
      ( btrim(coalesce(OLD."UBC Asset Tag",'')) <> ''
        AND upper(btrim(coalesce(e."Building",'')))     = upper(btrim(coalesce(OLD."Building",'')))
        AND upper(btrim(coalesce(e."Equipment ID",''))) = upper(btrim(coalesce(OLD."UBC Asset Tag",''))) )
      OR
      ( btrim(coalesce(NEW."UBC Asset Tag",'')) <> ''
        AND upper(btrim(coalesce(e."Building",'')))     = upper(btrim(coalesce(NEW."Building",'')))
        AND upper(btrim(coalesce(e."Equipment ID",''))) = upper(btrim(coalesce(NEW."UBC Asset Tag",''))) )
    );
  RETURN NULL;
END; $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sdi_ai ON "sdi_dataset_EL";
CREATE TRIGGER trg_sdi_ai AFTER INSERT ON "sdi_dataset_EL" FOR EACH ROW EXECUTE FUNCTION ebs_matching_ai();
DROP TRIGGER IF EXISTS trg_sdi_ad ON "sdi_dataset_EL";
CREATE TRIGGER trg_sdi_ad AFTER DELETE ON "sdi_dataset_EL" FOR EACH ROW EXECUTE FUNCTION ebs_matching_ad();
DROP TRIGGER IF EXISTS trg_sdi_au ON "sdi_dataset_EL";
CREATE TRIGGER trg_sdi_au AFTER UPDATE OF "Building","UBC Asset Tag" ON "sdi_dataset_EL" FOR EACH ROW EXECUTE FUNCTION ebs_matching_au();
