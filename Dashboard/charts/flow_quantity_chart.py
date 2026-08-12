import sqlite3
from contextlib import closing
import db as qrdb  # backend-agnostic QR_codes DB layer


# --- CONFIGURATION ---
DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"

PROCESS_CARDS = (
    {"key": "new", "process": 0, "label": "New Assets", "tone": "blue"},
    {"key": "update", "process": 1, "label": "Update Existing", "tone": "amber"},
    {"key": "manual", "process": 2, "label": "Manual Entry", "tone": "green"},
)

FLOW_STEPS = (
    {
        "key": "queue",
        "label": "SDI Queue",
        "tone": "blue",
        "caption": "Curated records ready for SDI packaging",
    },
    {
        "key": "requested",
        "label": "Requested",
        "tone": "amber",
        "caption": "Assets currently staged for Planon request",
    },
    {
        "key": "planon",
        "label": "Into Planon",
        "tone": "green",
        "caption": "Archived packages already sent forward",
    },
)

QR_SELECT_SQL = {
    "sdi_dataset": 'SELECT "QR Code" FROM "sdi_dataset"',
    "sdi_dataset_EL": 'SELECT "QR Code" FROM "sdi_dataset_EL"',
    "sdi_print_out": 'SELECT "QR Code" FROM "sdi_print_out"',
    "sdi_print_out_arch": 'SELECT "QR Code" FROM "sdi_print_out_arch"',
}


def _is_sdi_label(value):
    try:
        return float(value) != 1.0
    except (TypeError, ValueError):
        return str(value or "").strip() != ""


def _empty_workflow():
    cards = [{**card, "count": 0} for card in PROCESS_CARDS]
    return {
        "cards": cards,
        "total_distinct_qr": 0,
        "unclassified_count": 0,
        "flow": [{**step, "count": 0} for step in FLOW_STEPS],
    }


def _table_exists(conn, table_name):
    return qrdb.has_table(conn, table_name)  # backend-agnostic


def _base_qr(value):
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(None, 1)[0]


def _disposed_qrs(conn):
    """QRs with an active disposal (``disposed_assets.status = 'disposed'``).

    Subtracted from every pipeline count so a retired asset leaves the review
    cards, the Total QR pill and the SDI flow steps. Guarded and silent: a
    database without the 2026-08-11 migration must still render the chart.

    Mirrored by approval._disposed_qrs — the pipeline and the KPI bars must
    subtract the SAME set or approval.integrity_snapshot()'s reconciliation
    identity breaks.
    """
    try:
        if not _table_exists(conn, "disposed_assets"):
            return set()
        rows = conn.execute(
            'SELECT "qr_code" FROM "disposed_assets" WHERE "status" = ?', ("disposed",)
        ).fetchall()
    except Exception:
        return set()
    return {_base_qr(row[0]) for row in rows if _base_qr(row[0])}


def _distinct_qrs(conn, table_name):
    query = QR_SELECT_SQL.get(table_name)
    if query is None or not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(query).fetchall()
    return {_base_qr(row[0]) for row in rows if _base_qr(row[0])}


def _sdi_qr_filter(conn):
    if not _table_exists(conn, "QR_codes"):
        return None

    rows = conn.execute('SELECT "QR_code_ID", "sdi" FROM "QR_codes"').fetchall()
    return {_base_qr(qr_code) for qr_code, sdi in rows if _base_qr(qr_code) and _is_sdi_label(sdi)}


def _apply_sdi_filter(qrs, sdi_qrs):
    if sdi_qrs is None:
        return qrs
    return qrs & sdi_qrs


def _review_state_counts(conn):
    if not _table_exists(conn, "QR_code_assets"):
        return {card["process"]: 0 for card in PROCESS_CARDS}, 0

    qr_process = {}
    unclassified_qrs = set()
    disposed = _disposed_qrs(conn)
    rows = conn.execute('SELECT "code_assets", "Col_process" FROM "QR_code_assets"').fetchall()

    for code_assets, process_value in rows:
        qr = _base_qr(code_assets)
        if not qr or qr in disposed:
            continue
        try:
            process = int(str(process_value).strip())
        except (TypeError, ValueError):
            unclassified_qrs.add(qr)
            continue

        if process not in {0, 1, 2}:
            unclassified_qrs.add(qr)
            continue

        previous = qr_process.get(qr)
        if previous is None or process > previous:
            qr_process[qr] = process

    counts = {card["process"]: 0 for card in PROCESS_CARDS}
    for process in qr_process.values():
        counts[process] += 1

    return counts, len(unclassified_qrs - set(qr_process))


def _flow_counts(conn):
    sdi_qrs = _sdi_qr_filter(conn)
    # Disposal deletes the curated row and is refused for packaged QRs, so
    # today this subtraction only ever removes rows the queue no longer has.
    # It is kept explicit so the flow steps stay correct if a disposal ever
    # coexists with a package row.
    disposed = _disposed_qrs(conn)
    requested_qrs = _apply_sdi_filter(_distinct_qrs(conn, "sdi_print_out"), sdi_qrs) - disposed
    archived_qrs = _apply_sdi_filter(_distinct_qrs(conn, "sdi_print_out_arch"), sdi_qrs) - disposed
    curated_qrs = _apply_sdi_filter(
        _distinct_qrs(conn, "sdi_dataset") | _distinct_qrs(conn, "sdi_dataset_EL"),
        sdi_qrs,
    ) - disposed
    queue_qrs = curated_qrs - requested_qrs - archived_qrs

    return {
        "queue": len(queue_qrs),
        "requested": len(requested_qrs),
        "planon": len(archived_qrs),
    }


def build_asset_workflow(db_path=None):
    if db_path is None:
        db_path = DB_PATH

    workflow = _empty_workflow()

    try:
        with closing(qrdb.get_connection(sqlite_path=db_path)) as conn:
            review_counts, unclassified_count = _review_state_counts(conn)
            flow_counts = _flow_counts(conn)

        workflow["cards"] = [
            {**card, "count": review_counts.get(card["process"], 0)}
            for card in PROCESS_CARDS
        ]
        workflow["total_distinct_qr"] = sum(card["count"] for card in workflow["cards"])
        workflow["unclassified_count"] = unclassified_count
        workflow["flow"] = [{**step, "count": flow_counts[step["key"]]} for step in FLOW_STEPS]
        return workflow

    except Exception as e:
        print(f"\n[ERROR] SDI flow chart data failed: {e}")
        return workflow


if __name__ == "__main__":
    data = build_asset_workflow()
    print("Done. Workflow:", data)
