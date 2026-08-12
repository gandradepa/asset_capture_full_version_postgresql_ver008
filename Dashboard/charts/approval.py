#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import io
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
import pandas as pd
import db as qrdb  # backend-agnostic QR_codes DB layer

import matplotlib
matplotlib.use("Agg")  # headless for servers
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Wedge, Arc, Circle

# === CONFIG ===
DB_PATH_DEFAULT = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"
DB_PATH = os.getenv("DASHBOARD_DB_PATH", DB_PATH_DEFAULT)

TABLES_MAIN = ("sdi_dataset", "sdi_dataset_EL")
TABLE_BUILDINGS = "Buildings"
COMMON_COLS = ["Approved", "Asset Group", "Attribute", "Building", "Description", "QR Code"]

# UBC palette
COLOR_APPROVED = "#002145"
COLOR_NOTAPP   = "#E6ECF2"
COLOR_TARGET   = "#0055b7"

# Dark-blue tones per discipline for the Overview "QR Codes by Asset Type" bar
# chart (same navy schema, dark -> light: ME brand navy, EL UBC blue, BF steel).
# Re-stepped 2026-08-05 after the dataviz palette validator flagged the old
# EL #003b7e / BF #0055b7 pair as too close (dE 11.5); this trio passes >= 21.
# Keep in sync with the Chart.js TONES map in dashboard.html.
QR_TYPE_COLORS = {"ME": "#002145", "EL": "#0055b7", "BF": "#5b9bd5"}
QR_TYPE_ORDER = ("ME", "EL", "BF")

# Output DPI used by render_chart_png (must stay aligned with hitbox coordinates)
OUTPUT_DPI = 120

# ---------- Status filter helpers ----------
def _normalize_status_filter(status: str) -> str:
    """
    Normalize a status query param into one of:
      - "Approved"
      - "Not Approved"
      - "" (meaning All)
    """
    s = str(status or "").strip().lower().replace("_", " ").replace("-", " ")
    if s in {"approved", "1", "yes", "true"}:
        return "Approved"
    if s in {"not approved", "notapproved", "unapproved", "0", "no", "false"}:
        return "Not Approved"
    return ""

# ---------- Data helpers ----------
def _table_exists(conn, table: str) -> bool:
    return qrdb.has_table(conn, table)  # backend-agnostic; SQLite uses sqlite_master, PG uses information_schema

def _read_table(db_path: str, table: str) -> pd.DataFrame:
    with qrdb.get_connection(sqlite_path=db_path) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table}"', qrdb.raw_conn(conn))

def _read_main_union(db_path: str, tables: tuple[str, ...], cols: list[str]) -> pd.DataFrame:
    dfs = []
    with qrdb.get_connection(sqlite_path=db_path) as conn:
        for t in tables:
            if not _table_exists(conn, t): continue
            df = pd.read_sql_query(f'SELECT * FROM "{t}"', qrdb.raw_conn(conn))
            for c in cols:
                if c not in df.columns: df[c] = np.nan
            df = df[cols].copy()
            dfs.append(df)
    if not dfs:
        raise RuntimeError(f"None of the tables {tables} were found in the database.")
    return pd.concat(dfs, ignore_index=True)

def _qrs_for_user(user):
    """Returns the set of QR codes captured by the user (per QR_code_assets.user).
    Returns None when no user is set (signals: no user filter)."""
    if not user:
        return None
    qrs = set()
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cur = conn.cursor()
            if qrdb.is_postgres():
                # INSTR() and "..." string literals are SQLite-isms (PG parses
                # "..." as an identifier). Mirrors the split_part/strpos
                # expression used by the Activity Hours CTE in Asset_portal_dashboard.
                sql = (
                    "SELECT DISTINCT split_part(\"code_assets\", ' ', 1) AS qr "
                    'FROM "QR_code_assets" '
                    "WHERE \"user\" = ? AND strpos(\"code_assets\", ' ') > 0"
                )
            else:
                sql = (
                    'SELECT DISTINCT substr("code_assets", 1, instr("code_assets", " ") - 1) AS qr '
                    'FROM "QR_code_assets" '
                    'WHERE "user" = ? AND instr("code_assets", " ") > 0'
                )
            cur.execute(sql, (user,))
            for (qr,) in cur.fetchall():
                if qr:
                    qrs.add(str(qr).strip())
    except Exception:
        return set()
    return qrs

def users_with_data():
    """Distinct usernames that appear in QR_code_assets.user — users who have captured assets."""
    try:
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            # '' (not "") for the empty string: PG parses "" as a zero-length
            # identifier. COLLATE NOCASE is SQLite-only; GROUP BY + ORDER BY
            # LOWER() gives the same case-insensitive ordering on both backends
            # (PG rejects DISTINCT + ORDER BY LOWER(...)).
            rows = conn.execute(
                'SELECT "user" FROM "QR_code_assets" '
                "WHERE \"user\" IS NOT NULL AND TRIM(\"user\") <> '' "
                'GROUP BY "user" ORDER BY LOWER("user")'
            ).fetchall()
        return ["All"] + [str(r[0]).strip() for r in rows if r and r[0]]
    except Exception:
        return ["All"]

def _prepare_data(user=None) -> pd.DataFrame:
    df = _read_main_union(DB_PATH, TABLES_MAIN, COMMON_COLS)
    df2 = _read_table(DB_PATH, TABLE_BUILDINGS)

    df["Building_key"] = df["Building"].astype(str).str.strip()
    # --- CORREÇÃO: Erro de sintaxe corrigido aqui ---
    df2["Code_key"]    = df2["Code"].astype(str).str.strip()
    df2 = df2.drop_duplicates(subset=["Code_key"], keep="first")

    df3 = df.merge(df2, left_on="Building_key", right_on="Code_key", how="left")
    if "Approved" not in df3.columns: df3["Approved"] = ""
    df3["Approved"] = df3["Approved"].fillna("").astype(str).str.strip()
    df3["Approved_label"] = df3["Approved"].eq("1").replace({True: "Approved", False: "Not Approved"})

    df3["Name"] = df3["Name"].fillna("Unknown").astype(str).str.strip()
    df3["Asset Group"] = df3["Asset Group"].fillna("Unknown").astype(str).str.strip()

    qrs = _qrs_for_user(user)
    if qrs is not None:
        df3 = df3[df3["QR Code"].astype(str).str.strip().isin(qrs)]

    df4 = (
        df3.groupby(["Name", "Asset Group", "Approved_label"], dropna=False)["QR Code"]
           .nunique()
           .reset_index(name="QTY")
           .rename(columns={"Approved_label": "Approved"})
    )
    df4["QTY"] = pd.to_numeric(df4["QTY"], errors="coerce").fillna(0).astype(int)
    return df4

def _prepare_qr_type_counts(building: str = "All", user=None) -> pd.DataFrame:
    """Distinct QR-code counts per discipline, columns ["Type", "QTY"] in
    fixed ME, EL, BF order (missing types zero-filled).

    Discipline inference mirrors the reviewer-analysis hover CTE:
    sdi_dataset rows are BF when "Asset Group" contains 'backflow'
    (case-insensitive), otherwise ME; all sdi_dataset_EL rows are EL.
    `building` filters by Buildings.Name (same value the other approval
    charts receive); `user` reuses the QR_code_assets capture filter.
    """
    frames = []
    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        for table, default_type in (("sdi_dataset", "ME"), ("sdi_dataset_EL", "EL")):
            if not _table_exists(conn, table):
                continue
            df = pd.read_sql_query(f'SELECT * FROM "{table}"', qrdb.raw_conn(conn))
            for c in ("QR Code", "Asset Group", "Building"):
                if c not in df.columns:
                    df[c] = ""
            df = df[["QR Code", "Asset Group", "Building"]].copy()
            if default_type == "ME":
                is_bf = df["Asset Group"].fillna("").astype(str).str.lower().str.contains("backflow")
                df["Type"] = np.where(is_bf, "BF", "ME")
            else:
                df["Type"] = "EL"
            frames.append(df)
    if not frames:
        raise RuntimeError("Neither sdi_dataset nor sdi_dataset_EL was found in the database.")

    df = pd.concat(frames, ignore_index=True)
    df["QR Code"] = df["QR Code"].fillna("").astype(str).str.strip()
    df = df[df["QR Code"] != ""]

    if building and building != "All":
        bld = _read_table(DB_PATH, TABLE_BUILDINGS)
        bld["Code_key"] = bld["Code"].astype(str).str.strip()
        bld = bld.drop_duplicates(subset=["Code_key"], keep="first")
        df["Building_key"] = df["Building"].astype(str).str.strip()
        df = df.merge(bld[["Code_key", "Name"]], left_on="Building_key", right_on="Code_key", how="left")
        df["Name"] = df["Name"].fillna("Unknown").astype(str).str.strip()
        df = df[df["Name"] == building]

    qrs = _qrs_for_user(user)
    if qrs is not None:
        df = df[df["QR Code"].isin(qrs)]

    counts = df.groupby("Type")["QR Code"].nunique().reindex(list(QR_TYPE_ORDER), fill_value=0)
    out = counts.reset_index()
    out.columns = ["Type", "QTY"]
    out["QTY"] = pd.to_numeric(out["QTY"], errors="coerce").fillna(0).astype(int)
    return out

def _base_qr(value) -> str:
    """First whitespace-delimited token of a code_assets / QR Code value.

    Mirrors flow_quantity_chart._base_qr so the integrity math compares the
    same QR keys the pipeline "Total QR" pill counts.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(None, 1)[0]


def _disposed_qrs(conn) -> set:
    """QRs with an active disposal (``disposed_assets.status = 'disposed'``).

    Disposal already deletes the curated sdi_dataset / sdi_dataset_EL row, so
    the approval numbers drop it on their own; this set is what removes it from
    the capture-side readers as well. Guarded and silent: a database without
    the 2026-08-11 migration must still render every chart.

    Mirrored by flow_quantity_chart._disposed_qrs — the pipeline and the KPI
    bars must subtract the SAME set or the reconciliation identity in
    integrity_snapshot() breaks.
    """
    try:
        if not _table_exists(conn, "disposed_assets"):
            return set()
        rows = conn.execute(
            'SELECT "qr_code" FROM "disposed_assets" WHERE "status" = ?', ("disposed",)
        ).fetchall()
    except Exception:
        return set()
    return {_base_qr(r[0]) for r in rows if _base_qr(r[0])}


def _scan_capture_universe(conn) -> dict:
    """One pass over QR_code_assets, shared by the Manual Entry and integrity
    readers so both agree on the capture universe.

    Disposed QRs are dropped here, at the single source both readers use, so a
    retired asset leaves the KPI bars, the donut's Manual Entry slice and the
    integrity/unsynced warning together (2026-08-12). Without it a disposal
    reads as an orphan: captured, active, no curated row.

    code_assets is "<QR> <building> <type> - <seq>". Returns:
      max_proc    {qr: 0|1|2}  highest valid Col_process per QR
      building    {qr: code}   first building token seen
      type        {qr: "ME"|"EL"|"BF"}  first recognized discipline token
      unknown_type  QRs with a valid process but no recognized discipline
      unclassified  QRs whose ONLY Col_process values are invalid/unparseable
                    (same definition as flow_quantity_chart._review_state_counts)
    """
    max_proc: dict = {}
    building: dict = {}
    qr_type: dict = {}
    unclassified: set = set()
    disposed = _disposed_qrs(conn)
    for code_assets, proc in conn.execute(
        'SELECT "code_assets", "Col_process" FROM "QR_code_assets"'
    ).fetchall():
        parts = str(code_assets or "").strip().split()
        if not parts:
            continue
        qr = parts[0]
        if qr in disposed:
            continue
        try:
            p = int(str(proc).strip())
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
        if len(parts) > 2 and qr not in qr_type:
            t = parts[2].upper()
            if t in QR_TYPE_ORDER:
                qr_type[qr] = t
    return {
        "max_proc": max_proc,
        "building": building,
        "type": qr_type,
        "unknown_type": set(max_proc) - set(qr_type),
        "unclassified": unclassified - set(max_proc),
    }


def _curated_qrs(conn) -> set:
    """Distinct QRs present in either curated table, keyed by first token."""
    curated = set()
    for table in TABLES_MAIN:
        if not _table_exists(conn, table):
            continue
        for (qr,) in conn.execute(f'SELECT DISTINCT "QR Code" FROM "{table}"').fetchall():
            q = _base_qr(qr)
            if q:
                curated.add(q)
    return curated


# A capture only gains its curated row when a reviewer app next serves a
# request (the JSON sync runs from before_request), so a recent capture is
# legitimately curated-less. 96h clears a long weekend with no reviewer
# traffic; genuinely stranded assets stay stranded indefinitely (the 22 lost on
# 2026-07-29 were weeks old), so waiting costs nothing while a false alarm
# costs the guardrail its credibility.
ORPHAN_GRACE_HOURS = 96


def _capture_timestamps(conn) -> dict:
    """{qr: datetime} from QR_codes.date_set (TEXT in PostgreSQL).

    Unparseable or missing values are simply absent from the map; callers treat
    an unknown capture time as NOT in flight, since a capture with no QR_codes
    row cannot be excused as recent.
    """
    out: dict = {}
    if not _table_exists(conn, "QR_codes"):
        return out
    try:
        rows = conn.execute('SELECT "QR_code_ID", "date_set" FROM "QR_codes"').fetchall()
    except Exception:
        return out
    for qr_raw, raw in rows:
        qr = _base_qr(qr_raw)
        if not qr:
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        if isinstance(raw, datetime):
            out[qr] = raw
            continue
        try:
            out[qr] = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                out[qr] = datetime.fromisoformat(text[:19])
            except ValueError:
                continue
    return out


def _curated_type_buckets(conn) -> dict:
    """{qr: {types}} using the same discipline inference as the KPI bars.

    sdi_dataset rows are BF when "Asset Group" contains 'backflow', else ME;
    every sdi_dataset_EL row is EL — mirroring _prepare_qr_type_counts. A QR
    landing in more than one bucket is counted once per bucket by the bars but
    only once by the pipeline, so the extra memberships are a divergence
    channel in their own right.
    """
    buckets: dict = {}
    if _table_exists(conn, "sdi_dataset"):
        for qr, group in conn.execute('SELECT "QR Code", "Asset Group" FROM "sdi_dataset"').fetchall():
            q = _base_qr(qr)
            if not q:
                continue
            t = "BF" if "backflow" in str(group or "").lower() else "ME"
            buckets.setdefault(q, set()).add(t)
    if _table_exists(conn, "sdi_dataset_EL"):
        for (qr,) in conn.execute('SELECT "QR Code" FROM "sdi_dataset_EL"').fetchall():
            q = _base_qr(qr)
            if q:
                buckets.setdefault(q, set()).add("EL")
    return buckets


def integrity_snapshot(sample_limit: int = 10, grace_hours: Optional[int] = None) -> dict:
    """Capture-vs-curated reconciliation for the Overview KPI section.

    Read-only and deliberately GLOBAL — the invariant "pipeline Total QR ==
    sum of KPI bars" is system-wide, so this takes no building/user filter.

    Reported channels — together they account for the whole gap between the
    pipeline "Total QR" pill and the sum of the KPI bars:

        pipeline - bars == orphan_total + manual_unknown
                           - reverse_total - curated_double_counted

    (identity pinned by test/test_pipeline_kpi_equivalence.py, so no
    divergence can hide in an unnamed remainder)

    orphans  QRs whose max Col_process is 0/1 (active review states) with NO
             row in sdi_dataset / sdi_dataset_EL. This is the 22-QR silent-loss
             class: captured, expected in SDI, curated row never written.
             Bucketed per discipline, plus "unknown" for QRs whose discipline
             token the KPI bars would silently drop. Counts only STRANDED
             orphans (captured longer than grace_hours ago); recent captures are
             still in flight and reported as orphan_pending instead.
    orphan_total  stranded + pending — the term in the accounting identity.
    manual_unknown  uncurated Manual Entry QRs whose discipline token is not
             ME/EL/BF — the bars cannot place them, the pipeline still counts
             them.
    reverse  curated QRs with no validly classified capture row.
    curated_double_counted  extra type-bucket memberships (a QR curated as
             both ME/BF and EL is counted once per bucket by the bars).
    unclassified  QRs carrying only invalid Col_process values (also shown on
             the pipeline chart's own diagnostic line); excluded from both
             totals, so informational.

    All values are plain ints; sample lists are sorted and capped.
    """
    grace = ORPHAN_GRACE_HOURS if grace_hours is None else int(grace_hours)
    empty = {t: 0 for t in QR_TYPE_ORDER}
    out = {
        "orphans": {**empty, "unknown": 0},
        "orphan_total": 0,
        "orphan_stranded": 0,
        "orphan_pending": 0,
        "orphan_samples": [],
        "orphan_pending_samples": [],
        "manual_unknown": 0,
        "manual_unknown_samples": [],
        "reverse_total": 0,
        "reverse_samples": [],
        "curated_double_counted": 0,
        "unclassified": 0,
        "grace_hours": grace,
        "scope": "global",
    }
    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        if not _table_exists(conn, "QR_code_assets"):
            return out
        scan = _scan_capture_universe(conn)
        buckets = _curated_type_buckets(conn)
        captured_at = _capture_timestamps(conn)

    curated = set(buckets)
    max_proc = scan["max_proc"]
    known_type = scan["type"]

    active = {qr for qr, p in max_proc.items() if p in (0, 1)}
    orphans = active - curated
    cutoff = datetime.now() - timedelta(hours=grace)
    # In flight: captured recently enough that the reviewer sync may simply not
    # have run yet. An unknown capture time is never excused.
    pending = {qr for qr in orphans if captured_at.get(qr) is not None and captured_at[qr] > cutoff}
    stranded = orphans - pending

    for qr in stranded:
        t = known_type.get(qr)
        out["orphans"]["unknown" if t not in empty else t] += 1
    out["orphan_total"] = len(orphans)
    out["orphan_stranded"] = len(stranded)
    out["orphan_pending"] = len(pending)
    out["orphan_samples"] = sorted(stranded)[:sample_limit]
    out["orphan_pending_samples"] = sorted(pending)[:sample_limit]

    manual_unknown = {
        qr for qr, p in max_proc.items()
        if p == 2 and qr not in curated and qr not in known_type
    }
    out["manual_unknown"] = len(manual_unknown)
    out["manual_unknown_samples"] = sorted(manual_unknown)[:sample_limit]

    reverse = curated - set(max_proc)
    out["reverse_total"] = len(reverse)
    out["reverse_samples"] = sorted(reverse)[:sample_limit]

    out["curated_double_counted"] = sum(len(types) - 1 for types in buckets.values())
    out["unclassified"] = len(scan["unclassified"])
    return out


def _manual_excluded_by_type(building: str = "All", user=None) -> dict:
    """Distinct Manual Entry QRs with no curated row, split by discipline.

    A QR counts when its max valid Col_process in QR_code_assets is 2 (Manual
    Entry) and it has no row in sdi_dataset / sdi_dataset_EL. Discipline comes
    from the third token of code_assets ("<QR> <building> <type> - <seq>").
    These are the by-design KPI-vs-pipeline holdouts (Manual Entry = SDI
    exclusion state); the Overview bar chart adds them per type and the chip
    names their total so the section reconciles with the pipeline.
    """
    empty = {t: 0 for t in QR_TYPE_ORDER}
    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        if not _table_exists(conn, "QR_code_assets"):
            return dict(empty)
        scan = _scan_capture_universe(conn)
        per_qr = scan["max_proc"]
        qr_building = scan["building"]
        qr_type = scan["type"]

        manual_qrs = {qr for qr, p in per_qr.items() if p == 2}
        if not manual_qrs:
            return dict(empty)

        curated = _curated_qrs(conn)
    manual_qrs -= curated

    if building and building != "All":
        bld = _read_table(DB_PATH, TABLE_BUILDINGS)
        codes = {
            str(code).strip()
            for code, name in zip(bld.get("Code", []), bld.get("Name", []))
            if str(name or "").strip() == building
        }
        manual_qrs = {qr for qr in manual_qrs if qr_building.get(qr) in codes}

    qrs_user = _qrs_for_user(user)
    if qrs_user is not None:
        manual_qrs &= {str(q).strip() for q in qrs_user}

    out = dict(empty)
    for qr in manual_qrs:
        t = qr_type.get(qr)
        if t in out:
            out[t] += 1
    return out


def _count_manual_excluded(building: str = "All", user=None) -> int:
    """Total Manual Entry QRs held out of the curated tables (chip + donut slice)."""
    return int(sum(_manual_excluded_by_type(building=building, user=user).values()))


def overview_kpi_payload(building: str = "All", user=None) -> dict:
    """JSON-safe data for the Overview KPI charts (Chart.js client rendering).

    Shape: {"qr_types": {"ME": n, "EL": n, "BF": n},
            "approval": {"approved": n, "not_approved": n},
            "manual_excluded": n,
            "integrity": {...}}   # see integrity_snapshot(); global scope
    All values are plain Python ints (numpy ints break jsonify).
    """
    counts = _prepare_qr_type_counts(building=building, user=user)
    manual = _manual_excluded_by_type(building=building, user=user)
    # Bars count the full capture universe (2026-08-06): curated + Manual Entry.
    # The approval numbers stay curated-only — the gauge measures review
    # performance on reviewable assets; Manual Entry is never approved here.
    qr_types = {str(t): int(q) + int(manual.get(str(t), 0)) for t, q in zip(counts["Type"], counts["QTY"])}

    df4 = _prepare_data(user)
    filtered = df4 if (building == "All" or not building) else df4[df4["Name"] == building]
    approved = int(filtered.loc[filtered["Approved"] == "Approved", "QTY"].sum())
    not_approved = int(filtered.loc[filtered["Approved"] == "Not Approved", "QTY"].sum())
    payload = {
        "qr_types": qr_types,
        "approval": {"approved": approved, "not_approved": not_approved},
        "manual_excluded": int(sum(manual.values())),
    }
    # Integrity is advisory: a failure here must never take down the charts.
    try:
        payload["integrity"] = integrity_snapshot()
    except Exception as exc:
        payload["integrity"] = {"error": str(exc)}
    return payload

# ---------- Public helpers used by Flask ----------
def building_options(user=None):
    try:
        df4 = _prepare_data(user)
        opts = sorted(df4["Name"].dropna().astype(str).unique(), key=lambda n: n.lower())
        return ["All"] + opts
    except Exception:
        return ["All"]

# ---------- Chart Drawing Functions ----------
def _draw_gauge_chart(ax, value, max_value, target_percent):
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    # --- CORREÇÃO: Título restaurado ---
    ax.set_title("Performance Control KPI", fontsize=16, weight='bold', color=COLOR_APPROVED, pad=20)
    
    start_angle, end_angle = 180, 0
    pointer_angle = start_angle - ((value / max_value) * 180 if max_value > 0 else 0)
    
    # 1. Anel exterior fino
    ax.add_patch(Arc((0, 0), width=2.4, height=2.4, angle=0, theta1=end_angle, theta2=start_angle,
                     edgecolor=COLOR_TARGET, lw=0.5, alpha=0.9))

    # 2. Arco de valor espesso
    ax.add_patch(Wedge((0, 0), r=1.0, theta1=end_angle, theta2=start_angle, width=0.3,
                       facecolor=COLOR_NOTAPP, alpha=0.8))
    ax.add_patch(Wedge((0, 0), r=1.0, theta1=pointer_angle, theta2=start_angle, width=0.3,
                       facecolor=COLOR_APPROVED))

    # 3. Etiqueta de valor central
    ax.text(0, -0.3, f"{value:.0f} / {max_value:.0f}", ha='center', va='center', fontsize=22, weight='bold', color=COLOR_APPROVED)
    ax.text(0, -0.5, "Approved Assets", ha='center', va='center', fontsize=10, color=COLOR_APPROVED)

    # 4. Ponteiro (Agulha)
    pointer_angle_rad = math.radians(pointer_angle)
    ax.arrow(0, 0, 0.7 * math.cos(pointer_angle_rad), 0.7 * math.sin(pointer_angle_rad),
             width=0.015, head_width=0, head_length=0, fc='black', ec='black', zorder=5)
    ax.add_patch(Circle((0,0), 0.05, facecolor='black', zorder=6))

    # 5. Etiquetas numéricas ao longo do arco
    for percent in [0, 50, 100]:
        val = max_value * (percent / 100.0)
        angle = start_angle - (percent * 1.8)
        angle_rad = math.radians(angle)
        x = 1.2 * math.cos(angle_rad)
        y = 1.2 * math.sin(angle_rad)
        ax.text(x, y, f"{int(val)}", ha='center', va='center', fontsize=10)

    # 6. Marcador do Alvo
    target_value = max_value * (target_percent / 100.0)
    target_angle = start_angle - ((target_value / max_value) * 180 if max_value > 0 else 0)
    target_rad = math.radians(target_angle)
    
    marker_radius = 1.25 
    tx = marker_radius * math.cos(target_rad)
    ty = marker_radius * math.sin(target_rad)
    
    ax.plot([tx], [ty], marker=(3, 0, target_angle + 90), 
            markersize=12, color=COLOR_TARGET, clip_on=False, zorder=10, linestyle='none')

    label_radius = 1.4
    lx = label_radius * math.cos(target_rad)
    ly = label_radius * math.sin(target_rad)
    
    ha = 'left' if tx > -0.1 else 'right'
    ax.text(lx, ly, f"Target: {target_percent}%", ha=ha, va='center', color=COLOR_TARGET, fontsize=10)

    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-0.6, 1.5)


def _build_bar_wide(filtered_data):
    wide = filtered_data.groupby(["Asset Group", "Approved"])["QTY"].sum().unstack(fill_value=0)
    for col in ["Not Approved", "Approved"]:
        if col not in wide.columns:
            wide[col] = 0
    wide = wide.loc[wide.sum(axis=1).sort_values(ascending=False).index]
    return wide


def _draw_bar_chart(ax, filtered_data):
    wide = _build_bar_wide(filtered_data)
    
    groups = wide.index.to_series().fillna("Unknown").astype(str).tolist()
    approved_vals = wide["Approved"].to_numpy()
    not_approved_vals = wide["Not Approved"].to_numpy()
    totals = approved_vals + not_approved_vals
    y = np.arange(len(groups))

    bars_not = ax.barh(y, not_approved_vals, color=COLOR_NOTAPP, height=0.7)
    bars_app = ax.barh(y, approved_vals, left=not_approved_vals, color=COLOR_APPROVED, height=0.7)

    max_total = max(totals) if len(totals) > 0 else 0
    for i, total in enumerate(totals):
        if total > 0:
            ax.text(total + (max_total * 0.02), i, str(int(total)), ha='left', va='center', fontsize=9)

    ax.set_yticks(y, groups)
    ax.tick_params(axis='y', labelsize=10)
    ax.invert_yaxis()
    # Remove default axes margins so the first/last bars sit closer to the top/bottom.
    # (Matplotlib adds ~5% padding by default, which becomes a large visible gap on tall charts.)
    ax.set_ylim(len(groups) - 0.5, -0.5)
    for s in ax.spines.values(): s.set_visible(False)
    ax.grid(False)
    ax.tick_params(axis="both", which="both", length=0)
    ax.set_xticks([])
    ax.set_xlim(0, max_total * 1.15 if max_total > 0 else 1)
    # --- CORREÇÃO: Título em negrito e centrado restaurado ---
    ax.set_title("Assets by Group", fontsize=18, color=COLOR_APPROVED, pad=4, weight='bold', ha='center')
    return {
        "groups": groups,
        "bars_not": bars_not,
        "bars_app": bars_app,
        "approved_vals": approved_vals,
        "not_approved_vals": not_approved_vals,
    }

def _draw_qr_type_bar(ax, counts):
    """Vertical bars: distinct QR codes per discipline (ME / EL / BF)."""
    types = counts["Type"].tolist()
    values = counts["QTY"].to_numpy()
    colors = [QR_TYPE_COLORS.get(t, COLOR_APPROVED) for t in types]
    x = np.arange(len(types))
    ax.bar(x, values, color=colors, width=0.6)

    max_val = int(values.max()) if len(values) else 0
    for xi, val in zip(x, values):
        ax.text(xi, val + max(max_val * 0.03, 0.5), f"{int(val):,}",
                ha="center", va="bottom", fontsize=13, weight="bold", color=COLOR_APPROVED)

    ax.set_xticks(x, types)
    ax.tick_params(axis="x", labelsize=12, colors=COLOR_APPROVED, length=0)
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(False)
    ax.set_ylim(0, max_val * 1.22 if max_val > 0 else 1)
    ax.set_title("QR Codes by Asset Type", fontsize=16, weight="bold", color=COLOR_APPROVED, pad=20)


def _draw_pie_chart(ax, app_total, not_total):
    grand_total = app_total + not_total
    
    def make_autopct(values):
        def my_autopct(pct):
            total = sum(values)
            val = int(round(pct*total/100.0))
            return f'{val}\n({pct:.0f}%)'
        return my_autopct

    wedges, texts, autotexts = ax.pie(
        [app_total, not_total], labels=["", ""],
        colors=[COLOR_APPROVED, COLOR_NOTAPP], 
        autopct=make_autopct([app_total, not_total]),
        startangle=90, counterclock=False,
        wedgeprops={"width": 0.4, "edgecolor": 'none'},
        pctdistance=0.7, textprops={"fontsize": 11, "ha":'center'}, radius=0.8,
    )
    
    for a in autotexts:
        a.set_fontweight("bold")
        a.set_color("black")
    if autotexts and len(autotexts) > 0:
      autotexts[0].set_color("white")
      
    ax.axis("equal")
    ax.set_title("Overall Approval", fontsize=14, color=COLOR_APPROVED, pad=10)

# ---------- Main Rendering Function ----------
def _normalize_fmt(fmt) -> str:
    """Only 'svg' (vector, zoom-crisp) and 'png' (default) are supported."""
    return "svg" if str(fmt or "").strip().lower() == "svg" else "png"


def _fig_bytes(fig, ax, fmt: str) -> bytes:
    fig.patch.set_facecolor('none')
    ax.set_facecolor('none')
    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=OUTPUT_DPI, transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _error_fig_bytes(err, fmt: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.text(0.5, 0.5, f"Data error:\n{err}", ha="center", va='center', wrap=True)
    return _fig_bytes(fig, ax, fmt)


def render_chart_png(building: str = "All", chart_type: str = "all", status: str = "all", user=None, fmt: str = "png") -> bytes:
    fmt = _normalize_fmt(fmt)

    if chart_type == "qr_types":
        try:
            counts = _prepare_qr_type_counts(building=building, user=user)
            # Match the Chart.js bars: the capture universe includes Manual Entry.
            manual = _manual_excluded_by_type(building=building, user=user)
            counts["QTY"] = [int(q) + int(manual.get(str(t), 0))
                             for t, q in zip(counts["Type"], counts["QTY"])]
        except Exception as e:
            return _error_fig_bytes(e, fmt)
        fig, ax = plt.subplots(figsize=(6, 4.5))
        if int(counts["QTY"].sum()) == 0:
            ax.text(0.5, 0.5, "No data", ha="center")
        else:
            _draw_qr_type_bar(ax, counts)
        return _fig_bytes(fig, ax, fmt)

    try:
        df4 = _prepare_data(user)
        filtered = df4 if (building == "All" or not building) else df4[df4["Name"] == building]
    except Exception as e:
        return _error_fig_bytes(e, fmt)

    app_total = int(filtered.loc[filtered["Approved"] == "Approved", "QTY"].sum())
    not_total = int(filtered.loc[filtered["Approved"] == "Not Approved", "QTY"].sum())
    grand_total = app_total + not_total

    fig = None
    if chart_type == "gauge":
        fig, ax = plt.subplots(figsize=(6, 4.5))
        if grand_total == 0: ax.text(0.5, 0.5, "No data", ha="center")
        else: _draw_gauge_chart(ax, app_total, grand_total, 90)
    elif chart_type == "bar":
        status_label = _normalize_status_filter(status)
        bar_filtered = filtered
        if status_label:
            bar_filtered = bar_filtered[bar_filtered["Approved"] == status_label]

        num_groups = len(bar_filtered["Asset Group"].unique())
        fig_height = max(8, num_groups * 1.0) 
        fig_width = 7 
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        if bar_filtered.empty: ax.text(0.5, 0.5, "No data", ha="center")
        else: _draw_bar_chart(ax, bar_filtered)
    elif chart_type == "pie":
        fig, ax = plt.subplots(figsize=(5, 4))
        if grand_total == 0: ax.text(0.5, 0.5, "No data", ha="center")
        else: _draw_pie_chart(ax, app_total, not_total)

    if fig:
        return _fig_bytes(fig, ax, fmt)

    return b""


def render_bar_hitboxes(building: str = "All", status: str = "all", user=None) -> dict:
    """
    Returns per-row hitboxes (and segment hitboxes) for the bar chart ("Assets by Group"),
    in natural PNG pixel coordinates (origin: top-left).
    """
    try:
        df4 = _prepare_data(user)
        filtered = df4 if (building == "All" or not building) else df4[df4["Name"] == building]
        status_label = _normalize_status_filter(status)
        if status_label:
            filtered = filtered[filtered["Approved"] == status_label]
    except Exception as e:
        return {"error": str(e), "building": building, "status": status, "width": 0, "height": 0, "groups": []}

    num_groups = len(filtered["Asset Group"].unique()) if not filtered.empty else 0
    fig_height = max(8, num_groups * 1.0)
    fig_width = 7

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    try:
        if filtered.empty:
            ax.text(0.5, 0.5, "No data", ha="center")
            fig.patch.set_facecolor('none')
            ax.set_facecolor('none')
            fig.tight_layout(pad=0.5)
            fig.canvas.draw()
            width_px, height_px = fig.canvas.get_width_height()
            scale = OUTPUT_DPI / float(fig.dpi or 100.0)
            return {
                "building": building,
                "status": status_label or "All",
                "width": int(round(width_px * scale)),
                "height": int(round(height_px * scale)),
                "groups": [],
            }

        info = _draw_bar_chart(ax, filtered) or {}

        fig.patch.set_facecolor('none')
        ax.set_facecolor('none')
        fig.tight_layout(pad=0.5)
        fig.canvas.draw()

        width_px, height_px = fig.canvas.get_width_height()
        scale = OUTPUT_DPI / float(fig.dpi or 100.0)
        width_out = int(round(width_px * scale))
        height_out = int(round(height_px * scale))
        renderer = fig.canvas.get_renderer()

        def to_img_bbox(bbox):
            # matplotlib display coords are bottom-left origin; convert to image coords (top-left origin)
            x0, y0, x1, y1 = bbox.x0, bbox.y0, bbox.x1, bbox.y1
            ix0 = int(round(x0 * scale))
            ix1 = int(round(x1 * scale))
            iy0 = int(round((height_px - y1) * scale))
            iy1 = int(round((height_px - y0) * scale))
            return [ix0, iy0, ix1, iy1]

        pad_y = int(round(4 * scale))
        groups_out = []

        groups = info.get("groups") or []
        bars_not = (info.get("bars_not").patches if info.get("bars_not") is not None else [])
        bars_app = (info.get("bars_app").patches if info.get("bars_app") is not None else [])
        approved_vals = info.get("approved_vals")
        not_vals = info.get("not_approved_vals")

        for i, name in enumerate(groups):
            if i >= len(bars_not) or i >= len(bars_app):
                continue

            bbox_not = bars_not[i].get_window_extent(renderer)
            bbox_app = bars_app[i].get_window_extent(renderer)

            seg_not = to_img_bbox(bbox_not)
            seg_app = to_img_bbox(bbox_app)

            # Row bbox uses the union y-range, but spans full figure width for easier hover (labels included).
            uy0 = min(seg_not[1], seg_app[1])
            uy1 = max(seg_not[3], seg_app[3])
            row_bbox = [
                0,
                max(0, uy0 - pad_y),
                int(width_out),
                min(int(height_out), uy1 + pad_y),
            ]

            groups_out.append(
                {
                    "asset_group": str(name),
                    "counts": {
                        "approved": int(approved_vals[i]) if approved_vals is not None else 0,
                        "not_approved": int(not_vals[i]) if not_vals is not None else 0,
                    },
                    "row_bbox": row_bbox,
                    "segments": {"approved_bbox": seg_app, "not_approved_bbox": seg_not},
                }
            )

        return {
            "building": building,
            "status": status_label or "All",
            "width": int(width_out),
            "height": int(height_out),
            "groups": groups_out,
        }
    finally:
        plt.close(fig)
