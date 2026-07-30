"""Daily backup of QR_codes.db and Output_jason_api/ on the Asset Capture VM.

Writes two artefacts to ``/home/developer/backup_app/``:
  * ``QR_codes_<YYYY-MM-DD>.db``         — online SQLite .backup (WAL-safe)
  * ``Output_jason_api_<YYYY-MM-DD>.tar.gz`` — compressed archive of the JSON dir

Retention: prunes any backup file older than ``--retention-days`` (default 14)
that matches one of our name patterns. We only delete files we produced — the
one-off ``QR_codes_2026-04-23_165308_post-recovery.db`` (named with an extra
tag) will not match the glob and is preserved.

Cron (developer user):
    0 2 * * *  /usr/bin/python3 /home/developer/scripts/backup_daily.py \
                   >> /home/developer/logs/backup.log 2>&1

Idempotent by date — re-running the same day overwrites the day's files.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
from typing import Iterable

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

DEFAULT_DB = "/home/developer/asset_capture_app_dev/data/QR_codes.db"
DEFAULT_JSON_DIR = "/home/developer/Output_jason_api"
DEFAULT_OUT = "/home/developer/backup_app"
DEFAULT_RETENTION = 14

DB_NAME_RE = re.compile(r"^QR_codes_(\d{4}-\d{2}-\d{2})\.db$")
PG_DUMP_NAME_RE = re.compile(r"^qr_code_db_(\d{4}-\d{2}-\d{2})\.dump$")
JSON_NAME_RE = re.compile(r"^Output_jason_api_(\d{4}-\d{2}-\d{2})\.tar\.gz$")


def _timestamp() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _log(line: str) -> None:
    print(f"{_timestamp()}  {line}", flush=True)


def backup_db(src: str, dst: str) -> int:
    """SQLite online backup. Returns bytes written."""
    tmp = f"{dst}.part"
    with sqlite3.connect(src) as s, sqlite3.connect(tmp) as d:
        s.backup(d)
    os.replace(tmp, dst)
    return os.path.getsize(dst)


def backup_db_postgres(dsn: str, dst: str) -> int:
    """PostgreSQL backup via pg_dump (custom/compressed format). Returns bytes written.
    Restore with: pg_restore -d <dbname> <dst>"""
    tmp = f"{dst}.part"
    cmd = ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "-f", tmp, dsn]
    subprocess.run(cmd, check=True)
    os.replace(tmp, dst)
    return os.path.getsize(dst)


def backup_json_dir(src_dir: str, dst: str) -> tuple[int, int]:
    """tar.gz the JSON directory. Returns (file_count, bytes_written)."""
    tmp = f"{dst}.part"
    count = 0
    with tarfile.open(tmp, "w:gz") as tar:
        for name in sorted(os.listdir(src_dir)):
            path = os.path.join(src_dir, name)
            if not os.path.isfile(path):
                continue
            tar.add(path, arcname=os.path.join("Output_jason_api", name))
            count += 1
    os.replace(tmp, dst)
    return count, os.path.getsize(dst)


def _candidates_for_pruning(out_dir: str) -> Iterable[tuple[str, _dt.date]]:
    for name in os.listdir(out_dir):
        m = DB_NAME_RE.match(name) or PG_DUMP_NAME_RE.match(name) or JSON_NAME_RE.match(name)
        if not m:
            continue
        try:
            d = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        yield os.path.join(out_dir, name), d


def prune(out_dir: str, retention_days: int) -> int:
    cutoff = _dt.date.today() - _dt.timedelta(days=retention_days)
    removed = 0
    for path, day in _candidates_for_pruning(out_dir):
        if day < cutoff:
            try:
                os.remove(path)
                _log(f"  prune: removed {os.path.basename(path)} (date {day})")
                removed += 1
            except OSError as exc:
                _log(f"  prune: FAILED to remove {path}: {exc}")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=DEFAULT_DB)
    parser.add_argument("--json-dir", default=DEFAULT_JSON_DIR)
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION)
    args = parser.parse_args()

    today = _dt.date.today().isoformat()
    os.makedirs(args.out_dir, exist_ok=True)

    if not qrdb.is_postgres() and not os.path.exists(args.db_path):
        _log(f"ERROR: DB not found: {args.db_path}")
        return 2
    if not os.path.isdir(args.json_dir):
        _log(f"ERROR: JSON dir not found: {args.json_dir}")
        return 2

    _log(f"backup_daily START  out={args.out_dir}  retention_days={args.retention_days}  backend={qrdb.backend()}")

    # 1. DB
    try:
        if qrdb.is_postgres():
            dsn = os.environ.get(
                "QR_PG_DSN", "host=127.0.0.1 port=5433 dbname=qr_code_db user=developer"
            )
            db_dst = os.path.join(args.out_dir, f"qr_code_db_{today}.dump")
            size = backup_db_postgres(dsn, db_dst)
        else:
            db_dst = os.path.join(args.out_dir, f"QR_codes_{today}.db")
            size = backup_db(args.db_path, db_dst)
        _log(f"  db:   wrote {db_dst}  ({size:,} bytes)")
    except Exception as exc:  # noqa: BLE001
        _log(f"  db:   FAILED — {exc}")
        return 1

    # 2. JSON dir
    json_dst = os.path.join(args.out_dir, f"Output_jason_api_{today}.tar.gz")
    try:
        n, size = backup_json_dir(args.json_dir, json_dst)
        _log(f"  json: wrote {json_dst}  ({n:,} files, {size:,} bytes)")
    except Exception as exc:  # noqa: BLE001
        _log(f"  json: FAILED — {exc}")
        return 1

    # 3. Retention prune (best-effort; don't fail the run if prune errors)
    try:
        removed = prune(args.out_dir, args.retention_days)
        _log(f"  prune: {removed} file(s) removed (> {args.retention_days} days old)")
    except Exception as exc:  # noqa: BLE001
        _log(f"  prune: FAILED — {exc}")

    _log("backup_daily OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
