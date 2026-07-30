"""
Backfill BF Description fields after adding/editing mechanical_dictionary entries.

Use case:
  BF uses the same mechanical_dictionary.py as ME and shares the protective
  skip pattern at asset_plate_reviewer_bf.py:1093 — existing BF rows whose
  Description was already populated won't pick up newly-added dictionary
  entries until a reviewer re-saves them. This script re-runs the same
  lookup the BF app uses (_resolve_description) and fills the gaps.

Differences from the ME backfill:
  - BF's resolver uses *simple-key only* matching (no composite "TAG|BF" keys)
    and has *no discipline filter*, so any dictionary key whose UBC Tag is a
    prefix match will hit, regardless of asset_type fields. We mirror that
    verbatim to stay in lockstep with the live app.
  - When no dictionary key matches, BF falls back to "BFP-{Application}
    {AssetGroup-first-word} BFP" via _compute_description — NOT to Asset Group
    alone. The detector for "looks like auto-format" accepts either shape.
  - BF JSON filenames have a digit-or-digit-dash-digit building suffix:
    {qr}_BF_{building}.json where building matches \\d+(?:-\\d+)?
  - BF writes to the same sdi_dataset table as ME, keyed on (QR Code, Building).

Safety:
  - Dry-run by default. Pass --apply to write.
  - Skips rows whose Description is non-empty (preserves human edits).
  - Optional --include-auto-format allows replacing values that match either
    of BF's auto-format shapes ("{desc} - {tag}" or "BFP-... BFP").
  - Parameterized SQL throughout. Audit rows written with source="system",
    app_name="reviewer_bf".

Usage:
  python scripts/backfill_bf_descriptions.py                       # dry-run, all BF
  python scripts/backfill_bf_descriptions.py --qr 0000123456       # dry-run, one QR
  python scripts/backfill_bf_descriptions.py --apply               # write
  python scripts/backfill_bf_descriptions.py --apply --include-auto-format

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
from typing import Optional

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_DB = os.path.join(REPO_ROOT, "asset_capture_app_dev", "data", "QR_codes.db")
DEFAULT_JSON_DIR = os.path.join(REPO_ROOT, "Output_jason_api")
DEFAULT_DICTIONARY = os.path.join(REPO_ROOT, "dictionary", "mechanical_dictionary.py")

SDI_TABLE = "sdi_dataset"

# Matches BF's regex at asset_plate_reviewer_bf.py:314
JSON_NAME_RE = re.compile(r"^([A-Za-z0-9]+)_([A-Za-z]+)_(\d+(?:-\d+)?)\.json$")

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


# --- BF resolver: ported verbatim from asset_plate_reviewer_bf.py:1078, 1091 ---

def _compute_description_bf(asset_group: str, ubc_tag: str, application: str = "") -> str:
    prefix = "BFP-"
    suffix = "BFP"
    app_part = (application or "").strip()
    if app_part:
        app_part += " "
    first_word_group = ""
    if asset_group:
        first_word_group = asset_group.strip().split()[0] + " "
    return f"{prefix}{app_part}{first_word_group}{suffix}".strip()


def resolve_description_bf(cd: dict, asset_group: str, ubc_tag: str, application: str, existing_desc) -> str:
    existing_text = str(existing_desc or "")
    if existing_text.strip():
        return existing_text
    tag = (ubc_tag or "").strip().upper()
    if tag and cd:
        for k in sorted(cd.keys(), key=len, reverse=True):
            entry = cd.get(k) or {}
            if not isinstance(entry, dict):
                continue
            if "|" in k:
                continue  # BF resolver ignores composite keys
            if tag.startswith(k.upper()) and entry.get("description"):
                base = entry["description"]
                return f"{base} - {ubc_tag}" if ubc_tag else base
    return _compute_description_bf(asset_group, ubc_tag, application)


# --- Auto-format detectors ---
# BF has two auto-format shapes:
#   1) Dictionary hit:        "{description} - {ubc_tag}"
#   2) Asset Group fallback:  "BFP-{maybe Application }{maybe AssetGroupWord }BFP"

def looks_like_dict_auto_format(current_desc: str, ubc_tag: str) -> bool:
    if not current_desc or not ubc_tag:
        return False
    return str(current_desc).strip().endswith(f" - {str(ubc_tag).strip()}")


def looks_like_bfp_fallback(current_desc: str) -> bool:
    s = (current_desc or "").strip()
    return s.startswith("BFP-") and s.endswith("BFP")


def looks_like_auto_format(current_desc: str, ubc_tag: str) -> bool:
    return looks_like_dict_auto_format(current_desc, ubc_tag) or looks_like_bfp_fallback(current_desc)


def write_json_atomic(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json-dir", default=DEFAULT_JSON_DIR)
    ap.add_argument("--dictionary", default=DEFAULT_DICTIONARY)
    ap.add_argument("--qr", default=None, help="Restrict to one QR code")
    ap.add_argument("--building", default=None, help="Restrict to one building")
    ap.add_argument("--apply", action="store_true", help="Actually write (default: dry-run)")
    ap.add_argument("--include-auto-format", action="store_true",
                    help="Also replace Descriptions matching auto-format shapes (default: only fill empty)")
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

    with qrdb.get_connection(sqlite_path=args.db) as conn:
        if not qrdb.is_postgres():
            conn.execute("PRAGMA foreign_keys = ON")

        for filename in sorted(os.listdir(args.json_dir)):
            m = JSON_NAME_RE.match(filename)
            if not m:
                continue
            qr, discipline, building = m.groups()
            if discipline.upper() != "BF":
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

            tag_val = sd.get("UBC Tag") or sd.get("UBC Asset Tag") or ""
            current_desc = str(sd.get("Description") or "").strip()
            asset_group = sd.get("Asset Group") or ""
            application = sd.get("Application") or ""

            if not str(tag_val).strip():
                # BF resolver still produces a non-empty fallback ("BFP-... BFP")
                # from Asset Group even when UBC Tag is empty. Allow that case.
                proposed = resolve_description_bf(cd, asset_group, "", application, "")
            else:
                proposed = resolve_description_bf(cd, asset_group, tag_val, application, "")

            if proposed == current_desc:
                skipped_no_change += 1
                continue

            if current_desc:
                if not (args.include_auto_format and looks_like_auto_format(current_desc, tag_val)):
                    skipped_human_edit += 1
                    if args.verbose:
                        print(f"[skip] {qr} ({building}): existing='{current_desc}' (preserved)")
                    continue

            if not proposed:
                skipped_empty_tag += 1
                if args.verbose:
                    print(f"[skip] {qr} ({building}): resolver produced empty value")
                continue

            sdi_before = fetch_sdi_row(conn, qr, building)
            if sdi_before is not None:
                sdi_existing_desc = str(sdi_before.get("Description") or "").strip()
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
                                app_name="reviewer_bf",
                                table_name=SDI_TABLE,
                                record_pk=f"{qr}|{building}",
                                op_type="UPDATE",
                                field_changes=changes,
                                source="system",
                                modified_by="backfill_bf_descriptions",
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
    print(f"Scanned BF JSONs:     {scanned}")
    print(f"{'Would change' if not args.apply else 'Changed'}:           {would_change}")
    print(f"Skipped (empty out):  {skipped_empty_tag}")
    print(f"Skipped (no change):  {skipped_no_change}")
    print(f"Skipped (human edit): {skipped_human_edit}")
    print(f"Errors:               {errors}")
    if not args.apply and would_change:
        print()
        print("Dry-run only. Re-run with --apply to write.")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
