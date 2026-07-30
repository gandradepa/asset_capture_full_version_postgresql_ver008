#!/usr/bin/env python3
"""Normalize EL Supply From persistence across SQLite and source JSON.

The canonical stored value is the full equipment tag used by the dashboard,
for example ``PNL-2N0D1`` instead of raw OCR text ``2N0D1``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "API"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from validators_shared import normalize_el_supply_from_tag  # noqa: E402

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as qrdb

DB_TABLES = (
    "sdi_dataset_EL",
    "sdi_print_out",
    "sdi_print_out_arch",
    "electrical_building_schema",
)


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
    table_stats: list[TableRepairStats] = field(default_factory=list)
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


def _extract_amperage(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:4] if digits else ""


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


def _asset_tag_column(table_name: str, columns: dict[str, sqlite3.Row]) -> str:
    if table_name == "electrical_building_schema" and "Equipment ID" in columns:
        return "Equipment ID"
    if "UBC Asset Tag" in columns:
        return "UBC Asset Tag"
    if "UBC Tag" in columns:
        return "UBC Tag"
    if "Equipment ID" in columns:
        return "Equipment ID"
    return ""


def _id_check_value(row: sqlite3.Row, tag_column: str, supply_from: str) -> str:
    return " | ".join(
        (
            _value(row, "Building"),
            _value(row, tag_column),
            str(supply_from or "").strip(),
        )
    )


def _load_parent_amperage_map(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    # Fed From Amperage is sourced from the SLD (electrical_building_schema),
    # not from captured assets; rows without an active SLD match stay blank.
    if not _table_exists(conn, "electrical_building_schema"):
        return {}
    columns = _table_columns(conn, "electrical_building_schema")
    if "Building" not in columns or "Equipment ID" not in columns:
        return {}

    rating_expr = '"Amperage Rating"' if "Amperage Rating" in columns else "''"
    new_draw_filter = ""
    if "new_draw" in columns:
        new_draw_filter = "WHERE TRIM(COALESCE(\"new_draw\", '')) = 'TRUE'"
    rows = conn.execute(
        f'''
        SELECT "Building", "Equipment ID", {rating_expr} AS rating
        FROM "electrical_building_schema"
        {new_draw_filter}
        '''
    ).fetchall()

    parent_map: dict[tuple[str, str], str] = {}
    for row in rows:
        building = str(row["Building"] or "").strip().upper()
        tag = str(row["Equipment ID"] or "").strip().upper()
        if not building or not tag:
            continue
        rating = _extract_amperage(row["rating"])
        parent_map[(building, tag)] = rating
    return parent_map


def _fed_from_amperage(parent_map: dict[tuple[str, str], str], building: str, supply_from: str) -> str:
    if not building or not supply_from:
        return ""
    return parent_map.get((building.strip().upper(), supply_from.strip().upper()), "")


def _updates_for_row(
    table_name: str,
    columns: dict[str, sqlite3.Row],
    row: sqlite3.Row,
    parent_map: dict[tuple[str, str], str],
) -> dict[str, str]:
    if not _is_electrical_row(table_name, row):
        return {}

    updates: dict[str, str] = {}
    current_supply = _value(row, "Supply From")
    normalized_supply = normalize_el_supply_from_tag(current_supply)
    if _column_writable(columns, "Supply From") and current_supply != normalized_supply:
        updates["Supply From"] = normalized_supply

    if _column_writable(columns, "Fed From Equipment ID"):
        current_fed_from = _value(row, "Fed From Equipment ID")
        if current_fed_from != normalized_supply:
            updates["Fed From Equipment ID"] = normalized_supply

    fed_from_amperage = _fed_from_amperage(parent_map, _value(row, "Building"), normalized_supply)
    if _column_writable(columns, "Fed From Amperage Rating"):
        if _value(row, "Fed From Amperage Rating") != fed_from_amperage:
            updates["Fed From Amperage Rating"] = fed_from_amperage

    fed_from_uom = "A" if fed_from_amperage else ""
    if _column_writable(columns, "Fed From Amperage Rating (UoM)"):
        if _value(row, "Fed From Amperage Rating (UoM)") != fed_from_uom:
            updates["Fed From Amperage Rating (UoM)"] = fed_from_uom

    tag_column = _asset_tag_column(table_name, columns)
    if tag_column and _column_writable(columns, "ID_check"):
        desired_id_check = _id_check_value(row, tag_column, normalized_supply)
        if str(row["ID_check"] or "") != desired_id_check:
            updates["ID_check"] = desired_id_check

    return updates


def repair_db(conn: sqlite3.Connection, dry_run: bool) -> list[TableRepairStats]:
    parent_map = _load_parent_amperage_map(conn)
    stats: list[TableRepairStats] = []

    for table_name in DB_TABLES:
        if not _table_exists(conn, table_name):
            stats.append(TableRepairStats(table_name=table_name, skipped=True))
            continue

        columns = _table_columns(conn, table_name)
        if "Supply From" not in columns:
            stats.append(
                TableRepairStats(
                    table_name=table_name,
                    skipped=True,
                    missing_columns=["Supply From"],
                )
            )
            continue

        rowid_expr = "ctid" if qrdb.is_postgres() else "rowid"
        rowid_pred = "ctid = ?::tid" if qrdb.is_postgres() else "rowid = ?"
        rows = conn.execute(f'SELECT {rowid_expr} AS _rowid, * FROM "{table_name}"').fetchall()
        table_stats = TableRepairStats(table_name=table_name, scanned_rows=len(rows))
        for row in rows:
            updates = _updates_for_row(table_name, columns, row, parent_map)
            if not updates:
                continue

            table_stats.changed_rows += 1
            if dry_run:
                continue

            set_clause = ", ".join(f"{_quote(column_name)} = ?" for column_name in updates)
            params = list(updates.values()) + [row["_rowid"]]
            conn.execute(f'UPDATE "{table_name}" SET {set_clause} WHERE {rowid_pred}', params)

        stats.append(table_stats)

    return stats


def _json_building_from_path(path: Path) -> str:
    stem_parts = path.stem.split("_")
    if len(stem_parts) >= 3:
        return stem_parts[-1].strip()
    return ""


def _repair_json_payload(
    payload: dict,
    building: str,
    parent_map: dict[tuple[str, str], str],
) -> bool:
    structured = payload.get("structured_data")
    if not isinstance(structured, dict):
        return False

    changed = False
    current_supply = str(structured.get("Supply From", "") or "").strip()
    normalized_supply = normalize_el_supply_from_tag(current_supply)
    if current_supply != normalized_supply:
        structured["Supply From"] = normalized_supply
        changed = True

    current_fed_from = str(structured.get("Fed From Equipment ID", "") or "").strip()
    if current_fed_from != normalized_supply:
        structured["Fed From Equipment ID"] = normalized_supply
        changed = True

    payload_building = str(payload.get("building", "") or "").strip() or building
    fed_from_amperage = _fed_from_amperage(parent_map, payload_building, normalized_supply)
    current_fed_from_amperage = str(structured.get("Fed From Amperage Rating", "") or "").strip()
    if current_fed_from_amperage != fed_from_amperage:
        structured["Fed From Amperage Rating"] = fed_from_amperage
        changed = True

    fed_from_uom = "A" if fed_from_amperage else ""
    current_fed_from_uom = str(structured.get("Fed From Amperage Rating (UoM)", "") or "").strip()
    if current_fed_from_uom != fed_from_uom:
        structured["Fed From Amperage Rating (UoM)"] = fed_from_uom
        changed = True

    return changed


def repair_json_dir(
    json_dir: Path,
    parent_map: dict[tuple[str, str], str],
    dry_run: bool,
) -> tuple[int, int]:
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

        if not _repair_json_payload(payload, _json_building_from_path(path), parent_map):
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
            summary.table_stats = repair_db(conn, dry_run=True)
            parent_map = _load_parent_amperage_map(conn)
        else:
            if not qrdb.is_postgres():
                conn.execute("BEGIN")
            try:
                summary.table_stats = repair_db(conn, dry_run=False)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            parent_map = _load_parent_amperage_map(conn)

        summary.json_scanned, summary.json_changed = repair_json_dir(json_dir, parent_map, dry_run=dry_run)
    finally:
        conn.close()

    return summary


def _print_summary(summary: RepairSummary) -> None:
    mode = "DRY RUN" if summary.dry_run else "APPLIED"
    print(f"{mode}: EL Supply From persistence repair")
    if summary.backup_path:
        print(f"Database backup: {summary.backup_path}")
    for stat in summary.table_stats:
        if stat.skipped:
            reason = f" missing {', '.join(stat.missing_columns)}" if stat.missing_columns else ""
            print(f"- {stat.table_name}: skipped{reason}")
            continue
        print(f"- {stat.table_name}: {stat.changed_rows} changed / {stat.scanned_rows} scanned")
    print(f"- JSON: {summary.json_changed} changed / {summary.json_scanned} scanned")


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
    raise SystemExit(main())
