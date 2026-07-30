"""Phase A data cleanup for QR_codes.db.

See DATABASE_RELATIONSHIP_IMPROVEMENT_PLAN.md, section 5.3 "Phase A". This is
the data-cleanup phase only -- it makes NO schema changes (no indexes, no FKs,
no triggers). It is idempotent and dry-run by default.

Actions (each logged to audit_trail, source='system', app_name='phase_a_cleanup',
inside one transaction so data + audit commit or roll back together):

  1. Delete 2 orphan rows that have no QR_codes parent, from sdi_dataset and
     process_type: 0000182427, 0000182428 (Building 750, unapproved, unpackaged).
  2. Delete the duplicate sdi_dataset row for QR 0000184805 -- KEEP Building 217
     (UBC Tag SP-1/2), DELETE the Building 063 row (operator decision 2026-06-04).
  3. Delete the sdi_dataset_EL row for QR 0000184490 -- discipline resolved to
     Mechanical; remove the spurious unapproved EL row (operator decision).
  4. Normalize Approved to the '0'/'1' convention:
        QR_codes      : NULL/'' -> '0'   (non-packaged rows only)
        sdi_dataset_EL: ''      -> '0'   (non-packaged rows only)
     (sdi_dataset already uses '0'/'1'; left as-is.)
  5. Report-only: NULL asset_type / Building Code counts. Phase A intentionally
     leaves these unchanged (accept-and-document; see plan section 3.5/8.2).

Usage:
    python scripts/phase_a_data_cleanup.py            # dry-run (default)
    python scripts/phase_a_data_cleanup.py --apply     # backup + write
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.logger import log_change  # noqa: E402

DEFAULT_DB = ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db"
APP_NAME = "phase_a_cleanup"

ORPHAN_QRS = ("0000182427", "0000182428")
DUP_QR = "0000184805"
DUP_DELETE_BUILDING = "063"      # keep 217 (SP-1/2)
CROSS_QR = "0000184490"          # delete the EL row, keep ME

NORM = "UPPER(TRIM(COALESCE(CAST({} AS TEXT), '')))"


def _packaged_qrs(conn: sqlite3.Connection) -> set[str]:
    qrs: set[str] = set()
    for tbl in ("sdi_print_out", "sdi_print_out_arch"):
        rows = conn.execute(
            f'SELECT DISTINCT {NORM.format(chr(34)+"QR Code"+chr(34))} q '
            f'FROM "{tbl}" WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT),\'\'))<>\'\''
        ).fetchall()
        qrs.update(r[0] for r in rows if r[0])
    return qrs


def _row_dict(conn: sqlite3.Connection, table: str, rowid: int) -> dict:
    cur = conn.execute(f'SELECT * FROM "{table}" WHERE rowid=?', (rowid,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else {}


def plan_deletes(conn: sqlite3.Connection) -> list[dict]:
    """Return ordered delete operations as {table, rowid, qr, snapshot, reason}."""
    ops: list[dict] = []

    # 1. Orphans in sdi_dataset + process_type (QR not in QR_codes)
    for qr in ORPHAN_QRS:
        for table in ("sdi_dataset", "process_type"):
            rows = conn.execute(
                f'SELECT rowid FROM "{table}" WHERE {NORM.format(chr(34)+"QR Code"+chr(34))}=?',
                (qr,),
            ).fetchall()
            for (rid,) in rows:
                ops.append({
                    "table": table, "rowid": rid, "qr": qr,
                    "snapshot": _row_dict(conn, table, rid),
                    "reason": "orphan: QR has no QR_codes parent (unapproved, unpackaged)",
                })

    # 2. Duplicate sdi_dataset row for DUP_QR -> delete Building 063 only
    rows = conn.execute(
        f'SELECT rowid, "Building" FROM sdi_dataset WHERE {NORM.format(chr(34)+"QR Code"+chr(34))}=?',
        (DUP_QR,),
    ).fetchall()
    for rid, building in rows:
        if (building or "").strip().upper() == DUP_DELETE_BUILDING.upper():
            ops.append({
                "table": "sdi_dataset", "rowid": rid, "qr": DUP_QR,
                "snapshot": _row_dict(conn, "sdi_dataset", rid),
                "reason": f"duplicate QR identity: keep Building 217 (SP-1/2), delete Building {building}",
            })

    # 3. Cross-discipline EL row for CROSS_QR -> delete (keep ME)
    rows = conn.execute(
        f'SELECT rowid FROM sdi_dataset_EL WHERE {NORM.format(chr(34)+"QR Code"+chr(34))}=?',
        (CROSS_QR,),
    ).fetchall()
    for (rid,) in rows:
        ops.append({
            "table": "sdi_dataset_EL", "rowid": rid, "qr": CROSS_QR,
            "snapshot": _row_dict(conn, "sdi_dataset_EL", rid),
            "reason": "discipline-isolation breach resolved to Mechanical; remove spurious unapproved EL row",
        })

    return ops


def plan_approved_normalization(
    conn: sqlite3.Connection, packaged: set[str], delete_keys: set[tuple] | None = None
) -> list[dict]:
    """Return Approved-normalization ops as {table, rowid, qr, old, reason}.

    Rows in delete_keys (table, rowid) are skipped -- they are being deleted in
    steps 1-3, so normalizing them would log an update for a vanished row.
    """
    delete_keys = delete_keys or set()
    ops: list[dict] = []

    # QR_codes: NULL/'' -> '0' (skip packaged)
    rows = conn.execute(
        'SELECT rowid, QR_code_ID, Approved FROM QR_codes '
        "WHERE TRIM(COALESCE(CAST(Approved AS TEXT),''))=''"
    ).fetchall()
    for rid, qr, old in rows:
        if ("QR_codes", rid) in delete_keys:
            continue
        if (qr or "").strip().upper() in packaged:
            continue  # never unapprove a packaged QR (guardrail would block anyway)
        ops.append({"table": "QR_codes", "rowid": rid, "qr": qr, "old": old,
                    "field": "Approved", "new": "0",
                    "reason": "normalize Approved encoding NULL/'' -> '0'"})

    # sdi_dataset_EL: '' -> '0' (skip packaged)
    rows = conn.execute(
        'SELECT rowid, "QR Code", Approved FROM sdi_dataset_EL '
        "WHERE TRIM(COALESCE(CAST(Approved AS TEXT),''))=''"
    ).fetchall()
    for rid, qr, old in rows:
        if ("sdi_dataset_EL", rid) in delete_keys:
            continue
        if (qr or "").strip().upper() in packaged:
            continue
        ops.append({"table": "sdi_dataset_EL", "rowid": rid, "qr": qr, "old": old,
                    "field": "Approved", "new": "0",
                    "reason": "normalize Approved encoding '' -> '0'"})
    return ops


def report_only(conn: sqlite3.Connection) -> dict:
    null_asset_type = conn.execute(
        "SELECT COUNT(*) FROM QR_codes WHERE TRIM(COALESCE(asset_type,''))=''"
    ).fetchone()[0]
    null_building = conn.execute(
        "SELECT COUNT(*) FROM QR_codes WHERE TRIM(COALESCE(\"Building Code\",''))=''"
    ).fetchone()[0]
    return {"qr_codes_null_asset_type": null_asset_type,
            "qr_codes_null_building_code": null_building,
            "note": "Phase A leaves these unchanged (accept-and-document)."}


def create_backup(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak_{ts}_phase_a")
    # WAL-safe: the SQLite backup API produces a complete, consistent snapshot
    # including un-checkpointed -wal content. A bare shutil.copy2 of the .db file
    # alone can miss recent commits when the DB is in WAL mode (the VM is).
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return backup


def run(db_path: Path, apply: bool) -> int:
    mode = "apply" if apply else "dry_run"
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")

    packaged = _packaged_qrs(conn)
    deletes = plan_deletes(conn)
    delete_keys = {(op["table"], op["rowid"]) for op in deletes}
    approved = plan_approved_normalization(conn, packaged, delete_keys)
    info = report_only(conn)

    print(f"\n=== Phase A cleanup ({mode}) :: {db_path} ===")
    print(f"\n[1-3] Row deletions planned: {len(deletes)}")
    for op in deletes:
        print(f"    DELETE {op['table']:<16} qr={op['qr']}  ({op['reason']})")
    print(f"\n[4] Approved normalizations planned: {len(approved)}")
    by_tbl: dict[str, int] = {}
    for op in approved:
        by_tbl[op["table"]] = by_tbl.get(op["table"], 0) + 1
    for tbl, n in by_tbl.items():
        print(f"    UPDATE {tbl:<16} Approved -> '0' on {n} non-packaged row(s)")
    print(f"\n[5] Report-only (unchanged): {json.dumps(info)}")

    applied = {"deletes": 0, "approved_updates": 0, "audit_rows": 0}

    if apply:
        backup = create_backup(db_path)
        print(f"\nBackup created: {backup}")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for op in deletes:
                audit_n = log_change(
                    conn, qr_code=op["qr"], app_name=APP_NAME, table_name=op["table"],
                    record_pk=op["qr"], op_type="DELETE", source="system", modified_by="system",
                    description=f"Phase A cleanup: {op['reason']}",
                    field_changes={"(deleted_row)": (op["snapshot"], None)},
                )
                conn.execute(f'DELETE FROM "{op["table"]}" WHERE rowid=?', (op["rowid"],))
                applied["deletes"] += 1
                applied["audit_rows"] += audit_n

            for op in approved:
                audit_n = log_change(
                    conn, qr_code=op["qr"], app_name=APP_NAME, table_name=op["table"],
                    record_pk=op["qr"], op_type="UPDATE", source="system", modified_by="system",
                    description=f"Phase A cleanup: {op['reason']}",
                    field_changes={op["field"]: (op["old"], op["new"])},
                )
                conn.execute(
                    f'UPDATE "{op["table"]}" SET "{op["field"]}"=? WHERE rowid=?',
                    (op["new"], op["rowid"]),
                )
                applied["approved_updates"] += 1
                applied["audit_rows"] += audit_n

            conn.commit()
            print(f"\nAPPLIED: {json.dumps(applied)}")
        except Exception as exc:
            conn.rollback()
            print(f"\nROLLED BACK due to error: {exc}", file=sys.stderr)
            conn.close()
            return 1

    # write report
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    report_path = logs / f"phase_a_cleanup_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps({
        "mode": mode, "db_path": str(db_path),
        "planned_deletes": [{k: v for k, v in o.items() if k != "snapshot"} for o in deletes],
        "planned_approved_updates": len(approved),
        "approved_by_table": by_tbl,
        "report_only": info, "applied": applied if apply else None,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nReport written: {report_path}")
    if not apply:
        print("\nDry-run only. Re-run with --apply to back up and write.")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase A data cleanup for QR_codes.db")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="Create a backup and write changes.")
    args = ap.parse_args()
    return run(args.db.resolve(), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
