-- C4: port audit_trail secondary indexes to PostgreSQL.
-- C3 created only pk_audit_trail; the 5 lookup indexes that exist on SQLite
-- (see scripts/migrate_create_audit_trail.py) were never ported -> audit-log
-- lookups would be unindexed post-cutover. Idempotent. Fold into canonical C3.
CREATE INDEX IF NOT EXISTS ix_audit_trail_qr        ON audit_trail(qr_code);
CREATE INDEX IF NOT EXISTS ix_audit_trail_date      ON audit_trail(modification_date);
CREATE INDEX IF NOT EXISTS ix_audit_trail_user      ON audit_trail(modified_by);
CREATE INDEX IF NOT EXISTS ix_audit_trail_app_table ON audit_trail(app_name, table_name);
CREATE INDEX IF NOT EXISTS ix_audit_trail_record    ON audit_trail(table_name, record_pk);
