-- Convert electrical_building_schema.ID_check from a stored TEXT column to a
-- GENERATED VIRTUAL column so its value is always live with respect to
-- Building / Equipment ID / Supply From. Removes the staleness window where a
-- stored ID_check still held the pre-edit composite key after Supply From was
-- updated, which caused the Check column to read False even when the SLD and
-- sdi_dataset_EL rows agreed.
--
-- sdi_dataset_EL.ID_check is already a GENERATED VIRTUAL column; this migration
-- brings the SLD side to parity.

BEGIN;

ALTER TABLE "electrical_building_schema" DROP COLUMN "ID_check";

ALTER TABLE "electrical_building_schema" ADD COLUMN "ID_check" TEXT
    GENERATED ALWAYS AS (
        TRIM(COALESCE("Building", '')) || ' | ' ||
        TRIM(COALESCE("Equipment ID", '')) || ' | ' ||
        TRIM(COALESCE("Supply From", ''))
    ) VIRTUAL;

COMMIT;
