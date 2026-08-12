"""Service layer for the Dashboard "Disposed" tool.

An asset is *disposed* when it has an active row in ``disposed_assets``
(``status = 'disposed'``). That membership - not a flag on ``QR_codes`` - is the
single source of truth: the review apps, the capture app and the AI
interpreters all subtract the same set, the way the review dashboards already
hide ``sdi_print_out_arch`` members.

Disposal deletes the curated ``sdi_dataset`` / ``sdi_dataset_EL`` row after
snapshotting it, and never touches a file on disk. Photos and
``Output_jason_api`` payloads stay where they are, so the whole operation is one
PostgreSQL transaction that either lands completely or not at all, and Restore
can rebuild the curated row from the snapshot.

Kept free of Flask imports so the transaction logic can be exercised directly;
callers pass ``modified_by`` and own the ``commit()``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import db as qrdb

try:
    from audit.logger import log_change as _audit_log_change
except Exception as _audit_exc:  # pragma: no cover - audit is optional at import time
    _audit_log_change = None
    print(f"WARNING: audit logger unavailable, disposals will not be audited: {_audit_exc}")


DISPOSED_TABLE = "disposed_assets"
APP_NAME = "dashboard_disposed"

# Reason codes, in dropdown order. Mirrored by chk_disposed_reason
# (scripts/migrations/2026-08-11_disposed_assets.sql, widened by
# 2026-08-12_disposed_reason_wrong_asset.sql) - keep both in step: a reason the
# constraint rejects fails the whole disposal transaction.
DISPOSAL_REASONS = ("Decommissioned", "Duplicated", "Wrong Asset", "User Request")

SDI_TABLE_ME_BF = "sdi_dataset"
SDI_TABLE_EL = "sdi_dataset_EL"
PACKAGE_TABLES = ("sdi_print_out", "sdi_print_out_arch")

# Generated column on sdi_dataset_EL: the database computes it, so a restore
# must never list it in the INSERT column set.
EL_GENERATED_COLUMNS = ("ID_check",)

# Highest photo sequence any discipline captures (ME goes to -4, BF/EL to -3).
MAX_PHOTO_SEQ = 4


# ------------------------------------------------------------------ utilities

def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_asset_type(raw_value: str) -> str:
    """'- ME' / 'Mechanical' / 'me' -> 'ME'. Mirrors the reviewer normalization."""
    token = re.sub(r"[^A-Za-z]", "", (raw_value or "")).upper()
    if token.startswith("ME") or token.startswith("MECH"):
        return "ME"
    if token.startswith("EL") or token.startswith("ELEC"):
        return "EL"
    if token.startswith("BF") or token.startswith("BACK"):
        return "BF"
    return token[:2] if token else ""


def sdi_table_for(asset_type: str) -> str:
    """Curated table that holds this discipline, or '' when unknown."""
    discipline = normalize_asset_type(asset_type)
    if discipline == "EL":
        return SDI_TABLE_EL
    if discipline in ("ME", "BF"):
        return SDI_TABLE_ME_BF
    return ""


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    """Build a plain dict from a row, whichever row factory is in play.

    Under PostgreSQL the codebase's ``row_factory = sqlite3.Row`` idiom maps to
    RealDictCursor, so rows may already be mappings; without it they are tuples
    and the column names come from ``cursor.description``.
    """
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {str(k): row[k] for k in row.keys()}
    names = [d[0] for d in (cursor.description or [])]
    return {name: row[idx] for idx, name in enumerate(names)}


def _fetch_one_dict(conn, sql: str, params: Tuple) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return _row_to_dict(cur, cur.fetchone())


def _fetch_all_dicts(conn, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    rows = cur.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [{str(k): r[k] for k in r.keys()} for r in rows]
    names = [d[0] for d in (cur.description or [])]
    return [{name: r[idx] for idx, name in enumerate(names)} for r in rows]


def _now_parts() -> str:
    """Local server time, matching audit_trail's date/time convention."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def has_disposal_table(conn) -> bool:
    """False before the migration has been applied, so callers degrade quietly."""
    try:
        return qrdb.has_table(conn, DISPOSED_TABLE)
    except Exception:
        return False


# ---------------------------------------------------------------- file lookups

def _dir_candidates(name: str, extra_roots: Tuple[str, ...] = ()) -> Path:
    here = Path(__file__).resolve().parent
    candidates = [Path("/home/developer") / name, here.parent / name, here / name]
    candidates.extend(Path(root) / name for root in extra_roots)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


JSON_DIR = _dir_candidates("Output_jason_api")
PHOTO_DIR = _dir_candidates("Capture_photos_upload")


def find_photos_for_qr(qr_code: str, photo_dir: Optional[Path] = None) -> List[str]:
    """Every captured photo filename for a QR, ordered by sequence.

    Matches the first space-delimited filename token exactly. A prefix match
    would make QR '123' pick up '1234 BUCH ME - 2.jpg'.
    """
    qr = _clean(qr_code)
    directory = photo_dir or PHOTO_DIR
    if not qr or not directory.exists():
        return []

    sanitized = re.sub(r"[^A-Za-z0-9_-]", "", qr)
    if not sanitized:
        return []

    found: List[Tuple[int, str]] = []
    try:
        for path in directory.glob(f"{sanitized}*"):
            if not path.is_file():
                continue
            stem = path.stem.strip()
            if stem.split(" ")[0] != sanitized:
                continue
            match = re.search(r"-\s*(\d+)$", stem)
            seq = int(match.group(1)) if match else 99
            found.append((seq, path.name))
    except Exception as exc:  # pragma: no cover - filesystem hiccup
        print(f"[disposed] photo scan failed for {qr}: {exc}")
        return []

    return [name for _, name in sorted(found, key=lambda item: (item[0], item[1]))]


def find_structured_json(qr_code: str, json_dir: Optional[Path] = None) -> Tuple[str, Dict[str, Any]]:
    """Return (filename, parsed payload) for a QR's structured extraction JSON.

    Filenames are ``{QR}_{DISCIPLINE}_{BUILDING}.json``; the elapsed-time
    sidecar ``{QR}_et.json`` is deliberately excluded here.
    """
    qr = _clean(qr_code)
    directory = json_dir or JSON_DIR
    if not qr or not directory.exists():
        return "", {}

    sanitized = re.sub(r"[^A-Za-z0-9_-]", "", qr)
    if not sanitized:
        return "", {}

    pattern = re.compile(rf"^{re.escape(sanitized)}_([A-Za-z]+)_(.+)\.json$")
    try:
        for path in sorted(directory.glob(f"{sanitized}_*.json")):
            if not path.is_file():
                continue
            match = pattern.match(path.name)
            if not match or match.group(1).lower() == "et":
                continue
            with open(path, "r", encoding="utf-8") as fh:
                return path.name, json.load(fh)
    except Exception as exc:
        print(f"[disposed] structured JSON read failed for {qr}: {exc}")
    return "", {}


def find_elapsed_json(qr_code: str, json_dir: Optional[Path] = None) -> Dict[str, Any]:
    qr = _clean(qr_code)
    directory = json_dir or JSON_DIR
    if not qr or not directory.exists():
        return {}
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "", qr)
    path = directory / f"{sanitized}_et.json"
    try:
        if path.is_file():
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        print(f"[disposed] elapsed JSON read failed for {qr}: {exc}")
    return {}


# ------------------------------------------------------------------ DB lookups

def lookup_qr_row(conn, qr_code: str) -> Optional[Dict[str, Any]]:
    """Resolve a QR to its QR_codes row: exact match, then zero-stripped."""
    qr = _clean(qr_code)
    if not qr:
        return None

    row = _fetch_one_dict(
        conn,
        'SELECT * FROM "QR_codes" WHERE TRIM("QR_code_ID") = TRIM(?) LIMIT 1',
        (qr,),
    )
    if row is not None:
        return row

    return _fetch_one_dict(
        conn,
        'SELECT * FROM "QR_codes" WHERE LTRIM(TRIM("QR_code_ID"), \'0\') = ? LIMIT 1',
        (qr.lstrip("0") or "0",),
    )


def active_disposal(conn, qr_code: str) -> Optional[Dict[str, Any]]:
    """The open disposal for this QR, or None."""
    qr = _clean(qr_code)
    if not qr or not has_disposal_table(conn):
        return None
    return _fetch_one_dict(
        conn,
        f'SELECT * FROM "{DISPOSED_TABLE}" WHERE TRIM("qr_code") = TRIM(?)'
        f' AND "status" = \'disposed\' LIMIT 1',
        (qr,),
    )


def disposed_qr_set(conn) -> set:
    """Every currently-disposed QR code. Used by the hide-from-listing filters."""
    if not has_disposal_table(conn):
        return set()
    cur = conn.cursor()
    cur.execute(f'SELECT "qr_code" FROM "{DISPOSED_TABLE}" WHERE "status" = \'disposed\'')
    result = set()
    for row in cur.fetchall():
        value = row["qr_code"] if hasattr(row, "keys") else row[0]
        if value:
            result.add(str(value).strip())
    return result


def fetch_sdi_row(conn, table: str, qr_code: str) -> Optional[Dict[str, Any]]:
    """Snapshot the curated row for a QR, or None when it was never approved."""
    if not table or not qrdb.has_table(conn, table):
        return None
    return _fetch_one_dict(
        conn,
        f'SELECT * FROM "{table}" WHERE TRIM("QR Code") = TRIM(?) LIMIT 1',
        (_clean(qr_code),),
    )


def fetch_qr_code_assets(conn, qr_code: str) -> List[Dict[str, Any]]:
    """Per-asset process rows. Snapshot only - these are never deleted.

    Removing them would let the reviewers' auto-register path recreate the QR
    from its JSON on the next page load.
    """
    if not qrdb.has_table(conn, "QR_code_assets"):
        return []
    qr = _clean(qr_code)
    columns = set(qrdb.table_columns(conn, "QR_code_assets"))
    if "qr_code_id" in columns:
        return _fetch_all_dicts(
            conn,
            'SELECT * FROM "QR_code_assets" WHERE TRIM("qr_code_id") = TRIM(?)',
            (qr,),
        )
    return _fetch_all_dicts(
        conn,
        'SELECT * FROM "QR_code_assets" WHERE "code_assets" LIKE ?',
        (f"{qr}%",),
    )


def qr_has_package_rows(conn, qr_code: str) -> bool:
    """True when the QR appears anywhere in an active or archived SDI package.

    Deliberately conservative - package schemas have shifted over time, so every
    column is compared as text, mirroring the capture app's package guard.
    """
    qr = _clean(qr_code)
    if not qr:
        return False
    for table in PACKAGE_TABLES:
        if not qrdb.has_table(conn, table):
            continue
        columns = qrdb.table_columns(conn, table)
        if not columns:
            continue
        predicates = " OR ".join(f'CAST("{col}" AS TEXT) = ?' for col in columns)
        cur = conn.cursor()
        cur.execute(
            f'SELECT 1 FROM "{table}" WHERE {predicates} LIMIT 1',
            tuple(qr for _ in columns),
        )
        if cur.fetchone() is not None:
            return True
    return False


# ---------------------------------------------------------------- eligibility

def _check(key: str, label: str, passed: bool, detail: str = "") -> Dict[str, Any]:
    return {"key": key, "label": label, "passed": bool(passed), "detail": detail}


def evaluate_eligibility(
    conn,
    qr_code: str,
    qr_row: Optional[Dict[str, Any]],
    structured: Optional[Dict[str, Any]] = None,
    sdi_row: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """The disposal gate, as a list of named checks the UI renders verbatim.

    Approval is judged from the curated row (``sdi_dataset`` for ME/BF,
    ``sdi_dataset_EL`` for EL) and the review JSON - deliberately NOT from
    ``QR_codes."Approved"``, which carries stale legacy values that would
    wrongly lock assets out of disposal (2026-08-11 correction). The JSON check
    covers the write-order race: the review apps write the JSON before the
    curated row, so an approval in flight shows ``Approved = "True"`` in the
    JSON first.
    """
    qr = _clean(qr_code)

    if qr_row is None:
        return [
            _check("qr_found", "QR code exists", False,
                   f"No asset is registered under QR {qr}." if qr else "Enter a QR code."),
            _check("not_disposed", "Not already disposed", False, "Waiting on a valid QR code."),
            _check("not_approved", "Not approved", False, "Waiting on a valid QR code."),
            _check("not_packaged", "Not in an SDI package", False, "Waiting on a valid QR code."),
        ]

    resolved_qr = _clean(qr_row.get("QR_code_ID")) or qr
    checks = [_check("qr_found", "QR code exists", True, f"Found in QR_codes as {resolved_qr}.")]

    existing = active_disposal(conn, resolved_qr)
    checks.append(_check(
        "not_disposed", "Not already disposed", existing is None,
        "" if existing is None else
        f"Disposed on {_clean(existing.get('disposed_at'))} by {_clean(existing.get('disposed_by'))}."
        " See the Disposed Register.",
    ))

    approved_sources = []
    if sdi_row and _clean(sdi_row.get("Approved")) == "1":
        table = SDI_TABLE_EL if "ID_check" in sdi_row else SDI_TABLE_ME_BF
        approved_sources.append(table)
    if structured:
        structured_data = structured.get("structured_data") or {}
        if _clean(structured_data.get("Approved")).lower() == "true":
            approved_sources.append("the review JSON")
    checks.append(_check(
        "not_approved", "Not approved", not approved_sources,
        "" if not approved_sources else
        "This asset is approved in " + ", ".join(approved_sources)
        + ". Approved assets cannot be disposed.",
    ))

    packaged = qr_has_package_rows(conn, resolved_qr)
    checks.append(_check(
        "not_packaged", "Not in an SDI package", not packaged,
        "" if not packaged else
        "This asset belongs to an SDI package. Remove it from the package first.",
    ))

    return checks


def eligibility_passed(checks: List[Dict[str, Any]]) -> bool:
    return all(check.get("passed") for check in checks)


class DisposalError(Exception):
    """Disposal or restore refused. ``checks`` carries the failed gate, if any."""

    def __init__(self, message: str, status: int = 409, checks: Optional[List] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.checks = checks or []


# ------------------------------------------------------------------- mutations

def _lock_qr_row(conn, qr_code: str) -> Optional[Dict[str, Any]]:
    """Re-read the QR_codes row under a row lock (PostgreSQL only).

    Serializes disposal against a reviewer's concurrent approve, which upserts
    the same row. SQLite has no row locks and serializes writes anyway.
    """
    suffix = " FOR UPDATE" if qrdb.is_postgres() else ""
    return _fetch_one_dict(
        conn,
        f'SELECT * FROM "QR_codes" WHERE "QR_code_ID" = ?{suffix}',
        (_clean(qr_code),),
    )


def _audit(conn, **kwargs) -> None:
    """Write audit rows inside the caller's transaction.

    Unlike the dictionary audit helper, failures are not swallowed: the audit
    row and the disposal must land together or not at all.
    """
    if _audit_log_change is None:
        return
    _audit_log_change(conn, app_name=APP_NAME, source="human", **kwargs)


def dispose_asset(
    conn,
    qr_code: str,
    reason: str,
    notes: str,
    modified_by: str,
) -> Dict[str, Any]:
    """Archive a QR and remove its curated row. Caller commits.

    Files are never touched: photos and JSON stay on disk, which keeps the whole
    operation inside one database transaction and lets Restore put the curated
    row back without regenerating anything.
    """
    if not has_disposal_table(conn):
        raise DisposalError(
            "The disposed_assets table is missing. Apply "
            "scripts/migrations/2026-08-11_disposed_assets.sql first.",
            status=503,
        )

    reason = _clean(reason)
    if reason not in DISPOSAL_REASONS:
        raise DisposalError(
            f"Select a disposal reason: {', '.join(DISPOSAL_REASONS)}.", status=400
        )

    qr_row = _lock_qr_row(conn, qr_code)
    if qr_row is None:
        # The lookup may have resolved a zero-stripped variant; re-resolve, then
        # lock the canonical id.
        resolved = lookup_qr_row(conn, qr_code)
        if resolved is None:
            raise DisposalError(f"No asset is registered under QR {_clean(qr_code)}.", status=404)
        qr_row = _lock_qr_row(conn, _clean(resolved.get("QR_code_ID")))
        if qr_row is None:
            raise DisposalError(f"No asset is registered under QR {_clean(qr_code)}.", status=404)

    qr = _clean(qr_row.get("QR_code_ID"))
    asset_type = normalize_asset_type(_clean(qr_row.get("asset_type")))
    sdi_table = sdi_table_for(asset_type)
    sdi_row = fetch_sdi_row(conn, sdi_table, qr) if sdi_table else None

    structured_name, structured = find_structured_json(qr)
    if not asset_type and structured_name:
        # QR_codes.asset_type can be blank on older rows; the JSON filename
        # token is what the review apps themselves treat as authoritative.
        match = re.match(r"^[^_]+_([A-Za-z]+)_", structured_name)
        if match:
            asset_type = normalize_asset_type(match.group(1))
            sdi_table = sdi_table_for(asset_type)
            sdi_row = fetch_sdi_row(conn, sdi_table, qr) if sdi_table else None

    checks = evaluate_eligibility(conn, qr, qr_row, structured, sdi_row)
    if not eligibility_passed(checks):
        failed = next(c for c in checks if not c["passed"])
        raise DisposalError(failed.get("detail") or failed["label"], status=409, checks=checks)

    asset_rows = fetch_qr_code_assets(conn, qr)
    photos = find_photos_for_qr(qr)
    elapsed = find_elapsed_json(qr)
    disposed_at = _now_parts()

    cur = conn.cursor()
    cur.execute(
        f'''
        INSERT INTO "{DISPOSED_TABLE}" (
            "qr_code", "building_code", "asset_type", "reason", "notes",
            "disposed_by", "disposed_at", "sdi_table", "sdi_row_json",
            "qr_codes_row_json", "qr_code_assets_json", "structured_json",
            "et_json", "photos_json", "status"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'disposed')
        RETURNING "id"
        ''',
        (
            qr,
            _clean(qr_row.get("Building Code")),
            asset_type,
            reason,
            _clean(notes),
            _clean(modified_by) or "system",
            disposed_at,
            sdi_table if sdi_row else "",
            json.dumps(sdi_row, default=str) if sdi_row else "",
            json.dumps(qr_row, default=str),
            json.dumps(asset_rows, default=str) if asset_rows else "",
            json.dumps(structured, default=str) if structured else "",
            json.dumps(elapsed, default=str) if elapsed else "",
            json.dumps(photos),
        ),
    )
    inserted = _row_to_dict(cur, cur.fetchone()) or {}
    disposal_id = inserted.get("id")
    if disposal_id is None:
        raise DisposalError("Could not record the disposal.", status=500)

    deleted_rows = 0
    if sdi_row is not None:
        cur = conn.cursor()
        cur.execute(f'DELETE FROM "{sdi_table}" WHERE TRIM("QR Code") = TRIM(?)', (qr,))
        deleted_rows = max(0, cur.rowcount)
        if deleted_rows != 1:
            # The snapshot said exactly one row was there. Anything else means
            # the table changed underneath us - abort rather than half-dispose.
            raise DisposalError(
                f"Expected to remove 1 row from {sdi_table}, removed {deleted_rows}.",
                status=500,
            )

    # A disposed asset must stop looking like pending AI work, or the cron gate
    # never goes quiet and the pending badge never clears. Restore puts the
    # original value back from the snapshot.
    ai_status_before = _clean(qr_row.get("ai_status"))
    ai_status_changed = ai_status_before == "0"
    if ai_status_changed:
        conn.execute('UPDATE "QR_codes" SET "ai_status" = ? WHERE "QR_code_ID" = ?', ("1", qr))

    description = f"Disposed ({reason}) via POST /api/disposed-assets"
    _audit(
        conn,
        qr_code=qr,
        table_name=DISPOSED_TABLE,
        record_pk=disposal_id,
        op_type="INSERT",
        field_changes={
            "qr_code": (None, qr),
            "reason": (None, reason),
            "asset_type": (None, asset_type),
            "notes": (None, _clean(notes)),
            "status": (None, "disposed"),
        },
        modified_by=modified_by,
        description=description,
    )
    if sdi_row is not None:
        _audit(
            conn,
            qr_code=qr,
            table_name=sdi_table,
            record_pk=qr,
            op_type="DELETE",
            field_changes={key: (value, None) for key, value in sdi_row.items()},
            modified_by=modified_by,
            description=description,
        )
    if ai_status_changed:
        _audit(
            conn,
            qr_code=qr,
            table_name="QR_codes",
            record_pk=qr,
            op_type="UPDATE",
            field_changes={"ai_status": (ai_status_before, "1")},
            modified_by=modified_by,
            description=f"{description} (withdrawn from the AI queue)",
        )

    return {
        "id": disposal_id,
        "qr_code": qr,
        "asset_type": asset_type,
        "building_code": _clean(qr_row.get("Building Code")),
        "reason": reason,
        "disposed_at": disposed_at,
        "disposed_by": _clean(modified_by),
        "sdi_table": sdi_table if sdi_row else "",
        "sdi_rows_removed": deleted_rows,
        "photos": photos,
    }


def restore_asset(conn, disposal_id: int, modified_by: str) -> Dict[str, Any]:
    """Reverse a disposal, rebuilding the curated row from its snapshot.

    The disposal row is kept and marked 'restored' so the history survives; the
    partial unique index then allows the QR to be disposed again later.
    """
    if not has_disposal_table(conn):
        raise DisposalError("The disposed_assets table is missing.", status=503)

    record = _fetch_one_dict(
        conn, f'SELECT * FROM "{DISPOSED_TABLE}" WHERE "id" = ?', (disposal_id,)
    )
    if record is None:
        raise DisposalError(f"Disposal {disposal_id} was not found.", status=404)
    if _clean(record.get("status")) != "disposed":
        raise DisposalError("This disposal has already been restored.", status=409)

    qr = _clean(record.get("qr_code"))
    qr_row = _lock_qr_row(conn, qr)
    if qr_row is None:
        raise DisposalError(
            f"QR {qr} no longer exists in QR_codes, so it cannot be restored. "
            "The disposal record is kept for history.",
            status=409,
        )

    sdi_table = _clean(record.get("sdi_table"))
    snapshot: Dict[str, Any] = {}
    if sdi_table and _clean(record.get("sdi_row_json")):
        try:
            snapshot = json.loads(record["sdi_row_json"]) or {}
        except (TypeError, ValueError) as exc:
            raise DisposalError(f"The stored snapshot is unreadable: {exc}", status=500)

    restored_columns = 0
    if snapshot and sdi_table:
        if not qrdb.has_table(conn, sdi_table):
            raise DisposalError(f"Table {sdi_table} is missing.", status=500)

        existing = fetch_sdi_row(conn, sdi_table, qr)
        if existing is not None:
            raise DisposalError(
                f"{sdi_table} already holds a row for QR {qr}; restore would overwrite it.",
                status=409,
            )

        live_columns = set(qrdb.table_columns(conn, sdi_table))
        columns = [
            col for col in snapshot.keys()
            if col in live_columns and col not in EL_GENERATED_COLUMNS
        ]
        if not columns:
            raise DisposalError("The snapshot has no columns in common with the table.", status=500)

        values = []
        for col in columns:
            value = snapshot.get(col)
            if col == "Main Asset" and _clean(value) == "":
                # fk_sdi_mainasset rejects '' but accepts NULL.
                value = None
            elif col == "Approved" and _clean(value) == "1":
                # Only unapproved assets can be disposed, so a '1' here would be
                # a corrupted snapshot. Restore it as pending, not approved.
                value = "0"
            values.append(value)

        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(f'"{col}"' for col in columns)
        conn.execute(
            f'INSERT INTO "{sdi_table}" ({column_sql}) VALUES ({placeholders})',
            tuple(values),
        )
        restored_columns = len(columns)

    # Put the pre-disposal ai_status back so the asset resumes wherever it was
    # in the extraction pipeline.
    ai_status_restored = None
    if _clean(record.get("qr_codes_row_json")):
        try:
            original = json.loads(record["qr_codes_row_json"]) or {}
        except (TypeError, ValueError):
            original = {}
        previous = _clean(original.get("ai_status"))
        current = _clean(qr_row.get("ai_status"))
        if previous and previous != current:
            conn.execute(
                'UPDATE "QR_codes" SET "ai_status" = ? WHERE "QR_code_ID" = ?', (previous, qr)
            )
            ai_status_restored = (current, previous)

    restored_at = _now_parts()
    conn.execute(
        f'UPDATE "{DISPOSED_TABLE}" SET "status" = \'restored\', "restored_by" = ?,'
        f' "restored_at" = ? WHERE "id" = ?',
        (_clean(modified_by) or "system", restored_at, disposal_id),
    )

    description = f"Restored disposal {disposal_id} via POST /api/disposed-assets/{disposal_id}/restore"
    if snapshot and sdi_table:
        _audit(
            conn,
            qr_code=qr,
            table_name=sdi_table,
            record_pk=qr,
            op_type="INSERT",
            field_changes={key: (None, value) for key, value in snapshot.items()},
            modified_by=modified_by,
            description=description,
        )
    _audit(
        conn,
        qr_code=qr,
        table_name=DISPOSED_TABLE,
        record_pk=disposal_id,
        op_type="UPDATE",
        field_changes={"status": ("disposed", "restored"), "restored_by": (None, _clean(modified_by))},
        modified_by=modified_by,
        description=description,
    )
    if ai_status_restored:
        _audit(
            conn,
            qr_code=qr,
            table_name="QR_codes",
            record_pk=qr,
            op_type="UPDATE",
            field_changes={"ai_status": ai_status_restored},
            modified_by=modified_by,
            description=f"{description} (returned to the AI queue)",
        )

    structured_name, _ = find_structured_json(qr)
    return {
        "id": disposal_id,
        "qr_code": qr,
        "sdi_table": sdi_table,
        "restored_columns": restored_columns,
        "restored_at": restored_at,
        # Reviewer listings are built from the JSON files on disk; without one
        # the asset will not reappear even though the database is correct.
        "review_json_present": bool(structured_name),
    }


# ---------------------------------------------------------------------- register

REGISTER_COLUMNS = (
    "id", "qr_code", "building_code", "asset_type", "reason", "notes",
    "disposed_by", "disposed_at", "sdi_table", "status", "restored_by", "restored_at",
)


def list_disposals(
    conn,
    search: str = "",
    reason: str = "",
    asset_type: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """The register, newest first. Heavy snapshot columns are left out."""
    if not has_disposal_table(conn):
        return []

    where = ["1=1"]
    params: List[Any] = []

    search = _clean(search)
    if search:
        where.append('(LOWER("qr_code") LIKE ? OR LOWER("building_code") LIKE ?'
                     ' OR LOWER("notes") LIKE ? OR LOWER("disposed_by") LIKE ?)')
        needle = f"%{search.lower()}%"
        params.extend([needle, needle, needle, needle])

    if _clean(reason):
        where.append('"reason" = ?')
        params.append(_clean(reason))
    if _clean(asset_type):
        where.append('"asset_type" = ?')
        params.append(normalize_asset_type(asset_type))
    if _clean(status):
        where.append('"status" = ?')
        params.append(_clean(status))
    if _clean(date_from):
        where.append('"disposed_at" >= ?')
        params.append(_clean(date_from))
    if _clean(date_to):
        # Inclusive of the whole end day, since disposed_at carries a time.
        where.append('"disposed_at" <= ?')
        params.append(f"{_clean(date_to)} 23:59:59")

    safe_limit = max(1, min(int(limit or 500), 2000))
    params.append(safe_limit)
    columns = ", ".join(f'"{col}"' for col in REGISTER_COLUMNS)
    return _fetch_all_dicts(
        conn,
        f'SELECT {columns} FROM "{DISPOSED_TABLE}" WHERE {" AND ".join(where)}'
        f' ORDER BY "id" DESC LIMIT ?',
        tuple(params),
    )


def _parse_json_column(record: Dict[str, Any], column: str) -> Any:
    raw = _clean(record.get(column))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def get_disposal_detail(conn, disposal_id: int) -> Optional[Dict[str, Any]]:
    """One disposal with its snapshots expanded and its photos re-probed."""
    if not has_disposal_table(conn):
        return None

    record = _fetch_one_dict(
        conn, f'SELECT * FROM "{DISPOSED_TABLE}" WHERE "id" = ?', (disposal_id,)
    )
    if record is None:
        return None

    detail = {col: record.get(col) for col in REGISTER_COLUMNS}
    detail["sdi_row"] = _parse_json_column(record, "sdi_row_json") or {}
    detail["qr_codes_row"] = _parse_json_column(record, "qr_codes_row_json") or {}
    detail["qr_code_assets"] = _parse_json_column(record, "qr_code_assets_json") or []
    structured = _parse_json_column(record, "structured_json") or {}
    detail["structured"] = structured
    detail["structured_data"] = structured.get("structured_data") or {}
    detail["elapsed"] = _parse_json_column(record, "et_json") or {}

    archived_photos = _parse_json_column(record, "photos_json") or []
    on_disk = set(find_photos_for_qr(_clean(record.get("qr_code"))))
    detail["photos"] = [
        {"filename": name, "available": name in on_disk} for name in archived_photos
    ]
    return detail
