"""Shared optional Installation Date support for the three review apps."""

from __future__ import annotations

from datetime import date, datetime
import re

DISPLAY_FORMAT = "%d/%m/%Y"
STORAGE_FORMAT = "%Y-%m-%d"


class InstallationDateError(ValueError):
    pass


def parse_installation_date(value: object, *, today: date | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", raw):
        raise InstallationDateError("Installation Date must use DD/MM/YYYY and be a valid date.")
    try:
        parsed = datetime.strptime(raw, DISPLAY_FORMAT).date()
    except ValueError as exc:
        raise InstallationDateError("Installation Date must use DD/MM/YYYY and be a valid date.") from exc
    if parsed > (today or date.today()):
        raise InstallationDateError("Installation Date cannot be later than today.")
    return parsed.strftime(STORAGE_FORMAT)


def format_installation_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, STORAGE_FORMAT).strftime(DISPLAY_FORMAT)
    except ValueError:
        return ""


def get_installation_date(qrdb, db_path: str, qr_code: object) -> str:
    qr = str(qr_code or "").strip()
    if not qr:
        return ""
    with qrdb.get_connection(sqlite_path=db_path, timeout=10) as conn:
        columns = set(qrdb.table_columns(conn, "QR_codes"))
        if not {"QR_code_ID", "installation_date"}.issubset(columns):
            return ""
        row = conn.execute(
            'SELECT installation_date FROM "QR_codes" WHERE "QR_code_ID" = ?', (qr,)
        ).fetchone()
    return format_installation_date(row[0] if row else "")


def update_installation_date(qrdb, db_path: str, qr_code: object, iso_value: str, *, modified_by: str, app_name: str, audit_log_change=None) -> bool:
    qr = str(qr_code or "").strip()
    if not qr:
        raise LookupError("Cannot save Installation Date without a QR code.")
    with qrdb.get_connection(sqlite_path=db_path, timeout=15) as conn:
        columns = set(qrdb.table_columns(conn, "QR_codes"))
        if not {"QR_code_ID", "installation_date"}.issubset(columns):
            raise RuntimeError("QR_codes.installation_date is unavailable.")
        row = conn.execute(
            'SELECT installation_date FROM "QR_codes" WHERE "QR_code_ID" = ?', (qr,)
        ).fetchone()
        if row is None:
            raise LookupError(f"QR code {qr} was not found in QR_codes.")
        old_value = str(row[0] or "").strip()
        if old_value == iso_value:
            return False
        conn.execute(
            'UPDATE "QR_codes" SET installation_date = ? WHERE "QR_code_ID" = ?',
            (iso_value, qr),
        )
        if audit_log_change:
            audit_log_change(
                conn, qr_code=qr, app_name=app_name, table_name="QR_codes",
                record_pk=qr, op_type="UPDATE",
                field_changes={"installation_date": (old_value, iso_value)},
                modified_by=modified_by, source="human",
                description="Review Installation Date update",
            )
        conn.commit()
        return True
