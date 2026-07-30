"""Phase B relationship guardrails for QR_codes.db (SAFE SUBSET).

See DATABASE_RELATIONSHIP_IMPROVEMENT_PLAN.md section 5.3 "Phase B". This is the
NON-DESTRUCTIVE hardening phase: it adds indexes and ONE cascade-delete trigger.
It rebuilds no tables, adds no declared foreign keys, and changes no data.

Scope was narrowed from the original plan after a write-path code analysis
(2026-06-04). Objects that would break live workflows are intentionally OMITTED
and tracked in DEFERRED below.

INSTALLS:
  Trigger:
    trg_qr_codes_cascade_delete  AFTER DELETE ON QR_codes -> delete child rows
      in sdi_dataset, sdi_dataset_EL, process_type, json_files, QR_code_assets.
      Safe: QR rename is UPDATE-only (never deletes the parent); packaged QRs
      cannot be deleted (existing trg_qr_codes_block_packaged_delete aborts
      first, so this AFTER trigger never fires for them); purge wants children
      gone. Also fixes purge_qr_from_db.py omitting sdi_dataset_EL/json_files.
  Unique indexes (normalized, partial -- mirror idx_qr_codes_qr_code_id_norm_unique):
    ux_process_type_qr_norm          process_type("QR Code")
    ux_buildings_code_norm           Buildings(Code)
    ux_ubc_master_code_norm          "UBC - Asset Data Master Info"(Code)
    ux_main_asset_desc_norm          main_asset_description(main_asset)
    ux_attribute_code_norm           Attribute(Code)
    ux_bf_app_type_code_norm         bf_applicaton_type(Code)

DEFERRED (need coordinated app changes -- NOT installed here):
  * UNIQUE on sdi_dataset(QR) -- review upsert keys on (QR,Building); cross-building
    INSERT would raise IntegrityError. Needs an app-side handler first.
  * UNIQUE on sdi_dataset_EL -- SLD "missed asset" path bare-inserts duplicate
    (QR,Building) rows by design (review/.../sld_blueprint.py).
  * Any index/trigger on json_files -- dropped every cron run by
    to_sql(if_exists='replace') in API/updating_process_database.py.
  * Parent-must-exist INSERT/UPDATE guards on child tables -- risk 500s in
    review/SLD/cron writers; cascade-delete already covers the orphan-from-delete
    vector and capture inserts parent-first.
  * UNIQUE on Asset_Group(Name) -- 6 duplicate name groups; resolve first.

Usage:
    python scripts/phase_b_relationship_guardrails.py            # dry-run (default)
    python scripts/phase_b_relationship_guardrails.py --apply     # backup + install
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db"

# Unique indexes: (index_name, table, column_expr_for_uniqueness)
NORM = 'UPPER(TRIM(COALESCE(CAST({col} AS TEXT), \'\')))'
NONBLANK = 'TRIM(COALESCE(CAST({col} AS TEXT), \'\')) <> \'\''

UNIQUE_INDEXES = [
    ("ux_process_type_qr_norm",   "process_type",                  '"QR Code"'),
    ("ux_buildings_code_norm",    "Buildings",                     '"Code"'),
    ("ux_ubc_master_code_norm",   "UBC - Asset Data Master Info",  '"Code"'),
    ("ux_main_asset_desc_norm",   "main_asset_description",        '"main_asset"'),
    ("ux_attribute_code_norm",    "Attribute",                     '"Code"'),
    ("ux_bf_app_type_code_norm",  "bf_applicaton_type",            '"Code"'),
]

CASCADE_TRIGGER_NAME = "trg_qr_codes_cascade_delete"
CASCADE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_qr_codes_cascade_delete
AFTER DELETE ON "QR_codes"
FOR EACH ROW
BEGIN
    DELETE FROM "sdi_dataset"
     WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
           UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')));
    DELETE FROM "sdi_dataset_EL"
     WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
           UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')));
    DELETE FROM "process_type"
     WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
           UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')));
    DELETE FROM "json_files"
     WHERE UPPER(TRIM(COALESCE(CAST("code" AS TEXT), ''))) =
           UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')));
    DELETE FROM "QR_code_assets"
     WHERE UPPER(TRIM(COALESCE(CAST(
             CASE WHEN INSTR("code_assets", ' ') > 0
                  THEN SUBSTR("code_assets", 1, INSTR("code_assets", ' ') - 1)
                  ELSE "code_assets" END AS TEXT), ''))) =
           UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')));
END;
"""


def obj_exists(conn, kind, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?", (kind, name)
    ).fetchone() is not None


def dup_groups(conn, table, col_expr):
    n = NORM.format(col=col_expr)
    nb = NONBLANK.format(col=col_expr)
    return conn.execute(
        f'SELECT COUNT(*) FROM (SELECT {n} k, COUNT(*) c FROM "{table}" '
        f'WHERE {nb} GROUP BY k HAVING c > 1)'
    ).fetchone()[0]


def index_sql(name, table, col_expr):
    n = NORM.format(col=col_expr)
    nb = NONBLANK.format(col=col_expr)
    return (f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}" '
            f'ON "{table}" ({n}) WHERE {nb};')


def preflight(conn):
    findings = []
    plan = {"indexes_to_create": [], "indexes_already_present": [],
            "trigger_to_create": None, "trigger_already_present": False}
    for name, table, col in UNIQUE_INDEXES:
        if not obj_exists(conn, "table", table):
            findings.append({"severity": "blocking", "detail": f"missing table {table!r}"})
            continue
        d = dup_groups(conn, table, col)
        if d > 0:
            findings.append({"severity": "blocking",
                             "detail": f"{table}.{col} has {d} duplicate-key group(s); cannot create UNIQUE index {name}"})
        if obj_exists(conn, "index", name):
            plan["indexes_already_present"].append(name)
        else:
            plan["indexes_to_create"].append({"name": name, "table": table, "column": col, "dup_groups": d})
    if obj_exists(conn, "trigger", CASCADE_TRIGGER_NAME):
        plan["trigger_already_present"] = True
    else:
        plan["trigger_to_create"] = CASCADE_TRIGGER_NAME
    return plan, findings


def create_backup(db_path):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak_{ts}_phase_b")
    # WAL-safe snapshot via the SQLite backup API (a bare file copy of the .db
    # can miss un-checkpointed -wal content when the DB is in WAL mode).
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return backup


def run(db_path, apply):
    mode = "apply" if apply else "dry_run"
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    plan, findings = preflight(conn)
    blocking = [f for f in findings if f["severity"] == "blocking"]

    print(f"\n=== Phase B relationship guardrails ({mode}) :: {db_path} ===")
    print(f"\nUnique indexes to create: {len(plan['indexes_to_create'])}")
    for ix in plan["indexes_to_create"]:
        print(f"    CREATE {ix['name']:<26} on {ix['table']}({ix['column']})  [dup_groups={ix['dup_groups']}]")
    if plan["indexes_already_present"]:
        print(f"  already present (skip): {plan['indexes_already_present']}")
    print(f"\nCascade-delete trigger: {'CREATE ' + CASCADE_TRIGGER_NAME if plan['trigger_to_create'] else 'already present (skip)'}")
    if findings:
        print("\nPREFLIGHT FINDINGS:")
        for f in findings:
            print(f"    [{f['severity']}] {f['detail']}")

    applied = {"indexes_created": [], "trigger_created": False}
    if apply:
        if blocking:
            print("\nAPPLY BLOCKED by preflight findings above.", file=sys.stderr)
            conn.close()
            return 1
        backup = create_backup(db_path)
        print(f"\nBackup created: {backup}")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for ix in plan["indexes_to_create"]:
                conn.execute(index_sql(ix["name"], ix["table"], ix["column"]))
                applied["indexes_created"].append(ix["name"])
            if plan["trigger_to_create"]:
                conn.executescript(CASCADE_TRIGGER_SQL)
                applied["trigger_created"] = True
            conn.commit()
            print(f"\nAPPLIED: {json.dumps(applied)}")
        except Exception as exc:
            conn.rollback()
            print(f"\nROLLED BACK due to error: {exc}", file=sys.stderr)
            conn.close()
            return 1

    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    rpt = logs / f"phase_b_guardrails_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    rpt.write_text(json.dumps({"mode": mode, "db_path": str(db_path),
                               "plan": plan, "findings": findings,
                               "applied": applied if apply else None}, indent=2), encoding="utf-8")
    print(f"\nReport written: {rpt}")
    if not apply:
        print("\nDry-run only. Re-run with --apply to back up and install.")
    conn.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Phase B relationship guardrails (safe subset) for QR_codes.db")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="Create a backup and install indexes/trigger.")
    args = ap.parse_args()
    return run(args.db.resolve(), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
