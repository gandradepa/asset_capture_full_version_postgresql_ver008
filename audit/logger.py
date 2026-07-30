"""Audit-trail logger shared across all Asset Capture apps.

The single public entry point is `log_change`. Callers pass in their own
DB connection (sqlite3.Connection or SQLAlchemy session) and a dict of
{field: (old, new)} changes. The logger writes one row per changed field
into the `audit_trail` table and **does not commit** — it participates in
the caller's transaction so the data write and the audit rows succeed or
roll back together.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Mapping

AUDIT_TABLE = "audit_trail"

_log = logging.getLogger(__name__)

_INSERT_SQL = f"""
INSERT INTO {AUDIT_TABLE} (
    qr_code, description, modification_date, modification_time,
    modified_by, source, app_name, table_name, record_pk,
    op_type, field_name, old_value, new_value
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_VALID_OPS = {"INSERT", "UPDATE", "DELETE"}


def _norm(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s != "" else None
    return v


def _coerce(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list, tuple)):
        try:
            return json.dumps(v, default=str, ensure_ascii=False)
        except Exception:
            return str(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:
            return repr(v)
    return str(v)


def _resolve_modified_by(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from flask_login import current_user  # type: ignore

        if getattr(current_user, "is_authenticated", False):
            uname = getattr(current_user, "username", None)
            if uname:
                return str(uname)
    except Exception:
        pass
    return "system"


def _execute_many(conn, rows: list[tuple]) -> None:
    # Any DBAPI-style connection exposing executemany (sqlite3, or the Phase C `db.py`
    # PostgreSQL wrapper which translates '?'->'%s') takes the '?'-placeholder path.
    # Genuine SQLAlchemy Session/Connection objects lack executemany and fall through
    # to the text() path below. (Was: isinstance(conn, sqlite3.Connection).)
    if hasattr(conn, "executemany"):
        conn.executemany(_INSERT_SQL, rows)
        return

    # SQLAlchemy Session / Connection / Engine.connect()
    try:
        from sqlalchemy import text  # type: ignore
    except ImportError:
        raise TypeError(
            f"Unsupported connection type for audit logger: {type(conn).__name__}"
        )

    sa_sql = text(
        f"""
        INSERT INTO {AUDIT_TABLE} (
            qr_code, description, modification_date, modification_time,
            modified_by, source, app_name, table_name, record_pk,
            op_type, field_name, old_value, new_value
        ) VALUES (
            :qr_code, :description, :modification_date, :modification_time,
            :modified_by, :source, :app_name, :table_name, :record_pk,
            :op_type, :field_name, :old_value, :new_value
        )
        """
    )
    payload = [
        dict(
            qr_code=r[0], description=r[1],
            modification_date=r[2], modification_time=r[3],
            modified_by=r[4], source=r[5],
            app_name=r[6], table_name=r[7], record_pk=r[8],
            op_type=r[9], field_name=r[10],
            old_value=r[11], new_value=r[12],
        )
        for r in rows
    ]
    conn.execute(sa_sql, payload)


def log_change(
    conn,
    *,
    qr_code: str | None,
    app_name: str,
    table_name: str,
    record_pk,
    op_type: str,
    field_changes: Mapping[str, tuple],
    modified_by: str | None = None,
    source: str = "human",
    description: str = "",
    now_utc: datetime | None = None,
) -> int:
    """Insert one audit row per changed field. Returns rows written.

    Caller is responsible for `conn.commit()`. No-op rows (where the
    normalized old and new values are equal) are filtered out.
    """
    if op_type not in _VALID_OPS:
        raise ValueError(f"op_type must be one of {_VALID_OPS}, got {op_type!r}")
    if not field_changes:
        return 0

    # Local server time (America/Vancouver in production). Matches the rest of
    # the system (e.g. asset_capture_app_dev/app.py date_hour) and avoids the
    # UTC offset surprise in the dashboard. The arg name is kept for backward
    # compat — callers can still pass an explicit datetime.
    now = now_utc or datetime.now()
    mod_date = now.strftime("%Y-%m-%d")
    mod_time = now.strftime("%H:%M:%S")
    user = _resolve_modified_by(modified_by)
    src = source or "human"
    pk_str = "" if record_pk is None else str(record_pk)
    qr = None if qr_code in (None, "") else str(qr_code)

    rows: list[tuple] = []
    for field, change in field_changes.items():
        try:
            old, new = change
        except (TypeError, ValueError):
            old, new = None, change
        if _norm(old) == _norm(new):
            continue
        rows.append((
            qr,
            description or "",
            mod_date,
            mod_time,
            user,
            src,
            app_name,
            table_name,
            pk_str,
            op_type,
            field,
            _coerce(old),
            _coerce(new),
        ))

    if not rows:
        return 0

    try:
        _execute_many(conn, rows)
    except Exception as exc:
        # Audit failures must never silently swallow the original write,
        # but we also don't want to mask the underlying DB error: re-raise
        # so the caller's transaction rolls back atomically.
        _log.exception("audit.log_change failed: %s", exc)
        raise
    return len(rows)
