"""Reconcile captured QR codes against the curated SDI tables (read-only).

Every QR that has been captured and left in an active review state is expected
to own a curated row: QR_code_assets.Col_process 0 (New Assets) or 1 (Update
Existing) -> a row in sdi_dataset (ME/BF) or sdi_dataset_EL (EL). Manual Entry
(Col_process 2) is the one legitimate exception: it is a terminal SDI-exclusion
state, entered into Planon by hand.

Why this exists: on 2026-08-05, 22 ME QRs were captured, their JSONs marked
processed, and their curated rows never written -- the reviewer's JSON sync
swallowed a foreign-key IntegrityError. The only symptom was that the Dashboard
KPI section counted 1,540 QRs while the pipeline counted 1,577. This audit turns
that arithmetic into a named, greppable finding per QR.

It is the cron/forensics twin of Dashboard/charts/approval.integrity_snapshot(),
which surfaces the same reconciliation live on the Overview KPI chip. The set
math is duplicated on purpose -- scripts/ tools must not import Dashboard venv
modules -- and kept in lockstep by test/test_audit_capture_vs_curated.py plus
test/test_pipeline_kpi_equivalence.py.

A capture only gains its curated row when a reviewer app next serves a request
(the JSON sync runs from before_request), so a recent capture is legitimately
curated-less. Verified against production on 2026-08-06: every curated-less
active capture was under 6 hours old and resolved normally. Those are reported
as capture_pending [INFO], not DRIFT -- alarming on normal in-flight work is
how 8,259 findings came to be ignored in logs/sdi_audit.log.

Checks:
    capture_orphan          [DRIFT]     max Col_process 0/1, NO row in
                                        sdi_dataset / sdi_dataset_EL, and
                                        captured more than --grace-hours ago.
                                        The asset is stranded: invisible to
                                        review, SDI packaging and Planon export.
    capture_pending         [INFO]      same, but captured recently (or awaiting
                                        its first reviewer request) - expected to
                                        resolve on its own.
    curated_dual_discipline [DRIFT]     one QR curated under two disciplines
                                        (e.g. both ME and EL) -- ambiguous
                                        ownership, and double-counted by the
                                        Overview bars.
    curated_unanchored      [WORKLIST]  a curated row whose QR has no validly
                                        classified QR_code_assets row. Usually a
                                        purged/renamed capture; needs a
                                        case-by-case decision.
    unknown_discipline      [INFO]      third token of code_assets is not
                                        ME/EL/BF, so the KPI bars cannot place
                                        the QR. Data-entry / rename artifact.
    unclassified_process    [INFO]      QR whose only Col_process values are
                                        invalid or unparseable. Also counted on
                                        the pipeline chart's diagnostic line.

Exit code:
    0 - no DRIFT findings (WORKLIST/INFO may still be printed)
    1 - one or more DRIFT findings (or any finding when --strict)
    2 - setup error (missing DB / required table)

Usage:
    python3 scripts/audit_capture_vs_curated.py               # human report
    python3 scripts/audit_capture_vs_curated.py --qr 0000188207
    python3 scripts/audit_capture_vs_curated.py --building 353
    python3 scripts/audit_capture_vs_curated.py --strict      # INFO also -> exit 1

Cron (hourly). Deliberately NOT --quiet: a clean run must still write its
[AUDIT] OK + RUN_AT line, otherwise a stale log is indistinguishable from a
healthy one -- which is exactly how 8,259 findings went unread in
logs/sdi_audit.log before 2026-08-06.
    15 * * * *  /usr/bin/python3 /home/developer/scripts/audit_capture_vs_curated.py \
                    >> /home/developer/logs/capture_audit.log 2>&1
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from contextlib import closing
from typing import Optional

# Backend-agnostic DB layer (SQLite default; PostgreSQL when DB_BACKEND=postgres).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as qrdb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
DEFAULT_DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(REPO_ROOT, "asset_capture_app_dev", "data", "QR_codes.db")
)

# QR strings that must never participate in a join / SDI operation.
PLACEHOLDER_QRS = {"", "none", "nan", "null"}

CURATED_TABLES = ("sdi_dataset", "sdi_dataset_EL")
DISCIPLINES = ("ME", "EL", "BF")

SEVERITY = {
    "capture_orphan": "DRIFT",
    "curated_dual_discipline": "DRIFT",
    "curated_unanchored": "WORKLIST",
    "capture_pending": "INFO",
    "unknown_discipline": "INFO",
    "unclassified_process": "INFO",
}

# Hours a capture may go without a curated row before it counts as stranded.
# 96h clears a long weekend with no reviewer traffic; a genuinely lost asset
# stays lost indefinitely, so waiting costs nothing while a false alarm costs
# the guardrail its credibility. Keep aligned with
# Dashboard/charts/approval.ORPHAN_GRACE_HOURS.
DEFAULT_GRACE_HOURS = 96
# Only genuine cross-table corruption fails the run. WORKLIST (human decision)
# and INFO (data-quality) print but do not flip the exit code unless --strict.
EXIT_SEVERITIES = {"DRIFT"}

PROCESS_LABELS = {0: "New Assets", 1: "Update Existing", 2: "Manual Entry"}


class AuditSetupError(RuntimeError):
    """Missing database or required table — reported as exit code 2."""


def _norm(value) -> str:
    return "" if value is None else str(value).strip()


def _base_qr(value) -> str:
    """First whitespace-delimited token.

    code_assets is "<qr> <building> <type> - <seq>"; curated "QR Code" values
    are bare but are normalized the same way so both sides agree (and match
    Dashboard/charts/flow_quantity_chart._base_qr, which defines the pipeline
    "Total QR" the KPI section reconciles against).
    """
    text = _norm(value)
    if not text:
        return ""
    return text.split(None, 1)[0]


def _is_placeholder_qr(qr: str) -> bool:
    return qr.strip().lower() in PLACEHOLDER_QRS


def _scan_capture(conn) -> dict:
    """One pass over QR_code_assets -> per-QR process/building/discipline."""
    max_proc: dict = {}
    building: dict = {}
    discipline: dict = {}
    raw_type: dict = {}
    unclassified: set = set()

    for code_assets, proc in conn.execute(
        'SELECT "code_assets", "Col_process" FROM "QR_code_assets"'
    ).fetchall():
        parts = _norm(code_assets).split()
        if not parts:
            continue
        qr = parts[0]
        if _is_placeholder_qr(qr):
            continue
        try:
            p = int(_norm(proc))
        except (TypeError, ValueError):
            unclassified.add(qr)
            continue
        if p not in (0, 1, 2):
            unclassified.add(qr)
            continue

        prev = max_proc.get(qr)
        if prev is None or p > prev:
            max_proc[qr] = p
        if len(parts) > 1 and qr not in building:
            building[qr] = parts[1]
        if len(parts) > 2:
            t = parts[2].upper()
            if qr not in raw_type:
                raw_type[qr] = t
            if t in DISCIPLINES and qr not in discipline:
                discipline[qr] = t

    return {
        "max_proc": max_proc,
        "building": building,
        "discipline": discipline,
        "raw_type": raw_type,
        # A QR with at least one valid row is classified; drop it from the set.
        "unclassified": unclassified - set(max_proc),
    }


def _scan_curated(conn) -> dict:
    """{qr: {"types": {...}, "buildings": {...}}} across both curated tables.

    Discipline inference mirrors the KPI bars: sdi_dataset rows are BF when
    "Asset Group" contains 'backflow', otherwise ME; sdi_dataset_EL is EL.
    """
    curated: dict = {}
    for table in CURATED_TABLES:
        if not qrdb.has_table(conn, table):
            continue
        rows = conn.execute(f'SELECT "QR Code", "Asset Group", "Building" FROM "{table}"').fetchall()
        for qr_raw, group, bld in rows:
            qr = _base_qr(qr_raw)
            if not qr or _is_placeholder_qr(qr):
                continue
            if table == "sdi_dataset_EL":
                t = "EL"
            else:
                t = "BF" if "backflow" in _norm(group).lower() else "ME"
            entry = curated.setdefault(qr, {"types": set(), "buildings": set()})
            entry["types"].add(t)
            if _norm(bld):
                entry["buildings"].add(_norm(bld))
    return curated


def _capture_timestamps(conn) -> dict:
    """{qr: datetime} from QR_codes.date_set (TEXT under PostgreSQL).

    Missing/unparseable values are absent from the map; an unknown capture time
    is never treated as in-flight, since a capture with no QR_codes row is
    itself a defect.
    """
    out: dict = {}
    if not qrdb.has_table(conn, "QR_codes"):
        return out
    try:
        rows = conn.execute('SELECT "QR_code_ID", "date_set" FROM "QR_codes"').fetchall()
    except Exception:
        return out
    for qr_raw, raw in rows:
        qr = _base_qr(qr_raw)
        if not qr:
            continue
        if isinstance(raw, datetime.datetime):
            out[qr] = raw
            continue
        text = _norm(raw)
        if not text:
            continue
        try:
            out[qr] = datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                out[qr] = datetime.datetime.fromisoformat(text[:19])
            except ValueError:
                continue
    return out


def audit(
    db_path: str,
    only_qr: Optional[str] = None,
    only_building: Optional[str] = None,
    grace_hours: int = DEFAULT_GRACE_HOURS,
) -> tuple[int, list[dict]]:
    """Reconcile capture vs curated. Returns (scanned_qr_count, anomalies)."""
    if not qrdb.is_postgres() and not os.path.exists(db_path):
        raise AuditSetupError(f"database not found: {db_path}")

    # closing(), not the connection's own context manager: this audit is
    # read-only and must release the handle before returning.
    with closing(qrdb.get_connection(sqlite_path=db_path)) as conn:
        if not qrdb.has_table(conn, "QR_code_assets"):
            raise AuditSetupError('required table "QR_code_assets" is missing')
        if not any(qrdb.has_table(conn, t) for t in CURATED_TABLES):
            raise AuditSetupError(f"none of the curated tables {CURATED_TABLES} were found")
        capture = _scan_capture(conn)
        curated = _scan_curated(conn)
        captured_at = _capture_timestamps(conn)

    max_proc = capture["max_proc"]
    discipline = capture["discipline"]
    building = capture["building"]
    anomalies: list[dict] = []

    def _building_of(qr: str) -> str:
        if qr in building:
            return building[qr]
        blds = curated.get(qr, {}).get("buildings", set())
        return sorted(blds)[0] if blds else ""

    def add(kind: str, qr: str, detail: str, qtype: str = "") -> None:
        bld = _building_of(qr)
        if only_qr and qr != only_qr:
            return
        if only_building and bld != only_building:
            return
        anomalies.append({
            "kind": kind,
            "severity": SEVERITY[kind],
            "qr": qr,
            "building": bld,
            "type": qtype,
            "detail": detail,
        })

    # capture_orphan — the silent-loss class. Recent captures are still in
    # flight (the reviewer sync runs per request), so they report as INFO.
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=grace_hours)
    for qr, p in sorted(max_proc.items()):
        if p not in (0, 1) or qr in curated:
            continue
        t = discipline.get(qr, capture["raw_type"].get(qr, ""))
        seen = captured_at.get(qr)
        if seen is not None and seen > cutoff:
            add(
                "capture_pending", qr,
                f"Col_process={p} ({PROCESS_LABELS[p]}) with no curated row yet, "
                f"captured {seen:%Y-%m-%d %H:%M} (within the {grace_hours}h grace "
                "window) - expected to resolve on the next reviewer request",
                t,
            )
            continue
        age = f"captured {seen:%Y-%m-%d %H:%M}" if seen else "capture time unknown"
        add(
            "capture_orphan", qr,
            f"Col_process={p} ({PROCESS_LABELS[p]}) but no row in "
            f"{' / '.join(CURATED_TABLES)} - asset is stranded outside review and "
            f"SDI ({age}, past the {grace_hours}h grace window)",
            t,
        )

    # unknown_discipline — the KPI bars cannot place these.
    for qr in sorted(max_proc):
        if qr in discipline:
            continue
        raw = capture["raw_type"].get(qr, "")
        add(
            "unknown_discipline", qr,
            f"code_assets discipline token {raw!r} is not one of {DISCIPLINES}; "
            "the Overview bar chart cannot place this QR",
            raw,
        )

    # unclassified_process — invalid Col_process only.
    for qr in sorted(capture["unclassified"]):
        add(
            "unclassified_process", qr,
            "every QR_code_assets row carries an invalid/unparseable Col_process "
            "(expected 0, 1 or 2); excluded from the pipeline and KPI totals",
            capture["raw_type"].get(qr, ""),
        )

    # curated_unanchored / curated_dual_discipline
    for qr, entry in sorted(curated.items()):
        if qr not in max_proc:
            add(
                "curated_unanchored", qr,
                "curated row exists but no QR_code_assets row carries a valid "
                "Col_process - capture record purged, renamed or never registered",
                "/".join(sorted(entry["types"])),
            )
        if len(entry["types"]) > 1:
            add(
                "curated_dual_discipline", qr,
                "curated under multiple disciplines "
                f"({', '.join(sorted(entry['types']))}) - ambiguous ownership and "
                "double-counted by the Overview bar chart",
                "/".join(sorted(entry["types"])),
            )

    scanned = len(set(max_proc) | set(curated) | capture["unclassified"])
    return scanned, anomalies


def _print_report(scanned: int, anomalies: list[dict], quiet: bool) -> None:
    by_sev: dict[str, int] = {}
    for a in anomalies:
        by_sev[a["severity"]] = by_sev.get(a["severity"], 0) + 1

    if not anomalies:
        if not quiet:
            print(f"[AUDIT] OK - reconciled {scanned} QR code(s), no findings")
        return

    sev_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_sev.items()))
    print(f"[AUDIT] FINDINGS: {len(anomalies)} across {scanned} QR code(s) ({sev_summary})")
    for a in sorted(anomalies, key=lambda x: (x["severity"], x["kind"], x["building"], x["qr"])):
        print(f'  - [{a["severity"]}] {a["kind"]}  qr={a["qr"]}  building={a["building"]}  type={a["type"]}')
        print(f"      {a['detail']}")

    orphans = [a for a in anomalies if a["kind"] == "capture_orphan"]
    if orphans and not quiet:
        print(f"\n[AUDIT] WORKLIST - stranded captures ({len(orphans)}):")
        print(f'  {"building":<10} {"qr":<14} {"type":<6} recovery')
        for a in sorted(orphans, key=lambda x: (x["building"], x["qr"])):
            print(f'  {a["building"]:<10} {a["qr"]:<14} {a["type"]:<6} '
                  f'scripts/recover_sdi_row_from_json.py --qr {a["qr"]}')


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile captured QRs against the curated SDI tables (read-only)."
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--qr", default=None, help="drill down on a single QR code")
    parser.add_argument("--building", default=None, help="filter to one building code")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the OK line and worklist table (not recommended for cron)")
    parser.add_argument("--strict", action="store_true",
                        help="treat WORKLIST/INFO findings as failures too (exit 1)")
    parser.add_argument("--grace-hours", type=int, default=DEFAULT_GRACE_HOURS,
                        help="hours a capture may lack a curated row before it counts "
                             f"as stranded (default {DEFAULT_GRACE_HOURS})")
    args = parser.parse_args(argv)

    try:
        scanned, anomalies = audit(args.db_path, args.qr, args.building, args.grace_hours)
    except AuditSetupError as exc:
        print(f"[AUDIT] ERROR: {exc}", file=sys.stderr)
        return 2

    _print_report(scanned, anomalies, args.quiet)

    fail_set = set(SEVERITY.values()) if args.strict else EXIT_SEVERITIES
    failing = [a for a in anomalies if a["severity"] in fail_set]
    # The RUN_AT marker is printed on EVERY run so the log tail proves both
    # liveness and current state; the Dashboard log viewer parses this line.
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[AUDIT] RUN_AT={stamp} SCANNED={scanned} FINDINGS={len(anomalies)} FAILING={len(failing)}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
