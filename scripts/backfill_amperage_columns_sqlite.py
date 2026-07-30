#!/usr/bin/env python3
"""One-time SQLite backfill for EL/SDI export source columns.

This migration is idempotent. It can be run safely more than once against the
same database.

It applies the current EL/SDI source-of-truth rules used by the application:
- "Amperage Rating" is the canonical amperage field
- values are stored as integer-only text
- "Amperage Rating (UoM)" is "AMP" when a rating exists, otherwise blank
- "Fed From Amperage Rating (UoM)" is "A" when a fed-from rating exists, otherwise blank
- "Fed From Equipment ID" is the canonical stored mirror of "Supply From"
- "Power Type" is stored canonically from the EL equipment tag parser
- "Power Rating" and "Power Rating (UoM)" are carried from the curated EL row
- "Voltage Rating" is the canonical voltage field, mirrored to legacy "Volts"
- "Voltage Rating (UoM)" is "VLT" when a voltage rating exists, otherwise blank
- legacy "Ampere" is kept as a compatibility mirror when that column exists
- "Equipment ID" is stored canonically and backfilled from the UBC tag source
- "Equipment Type" is stored canonically and backfilled from Equipment ID or
  the UBC tag source using the current EL prefix mapping

By default the script creates a consistent SQLite backup before mutating the
database. Use --dry-run first if you want to preview the changes.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
API_DIR = REPO_ROOT / "API"
if str(API_DIR) not in sys.path:
    sys.path.append(str(API_DIR))

try:
    from electrical_equipment_rules import derive_electrical_equipment_type, derive_electrical_power_type
except ModuleNotFoundError:
    _ELECTRICAL_EQUIPMENT_TYPE_MAP = {
        "MDP": "Main Distribution Panel",
        "CDP": "Central Distribution Panel",
        "SPL": "Splitter",
        "MCC": "Motor Control Center",
        "PNL": "Panel",
        "SWBD": "Switchboard",
        "ATS": "Automatic Transfer Switch",
        "TX": "Transformer",
    }

    def derive_electrical_equipment_type(tag_value: object) -> str:
        tag_upper = str(tag_value or "").strip().upper()
        if not tag_upper:
            return ""
        for code, name in sorted(_ELECTRICAL_EQUIPMENT_TYPE_MAP.items(), key=lambda item: len(item[0]), reverse=True):
            if tag_upper.startswith(code):
                return name
        return ""

    _ELECTRICAL_POWER_TYPE_CODES = {"NES", "NE", "NS", "ES", "N", "E", "S"}
    _ELECTRICAL_POWER_TYPE_RE = re.compile(r"NES|NE|NS|ES|N|E|S")

    def derive_electrical_power_type(tag_value: object) -> str:
        tag_upper = str(tag_value or "").strip().upper()
        if not tag_upper:
            return ""
        parts = tag_upper.split("-")
        if len(parts) <= 1:
            return ""
        match = _ELECTRICAL_POWER_TYPE_RE.search(parts[1])
        if not match:
            return ""
        system_code = match.group(0)
        return system_code if system_code in _ELECTRICAL_POWER_TYPE_CODES else ""

try:
    from validators_shared import normalize_el_supply_from_tag
except ModuleNotFoundError:
    def normalize_el_supply_from_tag(value: object) -> str:
        clean = str(value or "").upper().replace("EQUIPMENT NAME:", "").replace("MAIN", "").strip()
        if not clean:
            return ""
        if clean.startswith("TX") or (clean.startswith("T") and len(clean) > 1 and clean[1].isdigit()):
            return clean
        if clean[0].isdigit():
            return f"PNL-{clean}"
        for abbr in ("MDP", "CDP", "SPL", "MCC", "PNL", "SWBD", "ATS"):
            if clean.startswith(abbr):
                remainder = clean[len(abbr):].lstrip(" -_")
                return f"{abbr}-{remainder}" if remainder else abbr
        return clean

TARGET_TABLES = ("sdi_dataset_EL", "sdi_print_out", "sdi_print_out_arch")
BACKUP_LABEL = "amperage_columns_migration"


@dataclass
class TableStats:
    table_name: str
    total_rows: int = 0
    changed_rows: int = 0
    equipment_nonblank: int = 0
    equipment_type_nonblank: int = 0
    power_type_nonblank: int = 0
    rating_nonblank: int = 0
    fed_from_equipment_nonblank: int = 0
    fed_from_rating_nonblank: int = 0
    fed_from_uom_nonblank: int = 0
    power_rating_nonblank: int = 0
    power_uom_nonblank: int = 0
    voltage_rating_nonblank: int = 0
    voltage_uom_nonblank: int = 0
    uom_nonblank: int = 0
    ampere_nonblank: int = 0
    skipped: bool = False
    missing_columns: list[str] = field(default_factory=list)


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "asset_capture_app_dev" / "data" / "QR_codes.db"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _get_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [row[1] for row in rows]


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    if column_name in _get_columns(conn, table_name):
        return
    conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}')


def _extract_amperage(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"\d{1,4}", text)
    return match.group(0) if match else ""


def _canonical_amperage(rating_value: object, ampere_value: object) -> str:
    for candidate in (rating_value, ampere_value):
        normalized = _extract_amperage(candidate)
        if normalized:
            return normalized
    return ""


def _canonical_equipment_id(tag_value: object) -> str:
    return str(tag_value or "").strip()


def _desired_equipment_id(table_name: str, row: sqlite3.Row) -> str:
    if table_name == "sdi_dataset_EL":
        return _canonical_equipment_id(row["Tag Source"])
    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if attribute_value == "electrical":
        return _canonical_equipment_id(row["Tag Source"])
    return str(row["Equipment ID"] or "").strip()


def _desired_equipment_type(table_name: str, row: sqlite3.Row, desired_equipment_id: str) -> str:
    source_value = desired_equipment_id or str(row["Tag Source"] or "").strip()
    if table_name == "sdi_dataset_EL":
        return derive_electrical_equipment_type(source_value)
    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if attribute_value == "electrical":
        return derive_electrical_equipment_type(source_value)
    return str(row["Equipment Type"] or "").strip()


def _desired_power_type(conn: sqlite3.Connection, table_name: str, row: sqlite3.Row, desired_equipment_id: str) -> str:
    source_value = desired_equipment_id or str(row["Tag Source"] or "").strip()
    if table_name == "sdi_dataset_EL":
        return derive_electrical_power_type(source_value)

    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if attribute_value == "electrical" and _table_exists(conn, "sdi_dataset_EL"):
        qr_text = str(row["QR Code"] or "").strip()
        building_text = str(row["Building"] or "").strip()
        if qr_text and building_text:
            parent = conn.execute(
                '''
                SELECT TRIM(COALESCE("Power Type", ''))
                FROM "sdi_dataset_EL"
                WHERE TRIM(COALESCE("Building", '')) = ?
                  AND UPPER(TRIM(COALESCE("QR Code", ''))) = UPPER(?)
                LIMIT 1
                ''',
                (building_text, qr_text),
            ).fetchone()
            if parent:
                return str(parent[0] or "").strip()
    return str(row["Power Type"] or "").strip()


def _lookup_fed_from_amperage(
    conn: sqlite3.Connection,
    parent_table_name: str,
    parent_tag_col: str,
    building_value: object,
    supply_from_value: object,
) -> str:
    building_text = str(building_value or "").strip()
    supply_text = str(supply_from_value or "").strip()
    if not building_text or not supply_text:
        return ""
    row = conn.execute(
        f'''
        SELECT TRIM(COALESCE("Amperage Rating", ''))
        FROM "{parent_table_name}"
        WHERE TRIM(COALESCE("Building", '')) = ?
          AND UPPER(TRIM(COALESCE("{parent_tag_col}", ''))) = UPPER(?)
        LIMIT 1
        ''',
        (building_text, supply_text),
    ).fetchone()
    if not row:
        return ""
    return _extract_amperage(row[0])


def _desired_fed_from_amperage(conn: sqlite3.Connection, table_name: str, row: sqlite3.Row) -> str:
    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if table_name != "sdi_dataset_EL" and attribute_value and attribute_value != "electrical":
        return str(row["Fed From Amperage Rating"] or "").strip()

    supply_from_value = normalize_el_supply_from_tag(row["Supply From"])
    if table_name == "sdi_dataset_EL":
        return _lookup_fed_from_amperage(conn, "sdi_dataset_EL", "UBC Asset Tag", row["Building"], supply_from_value)

    if _table_exists(conn, "sdi_dataset_EL"):
        return _lookup_fed_from_amperage(conn, "sdi_dataset_EL", "UBC Asset Tag", row["Building"], supply_from_value)

    if "UBC Tag" in _get_columns(conn, table_name):
        return _lookup_fed_from_amperage(conn, table_name, "UBC Tag", row["Building"], supply_from_value)
    return ""


def _desired_fed_from_amperage_uom(fed_from_rating_value: object) -> str:
    return "A" if str(fed_from_rating_value or "").strip() else ""


def _lookup_fed_from_equipment_id(
    conn: sqlite3.Connection,
    parent_table_name: str,
    building_value: object,
    qr_code_value: object,
) -> str:
    building_text = str(building_value or "").strip()
    qr_text = str(qr_code_value or "").strip()
    if not building_text or not qr_text:
        return ""
    row = conn.execute(
        f'''
        SELECT
            CASE
                WHEN TRIM(COALESCE("Fed From Equipment ID", '')) != '' THEN TRIM(COALESCE("Fed From Equipment ID", ''))
                ELSE TRIM(COALESCE("Supply From", ''))
            END
        FROM "{parent_table_name}"
        WHERE TRIM(COALESCE("Building", '')) = ?
          AND UPPER(TRIM(COALESCE("QR Code", ''))) = UPPER(?)
        LIMIT 1
        ''',
        (building_text, qr_text),
    ).fetchone()
    if not row:
        return ""
    return str(row[0] or "").strip()


def _desired_fed_from_equipment_id(conn: sqlite3.Connection, table_name: str, row: sqlite3.Row) -> str:
    supply_from_value = normalize_el_supply_from_tag(row["Supply From"])
    if table_name == "sdi_dataset_EL":
        return supply_from_value

    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if attribute_value == "electrical" and _table_exists(conn, "sdi_dataset_EL"):
        lookup_value = _lookup_fed_from_equipment_id(conn, "sdi_dataset_EL", row["Building"], row["QR Code"])
        if lookup_value:
            return normalize_el_supply_from_tag(lookup_value)

    return normalize_el_supply_from_tag(row["Fed From Equipment ID"]) or supply_from_value


def _stored_voltage_pair(rating_value: object, uom_value: object, legacy_volts_value: object) -> tuple[str, str]:
    rating = str(rating_value or "").strip() or str(legacy_volts_value or "").strip()
    if not rating:
        return "", ""
    uom = str(uom_value or "").strip() or "VLT"
    return rating, uom


def _lookup_voltage_pair(
    conn: sqlite3.Connection,
    parent_table_name: str,
    building_value: object,
    qr_code_value: object,
) -> tuple[str, str]:
    building_text = str(building_value or "").strip()
    qr_text = str(qr_code_value or "").strip()
    if not building_text or not qr_text:
        return "", ""
    row = conn.execute(
        f'''
        SELECT
            TRIM(COALESCE("Voltage Rating", '')),
            TRIM(COALESCE("Voltage Rating (UoM)", '')),
            TRIM(COALESCE("Volts", ''))
        FROM "{parent_table_name}"
        WHERE TRIM(COALESCE("Building", '')) = ?
          AND UPPER(TRIM(COALESCE("QR Code", ''))) = UPPER(?)
        LIMIT 1
        ''',
        (building_text, qr_text),
    ).fetchone()
    if not row:
        return "", ""
    return _stored_voltage_pair(row[0], row[1], row[2])


def _desired_voltage_pair(conn: sqlite3.Connection, table_name: str, row: sqlite3.Row) -> tuple[str, str]:
    current_pair = _stored_voltage_pair(row["Voltage Rating"], row["Voltage Rating (UoM)"], row["Volts"])
    if table_name == "sdi_dataset_EL":
        return current_pair

    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if attribute_value == "electrical" and _table_exists(conn, "sdi_dataset_EL"):
        lookup_pair = _lookup_voltage_pair(conn, "sdi_dataset_EL", row["Building"], row["QR Code"])
        if lookup_pair[0]:
            return lookup_pair
    return current_pair


def _stored_power_rating_pair(rating_value: object, uom_value: object) -> tuple[str, str]:
    rating = str(rating_value or "").strip()
    uom = str(uom_value or "").strip()
    if not rating:
        return "", ""
    return rating, uom


def _lookup_power_rating_pair(
    conn: sqlite3.Connection,
    parent_table_name: str,
    building_value: object,
    qr_code_value: object,
) -> tuple[str, str]:
    building_text = str(building_value or "").strip()
    qr_text = str(qr_code_value or "").strip()
    if not building_text or not qr_text:
        return "", ""
    row = conn.execute(
        f'''
        SELECT
            TRIM(COALESCE("Power Rating", '')),
            TRIM(COALESCE("Power Rating (UoM)", ''))
        FROM "{parent_table_name}"
        WHERE TRIM(COALESCE("Building", '')) = ?
          AND UPPER(TRIM(COALESCE("QR Code", ''))) = UPPER(?)
        LIMIT 1
        ''',
        (building_text, qr_text),
    ).fetchone()
    if not row:
        return "", ""
    return _stored_power_rating_pair(row[0], row[1])


def _desired_power_rating_pair(conn: sqlite3.Connection, table_name: str, row: sqlite3.Row) -> tuple[str, str]:
    current_pair = _stored_power_rating_pair(row["Power Rating"], row["Power Rating (UoM)"])
    if table_name == "sdi_dataset_EL":
        return current_pair

    attribute_value = str(row["Attribute"] or "").strip().casefold()
    if attribute_value and attribute_value != "electrical":
        return current_pair

    if _table_exists(conn, "sdi_dataset_EL"):
        return _lookup_power_rating_pair(conn, "sdi_dataset_EL", row["Building"], row["QR Code"])

    return current_pair


def _select_rows(conn: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    existing = set(_get_columns(conn, table_name))
    equipment_expr = '"Equipment ID"' if "Equipment ID" in existing else "'' AS \"Equipment ID\""
    equipment_type_expr = '"Equipment Type"' if "Equipment Type" in existing else "'' AS \"Equipment Type\""
    power_type_expr = '"Power Type"' if "Power Type" in existing else "'' AS \"Power Type\""
    rating_expr = '"Amperage Rating"' if "Amperage Rating" in existing else "'' AS \"Amperage Rating\""
    fed_from_equipment_expr = '"Fed From Equipment ID"' if "Fed From Equipment ID" in existing else "'' AS \"Fed From Equipment ID\""
    fed_from_rating_expr = '"Fed From Amperage Rating"' if "Fed From Amperage Rating" in existing else "'' AS \"Fed From Amperage Rating\""
    fed_from_uom_expr = '"Fed From Amperage Rating (UoM)"' if "Fed From Amperage Rating (UoM)" in existing else "'' AS \"Fed From Amperage Rating (UoM)\""
    power_rating_expr = '"Power Rating"' if "Power Rating" in existing else "'' AS \"Power Rating\""
    power_uom_expr = '"Power Rating (UoM)"' if "Power Rating (UoM)" in existing else "'' AS \"Power Rating (UoM)\""
    voltage_rating_expr = '"Voltage Rating"' if "Voltage Rating" in existing else "'' AS \"Voltage Rating\""
    voltage_uom_expr = '"Voltage Rating (UoM)"' if "Voltage Rating (UoM)" in existing else "'' AS \"Voltage Rating (UoM)\""
    volts_expr = '"Volts"' if "Volts" in existing else "'' AS \"Volts\""
    uom_expr = '"Amperage Rating (UoM)"' if "Amperage Rating (UoM)" in existing else "'' AS \"Amperage Rating (UoM)\""
    ampere_expr = '"Ampere"' if "Ampere" in existing else "'' AS \"Ampere\""
    attribute_expr = '"Attribute" AS "Attribute"' if "Attribute" in existing else "'' AS \"Attribute\""
    building_expr = '"Building" AS "Building"' if "Building" in existing else "'' AS \"Building\""
    supply_from_expr = '"Supply From" AS "Supply From"' if "Supply From" in existing else "'' AS \"Supply From\""
    qr_code_expr = '"QR Code" AS "QR Code"' if "QR Code" in existing else "'' AS \"QR Code\""
    if table_name == "sdi_dataset_EL":
        tag_expr = '"UBC Asset Tag" AS "Tag Source"' if "UBC Asset Tag" in existing else "'' AS \"Tag Source\""
    else:
        tag_expr = '"UBC Tag" AS "Tag Source"' if "UBC Tag" in existing else "'' AS \"Tag Source\""
    return conn.execute(
        f'''
        SELECT
            rowid AS _rowid,
            {equipment_expr},
            {equipment_type_expr},
            {power_type_expr},
            {rating_expr},
            {fed_from_equipment_expr},
            {fed_from_rating_expr},
            {fed_from_uom_expr},
            {power_rating_expr},
            {power_uom_expr},
            {voltage_rating_expr},
            {voltage_uom_expr},
            {volts_expr},
            {uom_expr},
            {ampere_expr},
            {attribute_expr},
            {building_expr},
            {supply_from_expr},
            {qr_code_expr},
            {tag_expr}
        FROM "{table_name}"
        '''
    ).fetchall()


def _collect_stats(conn: sqlite3.Connection, table_name: str) -> TableStats:
    if not _table_exists(conn, table_name):
        return TableStats(table_name=table_name, skipped=True)

    existing = set(_get_columns(conn, table_name))
    missing = [
        column_name
        for column_name in (
            "Equipment ID",
            "Equipment Type",
            "Power Type",
            "Amperage Rating",
            "Amperage Rating (UoM)",
            "Fed From Equipment ID",
            "Voltage Rating",
            "Voltage Rating (UoM)",
            "Power Rating",
            "Power Rating (UoM)",
        )
        if column_name not in existing
    ]
    if "Fed From Amperage Rating" not in existing:
        missing.append("Fed From Amperage Rating")
    if "Fed From Amperage Rating (UoM)" not in existing:
        missing.append("Fed From Amperage Rating (UoM)")
    stats = TableStats(table_name=table_name, missing_columns=missing)
    rows = _select_rows(conn, table_name)
    stats.total_rows = len(rows)

    for row in rows:
        desired_equipment = _desired_equipment_id(table_name, row)
        desired_equipment_type = _desired_equipment_type(table_name, row, desired_equipment)
        desired_power_type = _desired_power_type(conn, table_name, row, desired_equipment)
        canonical = _canonical_amperage(row["Amperage Rating"], row["Ampere"])
        desired_fed_from_equipment = _desired_fed_from_equipment_id(conn, table_name, row)
        desired_fed_from_rating = _desired_fed_from_amperage(conn, table_name, row)
        desired_fed_from_uom = _desired_fed_from_amperage_uom(desired_fed_from_rating)
        desired_power_rating, desired_power_uom = _desired_power_rating_pair(conn, table_name, row)
        desired_voltage_rating, desired_voltage_uom = _desired_voltage_pair(conn, table_name, row)
        desired_uom = "AMP" if canonical else ""
        desired_supply_from = normalize_el_supply_from_tag(row["Supply From"])
        current_equipment = str(row["Equipment ID"] or "").strip()
        current_equipment_type = str(row["Equipment Type"] or "").strip()
        current_power_type = str(row["Power Type"] or "").strip()
        current_rating = str(row["Amperage Rating"] or "").strip()
        current_supply_from = str(row["Supply From"] or "").strip()
        current_fed_from_equipment = str(row["Fed From Equipment ID"] or "").strip()
        current_fed_from_rating = str(row["Fed From Amperage Rating"] or "").strip()
        current_fed_from_uom = str(row["Fed From Amperage Rating (UoM)"] or "").strip()
        current_power_rating = str(row["Power Rating"] or "").strip()
        current_power_uom = str(row["Power Rating (UoM)"] or "").strip()
        current_voltage_rating = str(row["Voltage Rating"] or "").strip()
        current_voltage_uom = str(row["Voltage Rating (UoM)"] or "").strip()
        current_volts = str(row["Volts"] or "").strip()
        current_uom = str(row["Amperage Rating (UoM)"] or "").strip()
        current_ampere = str(row["Ampere"] or "").strip()

        if current_equipment != desired_equipment:
            stats.changed_rows += 1
            continue
        if current_equipment_type != desired_equipment_type:
            stats.changed_rows += 1
            continue
        if current_power_type != desired_power_type:
            stats.changed_rows += 1
            continue
        if current_rating != canonical:
            stats.changed_rows += 1
            continue
        if "Supply From" in existing and current_supply_from != desired_supply_from:
            stats.changed_rows += 1
            continue
        if current_fed_from_equipment != desired_fed_from_equipment:
            stats.changed_rows += 1
            continue
        if current_fed_from_rating != desired_fed_from_rating:
            stats.changed_rows += 1
            continue
        if current_fed_from_uom != desired_fed_from_uom:
            stats.changed_rows += 1
            continue
        if current_power_rating != desired_power_rating:
            stats.changed_rows += 1
            continue
        if current_power_uom != desired_power_uom:
            stats.changed_rows += 1
            continue
        if current_voltage_rating != desired_voltage_rating:
            stats.changed_rows += 1
            continue
        if current_voltage_uom != desired_voltage_uom:
            stats.changed_rows += 1
            continue
        if current_volts != desired_voltage_rating:
            stats.changed_rows += 1
            continue
        if current_uom != desired_uom:
            stats.changed_rows += 1
            continue
        if "Ampere" in existing and current_ampere != canonical:
            stats.changed_rows += 1

    final_rows = rows
    stats.equipment_nonblank = sum(1 for row in final_rows if str(row["Equipment ID"] or "").strip())
    stats.equipment_type_nonblank = sum(1 for row in final_rows if str(row["Equipment Type"] or "").strip())
    stats.power_type_nonblank = sum(1 for row in final_rows if _desired_power_type(conn, table_name, row, _desired_equipment_id(table_name, row)))
    stats.rating_nonblank = sum(1 for row in final_rows if str(row["Amperage Rating"] or "").strip())
    stats.fed_from_equipment_nonblank = sum(1 for row in final_rows if _desired_fed_from_equipment_id(conn, table_name, row))
    stats.fed_from_rating_nonblank = sum(1 for row in final_rows if _desired_fed_from_amperage(conn, table_name, row))
    stats.fed_from_uom_nonblank = sum(1 for row in final_rows if _desired_fed_from_amperage_uom(_desired_fed_from_amperage(conn, table_name, row)))
    stats.power_rating_nonblank = sum(1 for row in final_rows if _desired_power_rating_pair(conn, table_name, row)[0])
    stats.power_uom_nonblank = sum(1 for row in final_rows if _desired_power_rating_pair(conn, table_name, row)[1])
    stats.voltage_rating_nonblank = sum(1 for row in final_rows if _desired_voltage_pair(conn, table_name, row)[0])
    stats.voltage_uom_nonblank = sum(1 for row in final_rows if _desired_voltage_pair(conn, table_name, row)[1])
    stats.uom_nonblank = sum(1 for row in final_rows if str(row["Amperage Rating (UoM)"] or "").strip())
    stats.ampere_nonblank = sum(1 for row in final_rows if str(row["Ampere"] or "").strip())
    return stats


def _apply_table(conn: sqlite3.Connection, table_name: str) -> TableStats:
    if not _table_exists(conn, table_name):
        return TableStats(table_name=table_name, skipped=True)

    _ensure_column(conn, table_name, "Equipment ID", "TEXT")
    _ensure_column(conn, table_name, "Equipment Type", "TEXT")
    _ensure_column(conn, table_name, "Power Type", "TEXT")
    _ensure_column(conn, table_name, "Amperage Rating", "TEXT")
    _ensure_column(conn, table_name, "Amperage Rating (UoM)", "TEXT")
    _ensure_column(conn, table_name, "Fed From Equipment ID", "TEXT")
    _ensure_column(conn, table_name, "Voltage Rating", "TEXT")
    _ensure_column(conn, table_name, "Voltage Rating (UoM)", "TEXT")
    _ensure_column(conn, table_name, "Fed From Amperage Rating", "TEXT")
    _ensure_column(conn, table_name, "Fed From Amperage Rating (UoM)", "TEXT")
    _ensure_column(conn, table_name, "Power Rating", "TEXT")
    _ensure_column(conn, table_name, "Power Rating (UoM)", "TEXT")
    existing = set(_get_columns(conn, table_name))
    has_ampere = "Ampere" in existing

    rows = _select_rows(conn, table_name)
    changed_rows = 0

    for row in rows:
        desired_equipment = _desired_equipment_id(table_name, row)
        desired_equipment_type = _desired_equipment_type(table_name, row, desired_equipment)
        desired_power_type = _desired_power_type(conn, table_name, row, desired_equipment)
        canonical = _canonical_amperage(row["Amperage Rating"], row["Ampere"])
        desired_fed_from_equipment = _desired_fed_from_equipment_id(conn, table_name, row)
        desired_fed_from_rating = _desired_fed_from_amperage(conn, table_name, row)
        desired_fed_from_uom = _desired_fed_from_amperage_uom(desired_fed_from_rating)
        desired_power_rating, desired_power_uom = _desired_power_rating_pair(conn, table_name, row)
        desired_voltage_rating, desired_voltage_uom = _desired_voltage_pair(conn, table_name, row)
        desired_uom = "AMP" if canonical else ""
        desired_supply_from = normalize_el_supply_from_tag(row["Supply From"])
        updates: dict[str, str] = {}

        if str(row["Equipment ID"] or "").strip() != desired_equipment:
            updates["Equipment ID"] = desired_equipment
        if str(row["Equipment Type"] or "").strip() != desired_equipment_type:
            updates["Equipment Type"] = desired_equipment_type
        if str(row["Power Type"] or "").strip() != desired_power_type:
            updates["Power Type"] = desired_power_type
        if str(row["Amperage Rating"] or "").strip() != canonical:
            updates["Amperage Rating"] = canonical
        if "Supply From" in existing and str(row["Supply From"] or "").strip() != desired_supply_from:
            updates["Supply From"] = desired_supply_from
        if str(row["Fed From Equipment ID"] or "").strip() != desired_fed_from_equipment:
            updates["Fed From Equipment ID"] = desired_fed_from_equipment
        if str(row["Fed From Amperage Rating"] or "").strip() != desired_fed_from_rating:
            updates["Fed From Amperage Rating"] = desired_fed_from_rating
        if str(row["Fed From Amperage Rating (UoM)"] or "").strip() != desired_fed_from_uom:
            updates["Fed From Amperage Rating (UoM)"] = desired_fed_from_uom
        if str(row["Power Rating"] or "").strip() != desired_power_rating:
            updates["Power Rating"] = desired_power_rating
        if str(row["Power Rating (UoM)"] or "").strip() != desired_power_uom:
            updates["Power Rating (UoM)"] = desired_power_uom
        if str(row["Voltage Rating"] or "").strip() != desired_voltage_rating:
            updates["Voltage Rating"] = desired_voltage_rating
        if str(row["Voltage Rating (UoM)"] or "").strip() != desired_voltage_uom:
            updates["Voltage Rating (UoM)"] = desired_voltage_uom
        if "Volts" in existing and str(row["Volts"] or "").strip() != desired_voltage_rating:
            updates["Volts"] = desired_voltage_rating
        if str(row["Amperage Rating (UoM)"] or "").strip() != desired_uom:
            updates["Amperage Rating (UoM)"] = desired_uom
        if has_ampere and str(row["Ampere"] or "").strip() != canonical:
            updates["Ampere"] = canonical

        if not updates:
            continue

        set_clause = ", ".join(f'"{column_name}" = ?' for column_name in updates)
        params = list(updates.values()) + [row["_rowid"]]
        conn.execute(f'UPDATE "{table_name}" SET {set_clause} WHERE rowid = ?', params)
        changed_rows += 1

    stats = _collect_stats(conn, table_name)
    stats.changed_rows = changed_rows
    return stats


def _create_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{db_path.stem}.bak_{timestamp}_{BACKUP_LABEL}{db_path.suffix}"
    backup_path = db_path.with_name(backup_name)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)

    return backup_path


def _print_stats(title: str, stats_list: list[TableStats]) -> None:
    print(title)
    for stats in stats_list:
        if stats.skipped:
            print(f"  - {stats.table_name}: skipped (table not found)")
            continue

        missing = f" missing={stats.missing_columns}" if stats.missing_columns else ""
        print(
            "  - "
            f"{stats.table_name}: total={stats.total_rows}, "
            f"changed={stats.changed_rows}, "
            f"equipment_nonblank={stats.equipment_nonblank}, "
            f"equipment_type_nonblank={stats.equipment_type_nonblank}, "
            f"power_type_nonblank={stats.power_type_nonblank}, "
            f"rating_nonblank={stats.rating_nonblank}, "
            f"fed_from_equipment_nonblank={stats.fed_from_equipment_nonblank}, "
            f"fed_from_rating_nonblank={stats.fed_from_rating_nonblank}, "
            f"fed_from_uom_nonblank={stats.fed_from_uom_nonblank}, "
            f"power_rating_nonblank={stats.power_rating_nonblank}, "
            f"power_uom_nonblank={stats.power_uom_nonblank}, "
            f"voltage_rating_nonblank={stats.voltage_rating_nonblank}, "
            f"voltage_uom_nonblank={stats.voltage_uom_nonblank}, "
            f"uom_nonblank={stats.uom_nonblank}, "
            f"ampere_nonblank={stats.ampere_nonblank}{missing}"
        )


def _run(db_path: Path, dry_run: bool, skip_backup: bool) -> int:
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    if dry_run:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            stats_list = [_collect_stats(conn, table_name) for table_name in TARGET_TABLES]
        _print_stats("Dry run summary", stats_list)
        print("No changes were applied.")
        return 0

    backup_path = None
    if not skip_backup:
        backup_path = _create_backup(db_path)

    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        stats_list = [_apply_table(conn, table_name) for table_name in TARGET_TABLES]
        conn.commit()

    if backup_path is not None:
        print(f"Backup created: {backup_path}")
    _print_stats("Migration summary", stats_list)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill EL/SDI export source columns in the SQLite database.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_default_db_path(),
        help="Path to QR_codes.db (defaults to the repo-local SQLite database).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying the database.",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not create a backup before applying the migration.",
    )
    args = parser.parse_args()
    return _run(args.db.resolve(), dry_run=args.dry_run, skip_backup=args.skip_backup)


if __name__ == "__main__":
    raise SystemExit(main())
