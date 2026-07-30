"""Read-only audit for SDI package/archive integrity.

Checks the active and archived SDI package tables without mutating the DB.
Use --strict in production monitors to return non-zero on blocking findings.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as qrdb


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db"
DEFAULT_JSON_DIR = ROOT / "Output_jason_api"
PACKAGE_TABLES = ("sdi_print_out", "sdi_print_out_arch")
SOURCE_TABLES = ("sdi_dataset", "sdi_dataset_EL")
BLOCKING_SEVERITIES = {"blocking"}
REQUIRED_GUARDRAIL_INDEXES = (
    "idx_sdi_print_out_qr_norm_unique",
    "idx_sdi_print_out_arch_qr_norm_unique",
)
REQUIRED_GUARDRAIL_TRIGGERS = (
    "trg_sdi_print_out_guard_insert",
    "trg_sdi_print_out_guard_update_qr_id",
    "trg_sdi_print_out_arch_guard_insert",
    "trg_sdi_print_out_arch_guard_update_qr_id",
    "trg_sdi_print_out_arch_guard_update_print_out",
    "trg_qr_codes_block_packaged_unapprove",
    "trg_qr_codes_block_packaged_delete",
    "trg_sdi_dataset_block_packaged_unapprove",
    "trg_sdi_dataset_el_block_packaged_unapprove",
)
# PostgreSQL equivalents (guardrails ported to PL/pgSQL + FK RESTRICT in C3/C4).
# The package delete-block is enforced by the FK RESTRICT (fk_spo_qr / fk_spoa_qr),
# not a trigger; the per-statement SQLite guard triggers collapse to one each on PG.
PG_REQUIRED_GUARDRAIL_INDEXES = (
    "ux_sdi_print_out_qr",
    "ux_sdi_print_out_arch_qr",
)
PG_REQUIRED_GUARDRAIL_TRIGGERS = (
    "trg_sdi_print_out_guard",
    "trg_sdi_print_out_arch_guard",
    "trg_qr_codes_block_packaged_unapprove",
    "trg_sdi_dataset_block_packaged_unapprove",
    "trg_sdi_dataset_el_block_packaged_unapprove",
)


def connect_ro(db_path: Path):
    if qrdb.is_postgres():
        conn = qrdb.get_connection()
        conn.row_factory = sqlite3.Row  # wrapper translates to RealDictCursor
        return conn
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, table_name: str) -> bool:
    return qrdb.has_table(conn, table_name)


def schema_object_exists(conn, object_type: str, object_name: str) -> bool:
    """Whether a named index/trigger exists — sqlite_master on SQLite, catalogs on PG."""
    if qrdb.is_postgres():
        if object_type == "index":
            sql = "SELECT 1 FROM pg_indexes WHERE indexname=?"
        elif object_type == "trigger":
            sql = "SELECT 1 FROM pg_trigger WHERE tgname=? AND NOT tgisinternal"
        else:
            return False
        return conn.execute(sql, (object_name,)).fetchone() is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
        (object_type, object_name),
    ).fetchone()
    return row is not None


def qmarks(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def norm(value: object) -> str:
    return "" if value is None else str(value).strip()


def norm_qr(value: object) -> str:
    return norm(value).upper()


def add(findings: list[dict], kind: str, detail: str, *, severity: str = "blocking", **fields: object) -> None:
    findings.append({
        "kind": kind,
        "severity": severity,
        "detail": detail,
        **fields,
    })


def package_qrs(conn: sqlite3.Connection) -> set[str]:
    qrs: set[str] = set()
    for table in PACKAGE_TABLES:
        if not table_exists(conn, table):
            continue
        rows = conn.execute(f'''
            SELECT DISTINCT UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr
            FROM "{table}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
        ''').fetchall()
        qrs.update(row["qr"] for row in rows if row["qr"])
    return qrs


def audit_package_tables(conn, findings: list[dict]) -> None:
    rowid_expr = "ctid" if qrdb.is_postgres() else "rowid"
    concat_ids = (
        "string_agg(TRIM(COALESCE(CAST(\"id_print_out\" AS TEXT), '')), ',')"
        if qrdb.is_postgres()
        else "GROUP_CONCAT(TRIM(COALESCE(CAST(\"id_print_out\" AS TEXT), '')))"
    )
    if all(table_exists(conn, table) for table in PACKAGE_TABLES):
        rows = conn.execute('''
            SELECT qr FROM (
                SELECT UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr
                FROM sdi_print_out
                WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
                INTERSECT
                SELECT UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr
                FROM sdi_print_out_arch
                WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
            )
        ''').fetchall()
        for row in rows:
            add(findings, "active_archive_overlap", "QR exists in both active and archive package tables.", qr=row["qr"])

    for table in PACKAGE_TABLES:
        if not table_exists(conn, table):
            continue
        duplicate_rows = conn.execute(f'''
            SELECT
                UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr,
                COUNT(*) AS count,
                {concat_ids} AS package_ids
            FROM "{table}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
            GROUP BY UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), '')))
            HAVING COUNT(*) > 1
        ''').fetchall()
        for row in duplicate_rows:
            add(
                findings,
                "duplicate_package_qr",
                f"QR appears {row['count']} times in {table}.",
                table=table,
                qr=row["qr"],
                package_ids=row["package_ids"],
            )

        blank_id_rows = conn.execute(f'''
            SELECT
                {rowid_expr} AS rowid,
                TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) AS qr
            FROM "{table}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
              AND TRIM(COALESCE(CAST("id_print_out" AS TEXT), '')) = ''
        ''').fetchall()
        for row in blank_id_rows:
            add(
                findings,
                "blank_package_id",
                f"Package row in {table} has a blank id_print_out.",
                table=table,
                rowid=row["rowid"],
                qr=row["qr"],
            )

        blank_qr_rows = conn.execute(f'''
            SELECT
                {rowid_expr} AS rowid,
                TRIM(COALESCE(CAST("id_print_out" AS TEXT), '')) AS package_id
            FROM "{table}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) = ''
        ''').fetchall()
        for row in blank_qr_rows:
            add(
                findings,
                "blank_package_qr",
                f"Package row in {table} has a blank QR Code.",
                table=table,
                rowid=row["rowid"],
                package_id=row["package_id"],
            )

    if table_exists(conn, "sdi_print_out_arch"):
        unexported_rows = conn.execute(f'''
            SELECT
                {rowid_expr} AS rowid,
                TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) AS qr,
                TRIM(COALESCE(CAST("id_print_out" AS TEXT), '')) AS package_id,
                TRIM(COALESCE(CAST("print_out" AS TEXT), '')) AS print_out
            FROM sdi_print_out_arch
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
              AND TRIM(COALESCE(CAST("print_out" AS TEXT), '')) <> '1'
        ''').fetchall()
        for row in unexported_rows:
            add(
                findings,
                "archived_unexported_row",
                "Historical archived package row has print_out not equal to 1.",
                severity="warning",
                table="sdi_print_out_arch",
                rowid=row["rowid"],
                qr=row["qr"],
                package_id=row["package_id"],
                print_out=row["print_out"],
            )


def audit_qr_codes_parent(conn: sqlite3.Connection, qrs: set[str], findings: list[dict]) -> None:
    if not qrs:
        return
    if not table_exists(conn, "QR_codes"):
        add(findings, "missing_qr_codes_table", "QR_codes table is missing.")
        return

    qrs_list = sorted(qrs)
    found: set[str] = set()
    for idx in range(0, len(qrs_list), 500):
        part = qrs_list[idx:idx + 500]
        rows = conn.execute(f'''
            SELECT DISTINCT UPPER(TRIM(COALESCE(CAST("QR_code_ID" AS TEXT), ''))) AS qr
            FROM "QR_codes"
            WHERE UPPER(TRIM(COALESCE(CAST("QR_code_ID" AS TEXT), ''))) IN ({qmarks(part)})
        ''', part).fetchall()
        found.update(row["qr"] for row in rows if row["qr"])

    for qr in sorted(qrs - found):
        add(findings, "packaged_qr_missing_qr_codes_parent", "Packaged QR has no QR_codes parent row.", qr=qr)


def audit_source_approval(conn, qrs: set[str], findings: list[dict]) -> None:
    if not qrs:
        return

    rowid_expr = "ctid" if qrdb.is_postgres() else "rowid"
    approved_qrs: set[str] = set()
    qrs_list = sorted(qrs)
    for table in SOURCE_TABLES:
        if not table_exists(conn, table):
            continue
        for idx in range(0, len(qrs_list), 500):
            part = qrs_list[idx:idx + 500]
            rows = conn.execute(f'''
                SELECT
                    {rowid_expr} AS rowid,
                    UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr,
                    TRIM(COALESCE(CAST("Approved" AS TEXT), '')) AS approved
                FROM "{table}"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) IN ({qmarks(part)})
            ''', part).fetchall()
            for row in rows:
                if row["approved"] == "1":
                    approved_qrs.add(row["qr"])
                else:
                    add(
                        findings,
                        "packaged_source_not_approved",
                        f"Packaged QR has a source row in {table} where Approved is not 1.",
                        table=table,
                        rowid=row["rowid"],
                        qr=row["qr"],
                        approved=row["approved"],
                    )

    for qr in sorted(qrs - approved_qrs):
        add(
            findings,
            "packaged_qr_without_approved_source",
            "Packaged QR has no approved source row in sdi_dataset or sdi_dataset_EL.",
                qr=qr,
            )


def audit_guardrail_objects(conn, findings: list[dict]) -> None:
    if qrdb.is_postgres():
        indexes = PG_REQUIRED_GUARDRAIL_INDEXES
        triggers = PG_REQUIRED_GUARDRAIL_TRIGGERS
    else:
        indexes = REQUIRED_GUARDRAIL_INDEXES
        triggers = REQUIRED_GUARDRAIL_TRIGGERS
    for index_name in indexes:
        if not schema_object_exists(conn, "index", index_name):
            add(
                findings,
                "guardrail_index_missing",
                f"Required SDI package guardrail index is missing: {index_name}.",
                object_name=index_name,
            )
    for trigger_name in triggers:
        if not schema_object_exists(conn, "trigger", trigger_name):
            add(
                findings,
                "guardrail_trigger_missing",
                f"Required SDI package guardrail trigger is missing: {trigger_name}.",
                object_name=trigger_name,
            )


def audit_review_json(json_dir: Path, qrs: set[str], findings: list[dict]) -> None:
    if not qrs or not json_dir.is_dir():
        return

    for path in sorted(json_dir.glob("*.json")):
        qr = norm_qr(path.name.split("_", 1)[0])
        if qr not in qrs:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            add(
                findings,
                "packaged_json_unreadable",
                f"Could not read packaged review JSON: {exc}",
                qr=qr,
                file=path.name,
            )
            continue
        structured = payload.get("structured_data")
        if not isinstance(structured, dict):
            add(
                findings,
                "packaged_json_missing_structured_data",
                "Packaged review JSON has no structured_data object.",
                severity="warning",
                qr=qr,
                file=path.name,
            )
            continue
        approved = str(structured.get("Approved", "") or "").strip()
        if approved != "True":
            add(
                findings,
                "packaged_json_not_approved",
                'Packaged review JSON structured_data.Approved is not "True".',
                qr=qr,
                file=path.name,
                approved=approved,
            )


def run_audit(db_path: Path, json_dir: Path) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    findings: list[dict] = []
    conn = connect_ro(db_path)
    try:
        audit_package_tables(conn, findings)
        qrs = package_qrs(conn)
        audit_qr_codes_parent(conn, qrs, findings)
        audit_source_approval(conn, qrs, findings)
        audit_guardrail_objects(conn, findings)
        audit_review_json(json_dir, qrs, findings)
    finally:
        conn.close()

    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_kind[finding["kind"]] = by_kind.get(finding["kind"], 0) + 1
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    return {
        "db_path": str(db_path),
        "json_dir": str(json_dir),
        "package_qr_count": len(qrs),
        "finding_count": len(findings),
        "findings_by_severity": by_severity,
        "findings_by_kind": by_kind,
        "findings": findings,
    }


def print_human(report: dict, sample: int) -> None:
    print(f"SDI package integrity audit")
    print(f"DB       : {report['db_path']}")
    print(f"JSON dir : {report['json_dir']}")
    print(f"Package QRs: {report['package_qr_count']}")
    print(f"Findings   : {report['finding_count']}")
    if report["findings_by_severity"]:
        print("Severity   : " + ", ".join(
            f"{severity}={count}" for severity, count in sorted(report["findings_by_severity"].items())
        ))
    if report["findings_by_kind"]:
        print("\nFindings by kind:")
        for kind, count in sorted(report["findings_by_kind"].items()):
            print(f"  {kind}: {count}")
    if report["findings"]:
        print(f"\nSample findings (first {sample}):")
        for finding in report["findings"][:sample]:
            print("  - " + json.dumps(finding, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit SDI active/archive package integrity.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to QR_codes.db")
    parser.add_argument("--json-dir", default=str(DEFAULT_JSON_DIR), help="Path to Output_jason_api")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print JSON report")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when blocking findings exist")
    parser.add_argument("--sample", type=int, default=20, help="Number of sample findings for human output")
    args = parser.parse_args()

    try:
        report = run_audit(Path(args.db), Path(args.json_dir))
    except Exception as exc:
        print(f"[audit] ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report, args.sample)

    has_blocking = any(finding.get("severity") in BLOCKING_SEVERITIES for finding in report["findings"])
    return 1 if args.strict and has_blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
