-- C4/C3 follow-up: auto-populate QR_code_assets.qr_code_id from the code_assets prefix on INSERT.
-- c3 §3 backfilled existing rows once; new Capture inserts leave it NULL. Capture upserts the
-- QR_codes parent BEFORE inserting child asset rows on the same connection, so the derived id
-- always has a valid FK parent. Keeps the app backend-agnostic. Idempotent.
CREATE OR REPLACE FUNCTION trg_qca_fill_qr_code_id() RETURNS trigger AS $$
BEGIN
  IF NEW."qr_code_id" IS NULL OR btrim(NEW."qr_code_id") = '' THEN
    NEW."qr_code_id" := btrim(split_part(NEW."code_assets", ' ', 1));
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_qca_set_qr_code_id ON "QR_code_assets";
CREATE TRIGGER trg_qca_set_qr_code_id
  BEFORE INSERT ON "QR_code_assets"
  FOR EACH ROW EXECUTE FUNCTION trg_qca_fill_qr_code_id();
