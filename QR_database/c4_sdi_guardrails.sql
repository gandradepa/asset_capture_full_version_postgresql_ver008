-- C4: port SDI package-lifecycle guardrails to PL/pgSQL on qr_code_db.
-- Faithful to scripts/migrate_sdi_package_db_guardrails.py (SQLite RAISE(ABORT) -> RAISE EXCEPTION).
-- "QR must exist in QR_codes" + "no delete while packaged" are already enforced by the FKs
-- (fk_spo_qr/fk_spoa_qr ON DELETE RESTRICT); these add the content/approval/mutual-exclusion checks.
-- Idempotent; one transaction.

-- Package insert/update guard (shared by sdi_print_out and sdi_print_out_arch via TG_TABLE_NAME)
CREATE OR REPLACE FUNCTION sdi_package_guard() RETURNS trigger AS $fn$
DECLARE
  qn   text := upper(btrim(coalesce(NEW."QR Code"::text, '')));
  other text := CASE WHEN TG_TABLE_NAME = 'sdi_print_out' THEN 'sdi_print_out_arch' ELSE 'sdi_print_out' END;
  cnt  bigint;
BEGIN
  IF btrim(coalesce(NEW."QR Code"::text, '')) = '' THEN
    RAISE EXCEPTION 'SDI guardrail: QR Code is required'; END IF;
  IF btrim(coalesce(NEW."id_print_out"::text, '')) = '' THEN
    RAISE EXCEPTION 'SDI guardrail: id_print_out is required'; END IF;
  IF TG_TABLE_NAME = 'sdi_print_out_arch' AND btrim(coalesce(NEW."print_out"::text, '')) <> '1' THEN
    RAISE EXCEPTION 'SDI guardrail: archived package rows must have print_out = 1'; END IF;
  IF NOT EXISTS (SELECT 1 FROM "QR_codes" WHERE upper(btrim("QR_code_ID"::text)) = qn) THEN
    RAISE EXCEPTION 'SDI guardrail: packaged QR must exist in QR_codes'; END IF;
  EXECUTE format('SELECT count(*) FROM %I WHERE upper(btrim("QR Code"::text)) = $1', other)
    INTO cnt USING qn;
  IF cnt > 0 THEN
    RAISE EXCEPTION 'SDI guardrail: packaged QR already exists in the other package table'; END IF;
  IF NOT EXISTS (SELECT 1 FROM "sdi_dataset"    WHERE upper(btrim("QR Code"::text)) = qn AND btrim(coalesce("Approved"::text,''))='1')
 AND NOT EXISTS (SELECT 1 FROM "sdi_dataset_EL" WHERE upper(btrim("QR Code"::text)) = qn AND btrim(coalesce("Approved"::text,''))='1') THEN
    RAISE EXCEPTION 'SDI guardrail: packaged QR source approval is required'; END IF;
  IF EXISTS (SELECT 1 FROM "sdi_dataset"    WHERE upper(btrim("QR Code"::text)) = qn AND btrim(coalesce("Approved"::text,''))<>'1')
  OR EXISTS (SELECT 1 FROM "sdi_dataset_EL" WHERE upper(btrim("QR Code"::text)) = qn AND btrim(coalesce("Approved"::text,''))<>'1') THEN
    RAISE EXCEPTION 'SDI guardrail: packaged QR has unapproved source state'; END IF;
  RETURN NEW;
END; $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sdi_print_out_guard ON "sdi_print_out";
CREATE TRIGGER trg_sdi_print_out_guard
  BEFORE INSERT OR UPDATE OF "QR Code", "id_print_out" ON "sdi_print_out"
  FOR EACH ROW EXECUTE FUNCTION sdi_package_guard();

DROP TRIGGER IF EXISTS trg_sdi_print_out_arch_guard ON "sdi_print_out_arch";
CREATE TRIGGER trg_sdi_print_out_arch_guard
  BEFORE INSERT OR UPDATE OF "QR Code", "id_print_out", "print_out" ON "sdi_print_out_arch"
  FOR EACH ROW EXECUTE FUNCTION sdi_package_guard();

-- "packaged rows must remain approved" — block unapprove (QR_codes uses QR_code_ID)
CREATE OR REPLACE FUNCTION sdi_block_unapprove_qr() RETURNS trigger AS $fn$
DECLARE qn text := upper(btrim(coalesce(NEW."QR_code_ID"::text, '')));
BEGIN
  IF btrim(coalesce(NEW."Approved"::text, '')) = '1' THEN RETURN NEW; END IF;
  IF EXISTS (SELECT 1 FROM "sdi_print_out"      WHERE upper(btrim("QR Code"::text)) = qn)
  OR EXISTS (SELECT 1 FROM "sdi_print_out_arch" WHERE upper(btrim("QR Code"::text)) = qn) THEN
    RAISE EXCEPTION 'SDI guardrail: packaged QR_codes rows must remain approved'; END IF;
  RETURN NEW;
END; $fn$ LANGUAGE plpgsql;

-- block unapprove on the source tables (use "QR Code")
CREATE OR REPLACE FUNCTION sdi_block_unapprove_src() RETURNS trigger AS $fn$
DECLARE qn text := upper(btrim(coalesce(NEW."QR Code"::text, '')));
BEGIN
  IF btrim(coalesce(NEW."Approved"::text, '')) = '1' THEN RETURN NEW; END IF;
  IF EXISTS (SELECT 1 FROM "sdi_print_out"      WHERE upper(btrim("QR Code"::text)) = qn)
  OR EXISTS (SELECT 1 FROM "sdi_print_out_arch" WHERE upper(btrim("QR Code"::text)) = qn) THEN
    RAISE EXCEPTION 'SDI guardrail: packaged source rows must remain approved'; END IF;
  RETURN NEW;
END; $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_qr_codes_block_packaged_unapprove ON "QR_codes";
CREATE TRIGGER trg_qr_codes_block_packaged_unapprove
  BEFORE UPDATE OF "Approved" ON "QR_codes" FOR EACH ROW EXECUTE FUNCTION sdi_block_unapprove_qr();
DROP TRIGGER IF EXISTS trg_sdi_dataset_block_packaged_unapprove ON "sdi_dataset";
CREATE TRIGGER trg_sdi_dataset_block_packaged_unapprove
  BEFORE UPDATE OF "Approved" ON "sdi_dataset" FOR EACH ROW EXECUTE FUNCTION sdi_block_unapprove_src();
DROP TRIGGER IF EXISTS trg_sdi_dataset_el_block_packaged_unapprove ON "sdi_dataset_EL";
CREATE TRIGGER trg_sdi_dataset_el_block_packaged_unapprove
  BEFORE UPDATE OF "Approved" ON "sdi_dataset_EL" FOR EACH ROW EXECUTE FUNCTION sdi_block_unapprove_src();
