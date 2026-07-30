#!/usr/bin/env bash
set -euo pipefail

# scripts/add_flagged_column_sqlite.sh
# Usage: ./scripts/add_flagged_column_sqlite.sh /path/to/database.db
# This script creates a timestamped backup of the SQLite DB, checks for an existing
# `Flagged` column on table `sdi_dataset_EL`, and if missing, adds it and updates existing rows.

DB_PATH="${1:-}"
# Optional second argument: table name (default: sdi_dataset_EL)
TABLE_NAME="${2:-sdi_dataset_EL}"
if [[ -z "$DB_PATH" ]]; then
  echo "Usage: $0 /path/to/database.db"
  exit 2
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "Error: database file '$DB_PATH' not found."
  exit 3
fi

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
BACKUP_PATH="${DB_PATH%.*}--backup-${TIMESTAMP}.${DB_PATH##*.}"

echo "Backing up '$DB_PATH' to '$BACKUP_PATH'..."
cp -p "$DB_PATH" "$BACKUP_PATH"

echo "Checking if table '$TABLE_NAME' exists..."
if ! sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='table' AND name='$TABLE_NAME' LIMIT 1;" | grep -q "$TABLE_NAME"; then
  echo "Error: table '$TABLE_NAME' does not exist in $DB_PATH"
  echo "Backup created at: $BACKUP_PATH"
  exit 4
fi

echo "Checking if column 'Flagged' already exists in '$TABLE_NAME'..."
if sqlite3 "$DB_PATH" "PRAGMA table_info('$TABLE_NAME');" | awk -F'|' '{print $2}' | grep -qx "Flagged"; then
  echo "Column 'Flagged' already exists. No changes made."
  exit 0
fi

echo "Applying ALTER TABLE and updating existing rows on '$TABLE_NAME'..."
sqlite3 "$DB_PATH" <<SQL
BEGIN;
ALTER TABLE $TABLE_NAME ADD COLUMN Flagged TEXT DEFAULT '0';
UPDATE $TABLE_NAME SET Flagged = '0' WHERE Flagged IS NULL;
COMMIT;
SQL

echo "Migration applied. Verifying results..."
sqlite3 "$DB_PATH" <<SQL
PRAGMA table_info('$TABLE_NAME');
SELECT COUNT(*) AS total, SUM(CASE WHEN Flagged IS NULL THEN 1 ELSE 0 END) AS nulos FROM $TABLE_NAME;
SQL

echo "Done. Backup available at: $BACKUP_PATH"
