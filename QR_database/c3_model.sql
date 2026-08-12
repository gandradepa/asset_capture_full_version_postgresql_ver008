-- C3 structural relationship model on qr_code_db (parallel, isolated mirror).
-- Run with: psql -1 -v ON_ERROR_STOP=1 -f c3_model.sql  (all-or-nothing).
-- DEFERRED (not here): column TYPE refinement (text->bigint/double/timestamptz);
-- trigger porting to PL/pgSQL (C4, where the apps exercise it).

-- 0. Normalize empty-string nullable FK columns to NULL (so '' doesn't fail the FK)
UPDATE "QR_codes"    SET "Building Code"=NULL WHERE btrim(coalesce("Building Code",''))='';
UPDATE "sdi_dataset" SET "Main Asset"   =NULL WHERE btrim(coalesce("Main Asset",''))='';

-- 1. Parent keys / FK targets (must exist before FKs reference them)
ALTER TABLE "QR_codes"               ADD CONSTRAINT pk_qr_codes        PRIMARY KEY ("QR_code_ID");
ALTER TABLE "Buildings"              ADD CONSTRAINT ux_buildings_code   UNIQUE ("Code");
ALTER TABLE "main_asset_description" ADD CONSTRAINT ux_main_asset       UNIQUE ("main_asset");

-- 2. Child / other keys
ALTER TABLE "sdi_dataset"            ADD CONSTRAINT pk_sdi_dataset      PRIMARY KEY ("QR Code");
ALTER TABLE "sdi_dataset_EL"         ADD CONSTRAINT pk_sdi_dataset_el   PRIMARY KEY ("QR Code");
ALTER TABLE "process_type"           ADD CONSTRAINT pk_process_type     PRIMARY KEY ("QR Code");
ALTER TABLE "json_files"             ADD CONSTRAINT pk_json_files       PRIMARY KEY ("code");
ALTER TABLE "QR_code_assets"         ADD CONSTRAINT pk_qr_code_assets   PRIMARY KEY ("ID");
ALTER TABLE "audit_trail"            ADD CONSTRAINT pk_audit_trail      PRIMARY KEY ("id");
ALTER TABLE "electrical_building_schema" ADD CONSTRAINT pk_ebs          PRIMARY KEY ("row_id");
ALTER TABLE "temp_code"              ADD CONSTRAINT pk_temp_code        PRIMARY KEY ("ID");
ALTER TABLE "schema_migrations"      ADD CONSTRAINT pk_schema_migr      PRIMARY KEY ("filename");
ALTER TABLE "new_device"             ADD CONSTRAINT ux_new_device_index UNIQUE ("index");
ALTER TABLE "sdi_print_out"          ADD CONSTRAINT ux_sdi_print_out_qr      UNIQUE ("QR Code");
ALTER TABLE "sdi_print_out_arch"     ADD CONSTRAINT ux_sdi_print_out_arch_qr UNIQUE ("QR Code");
ALTER TABLE "Attribute"              ADD CONSTRAINT ux_attribute_code   UNIQUE ("Code");
ALTER TABLE "bf_applicaton_type"     ADD CONSTRAINT ux_bf_app_code      UNIQUE ("Code");

-- 3. Explicit FK column on QR_code_assets (replaces string-prefix coupling)
ALTER TABLE "QR_code_assets" ADD COLUMN "qr_code_id" text;
UPDATE "QR_code_assets" SET "qr_code_id" = btrim(split_part("code_assets", ' ', 1));
CREATE INDEX ix_qca_qr_code_id ON "QR_code_assets" ("qr_code_id");

-- 4. Foreign keys (QR hub + validated lookups) — actions per plan section 4.2
ALTER TABLE "QR_code_assets"     ADD CONSTRAINT fk_qca_qr    FOREIGN KEY ("qr_code_id") REFERENCES "QR_codes"("QR_code_ID") ON DELETE CASCADE  ON UPDATE CASCADE;
ALTER TABLE "sdi_dataset"        ADD CONSTRAINT fk_sdi_qr    FOREIGN KEY ("QR Code")    REFERENCES "QR_codes"("QR_code_ID") ON DELETE CASCADE  ON UPDATE CASCADE;
ALTER TABLE "sdi_dataset_EL"     ADD CONSTRAINT fk_sdiel_qr  FOREIGN KEY ("QR Code")    REFERENCES "QR_codes"("QR_code_ID") ON DELETE CASCADE  ON UPDATE CASCADE;
ALTER TABLE "process_type"       ADD CONSTRAINT fk_pt_qr     FOREIGN KEY ("QR Code")    REFERENCES "QR_codes"("QR_code_ID") ON DELETE CASCADE  ON UPDATE CASCADE;
ALTER TABLE "json_files"         ADD CONSTRAINT fk_jf_qr     FOREIGN KEY ("code")       REFERENCES "QR_codes"("QR_code_ID") ON DELETE CASCADE  ON UPDATE CASCADE;
ALTER TABLE "sdi_print_out"      ADD CONSTRAINT fk_spo_qr    FOREIGN KEY ("QR Code")    REFERENCES "QR_codes"("QR_code_ID") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "sdi_print_out_arch" ADD CONSTRAINT fk_spoa_qr   FOREIGN KEY ("QR Code")    REFERENCES "QR_codes"("QR_code_ID") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "QR_codes"           ADD CONSTRAINT fk_qr_building   FOREIGN KEY ("Building Code") REFERENCES "Buildings"("Code")                 ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "sdi_dataset"        ADD CONSTRAINT fk_sdi_mainasset FOREIGN KEY ("Main Asset")    REFERENCES "main_asset_description"("main_asset") ON DELETE RESTRICT ON UPDATE CASCADE;

-- 5. CHECK constraints
ALTER TABLE "QR_codes"       ADD CONSTRAINT chk_qr_approved     CHECK ("Approved" IN ('0','1',''));
ALTER TABLE "sdi_dataset"    ADD CONSTRAINT chk_sdi_approved    CHECK ("Approved" IN ('0','1',''));
ALTER TABLE "sdi_dataset_EL" ADD CONSTRAINT chk_sdiel_approved  CHECK ("Approved" IN ('0','1',''));
ALTER TABLE "audit_trail"    ADD CONSTRAINT chk_audit_optype    CHECK ("op_type" IN ('INSERT','UPDATE','DELETE'));

-- 6. Generated ID_check (STORED) — exact SQLite expressions, now indexable
ALTER TABLE "sdi_dataset_EL" ADD COLUMN "ID_check" text
  GENERATED ALWAYS AS (btrim(coalesce("Building",'')) || ' | ' || btrim(coalesce("UBC Asset Tag",'')) || ' | ' || btrim(coalesce("Supply From",''))) STORED;
ALTER TABLE "electrical_building_schema" ADD COLUMN "ID_check" text
  GENERATED ALWAYS AS (btrim(coalesce("Building",'')) || ' | ' || btrim(coalesce("Equipment ID",'')) || ' | ' || btrim(coalesce("Supply From",''))) STORED;
CREATE INDEX ix_sdiel_id_check ON "sdi_dataset_EL" ("ID_check");
CREATE INDEX ix_ebs_id_check   ON "electrical_building_schema" ("ID_check");

-- 7. App-role privileges (prep for C4)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO assetcap_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO assetcap_app;
