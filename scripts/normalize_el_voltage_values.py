#!/usr/bin/env python3
"""Normalize EL voltage values to the bare form without embedded unit letters.

The canonical stored value is ``208/120`` (or ``600-208Y/120``, ``208Y/120``,
``600``); the unit is carried separately in ``Voltage Rating (UoM)`` (``VLT``
on the SDI tables, ``V`` on the SLD schema table). Legacy rows stored the unit
inside the value (``208/120V``), which display layers that append their own
unit then doubled into ``208/120VV``.

Repairs "Voltage Rating" and legacy "Volts" columns across the EL tables and
the structured "Volts" field in Output_jason_api ``*_EL_*.json`` payloads.

On ``electrical_building_schema`` ONLY, also normalizes the UoM columns to the
drawing-native display units (``VLT`` -> ``V``, ``AMP`` -> ``A``): rows copied
from the SDI side carry Planon UoM codes that the review-report SLD would
otherwise render verbatim ("208/120VLT | 100AMP"). The SDI tables keep their
``VLT``/``AMP`` codes untouched — that is the intentional Planon convention.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as qrdb

DB_TABLES = (
    "sdi_dataset_EL",
    "sdi_print_out",
    "sdi_print_out_arch",
    "electrical_building_schema",
)

VOLTAGE_COLUMNS = ("Voltage Rating", "Volts")

# Schema-table-only UoM normalization (drawing-native display units).
SCHEMA_UOM_TABLE = "electrical_building_schema"
SCHEMA_UOM_COLUMNS = {
    "Voltage Rating (UoM)": {"VLT": "V"},
    "Amperage Rating (UoM)": {"AMP": "A"},
}

_VOLTAGE_UNIT_SUFFIX_RE = re.compile(r"(?<=\d)\s*V(?:AC|DC|OLTS?)?\b", re.IGNORECASE)


def strip_voltage_unit_letters(value: object) -> str:
    return _VOLTAGE_UNIT_SUFFIX_RE.sub("", str(value or "").strip()).strip()


@dataclass
class TableRepairStats:
    table_name: str
    scanned_rows: int = 0
    changed_rows: int = 0
    skipped: bool = False
    missing_columns: list[str] = field(default_factory=list)


@dataclass
class RepairSummary:
    dry_run: bool
    backup_path: Path | None = None
    snapshot_path: Path | None = None
    table_stats: list[TableRepairStats] = field(default_factory=list)
    row_changes: list[dict] = field(default_factory=list)
    json_scanned: int = 0
    json_changed: int = 0

    @property
    def db_changed_rows(self) -> int:
        return sum(stat.changed_rows for stat in self.table_stats)


def default_db_path() -> Path:
    return REPO_ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db"


def default_json_dir() -> Path:
    return REPO_ROOT / "Output_jason_api"


def _table_exists(conn, table_name: str) -> bool:
    return qrdb.has_table(conn, table_name)


def _table_columns(conn, table_name: str) -> dict:
    """Map column name -> writable? (False for GENERATED/hidden columns).
    PG: information_schema is_generated; SQLite: table_xinfo hidden flag (2/3=generated)."""
    if qrdb.is_postgres():
        cur = qrdb.raw_conn(conn).cursor()
        try:
            cur.execute(
                "SELECT column_name, is_generated FROM information_schema.columns "
                "WHERE table_name=%s", (table_name,))
            return {r[0]: (r[1] != 'ALWAYS') for r in cur.fetchall()}
        finally:
            cur.close()
    rows = conn.execute(f'PRAGMA table_xinfo("{table_name}")').fetchall()
    return {row["name"]: (int(row["hidden"] or 0) == 0) for row in rows}


def _column_writable(columns: dict, column_name: str) -> bool:
    return bool(columns.get(column_name, False))


def _quote(column_name: str) -> str:
    return '"' + column_name.replace('"', '""') + '"'


def _value(row: sqlite3.Row, column_name: str) -> str:
    if column_name not in row.keys():
        return ""
    return str(row[column_name] or "").strip()


def _is_electrical_row(table_name: str, row: sqlite3.Row) -> bool:
    if table_name in {"sdi_dataset_EL", "electrical_building_schema"}:
        return True
    if "Attribute" not in row.keys():
        return True
    return _value(row, "Attribute").casefold() == "electrical"


def _updates_for_row(
    table_name: str,
    columns: dict,
    row: sqlite3.Row,
) -> dict[str, str]:
    if not _is_electrical_row(table_name, row):
        return {}

    updates: dict[str, str] = {}
    for column_name in VOLTAGE_COLUMNS:
        if column_name not in row.keys() or not _column_writable(columns, column_name):
            continue
        current = _value(row, column_name)
        normalized = strip_voltage_unit_letters(current)
        if current != normalized:
            updates[column_name] = normalized

    if table_name == SCHEMA_UOM_TABLE:
        for column_name, mapping in SCHEMA_UOM_COLUMNS.items():
            if column_name not in row.keys() or not _column_writable(columns, column_name):
                continue
            current = _value(row, column_name)
            normalized = mapping.get(current.upper(), current)
            if current != normalized:
                updates[column_name] = normalized
    return updates


def repair_db(conn, dry_run: bool) -> tuple[list[TableRepairStats], list[dict]]:
    stats: list[TableRepairStats] = []
    row_changes: list[dict] = []

    for table_name in DB_TABLES:
        if not _table_exists(conn, table_name):
            stats.append(TableRepairStats(table_name=table_name, skipped=True))
            continue

        columns = _table_columns(conn, table_name)
        if not any(column in columns for column in VOLTAGE_COLUMNS):
            stats.append(
                TableRepairStats(
                    table_name=table_name,
                    skipped=True,
                    missing_columns=list(VOLTAGE_COLUMNS),
                )
            )
            continue

        rowid_expr = "ctid" if qrdb.is_postgres() else "rowid"
        rowid_pred = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
        rows = conn.execute(f'SELECT {rowid_expr} AS _rowid, * FROM "{table_name}"').fetchall()
        table_stats = TableRepairStats(table_name=table_name, scanned_rows=len(rows))
        for row in rows:
            updates = _updates_for_row(table_name, columns, row)
            if not updates:
                continue

            table_stats.changed_rows += 1
            row_changes.append(
                {
                    "table": table_name,
                    "qr_code": _value(row, "QR Code"),
                    "equipment_id": _value(row, "Equipment ID") or _value(row, "UBC Asset Tag"),
                    "changes": {
                        column: {"old": _value(row, column), "new": new_value}
                        for column, new_value in updates.items()
                    },
                }
            )
            if dry_run:
                continue

            set_clause = ", ".join(f"{_quote(column_name)} = ?" for column_name in updates)
            params = list(updates.values()) + [row["_rowid"]]
            conn.execute(f'UPDATE "{table_name}" SET {set_clause} WHERE {rowid_pred}', params)

        stats.append(table_stats)

    return stats, row_changes


def _repair_json_payload(payload: dict) -> bool:
    structured = payload.get("structured_data")
    if not isinstance(structured, dict):
        return False

    changed = False
    current_volts = str(structured.get("Volts", "") or "").strip()
    normalized_volts = strip_voltage_unit_letters(current_volts)
    if current_volts != normalized_volts:
        structured["Volts"] = normalized_volts
        changed = True
    return changed


def repair_json_dir(json_dir: Path, dry_run: bool) -> tuple[int, int]:
    if not json_dir.exists():
        return 0, 0

    scanned = 0
    changed = 0
    for path in sorted(json_dir.glob("*_EL_*.json")):
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        if not _repair_json_payload(payload):
            continue

        changed += 1
        if not dry_run:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")

    return scanned, changed


def create_sqlite_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak_{timestamp}")
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return backup_path


def write_change_snapshot(row_changes: list[dict]) -> Path | None:
    if not row_changes:
        return None
    logs_dir = REPO_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = logs_dir / f"normalize_el_voltage_values_{timestamp}.json"
    snapshot_path.write_text(
        json.dumps(row_changes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return snapshot_path


def run_repair(db_path: Path, json_dir: Path, dry_run: bool, backup: bool = True) -> RepairSummary:
    summary = RepairSummary(dry_run=dry_run)

    if not dry_run and backup:
        if qrdb.is_postgres():
            print("[WARN] PostgreSQL backend: SQLite-file backup skipped. "
                  "Take a pg_dump first if you need a restore point.", file=sys.stderr)
        else:
            summary.backup_path = create_sqlite_backup(db_path)

    conn = qrdb.get_connection(sqlite_path=db_path)
    conn.row_factory = sqlite3.Row
    try:
        if dry_run:
            summary.table_stats, summary.row_changes = repair_db(conn, dry_run=True)
        else:
            if not qrdb.is_postgres():
                conn.execute("BEGIN")
            try:
                summary.table_stats, summary.row_changes = repair_db(conn, dry_run=False)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            summary.snapshot_path = write_change_snapshot(summary.row_changes)

        summary.json_scanned, summary.json_changed = repair_json_dir(json_dir, dry_run=dry_run)
    finally:
        conn.close()

    return summary


def _print_summary(summary: RepairSummary) -> None:
    mode = "DRY RUN" if summary.dry_run else "APPLIED"
    print(f"{mode}: EL voltage value normalization (bare value, unit in UoM column)")
    if summary.backup_path:
        print(f"Database backup: {summary.backup_path}")
    if summary.snapshot_path:
        print(f"Old/new change snapshot: {summary.snapshot_path}")
    for stat in summary.table_stats:
        if stat.skipped:
            reason = f" missing {', '.join(stat.missing_columns)}" if stat.missing_columns else ""
            print(f"- {stat.table_name}: skipped{reason}")
            continue
        print(f"- {stat.table_name}: {stat.changed_rows} changed / {stat.scanned_rows} scanned")
    print(f"- JSON: {summary.json_changed} changed / {summary.json_scanned} scanned")
    if summary.dry_run and summary.row_changes:
        print("Sample changes (up to 10):")
        for change in summary.row_changes[:10]:
            details = ", ".join(
                f'{column}: "{pair["old"]}" -> "{pair["new"]}"'
                for column, pair in change["changes"].items()
            )
            label = change["qr_code"] or change["equipment_id"] or "?"
            print(f"  [{change['table']}] {label}: {details}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=default_db_path())
    parser.add_argument("--json-dir", type=Path, default=default_json_dir())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = args.db_path.resolve()
    json_dir = args.json_dir.resolve()

    if not qrdb.is_postgres() and not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if not json_dir.exists():
        print(f"JSON directory not found: {json_dir}", file=sys.stderr)
        return 2

    try:
        summary = run_repair(db_path, json_dir, dry_run=args.dry_run, backup=not args.no_backup)
    except Exception as exc:
        print(f"Repair failed: {exc}", file=sys.stderr)
        return 1

    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
