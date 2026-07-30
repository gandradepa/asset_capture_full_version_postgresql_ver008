"""
Backfill ME Description fields after adding/editing mechanical_dictionary entries.

Use case:
  You added a new entry to dictionary/mechanical_dictionary.py (e.g. UBC Tag
  prefix "CV" -> description "Control Valve"). The ME review app only resolves
  Description on save/approve, so existing rows whose JSON+SDI were already
  processed will NOT pick up the new dictionary entry until a human re-saves.
  This script re-runs the same lookup the app uses (_resolve_description) and
  fills the gaps.

Safety:
  - Dry-run by default. Pass --apply to write.
  - Skips any row whose Description is non-empty (preserves human edits).
    This mirrors the protective skip at asset_plate_reviewer.py:734-735
    (CLAUDE.md invariant #6: do not erase human overrides).
  - Optional --include-auto-format allows replacing values that look like the
    app's own "{description} - {UBC Tag}" auto-format (rare; only useful if
    you renamed a dictionary entry).
  - Parameterized SQL throughout. Writes a per-field audit_trail row with
    source="system" so the change is attributable.
  - JSON write is atomic (temp file + os.replace). DB+JSON commit ordered so
    the DB is the source of truth if anything fails.

Usage:
  python scripts/backfill_me_descriptions.py                       # dry-run, all ME
  python scripts/backfill_me_descriptions.py --qr 0000184692       # dry-run, one QR
  python scripts/backfill_me_descriptions.py --apply               # write
  python scripts/backfill_me_descriptions.py --apply --qr 0000184692
  python scripts/backfill_me_descriptions.py --apply --include-auto-format

Recommend backing up the DB before --apply:
  cp asset_capture_app_dev/data/QR_codes.db asset_capture_app_dev/data/QR_codes.db.backup_$(date +%s)
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Optional

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_DB = os.path.join(REPO_ROOT, "asset_capture_app_dev", "data", "QR_codes.db")
DEFAULT_JSON_DIR = os.path.join(REPO_ROOT, "Output_jason_api")
DEFAULT_DICTIONARY = os.path.join(REPO_ROOT, "dictionary", "mechanical_dictionary.py")

SDI_TABLE = "sdi_dataset"
JSON_NAME_RE = re.compile(r"^([A-Za-z0-9]+)_([A-Za-z]+)_(.+)\.json$")

# Ensure repo root is importable so we can use the shared audit logger.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

try:
    from audit.logger import log_change as _audit_log_change
    from audit.diff import diff_dicts as _audit_diff_dicts
except Exception as exc:
    print(f"[warn] audit module not importable: {exc} — audit rows will be skipped")
    _audit_log_change = None
    _audit_diff_dicts = None


# --- Dictionary loader (mirrors asset_plate_reviewer.get_asset_dictionary) ---

def load_dictionary(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Dictionary file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ASSET_DICTIONARY":
                    return ast.literal_eval(node.value)
    raise ValueError(f"ASSET_DICTIONARY symbol not found in {path}")


# --- _resolve_description: ported verbatim from asset_plate_reviewer.py:722 ---
# Keep in lockstep with the app. If the app's resolution rules change, mirror
# the change here.

def _normalize_asset_type_values(value):
    if value is None:
        return set()
    vals = value if isinstance(value, (list, tuple, set)) else [value]
    out = set()
    for v in vals:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(v or "")).upper()
        if cleaned:
            out.add(cleaned)
    return out


def _get_entry_asset_types(entry: dict) -> set:
    if not isinstance(entry, dict):
        return set()
    normalized = set()
    for key, val in entry.items():
        key_clean = re.sub(r"[^a-z0-9]+", "", str(key).lower())
        if key_clean in ("assettype", "assettypes", "type"):
            normalized |= _normalize_asset_type_values(val)
    return normalized


def resolve_description(cd: dict, asset_group, ubc_tag, existing_desc, asset_type=None) -> str:
    if str(existing_desc or "").strip():
        return existing_desc

    tag_raw = str(ubc_tag or "").strip()
    tag = tag_raw.upper()
    json_types = _normalize_asset_type_values(asset_type)
    current_type = next(iter(json_types)) if json_types else "ME"

    prefix = ""

    if tag and cd:
        # 1) exact composite key (e.g. "CV-101|ME")
        entry = cd.get(f"{tag}|{current_type}")
        if isinstance(entry, dict) and entry.get("description"):
            prefix = str(entry["description"]).strip()

        # 2) composite prefix (e.g. "CV|ME")
        if not prefix:
            for key in sorted(cd.keys(), key=len, reverse=True):
                if "|" not in key:
                    continue
                try:
                    tag_prefix, key_type = key.split("|", 1)
                except ValueError:
                    continue
                if key_type.upper() != current_type:
                    continue
                if tag.startswith(str(tag_prefix or "").upper()):
                    entry = cd.get(key) or {}
                    desc = entry.get("description") if isinstance(entry, dict) else None
                    if desc:
                        prefix = str(desc).strip()
                        break

        # 3) legacy simple key (e.g. "CV")
        if not prefix:
            for key in sorted(cd.keys(), key=len, reverse=True):
                if "|" in key:
                    continue
                if tag.startswith(str(key or "").upper()):
                    entry = cd.get(key) or {}
                    if not isinstance(entry, dict):
                        continue
                    dict_types = _get_entry_asset_types(entry)
                    if json_types and dict_types and dict_types.isdisjoint(json_types):
                        continue
                    desc = entry.get("description")
                    if desc:
                        prefix = str(desc).strip()
                        break

    if not prefix:
        prefix = str(asset_group or "").strip()

    if prefix and tag_raw:
        return f"{prefix} - {tag_raw}"
    return prefix or tag_raw


# --- Auto-format detector ---

def looks_like_auto_format(current_desc: str, ubc_tag: str) -> bool:
    """Return True if `current_desc` matches the "{anything} - {tag}" shape
    the app emits at _resolve_description line 794. Used to decide whether
    --include-auto-format is allowed to overwrite the value."""
    if not current_desc or not ubc_tag:
        return False
    return str(current_desc).strip().endswith(f" - {str(ubc_tag).strip()}")


# --- Atomic JSON write ---

def write_json_atomic(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# --- Main loop ---

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json-dir", default=DEFAULT_JSON_DIR)
    ap.add_argument("--dictionary", default=DEFAULT_DICTIONARY)
    ap.add_argument("--qr", default=None, help="Restrict to one QR code")
    ap.add_argument("--building", default=None, help="Restrict to one building")
    ap.add_argument("--apply", action="store_true", help="Actually write (default: dry-run)")
    ap.add_argument("--include-auto-format", action="store_true",
                    help="Also replace Descriptions matching '{anything} - {UBC Tag}' (default: only fill empty)")
    ap.add_argument("--limit", type=int, default=0, help="Cap rows processed (0=no cap)")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args()


def fetch_sdi_row(conn: sqlite3.Connection, qr: str, building: str) -> Optional[dict]:
    cur = conn.execute(
        f'SELECT * FROM "{SDI_TABLE}" WHERE "QR Code"=? AND "Building"=?',
        (qr, building),
    )
    r = cur.fetchone()
    if not r:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, r))


def update_sdi_description(conn: sqlite3.Connection, qr: str, building: str, new_desc: str) -> None:
    conn.execute(
        f'UPDATE "{SDI_TABLE}" SET "Description"=? WHERE "QR Code"=? AND "Building"=?',
        (new_desc, qr, building),
    )


def main() -> int:
    args = parse_args()

    if not os.path.isfile(args.db):
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.json_dir):
        print(f"ERROR: JSON dir not found: {args.json_dir}", file=sys.stderr)
        return 2

    cd = load_dictionary(args.dictionary)
    print(f"Loaded {len(cd)} dictionary entries from {args.dictionary}")
    print(f"DB:       {args.db}")
    print(f"JSON dir: {args.json_dir}")
    print(f"Mode:     {'APPLY (writing)' if args.apply else 'DRY-RUN (no writes)'}")
    print(f"Policy:   {'fill-empty + replace auto-format' if args.include_auto_format else 'fill-empty only'}")
    if args.qr:
        print(f"Filter:   QR={args.qr}")
    if args.building:
        print(f"Filter:   Building={args.building}")
    print()

    scanned = 0
    would_change = 0
    skipped_empty_tag = 0
    skipped_no_change = 0
    skipped_human_edit = 0
    errors = 0

    # Single DB connection for the whole run; one transaction per QR.
    with qrdb.get_connection(sqlite_path=args.db) as conn:
        if not qrdb.is_postgres():
            conn.execute("PRAGMA foreign_keys = ON")

        for filename in sorted(os.listdir(args.json_dir)):
            m = JSON_NAME_RE.match(filename)
            if not m:
                continue
            qr, discipline, building = m.groups()
            if discipline.upper() != "ME":
                continue
            if args.qr and qr != args.qr:
                continue
            if args.building and building != args.building:
                continue
            if args.limit and scanned >= args.limit:
                break
            scanned += 1

            path = os.path.join(args.json_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as exc:
                print(f"[err] {filename}: cannot read JSON: {exc}")
                errors += 1
                continue

            sd = raw.get("structured_data") or {}
            if not isinstance(sd, dict):
                print(f"[err] {filename}: structured_data missing/invalid")
                errors += 1
                continue

            tag_val = sd.get("UBC Asset Tag") or sd.get("UBC Tag") or ""
            current_desc = str(sd.get("Description") or "").strip()
            asset_group = sd.get("Asset Group") or ""

            if not str(tag_val).strip():
                skipped_empty_tag += 1
                if args.verbose:
                    print(f"[skip] {qr} ({building}): no UBC Tag")
                continue

            # Decide policy: pass existing_desc="" to force lookup, then compare.
            proposed = resolve_description(cd, asset_group, tag_val, "", discipline)

            # If proposed didn't change anything from the dictionary's perspective
            # (proposed == current), nothing to do.
            if proposed == current_desc:
                skipped_no_change += 1
                continue

            if current_desc:
                # Has a value. Only replace if it looks auto-generated AND user
                # asked us to. Otherwise treat as human edit and preserve.
                if not (args.include_auto_format and looks_like_auto_format(current_desc, tag_val)):
                    skipped_human_edit += 1
                    if args.verbose:
                        print(f"[skip] {qr} ({building}): existing='{current_desc}' (preserved)")
                    continue

            # Check SDI row exists; if missing, we still update JSON but warn.
            sdi_before = fetch_sdi_row(conn, qr, building)
            sdi_existing_desc = ""
            if sdi_before is not None:
                sdi_existing_desc = str(sdi_before.get("Description") or "").strip()
                # Same protective rule for SDI side: don't clobber a human edit
                # unless current matches auto-format and user opted in.
                if sdi_existing_desc and sdi_existing_desc != current_desc:
                    if not (args.include_auto_format and looks_like_auto_format(sdi_existing_desc, tag_val)):
                        skipped_human_edit += 1
                        if args.verbose:
                            print(f"[skip] {qr} ({building}): SDI Description='{sdi_existing_desc}' differs from JSON; preserving SDI")
                        continue

            would_change += 1
            print(f"[fix ] {qr} ({building}): '{current_desc}' -> '{proposed}'  (tag={tag_val})")

            if not args.apply:
                continue

            # APPLY: DB transaction first, then atomic JSON write.
            try:
                if sdi_before is not None:
                    update_sdi_description(conn, qr, building, proposed)
                    if _audit_log_change and _audit_diff_dicts:
                        sdi_after = {**sdi_before, "Description": proposed}
                        changes = _audit_diff_dicts(sdi_before, sdi_after)
                        if changes:
                            _audit_log_change(
                                conn,
                                qr_code=qr,
                                app_name="reviewer_me",
                                table_name=SDI_TABLE,
                                record_pk=f"{qr}|{building}",
                                op_type="UPDATE",
                                field_changes=changes,
                                source="system",
                                modified_by="backfill_me_descriptions",
                                description="dictionary backfill: re-resolved Description after dictionary edit",
                            )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"[err] {qr} ({building}): DB update failed: {exc} — JSON not modified")
                errors += 1
                continue

            try:
                sd["Description"] = proposed
                raw["structured_data"] = sd
                raw["modified"] = True
                write_json_atomic(path, raw)
            except Exception as exc:
                print(f"[err] {qr} ({building}): DB updated but JSON write FAILED: {exc}")
                print("       Re-run after fixing filesystem issue, or restore manually.")
                errors += 1

    print()
    print(f"Scanned ME JSONs:     {scanned}")
    print(f"{'Would change' if not args.apply else 'Changed'}:           {would_change}")
    print(f"Skipped (no UBC Tag): {skipped_empty_tag}")
    print(f"Skipped (no change):  {skipped_no_change}")
    print(f"Skipped (human edit): {skipped_human_edit}")
    print(f"Errors:               {errors}")
    if not args.apply and would_change:
        print()
        print("Dry-run only. Re-run with --apply to write.")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
