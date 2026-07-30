import io
import sqlite3
import sys
from pathlib import Path
import db as qrdb  # backend-agnostic QR_codes DB layer

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

try:
    from . import completeness_score as shared
    from . import ai_confidence_score as ai_conf_shared
except ImportError:
    current_dir = Path(__file__).resolve().parent
    if str(current_dir) not in sys.path:
        sys.path.insert(0, str(current_dir))
    import completeness_score as shared
    import ai_confidence_score as ai_conf_shared


ASSET_TYPE_ORDER = {"BF": 0, "EL": 1, "ME": 2}
COMPLETENESS_COLOR = "#0055b7"
AI_CONFIDENCE_COLOR = "#7b2cbf"
TEXT_MAIN = "#002145"
TEXT_MUTED = "#6b7280"
GRID_COLOR = "#d9dee8"
ROW_FILL = "#f8fafc"
CONNECTOR_RGB = (107 / 255.0, 114 / 255.0, 128 / 255.0)


def _empty_state_png(building: str, process_scope: str, message: str = "No data for this chart") -> bytes:
    fig, ax = plt.subplots(figsize=(9, 3.8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")
    ax.text(0.5, 0.62, "Data Quality Comparison", ha="center", va="center",
            fontsize=18, fontweight="bold", color=TEXT_MAIN)
    ax.text(0.5, 0.46, message, ha="center", va="center",
            fontsize=12, color=TEXT_MUTED)
    ax.text(0.5, 0.31, f"{building}  |  Scope: {process_scope.title()}",
            ha="center", va="center", fontsize=10.5, color=TEXT_MUTED)
    buffer = io.BytesIO()
    plt.savefig(buffer, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


def _load_building_df():
    conn = qrdb.get_connection(sqlite_path=str(shared.DB_PATH))
    try:
        building_df = pd.read_sql_query('SELECT "Code", "Name" FROM "Buildings"', qrdb.raw_conn(conn))
    finally:
        conn.close()
    building_df = building_df.rename(columns={"Code": "building_number", "Name": "Property"})
    building_df["building_number"] = building_df["building_number"].astype(str)
    return building_df


def _row_metrics(payload: dict):
    completeness_row = shared._row_metrics(payload)
    if not completeness_row:
        return None

    avg_ai_conf = ai_conf_shared._extract_avg_ai_conf(payload)
    completeness_row["Avg_ai_conf"] = np.nan if avg_ai_conf is None else float(avg_ai_conf)
    return completeness_row


def _load_metric_rows(process_scope: str, user=None) -> pd.DataFrame:
    records = []
    user_qrs = None
    if user:
        try:
            from . import approval as _approval_mod
        except ImportError:
            import approval as _approval_mod
        user_qrs = _approval_mod._qrs_for_user(user)
    if shared.JSON_OUTPUT_DIR.exists():
        for json_path in shared.JSON_OUTPUT_DIR.iterdir():
            if not json_path.name.endswith(".json"):
                continue
            if not any(token in json_path.name for token in ("_ME_", "_BF_", "_EL_")):
                continue
            if user_qrs is not None:
                qr_prefix = json_path.name.split("_", 1)[0]
                if qr_prefix not in user_qrs:
                    continue
            payload = shared._read_json_payload(json_path)
            if not payload:
                continue
            row = _row_metrics(payload)
            if row:
                records.append(row)

    if not records:
        return pd.DataFrame()

    process_scope = shared._as_text(process_scope).lower()
    if process_scope not in {"open", "all"}:
        process_scope = "all"

    df = pd.DataFrame(records)
    df["building_number"] = df["building_number"].astype(str)
    if process_scope == "open":
        df = df[df["Approved"] == False].copy()
    return df


def _aggregate_chart_data(building: str, process_scope: str, user=None):
    metric_rows = _load_metric_rows(process_scope, user=user)
    if metric_rows.empty:
        return pd.DataFrame()

    building_df = _load_building_df()
    metric_rows = pd.merge(metric_rows, building_df, on="building_number", how="left")

    if building != "All":
        metric_rows = metric_rows[metric_rows["Property"] == building].copy()

    if metric_rows.empty:
        return pd.DataFrame()

    summary = metric_rows.groupby("asset_type", as_index=False).agg(
        field_qty=("field_qty", "sum"),
        found_value=("found_value", "sum"),
        avg_ai_conf=("Avg_ai_conf", "mean"),
    )
    summary["completeness_score"] = (
        (summary["found_value"] / summary["field_qty"].replace(0, np.nan)) * 100
    ).replace([np.inf, -np.inf], np.nan)

    summary["sort_order"] = summary["asset_type"].map(lambda value: ASSET_TYPE_ORDER.get(value, 999))
    summary = summary.sort_values(["sort_order", "asset_type"]).reset_index(drop=True)
    return summary


def _label_marker(ax, x, y, text, label_kind, counterpart=None):
    if counterpart is not None and not np.isnan(counterpart) and abs(x - counterpart) < 8:
        y_offset = 0.18 if label_kind == "completeness" else -0.18
        va = "bottom" if label_kind == "completeness" else "top"
        ax.text(x, y + y_offset, text, ha="center", va=va, fontsize=10,
                color=TEXT_MAIN, fontweight="600", zorder=5)
        return

    if counterpart is not None and not np.isnan(counterpart):
        if label_kind == "completeness":
            place_left = x <= counterpart
        else:
            place_left = x > counterpart
    else:
        place_left = label_kind == "completeness"

    x_offset = -2.6 if place_left else 2.6
    ha = "right" if place_left else "left"
    if place_left:
        label_x = max(1.5, x + x_offset)
    else:
        label_x = min(98.5, x + x_offset)
    ax.text(label_x, y, text, ha=ha, va="center", fontsize=10,
            color=TEXT_MAIN, fontweight="600", zorder=5)


def render_chart_png(building: str = "All", process_scope: str = "all", user=None) -> bytes:
    process_scope = shared._as_text(process_scope).lower()
    if process_scope not in {"open", "all"}:
        process_scope = "all"

    try:
        chart_data = _aggregate_chart_data(building=building, process_scope=process_scope, user=user)
    except Exception as exc:
        print(f"Data quality chart build error for '{building}': {exc}")
        return _empty_state_png(building, process_scope, "No data for this chart")

    if chart_data.empty:
        return _empty_state_png(building, process_scope, "No data for this chart")

    row_count = len(chart_data)
    fig_height = max(4.0, 1.35 * row_count + 1.9)
    fig, ax = plt.subplots(figsize=(10.5, fig_height))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    y_positions = np.arange(row_count)

    for y in y_positions:
        ax.axhspan(y - 0.34, y + 0.34, color=ROW_FILL, zorder=0)

    ax.set_xlim(0, 100)
    ax.set_ylim(-0.65, row_count - 0.35)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(chart_data["asset_type"], fontsize=12, fontweight="bold", color=TEXT_MAIN)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels([f"{tick}%" for tick in [0, 25, 50, 75, 100]], fontsize=10, color=TEXT_MUTED)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID_COLOR, linewidth=1, linestyle="--", alpha=0.8)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", colors=TEXT_MUTED)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(GRID_COLOR)

    for idx, row in chart_data.iterrows():
        y = y_positions[idx]
        completeness_score = float(row["completeness_score"]) if pd.notna(row["completeness_score"]) else np.nan
        ai_conf_score = float(row["avg_ai_conf"]) if pd.notna(row["avg_ai_conf"]) else np.nan

        if not np.isnan(completeness_score) and not np.isnan(ai_conf_score):
            gap = abs(completeness_score - ai_conf_score)
            connector_alpha = min(0.9, 0.25 + gap / 110.0)
            connector_width = 2.0 + gap / 60.0
            ax.hlines(
                y,
                min(completeness_score, ai_conf_score),
                max(completeness_score, ai_conf_score),
                color=(*CONNECTOR_RGB, connector_alpha),
                linewidth=connector_width,
                zorder=1,
            )

        if not np.isnan(completeness_score):
            ax.scatter(completeness_score, y, s=110, marker="o", color=COMPLETENESS_COLOR,
                       edgecolor="white", linewidth=1.8, zorder=3)
            completeness_text = f"{int(round(completeness_score))}%"
            _label_marker(ax, completeness_score, y, completeness_text, "completeness", counterpart=ai_conf_score)
        else:
            ax.text(2.5, y + 0.2, "Completeness: N/A", ha="left", va="bottom", fontsize=10,
                    color=TEXT_MUTED, fontweight="600", zorder=5)

        if not np.isnan(ai_conf_score):
            ax.scatter(ai_conf_score, y, s=120, marker="D", color=AI_CONFIDENCE_COLOR,
                       edgecolor="white", linewidth=1.8, zorder=4)
            confidence_text = f"{ai_conf_score:.1f}%"
            _label_marker(ax, ai_conf_score, y, confidence_text, "confidence", counterpart=completeness_score)
        else:
            ax.text(99.2, y - 0.2, "AI Confidence: N/A", ha="right", va="top", fontsize=10,
                    color=TEXT_MUTED, fontweight="600", zorder=5)

    legend_handles = [
        Line2D([0], [0], marker="o", color="none", label="Completeness",
               markerfacecolor=COMPLETENESS_COLOR, markeredgecolor="white", markeredgewidth=1.8, markersize=10),
        Line2D([0], [0], marker="D", color="none", label="AI Confidence",
               markerfacecolor=AI_CONFIDENCE_COLOR, markeredgecolor="white", markeredgewidth=1.8, markersize=9),
    ]
    fig.suptitle(
        f"Data Quality Comparison for: {building}",
        fontsize=16,
        fontweight="bold",
        color=TEXT_MAIN,
        y=0.98,
    )
    ax.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.085),
        ncol=2,
        frameon=False,
        fontsize=10,
        borderaxespad=0.0,
        handletextpad=0.45,
        columnspacing=1.4,
    )

    buffer = io.BytesIO()
    plt.tight_layout(rect=[0, 0, 1, 0.89])
    plt.savefig(buffer, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()
