BEGIN TRANSACTION;

-- SQLQuery.sql
-- Purpose: Add a new column `Flagged` to table `sdi_dataset_EL` (SQLite-safe).
-- DB: SQLite (used by AssetCap server). This file contains the SQL to run; for a safe, idempotent
-- execution use the helper script `scripts/add_flagged_column_sqlite.sh` which will create a backup,
-- check if the column already exists and apply the ALTER + UPDATE if needed.

BEGIN TRANSACTION;

-- Add the new column with default '0' (SQLite accepts ADD COLUMN with a constant default)
ALTER TABLE sdi_dataset_EL
ADD COLUMN Flagged TEXT DEFAULT '0';

-- Ensure existing rows are set to the default value
UPDATE sdi_dataset_EL
SET Flagged = '0'
WHERE Flagged IS NULL;

COMMIT;

-- Verification queries (run after the migration if you want to inspect results):
PRAGMA table_info('sdi_dataset_EL');
SELECT COUNT(*) AS total, SUM(CASE WHEN Flagged IS NULL THEN 1 ELSE 0 END) AS nulos
FROM sdi_dataset_EL;

-- Notes:
-- 1) SQLite does not support DROP COLUMN directly; rolling back would require recreating the table.
-- 2) The helper script will copy the DB file to a timestamped backup before modifying it.