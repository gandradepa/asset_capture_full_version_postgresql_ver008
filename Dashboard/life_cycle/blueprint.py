"""
Life Cycle Assessment - Flask Blueprint.

Converted from the standalone ``life_cycle_dashboard/app.py`` (ver005) into an
in-process blueprint mounted under ``/life-cycle`` inside the Asset Portal
dashboard. It reads the ``life_cycle`` table from PostgreSQL and shows it as two
tabs (Complete / Incomplete) gated behind an asset-group dropdown.

Integration notes vs. the original standalone app:
  * ``Flask(...)`` -> ``Blueprint("life_cycle", ...)`` with a blueprint-scoped
    static path (``/life-cycle/static``) so it can't collide with the portal's
    own ``/static``.
  * Every data route is gated by Flask-Login + the RBAC permission
    ``operations / lifecycle_assessment`` (viewer). ``/health`` stays open so it
    can serve as a liveness probe behind the reverse proxy.
  * ``?embedded=true`` hides the standalone header so the page sits cleanly
    inside the dashboard iframe (mirrors the SDI Process app).
  * The database target is derived once from ``LIFE_CYCLE_DSN`` / the portal's
    ``QR_PG_DSN`` (defaulting to the dev sandbox) and exported as
    ``LIFE_CYCLE_SA_DSN`` for the pipeline BEFORE it is imported.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (
    Blueprint, render_template, request, Response, g, current_app,
)
from flask_login import login_required, current_user

# --- Import path bootstrap -------------------------------------------------
# Works both when imported by the portal (which already puts Developer/ and
# Developer/auth_service/ on sys.path) and from a standalone smoke-test harness.
BASE_DIR = Path(__file__).resolve().parent          # .../Dashboard/life_cycle
DEVELOPER_DIR = BASE_DIR.parent.parent              # .../Developer
PIPELINE_DIR = DEVELOPER_DIR / "life_cycle_pipeline"
AUTH_DIR = DEVELOPER_DIR / "auth_service"
for _p in (DEVELOPER_DIR, AUTH_DIR, PIPELINE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# RBAC helpers (shared auth_service). has_permission/access_denied_response are
# used for the HTML page route; require_permission for the JSON/mutation routes.
from auth_model import has_permission, require_permission, access_denied_response

# Same-package Excel export helper.
from . import excel_export
from .completeness import COMPLETENESS_COLUMNS, is_complete


# --- Database target: one source, two DSN formats --------------------------
def _sa_url_from_libpq(dsn: str) -> str:
    """Convert a libpq keyword/value DSN into a SQLAlchemy URL."""
    p = psycopg2.extensions.parse_dsn(dsn)
    user = p.get("user", "")
    pwd = p.get("password")
    host = p.get("host", "localhost")
    port = p.get("port", "5432")
    dbname = p.get("dbname", "")
    auth = quote(user, safe="")
    if pwd:
        auth += ":" + quote(pwd, safe="")
    return f"postgresql+psycopg2://{auth}@{host}:{port}/{dbname}"


# psycopg2 (read) DSN: explicit override, else the portal's QR_PG_DSN, else dev.
PG_DSN = (
    os.environ.get("LIFE_CYCLE_DSN")
    or os.environ.get("QR_PG_DSN")
    or "host=localhost port=5432 dbname=qr_code_db_sandbox user=postgres"
)
# Make the SQLAlchemy URL available to the pipeline BEFORE importing it
# (track_assets / load_life_cycle read LIFE_CYCLE_SA_DSN at import time). An
# explicit LIFE_CYCLE_SA_DSN in the environment always wins (setdefault).
os.environ.setdefault("LIFE_CYCLE_SA_DSN", _sa_url_from_libpq(PG_DSN))

# Database name shown in the dashboard footer ("Source: <db>.life_cycle"), so it
# reflects the actual environment (qr_code_db in prod, sandbox in dev).
try:
    DB_NAME = psycopg2.extensions.parse_dsn(PG_DSN).get("dbname") or "qr_code_db"
except Exception:  # noqa: BLE001 - malformed DSN -> fall back to the prod name
    DB_NAME = "qr_code_db"

# Import the data-source -> PostgreSQL pipeline (lives in life_cycle_pipeline/).
# Imported at module load, NOT lazily inside /refresh: importing pandas/sqlalchemy
# during the request writes .pyc files that a dev reloader can mistake for code
# changes and restart the server mid-upload.
from track_assets import build_life_cycle              # noqa: E402
from load_life_cycle import load as load_life_cycle    # noqa: E402


life_cycle_bp = Blueprint(
    "life_cycle",
    __name__,
    template_folder="templates",
    static_folder="static",
    # Flask prepends the blueprint's url_prefix ("/life-cycle") to this, so
    # "/static" yields the final, collision-free "/life-cycle/static/...".
    static_url_path="/static",
)

# Permission this feature is gated behind (keep byte-identical to app_registry).
PERM = ("operations", "lifecycle_assessment", "viewer")

TABLE = "life_cycle"

# Columns shown in the tables, in display order.
DISPLAY_COLUMNS = [
    "QR Code",
    "Description",
    "Asset Tag",
    "Property Name",
    "Space Number",
    "Space Name",
    "Floor Name",
    "Make",
    "Model",
    "Serial Number",
    "Installation Date",
    "years",
    "classification",
]

# Columns that control the Complete / Incomplete tab split.
KEY_COLUMNS = COMPLETENESS_COLUMNS

# Extra columns needed for grouping / the dropdown.
GROUP_CODE = "Asset Group Code"
GROUP_NAME = "Asset Group Name"

# Extra capture columns shown on both tabs, computed at read time:
#   flag         -> the QR was field-captured (a QR_codes row with a date_set exists)
#   Capture Date -> QR_codes.date_set (date only) for that QR Code
CAPTURE_EXTRA_COLUMNS = ["flag", "Capture Date"]


def _connect():
    return psycopg2.connect(PG_DSN)


def _last_loaded():
    """Return the 'last loaded' stamp (the time the life_cycle data was last
    rebuilt) as ``YYYY-MM-DD HH:MM:SS``, or ``None`` if it can't be determined.

    Primary source is the ``life_cycle_meta`` stamp written by load_life_cycle.
    Before the first instrumented Update Database run that row won't exist, so we
    fall back to the life_cycle relation file's modification time, which closely
    tracks the last load. Never raises, so a missing table or DB hiccup can't
    take the dashboard down."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT "value" FROM "life_cycle_meta" WHERE "key" = %s',
                    ("last_loaded",),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return row[0]
    except Exception:  # noqa: BLE001 - meta table absent -> try the fallback
        pass

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT to_char((pg_stat_file(pg_relation_filepath(%s))).modification, "
                    "'YYYY-MM-DD HH24:MI:SS')",
                    (TABLE,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:  # noqa: BLE001 - no permission / table absent -> hide it
        return None


def _fetch_rows():
    """Return (groups, rows). Each row is a dict with display columns plus the
    group code, a ``complete`` boolean, a ``captured`` boolean (the QR was
    field-captured) and a ``Capture Date`` (date the QR was captured).

    The captured flag and capture date are both computed at read time against
    the ``QR_codes`` table, so they always reflect the current field-capture
    state without rerunning the pipeline. The flag is true exactly when a
    capture date exists, keeping the dot and the date consistent. They are
    surfaced on both tabs (see CAPTURE_EXTRA_COLUMNS)."""
    select_cols = DISPLAY_COLUMNS + [GROUP_CODE, GROUP_NAME]
    # Alias life_cycle as lc and qualify its columns; Postgres strips the "lc."
    # qualifier from the output names, so the RealDictCursor keys are unchanged.
    col_sql = ", ".join(f'lc."{c}"' for c in select_cols)
    sql = f'''
        SELECT {col_sql},
               EXISTS (SELECT 1 FROM "QR_codes" q
                        WHERE q."QR_code_ID" = lc."QR Code"
                          AND q."date_set" IS NOT NULL) AS captured,
               (SELECT LEFT(MAX(q."date_set"), 10)   -- date only (YYYY-MM-DD), drop the time
                  FROM "QR_codes" q
                 WHERE q."QR_code_ID" = lc."QR Code") AS "Capture Date"
        FROM "{TABLE}" lc
        ORDER BY lc."QR Code"
    '''
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            raw = cur.fetchall()

    groups: dict[str, dict] = {}
    rows = []
    for r in raw:
        code = r.get(GROUP_CODE)
        name = r.get(GROUP_NAME)
        complete = is_complete(r)
        rows.append({**r, "complete": complete, "captured": bool(r.get("captured"))})
        if code is not None:
            g_ = groups.setdefault(code, {"code": code, "name": name, "total": 0,
                                          "complete": 0, "incomplete": 0})
            g_["total"] += 1
            g_["complete" if complete else "incomplete"] += 1

    group_list = sorted(groups.values(), key=lambda g_: (g_["name"] or "", g_["code"]))
    return group_list, rows


# Age-classification buckets for the whole-table summary measure shown beside
# the asset-group selector. Order matches the on-screen rows; the last bucket
# collects rows with no classification (i.e. no installation date).
CLASSIFICATION_BUCKETS = [
    ("G", "Good",     "under 8 yrs", "good"),
    ("Y", "Caution",  "8–10 yrs",    "caution"),
    ("R", "Critical", "10 yrs +",    "critical"),
    ("U", "Unknown",  "no date",     "unknown"),
]


def _classification_summary(rows):
    """Quantity + percentage of each age-classification bucket across *all*
    life_cycle rows (the whole table, not the Complete/Incomplete split). Rows
    with no classification fall into the 'Unknown · no date' bucket."""
    counts = {"G": 0, "Y": 0, "R": 0, "U": 0}
    for r in rows:
        cls = (r.get("classification") or "").strip().upper()
        counts[cls if cls in ("G", "Y", "R") else "U"] += 1
    total = len(rows)

    items = []
    for key, label, sub, tone in CLASSIFICATION_BUCKETS:
        n = counts[key]
        pct = round(n * 100.0 / total, 1) if total else 0.0
        # "33.1%", "25%", "0%" — drop a trailing ".0" like the design.
        pct_label = f"{pct:.1f}".rstrip("0").rstrip(".") + "%"
        items.append({
            "key": key, "label": label, "sub": sub, "tone": tone,
            "count": n, "pct": pct, "pct_label": pct_label,
        })
    return {"total": total, "items": items}


@life_cycle_bp.before_request
def _detect_embedded_mode():
    # ?embedded=true -> hide the standalone header so the page sits cleanly in
    # the dashboard iframe (mirrors SDI_process/app.py:detect_embedded_mode).
    g.embedded = request.args.get("embedded", "").lower() == "true"


@life_cycle_bp.route("/")
@login_required
def index():
    # HTML page route: return a styled 403 page (not JSON) when not permitted,
    # matching the project convention for browser-rendered routes.
    if not has_permission(current_user, *PERM):
        return access_denied_response("Life Cycle Assessment")
    groups, rows = _fetch_rows()
    return render_template(
        "life_cycle/dashboard.html",
        title="Life Cycle Assessment",
        acshell_active="life",
        db_name=DB_NAME,
        groups=groups,
        rows=rows,
        display_columns=DISPLAY_COLUMNS,
        capture_columns=CAPTURE_EXTRA_COLUMNS,
        key_columns=KEY_COLUMNS,
        group_code_field=GROUP_CODE,
        last_loaded=_last_loaded(),
        classification_summary=_classification_summary(rows),
    )


@life_cycle_bp.route("/export", methods=["POST"])
@login_required
@require_permission(*PERM)
def export_xlsx():
    """Build a styled XLSX from the rows the client currently has visible.

    The client posts the active tab's visible QR codes in display order, so the
    export follows the active filters *and* sort exactly. We rebuild the full
    row dicts from the database (raw values) and hand them to excel_export.
    """
    data = request.get_json(silent=True) or {}
    qr_codes = data.get("qr_codes") or []
    tab = (data.get("tab") or "complete").lower()
    group_code = data.get("group_code") or ""

    _, rows = _fetch_rows()
    by_qr = {str(r.get("QR Code")): r for r in rows}
    ordered = [by_qr[str(q)] for q in qr_codes if str(q) in by_qr]

    group_name = ordered[0].get(GROUP_NAME) if ordered else ""
    view_label = "Complete" if tab == "complete" else "Incomplete"
    logo_path = str(BASE_DIR / "static" / "img" / "ubc-facilities_logo.jpg")

    xlsx = excel_export.build_workbook(group_name, view_label, ordered, logo_path=logo_path)

    safe_group = (group_code or "all").replace("/", "-").replace(" ", "_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"life_cycle_{safe_group}_{view_label.lower()}_{stamp}.xlsx"
    return Response(
        xlsx,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@life_cycle_bp.route("/refresh", methods=["POST"])
@login_required
@require_permission(*PERM)
def refresh():
    """Run the data-source -> PostgreSQL interface against a user-chosen file.

    The browser uploads the Excel workbook the user picked with the header
    button; we hand its bytes to the track_assets/load_life_cycle pipeline,
    which rebuilds the ``life_cycle`` table (plus its ``space_floor`` reference
    table and FK) from scratch. The dashboard then reloads to show fresh data.

    Returns a small JSON summary, or a JSON error (keeping the app up) when the
    upload is missing, the workbook can't be parsed, or the load fails.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return {"ok": False, "error": "No file was selected."}, 400

    try:
        # Read the upload into memory and hand it to the pipeline as a file-like
        # object, so nothing depends on a server-side path.
        df = build_life_cycle(BytesIO(upload.read()))
        summary = load_life_cycle(df=df)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user
        current_app.logger.exception("life cycle refresh failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500

    return {
        "ok": True,
        "filename": upload.filename,
        "rows": summary.get("rows"),
        "columns": summary.get("columns"),
        "ref_rows": summary.get("ref_rows"),
        "fk_ok": summary.get("fk_ok"),
        "last_loaded": summary.get("last_loaded"),
    }


@life_cycle_bp.route("/health")
def health():
    # Open (no auth) so it can serve as a liveness probe behind the proxy.
    return "OK", 200
