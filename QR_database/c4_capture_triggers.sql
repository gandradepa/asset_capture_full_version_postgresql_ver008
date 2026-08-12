-- C4: port Capture-critical SQLite triggers to PL/pgSQL on qr_code_db (parallel mirror).
-- Ports: auto_fill_all_on_insert/update, T_set_ai_status, trg_qr_codes_sdi_default_zero,
--        trg_sync_process_type_dataset(_EL). (matching_check trg_sdi_ai/ad/au = EL/SLD, deferred.)
-- Recreates Buildings_with_SpaceUID (the view the auto-fill Floor Code lookup needs).
-- Idempotent; safe to re-run. Applied as one transaction (psql -1).

CREATE OR REPLACE VIEW "Buildings_with_SpaceUID" AS
SELECT b.*,
       s."Space number", s."Space Name", s."Floor Name",
       btrim(COALESCE(s."Space number",'') || ' ' || COALESCE(s."Space Name",'') || ' ' || COALESCE(s."Floor Name",'')) AS "Location",
       s."Floor Code"
FROM "Buildings" b
LEFT JOIN "SpaceUID" s ON b."Code" = s."Property.Property code";

ALTER TABLE "QR_codes" ALTER COLUMN "sdi" SET DEFAULT '0';   -- was trg_qr_codes_sdi_default_zero

-- BEFORE INSERT: auto-fill Location-derived fields + date_set + sdi default + ai_status
CREATE OR REPLACE FUNCTION qr_autofill_ins() RETURNS trigger AS $fn$
DECLARE loc text := COALESCE(NEW."Location", '');
BEGIN
  IF position(' ' in loc) > 0 THEN
    NEW."Space" := substr(loc, 1, position(' ' in loc) - 1);
  ELSE
    NEW."Space" := loc;
  END IF;
  NEW."Floor" := split_part(btrim(loc), ' ', -1);
  IF (length(loc) - length(replace(loc, ' ', ''))) >= 2 THEN
    NEW."Space Details" := btrim(replace(btrim(substr(loc, position(' ' in loc) + 1,
        greatest(length(loc) - position(' ' in loc) - length(split_part(btrim(loc), ' ', -1)), 0))),
        'Floor:', ''));
  ELSE
    NEW."Space Details" := NULL;
  END IF;
  NEW."Floor Code" := (SELECT "Floor Code" FROM "Buildings_with_SpaceUID" WHERE "Location" = NEW."Location" LIMIT 1);
  IF NEW."date_set" IS NULL OR btrim(NEW."date_set") = '' THEN
    NEW."date_set" := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS');
  END IF;
  IF NEW."sdi" IS NULL THEN NEW."sdi" := '0'; END IF;
  NEW."ai_status" := CASE WHEN
        EXISTS (SELECT 1 FROM "sdi_dataset"    WHERE upper(btrim("QR Code")) = upper(btrim(NEW."QR_code_ID")))
     OR EXISTS (SELECT 1 FROM "sdi_dataset_EL" WHERE upper(btrim("QR Code")) = upper(btrim(NEW."QR_code_ID")))
      THEN '1' ELSE '0' END;
  RETURN NEW;
END; $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_qr_autofill_ins ON "QR_codes";
CREATE TRIGGER trg_qr_autofill_ins BEFORE INSERT ON "QR_codes"
FOR EACH ROW EXECUTE FUNCTION qr_autofill_ins();

-- BEFORE UPDATE OF Location: re-derive Space/Floor/Space Details/Floor Code only
CREATE OR REPLACE FUNCTION qr_autofill_upd() RETURNS trigger AS $fn$
DECLARE loc text := COALESCE(NEW."Location", '');
BEGIN
  IF position(' ' in loc) > 0 THEN
    NEW."Space" := substr(loc, 1, position(' ' in loc) - 1);
  ELSE
    NEW."Space" := loc;
  END IF;
  NEW."Floor" := split_part(btrim(loc), ' ', -1);
  IF (length(loc) - length(replace(loc, ' ', ''))) >= 2 THEN
    NEW."Space Details" := btrim(replace(btrim(substr(loc, position(' ' in loc) + 1,
        greatest(length(loc) - position(' ' in loc) - length(split_part(btrim(loc), ' ', -1)), 0))),
        'Floor:', ''));
  ELSE
    NEW."Space Details" := NULL;
  END IF;
  NEW."Floor Code" := (SELECT "Floor Code" FROM "Buildings_with_SpaceUID" WHERE "Location" = NEW."Location" LIMIT 1);
  RETURN NEW;
END; $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_qr_autofill_upd ON "QR_codes";
CREATE TRIGGER trg_qr_autofill_upd BEFORE UPDATE OF "Location" ON "QR_codes"
FOR EACH ROW EXECUTE FUNCTION qr_autofill_upd();

-- AFTER INSERT on sdi_dataset / sdi_dataset_EL: ensure a process_type row exists
CREATE OR REPLACE FUNCTION sync_process_type() RETURNS trigger AS $fn$
BEGIN
  IF NEW."QR Code" IS NOT NULL THEN
    INSERT INTO "process_type" ("QR Code") VALUES (NEW."QR Code")
    ON CONFLICT ("QR Code") DO NOTHING;
  END IF;
  RETURN NULL;
END; $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_pt_sdi ON "sdi_dataset";
CREATE TRIGGER trg_sync_pt_sdi AFTER INSERT ON "sdi_dataset"
FOR EACH ROW EXECUTE FUNCTION sync_process_type();

DROP TRIGGER IF EXISTS trg_sync_pt_sdiel ON "sdi_dataset_EL";
CREATE TRIGGER trg_sync_pt_sdiel AFTER INSERT ON "sdi_dataset_EL"
FOR EACH ROW EXECUTE FUNCTION sync_process_type();
