import io
import json
import os
import sqlite3
from pathlib import Path
import db as qrdb  # backend-agnostic QR_codes DB layer

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

ROOT_DIR = Path(__file__).resolve().parents[2]
DEV_ROOT = Path(os.environ.get("DEV_PATH", "/home/developer"))
VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _env_path(name: str):
    value = os.environ.get(name)
    return Path(value) if value else None


def _resolve_path(*candidates: Path) -> Path:
    usable = [Path(candidate) for candidate in candidates if candidate]
    return next((candidate for candidate in usable if candidate.exists()), usable[0])


DB_PATH = _resolve_path(
    _env_path("QR_CODES_DB_PATH"),
    _env_path("DB_PATH"),
    ROOT_DIR / "asset_capture_app_dev" / "data" / "QR_codes.db",
    DEV_ROOT / "asset_capture_app_dev" / "data" / "QR_codes.db",
)

JSON_OUTPUT_DIR = _resolve_path(
    _env_path("JSON_OUTPUT_DIR"),
    ROOT_DIR / "Output_jason_api",
    DEV_ROOT / "Output_jason_api",
)

PHOTO_UPLOAD_DIR = _resolve_path(
    _env_path("PHOTO_UPLOAD_DIR"),
    ROOT_DIR / "Capture_photos_upload",
    DEV_ROOT / "Capture_photos_upload",
)


def _as_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_value(value) -> bool:
    return bool(_as_text(value))


def _normalize_asset_type(value) -> str:
    return _as_text(value).replace("-", "").strip().upper()


def _normalize_building(value) -> str:
    return _as_text(value)


def _normalize_qr(value) -> str:
    return _as_text(value)


def _coerce_approved(value) -> bool:
    if isinstance(value, bool):
        return value
    return _as_text(value).lower() in {"1", "true", "yes", "y"}


def _structured_data(payload: dict) -> dict:
    data = payload.get("structured_data")
    return data if isinstance(data, dict) else payload


def _read_json_payload(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _has_me_seq3_photo(qr_code: str, building_number: str) -> bool:
    if not PHOTO_UPLOAD_DIR.exists():
        return False
    stem = f"{qr_code} {building_number} ME - 3"
    for ext in VALID_IMAGE_EXTS:
        if (PHOTO_UPLOAD_DIR / f"{stem}{ext}").exists():
            return True
    return False


def _el_supply_from(data: dict) -> str:
    return (
        _as_text(data.get("Supply From"))
        or _as_text(data.get("Fed From"))
        or _as_text(data.get("Fed"))
    )


def _row_metrics(payload: dict):
    structured = _structured_data(payload)
    asset_type = _normalize_asset_type(payload.get("asset_type") or structured.get("asset_type"))
    building_number = _normalize_building(payload.get("building_number"))
    qr_code = _normalize_qr(payload.get("qr_code"))
    approved = _coerce_approved(payload.get("Approved")) or _coerce_approved(structured.get("Approved"))

    if asset_type == "BF":
        required_fields = [
            _as_text(structured.get("Manufacturer")),
            _as_text(structured.get("Model")),
            _as_text(structured.get("Serial Number")),
            _as_text(structured.get("Diameter")),
        ]
    elif asset_type == "EL":
        required_fields = [
            _as_text(structured.get("UBC Asset Tag")),
            _as_text(structured.get("Ampere")),
            _el_supply_from(structured),
        ]
    elif asset_type == "ME":
        required_fields = [
            _as_text(structured.get("Manufacturer")),
            _as_text(structured.get("Model")),
            _as_text(structured.get("Serial Number")),
            _as_text(structured.get("Year")),
            _as_text(structured.get("UBC Tag")),
        ]
        has_tsbc_source = _has_me_seq3_photo(qr_code, building_number)
        if not has_tsbc_source and _has_value(structured.get("Technical Safety BC")):
            has_tsbc_source = True
        if has_tsbc_source:
            required_fields.append(_as_text(structured.get("Technical Safety BC")))
    else:
        return None

    field_qty = len(required_fields)
    found_value = sum(1 for value in required_fields if _has_value(value))
    return {
        "building_number": building_number,
        "asset_type": asset_type,
        "Approved": approved,
        "field_qty": float(field_qty),
        "found_value": float(found_value),
    }


def render_chart_png(building: str = "All", process_scope: str = "all") -> bytes:
    """
    Renders the completeness score chart for a given building and returns it as PNG bytes.
    """
    try:
        conn = qrdb.get_connection(sqlite_path=str(DB_PATH))
        query = 'SELECT * FROM "Buildings"'
        building_df = pd.read_sql_query(query, qrdb.raw_conn(conn))
        conn.close()
        building_df = building_df[["Code", "Name"]]
        building_df = building_df.rename(columns={"Code": "building_number", "Name": "Property"})
        building_df["building_number"] = building_df["building_number"].astype(str)
    except Exception as e:
        print(f"CRITICAL: Could not read buildings from database. {e}")
        return b""

    records = []
    if JSON_OUTPUT_DIR.exists():
        for json_path in JSON_OUTPUT_DIR.iterdir():
            if not json_path.name.endswith(".json"):
                continue
            if not any(token in json_path.name for token in ("_ME_", "_BF_", "_EL_")):
                continue
            payload = _read_json_payload(json_path)
            if not payload:
                continue
            row = _row_metrics(payload)
            if row:
                records.append(row)

    if not records:
        return b""

    process_scope = _as_text(process_scope).lower()
    if process_scope not in {"open", "all"}:
        process_scope = "all"

    completeness_df = pd.DataFrame(records)
    completeness_df["building_number"] = completeness_df["building_number"].astype(str)
    if process_scope == "open":
        completeness_df = completeness_df[completeness_df["Approved"] == False]

    final_summary = completeness_df.groupby(["building_number", "asset_type"]).agg(
        field_qty=("field_qty", "sum"),
        found_value=("found_value", "sum"),
    ).reset_index()

    merged_df = pd.merge(final_summary, building_df, on="building_number", how="left")
    completeness_summary = merged_df[["Property", "building_number", "asset_type", "field_qty", "found_value"]].copy()
    completeness_summary.loc[:, "% of Completeness"] = (
        (completeness_summary["found_value"] / completeness_summary["field_qty"].replace(0, 1)) * 100
    ).replace([np.inf, -np.inf], 0).fillna(0).round(2)

    if building != "All":
        data_to_plot = completeness_summary[completeness_summary["Property"] == building].sort_values("asset_type")
    else:
        if not completeness_summary.empty:
            consolidated_data = completeness_summary.groupby("asset_type").agg(
                field_qty=("field_qty", "sum"),
                found_value=("found_value", "sum"),
            ).reset_index()
            consolidated_data.loc[:, "% of Completeness"] = (
                (consolidated_data["found_value"] / consolidated_data["field_qty"].replace(0, 1)) * 100
            ).replace([np.inf, -np.inf], 0).fillna(0).round(2)
            data_to_plot = consolidated_data.sort_values("asset_type")
        else:
            data_to_plot = pd.DataFrame()

    if data_to_plot.empty:
        return b""

    num_charts = len(data_to_plot)
    fig, axes = plt.subplots(1, num_charts, figsize=(num_charts * 2.5, 5), squeeze=False)
    axes = axes.flatten()
    cmap = LinearSegmentedColormap.from_list("custom_RdYlGn", ["#008000", "#fcf300", "#9d0208"])

    for i, (_, row) in enumerate(data_to_plot.iterrows()):
        ax = axes[i]
        asset_type, score = row["asset_type"], row["% of Completeness"]
        gradient = np.linspace(0, 1, 256).reshape(-1, 1)
        ax.imshow(gradient, aspect="auto", cmap=cmap, extent=[0, 1, 0, 100])
        ax.axhline(score, color="black", lw=1.5)
        ax.text(1.1, score, f"{int(round(score))}", va="center", ha="left", fontsize=12, fontweight="bold")
        ax.set_title(asset_type, fontsize=14, fontweight="bold")
        ax.set_ylim(0, 100)
        ax.set_xticks([])
        ax.spines[["top", "right", "bottom"]].set_visible(False)

    fig.suptitle(f"Completeness Score for: {building}", fontsize=16, fontweight="bold", y=1.05)
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    buffer = io.BytesIO()
    plt.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)

    return buffer.getvalue()
