"""Install SQLite guardrails for SDI package lifecycle integrity.

The migration is intentionally limited to expression indexes and triggers.
It does not rebuild package tables or add foreign keys. Historical archived
rows with print_out != 1 are reported as warnings but are not rewritten.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db"
BACKUP_LABEL = "sdi_package_db_guardrails"
PACKAGE_TABLES = ("sdi_print_out", "sdi_print_out_arch")
SOURCE_TABLES = ("sdi_dataset", "sdi_dataset_EL")

REQUIRED_INDEXES = (
    "idx_sdi_print_out_qr_norm_unique",
    "idx_sdi_print_out_arch_qr_norm_unique",
)

REQUIRED_TRIGGERS = (
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


def norm(value: object) -> str:
    return "" if value is None else str(value).strip()


def qmarks(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _add(findings: list[dict], kind: str, detail: str, *, severity: str = "blocking", **fields: object) -> None:
    findings.append({
        "kind": kind,
        "severity": severity,
        "detail": detail,
        **fields,
    })


def _collect_package_qrs(conn: sqlite3.Connection) -> set[str]:
    qrs: set[str] = set()
    for table_name in PACKAGE_TABLES:
        if not table_exists(conn, table_name):
            continue
        rows = conn.execute(f'''
            SELECT DISTINCT UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr
            FROM "{table_name}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
        ''').fetchall()
        qrs.update(row["qr"] for row in rows if row["qr"])
    return qrs


def _audit_package_tables(conn: sqlite3.Connection, findings: list[dict]) -> None:
    for table_name in PACKAGE_TABLES:
        if not table_exists(conn, table_name):
            _add(findings, "missing_package_table", f"Required package table {table_name} is missing.")
            continue

        duplicate_rows = conn.execute(f'''
            SELECT
                UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr,
                COUNT(*) AS count,
                GROUP_CONCAT(TRIM(COALESCE(CAST("id_print_out" AS TEXT), ''))) AS package_ids
            FROM "{table_name}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
            GROUP BY UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), '')))
            HAVING COUNT(*) > 1
        ''').fetchall()
        for row in duplicate_rows:
            _add(
                findings,
                "duplicate_package_qr",
                f"QR appears {row['count']} times in {table_name}.",
                table=table_name,
                qr=row["qr"],
                package_ids=row["package_ids"],
            )

        blank_qr_rows = conn.execute(f'''
            SELECT rowid AS rowid, TRIM(COALESCE(CAST("id_print_out" AS TEXT), '')) AS package_id
            FROM "{table_name}"
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) = ''
        ''').fetchall()
        for row in blank_qr_rows:
            _add(
                findings,
                "blank_package_qr",
                f"Package row in {table_name} has a blank QR Code.",
                table=table_name,
                rowid=row["rowid"],
                package_id=row["package_id"],
            )

        blank_id_rows = conn.execute(f'''
            SELECT rowid AS rowid, TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) AS qr
            FROM "{table_name}"
            WHERE TRIM(COALESCE(CAST("id_print_out" AS TEXT), '')) = ''
        ''').fetchall()
        for row in blank_id_rows:
            _add(
                findings,
                "blank_package_id",
                f"Package row in {table_name} has a blank id_print_out.",
                table=table_name,
                rowid=row["rowid"],
                qr=row["qr"],
            )

    if all(table_exists(conn, table_name) for table_name in PACKAGE_TABLES):
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
            _add(findings, "active_archive_overlap", "QR exists in both active and archive package tables.", qr=row["qr"])

    if table_exists(conn, "sdi_print_out_arch"):
        unexported_rows = conn.execute('''
            SELECT
                rowid AS rowid,
                TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) AS qr,
                TRIM(COALESCE(CAST("id_print_out" AS TEXT), '')) AS package_id,
                TRIM(COALESCE(CAST("print_out" AS TEXT), '')) AS print_out
            FROM sdi_print_out_arch
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
              AND TRIM(COALESCE(CAST("print_out" AS TEXT), '')) <> '1'
        ''').fetchall()
        for row in unexported_rows:
            _add(
                findings,
                "historical_archived_unexported_row",
                "Historical archived package row has print_out not equal to 1; migration will grandfather it.",
                severity="warning",
                table="sdi_print_out_arch",
                rowid=row["rowid"],
                qr=row["qr"],
                package_id=row["package_id"],
                print_out=row["print_out"],
            )


def _audit_qr_code_parent(conn: sqlite3.Connection, qrs: set[str], findings: list[dict]) -> None:
    if not qrs:
        return
    if not table_exists(conn, "QR_codes"):
        _add(findings, "missing_qr_codes_table", "QR_codes table is missing.")
        return

    qrs_list = sorted(qrs)
    found: set[str] = set()
    for idx in range(0, len(qrs_list), 500):
        part = qrs_list[idx:idx + 500]
        rows = conn.execute(f'''
            SELECT DISTINCT UPPER(TRIM(COALESCE(CAST("QR_code_ID" AS TEXT), ''))) AS qr
            FROM QR_codes
            WHERE UPPER(TRIM(COALESCE(CAST("QR_code_ID" AS TEXT), ''))) IN ({qmarks(part)})
        ''', part).fetchall()
        found.update(row["qr"] for row in rows if row["qr"])

    for qr in sorted(qrs - found):
        _add(findings, "packaged_qr_missing_qr_codes_parent", "Packaged QR has no QR_codes parent row.", qr=qr)


def _audit_source_approval(conn: sqlite3.Connection, qrs: set[str], findings: list[dict]) -> None:
    if not qrs:
        return
    for table_name in SOURCE_TABLES:
        if not table_exists(conn, table_name):
            _add(findings, "missing_source_table", f"Source table {table_name} is missing.")

    qrs_list = sorted(qrs)
    approved_qrs: set[str] = set()
    for table_name in SOURCE_TABLES:
        if not table_exists(conn, table_name):
            continue
        for idx in range(0, len(qrs_list), 500):
            part = qrs_list[idx:idx + 500]
            rows = conn.execute(f'''
                SELECT
                    rowid AS rowid,
                    UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) AS qr,
                    TRIM(COALESCE(CAST("Approved" AS TEXT), '')) AS approved
                FROM "{table_name}"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) IN ({qmarks(part)})
            ''', part).fetchall()
            for row in rows:
                if row["approved"] == "1":
                    approved_qrs.add(row["qr"])
                else:
                    _add(
                        findings,
                        "packaged_source_not_approved",
                        f"Packaged QR has a source row in {table_name} where Approved is not 1.",
                        table=table_name,
                        rowid=row["rowid"],
                        qr=row["qr"],
                        approved=row["approved"],
                    )

    for qr in sorted(qrs - approved_qrs):
        _add(
            findings,
            "packaged_qr_without_approved_source",
            "Packaged QR has no approved source row in sdi_dataset or sdi_dataset_EL.",
            qr=qr,
        )


def preflight(conn: sqlite3.Connection) -> dict:
    findings: list[dict] = []
    _audit_package_tables(conn, findings)
    qrs = _collect_package_qrs(conn)
    _audit_qr_code_parent(conn, qrs, findings)
    _audit_source_approval(conn, qrs, findings)
    by_kind: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_kind[finding["kind"]] = by_kind.get(finding["kind"], 0) + 1
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
    return {
        "package_qr_count": len(qrs),
        "finding_count": len(findings),
        "findings_by_kind": by_kind,
        "findings_by_severity": by_severity,
        "findings": findings,
    }


def _create_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}.bak_{timestamp}_{BACKUP_LABEL}{db_path.suffix}")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)
    return backup_path


def _report_path(db_path: Path, mode: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return db_path.with_name(f"{BACKUP_LABEL}_{mode}_report_{timestamp}.json")


def _package_insert_trigger(table_name: str, other_table: str, trigger_name: str, *, require_exported: bool) -> str:
    exported_guard = ""
    if require_exported:
        exported_guard = '''
            SELECT RAISE(ABORT, 'SDI guardrail: archived package rows must have print_out = 1')
            WHERE TRIM(COALESCE(CAST(NEW."print_out" AS TEXT), '')) <> '1';
        '''

    return f'''
        CREATE TRIGGER "{trigger_name}"
        BEFORE INSERT ON "{table_name}"
        FOR EACH ROW
        BEGIN
            SELECT RAISE(ABORT, 'SDI guardrail: QR Code is required')
            WHERE TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')) = '';

            SELECT RAISE(ABORT, 'SDI guardrail: id_print_out is required')
            WHERE TRIM(COALESCE(CAST(NEW."id_print_out" AS TEXT), '')) = '';

            {exported_guard}

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR must exist in QR_codes')
            WHERE NOT EXISTS (
                SELECT 1
                FROM "QR_codes"
                WHERE UPPER(TRIM(COALESCE(CAST("QR_code_ID" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
            );

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR already exists in the other package table')
            WHERE EXISTS (
                SELECT 1
                FROM "{other_table}"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
            );

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR source approval is required')
            WHERE NOT EXISTS (
                SELECT 1
                FROM "sdi_dataset"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) = '1'
            )
            AND NOT EXISTS (
                SELECT 1
                FROM "sdi_dataset_EL"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) = '1'
            );

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR has unapproved source state')
            WHERE EXISTS (
                SELECT 1
                FROM "sdi_dataset"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) <> '1'
            )
            OR EXISTS (
                SELECT 1
                FROM "sdi_dataset_EL"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) <> '1'
            );
        END;
    '''


def _package_update_trigger(table_name: str, other_table: str, trigger_name: str, *, require_exported: bool) -> str:
    exported_guard = ""
    if require_exported:
        exported_guard = '''
            SELECT RAISE(ABORT, 'SDI guardrail: archived package rows must have print_out = 1')
            WHERE TRIM(COALESCE(CAST(NEW."print_out" AS TEXT), '')) <> '1';
        '''

    return f'''
        CREATE TRIGGER "{trigger_name}"
        BEFORE UPDATE OF "QR Code", "id_print_out" ON "{table_name}"
        FOR EACH ROW
        BEGIN
            SELECT RAISE(ABORT, 'SDI guardrail: QR Code is required')
            WHERE TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')) = '';

            SELECT RAISE(ABORT, 'SDI guardrail: id_print_out is required')
            WHERE TRIM(COALESCE(CAST(NEW."id_print_out" AS TEXT), '')) = '';

            {exported_guard}

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR must exist in QR_codes')
            WHERE NOT EXISTS (
                SELECT 1
                FROM "QR_codes"
                WHERE UPPER(TRIM(COALESCE(CAST("QR_code_ID" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
            );

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR already exists in the other package table')
            WHERE EXISTS (
                SELECT 1
                FROM "{other_table}"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
            );

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR source approval is required')
            WHERE NOT EXISTS (
                SELECT 1
                FROM "sdi_dataset"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) = '1'
            )
            AND NOT EXISTS (
                SELECT 1
                FROM "sdi_dataset_EL"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) = '1'
            );

            SELECT RAISE(ABORT, 'SDI guardrail: packaged QR has unapproved source state')
            WHERE EXISTS (
                SELECT 1
                FROM "sdi_dataset"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) <> '1'
            )
            OR EXISTS (
                SELECT 1
                FROM "sdi_dataset_EL"
                WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                      UPPER(TRIM(COALESCE(CAST(NEW."QR Code" AS TEXT), '')))
                  AND TRIM(COALESCE(CAST("Approved" AS TEXT), '')) <> '1'
            );
        END;
    '''


def guardrail_ddl() -> list[tuple[str, str, str]]:
    return [
        ("index", REQUIRED_INDEXES[0], '''
            CREATE UNIQUE INDEX "idx_sdi_print_out_qr_norm_unique"
            ON "sdi_print_out" (UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))))
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
        '''),
        ("index", REQUIRED_INDEXES[1], '''
            CREATE UNIQUE INDEX "idx_sdi_print_out_arch_qr_norm_unique"
            ON "sdi_print_out_arch" (UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))))
            WHERE TRIM(COALESCE(CAST("QR Code" AS TEXT), '')) <> ''
        '''),
        (
            "trigger",
            "trg_sdi_print_out_guard_insert",
            _package_insert_trigger(
                "sdi_print_out",
                "sdi_print_out_arch",
                "trg_sdi_print_out_guard_insert",
                require_exported=False,
            ),
        ),
        (
            "trigger",
            "trg_sdi_print_out_guard_update_qr_id",
            _package_update_trigger(
                "sdi_print_out",
                "sdi_print_out_arch",
                "trg_sdi_print_out_guard_update_qr_id",
                require_exported=False,
            ),
        ),
        (
            "trigger",
            "trg_sdi_print_out_arch_guard_insert",
            _package_insert_trigger(
                "sdi_print_out_arch",
                "sdi_print_out",
                "trg_sdi_print_out_arch_guard_insert",
                require_exported=True,
            ),
        ),
        (
            "trigger",
            "trg_sdi_print_out_arch_guard_update_qr_id",
            _package_update_trigger(
                "sdi_print_out_arch",
                "sdi_print_out",
                "trg_sdi_print_out_arch_guard_update_qr_id",
                require_exported=True,
            ),
        ),
        ("trigger", "trg_sdi_print_out_arch_guard_update_print_out", '''
            CREATE TRIGGER "trg_sdi_print_out_arch_guard_update_print_out"
            BEFORE UPDATE OF "print_out" ON "sdi_print_out_arch"
            FOR EACH ROW
            WHEN TRIM(COALESCE(CAST(NEW."print_out" AS TEXT), '')) <> '1'
            BEGIN
                SELECT RAISE(ABORT, 'SDI guardrail: archived package rows must have print_out = 1');
            END;
        '''),
        ("trigger", "trg_qr_codes_block_packaged_unapprove", '''
            CREATE TRIGGER "trg_qr_codes_block_packaged_unapprove"
            BEFORE UPDATE OF "Approved" ON "QR_codes"
            FOR EACH ROW
            WHEN TRIM(COALESCE(CAST(NEW."Approved" AS TEXT), '')) <> '1'
             AND (
                EXISTS (
                    SELECT 1 FROM "sdi_print_out"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')))
                )
                OR EXISTS (
                    SELECT 1 FROM "sdi_print_out_arch"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')))
                )
             )
            BEGIN
                SELECT RAISE(ABORT, 'SDI guardrail: packaged QR_codes rows must remain approved');
            END;
        '''),
        ("trigger", "trg_qr_codes_block_packaged_delete", '''
            CREATE TRIGGER "trg_qr_codes_block_packaged_delete"
            BEFORE DELETE ON "QR_codes"
            FOR EACH ROW
            WHEN EXISTS (
                    SELECT 1 FROM "sdi_print_out"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')))
                 )
              OR EXISTS (
                    SELECT 1 FROM "sdi_print_out_arch"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR_code_ID" AS TEXT), '')))
                 )
            BEGIN
                SELECT RAISE(ABORT, 'SDI guardrail: cannot delete QR_codes row while QR is packaged');
            END;
        '''),
        ("trigger", "trg_sdi_dataset_block_packaged_unapprove", '''
            CREATE TRIGGER "trg_sdi_dataset_block_packaged_unapprove"
            BEFORE UPDATE OF "Approved" ON "sdi_dataset"
            FOR EACH ROW
            WHEN TRIM(COALESCE(CAST(NEW."Approved" AS TEXT), '')) <> '1'
             AND (
                EXISTS (
                    SELECT 1 FROM "sdi_print_out"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR Code" AS TEXT), '')))
                )
                OR EXISTS (
                    SELECT 1 FROM "sdi_print_out_arch"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR Code" AS TEXT), '')))
                )
             )
            BEGIN
                SELECT RAISE(ABORT, 'SDI guardrail: packaged sdi_dataset rows must remain approved');
            END;
        '''),
        ("trigger", "trg_sdi_dataset_el_block_packaged_unapprove", '''
            CREATE TRIGGER "trg_sdi_dataset_el_block_packaged_unapprove"
            BEFORE UPDATE OF "Approved" ON "sdi_dataset_EL"
            FOR EACH ROW
            WHEN TRIM(COALESCE(CAST(NEW."Approved" AS TEXT), '')) <> '1'
             AND (
                EXISTS (
                    SELECT 1 FROM "sdi_print_out"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR Code" AS TEXT), '')))
                )
                OR EXISTS (
                    SELECT 1 FROM "sdi_print_out_arch"
                    WHERE UPPER(TRIM(COALESCE(CAST("QR Code" AS TEXT), ''))) =
                          UPPER(TRIM(COALESCE(CAST(OLD."QR Code" AS TEXT), '')))
                )
             )
            BEGIN
                SELECT RAISE(ABORT, 'SDI guardrail: packaged sdi_dataset_EL rows must remain approved');
            END;
        '''),
    ]


def apply_guardrails(conn: sqlite3.Connection) -> list[dict]:
    applied: list[dict] = []
    ddl_items = guardrail_ddl()
    for object_type, name, _ddl in ddl_items:
        if object_type == "trigger":
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        elif object_type == "index":
            conn.execute(f'DROP INDEX IF EXISTS "{name}"')

    for object_type, name, ddl in ddl_items:
        conn.execute(ddl)
        applied.append({"type": object_type, "name": name})
    return applied


def _write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _print_summary(report: dict) -> None:
    print("SDI package DB guardrails migration")
    print(f"Mode      : {report['mode']}")
    print(f"DB        : {report['db_path']}")
    print(f"Report    : {report['report_path']}")
    if report.get("backup_path"):
        print(f"Backup    : {report['backup_path']}")
    preflight_report = report["preflight"]
    print(f"Package QRs: {preflight_report['package_qr_count']}")
    print(f"Findings   : {preflight_report['finding_count']}")
    if preflight_report["findings_by_severity"]:
        print("Severity   : " + ", ".join(
            f"{severity}={count}"
            for severity, count in sorted(preflight_report["findings_by_severity"].items())
        ))
    if preflight_report["findings_by_kind"]:
        print("Findings by kind:")
        for kind, count in sorted(preflight_report["findings_by_kind"].items()):
            print(f"  {kind}: {count}")
    if report["mode"] == "apply" and report.get("applied_objects"):
        print("Applied objects:")
        for item in report["applied_objects"]:
            print(f"  {item['type']}: {item['name']}")


def run(db_path: Path, apply: bool) -> int:
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    mode = "apply" if apply else "dry_run"
    report_path = _report_path(db_path, mode)
    backup_path: Path | None = None
    applied_objects: list[dict] = []

    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        preflight_report = preflight(conn)

    blocking_findings = [
        finding for finding in preflight_report["findings"]
        if finding.get("severity") == "blocking"
    ]

    if apply:
        if blocking_findings:
            report = {
                "mode": mode,
                "db_path": str(db_path),
                "backup_path": None,
                "report_path": str(report_path),
                "applied_objects": [],
                "preflight": preflight_report,
                "status": "blocked",
            }
            _write_report(report_path, report)
            _print_summary(report)
            print("Apply blocked because preflight found blocking findings.", file=sys.stderr)
            return 1

        backup_path = _create_backup(db_path)
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            applied_objects = apply_guardrails(conn)
            conn.commit()

    report = {
        "mode": mode,
        "db_path": str(db_path),
        "backup_path": str(backup_path) if backup_path else None,
        "report_path": str(report_path),
        "applied_objects": applied_objects,
        "preflight": preflight_report,
        "status": "applied" if apply else "dry_run",
    }
    _write_report(report_path, report)
    _print_summary(report)
    if not apply:
        print("No database changes were applied. Re-run with --apply to install guardrails.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install SQLite guardrails for SDI package tables.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to QR_codes.db")
    parser.add_argument("--dry-run", action="store_true", help="Run checks only; this is the default.")
    parser.add_argument("--apply", action="store_true", help="Create a backup and install guardrail indexes/triggers.")
    args = parser.parse_args()

    if args.dry_run and args.apply:
        print("Use either --dry-run or --apply, not both.", file=sys.stderr)
        return 2

    return run(args.db.resolve(), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
