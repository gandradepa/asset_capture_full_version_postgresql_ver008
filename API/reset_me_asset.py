#!/usr/bin/env python3
"""
reset_me_asset.py — Reversibly force ONE Mechanical (ME) QR to be re-extracted.

The ME extractor skips any QR that already has a JSON in Output_jason_api/ while
ME_OVERWRITE_EXISTING_JSON is off (the cost-control "skip-if-exists" guard in
API_interface_ME_ver00.py::process_single_asset). Setting ai_status=0 alone is
therefore NOT enough to re-read a nameplate: the run logs `skipped_existing_json`
and the JSON is left untouched.

This helper removes that block for a single QR, reversibly:
  1. Moves Output_jason_api/<QR>_ME_*.json aside to a timestamped .bak.
  2. Moves the optional API mirror copy (API/<same name>) aside too.
  3. Sets QR_codes.ai_status = 0 via the backend-agnostic db layer (live
     PostgreSQL in production; the frozen SQLite file is never the live target).

On the next ai_check cron cycle the extractor finds no JSON, runs a full
re-extraction, writes a fresh JSON, and sets ai_status back to 1 itself.

Safety:
  * Dry-run by default — prints the plan and changes nothing. Pass --apply to execute.
  * Refuses to run against the SQLite backend unless --allow-sqlite is given, so a
    stray run can never write ai_status to the frozen rollback DB instead of live PG.
  * JSON files are moved (not deleted) to <name>.bak_<UTCstamp>; reverse by moving back.

Usage:
    python reset_me_asset.py 0000188042            # dry-run (shows plan)
    python reset_me_asset.py 0000188042 --apply    # perform the reset
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

# Run from any cwd: make `import db` resolve to the sibling backend-agnostic layer.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

DEV_PATH = os.getenv("DEV_PATH", "/home/developer")
OUTPUT_FOLDER = os.path.join(DEV_PATH, "Output_jason_api")
API_FOLDER = os.path.join(DEV_PATH, "API")
SQLITE_FALLBACK = os.path.join(DEV_PATH, "asset_capture_app_dev", "data", "QR_codes.db")

# Load the DB backend selector the same way the platform does, so we target the live
# store. Without this, db.py defaults to SQLite (the frozen rollback file). load_dotenv
# does not override variables already present in the process environment.
try:
    from dotenv import load_dotenv

    _env_file = os.path.join(DEV_PATH, "db_backend.env")
    if os.path.isfile(_env_file):
        load_dotenv(_env_file)
except ImportError:
    pass

import db as qrdb  # noqa: E402  (intentionally after sys.path / env setup)

AI_STATUS_TABLE = "QR_codes"
QR_COLUMN_CANDIDATES = ("QR_code_ID", "QR Code", "QR", "QRCode", "QR_code")
AI_STATUS_COLUMN_CANDIDATES = ("ai_status", "AI Status", "aiStatus")


def _resolve_column(columns, candidates):
    """Case/space/underscore-insensitive column match (mirrors the ME script)."""
    norm = {c.lower().replace(" ", "").replace("_", ""): c for c in columns}
    for cand in candidates:
        key = cand.lower().replace(" ", "").replace("_", "")
        if key in norm:
            return norm[key]
    return None


def find_me_json_files(qr):
    """Existing <QR>_ME_*.json files in the output folder and the API mirror."""
    prefix = f"{qr}_ME_"
    hits = []
    for folder in (OUTPUT_FOLDER, API_FOLDER):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.startswith(prefix) and name.lower().endswith(".json"):
                hits.append(os.path.join(folder, name))
    return hits


def main():
    parser = argparse.ArgumentParser(
        description="Reversibly force one Mechanical (ME) QR to re-extract."
    )
    parser.add_argument("qr", help="QR code id, e.g. 0000188042")
    parser.add_argument("--apply", action="store_true",
                        help="Perform changes (default: dry-run).")
    parser.add_argument("--allow-sqlite", action="store_true",
                        help="Permit running against the SQLite backend (default: refuse).")
    args = parser.parse_args()

    qr = args.qr.strip()
    if not qr:
        parser.error("QR code must not be empty.")

    backend = qrdb.backend()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== reset_me_asset ({mode}) qr={qr} backend={backend} ==")

    if backend != "postgres" and not args.allow_sqlite:
        print(
            f"REFUSING: db backend is '{backend}', not 'postgres'. The live store is "
            f"PostgreSQL; writing ai_status to SQLite would hit the frozen rollback file. "
            f"Export DB_BACKEND=postgres (or pass --allow-sqlite for a genuine SQLite dev run)."
        )
        return 2

    json_files = find_me_json_files(qr)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    if json_files:
        print("JSON files to move aside:")
        for p in json_files:
            print(f"  {p}  ->  {p}.bak_{stamp}")
    else:
        print("No existing ME JSON found for this QR (extraction would already run once "
              "ai_status=0).")

    try:
        with qrdb.get_connection(sqlite_path=SQLITE_FALLBACK) as conn:
            cols = qrdb.table_columns(conn, AI_STATUS_TABLE)
            qr_col = _resolve_column(cols, QR_COLUMN_CANDIDATES)
            ai_col = _resolve_column(cols, AI_STATUS_COLUMN_CANDIDATES)
            if not qr_col or not ai_col:
                print(f"ERROR: could not resolve QR/ai_status columns in {AI_STATUS_TABLE}.")
                return 3

            row = conn.execute(
                f'SELECT "{ai_col}" FROM "{AI_STATUS_TABLE}" WHERE "{qr_col}" = ?', (qr,)
            ).fetchone()
            if row is None:
                print(f"ERROR: QR {qr} not found in {AI_STATUS_TABLE}. Nothing changed.")
                return 3
            current = str(row[0]).strip()
            print(f"Current ai_status = {current!r}  ->  target '0'")

            if not args.apply:
                print("\nDry-run only. Re-run with --apply to perform the reset.")
                return 0

            # Move JSON aside FIRST so a JSON never lingers alongside ai_status=0 (which
            # would just re-trigger the skip guard). If the DB update later fails, the
            # rollback leaves ai_status untouched and the ME stale-JSON detector resets
            # any 'ai_status=1 without JSON' rows back to 0 on its next run — self-healing.
            for p in json_files:
                dest = f"{p}.bak_{stamp}"
                os.replace(p, dest)
                print(f"moved: {p} -> {dest}")

            updated = conn.execute(
                f'UPDATE "{AI_STATUS_TABLE}" SET "{ai_col}" = ? WHERE "{qr_col}" = ?',
                ("0", qr),
            ).rowcount
            print(f"ai_status rows updated: {updated}")
            # The connection context manager commits on clean exit.
    except qrdb.DatabaseError as e:
        print(f"DB ERROR: {e}")
        return 4

    print("\nDone. The next ai_check cycle will re-extract this QR and write a fresh JSON.")
    print(f"To undo: move the .bak_{stamp} file(s) back and restore ai_status as needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
