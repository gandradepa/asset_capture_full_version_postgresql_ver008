"""
track_assets.py

Build the ``life_cycle`` pandas DataFrame from "UBC - Asset Basic Info.xlsx".

Rules:
  1. Keep only a fixed subset of columns (see KEEP_COLUMNS).
  2. Every kept column is a string, except Installation Date, which is
     formatted as MM/DD/YYYY.
  3. Keep only rows where Asset Group Code == "ME.91.902.4817.5956".
  4. Add two age columns derived from Installation Date relative to today:
     ``years`` = (today - Installation Date) / 360 as a float rounded to 2
     decimals, and ``months`` = round((today - Installation Date) / 30) as an
     integer (nearest, ties up, e.g. 139.5 -> 140). Both are NULL when
     Installation Date is missing. (These reflect age as of when the script runs.)
  5. Add a ``classification`` column from years: years <= 8 -> "G",
     8 < years < 10 -> "Y", years >= 10 -> "R"; NULL when years is NULL.
  6. Join ``Floor Name`` from the SpaceUID table on
     (Property Code = SpaceUID."Property.Property code",
      Space Number = SpaceUID."Space number"); placed after Space Name,
     NULL when there is no match.
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd

# Source workbook lives alongside this script.
SOURCE_FILE = Path(__file__).with_name("UBC - Asset Basic Info.xlsx")

# Only assets in this group are kept.
ASSET_GROUP_CODE = "ME.91.902.4817.5956"  # Heating Water Storage Tanks

# Columns to keep, in this order.
KEEP_COLUMNS = [
    "Code",
    "Description",
    "Status",
    "Asset Group",
    "Asset Group Name",
    "Asset Tag",
    "Make",
    "Model",
    "Product Version",
    "Serial Number",
    "Property code",
    "Property Name",
    "Space Number",
    "Space Name",
    "GPS Coordinates (lat,long)",
    "Installation Date",
]

# Formatted as MM/DD/YYYY; everything else stays a string.
DATE_COLUMNS = [
    "Installation Date",
]

# Source column name -> canonical output name. "UBC - Asset Basic Info.xlsx"
# names a few columns differently from the rest of the pipeline / table.
RENAME_COLUMNS = {
    "Code": "QR Code",
    "Asset Group": "Asset Group Code",
    "Property code": "Property Code",
}

# This column is split on the comma into Latitude / Longitude, then dropped.
GPS_COLUMN = "GPS Coordinates (lat,long)"

# Floor Name is joined in from the SpaceUID table on
# (Property Code = SpaceUID."Property.Property code", Space Number = SpaceUID."Space number").
SPACEUID_DSN = os.environ.get(
    "LIFE_CYCLE_SA_DSN",
    "postgresql+psycopg2://postgres@localhost:5432/qr_code_db_sandbox",
)


def _floor_name_lookup() -> pd.DataFrame:
    """Return one Floor Name per (Property Code, Space Number) from SpaceUID.

    SpaceUID's (Property.Property code, Space number) key is not unique, so
    DISTINCT ON collapses it to a single Floor Name per key. This guarantees the
    left join below can never multiply life_cycle rows.
    """
    from sqlalchemy import create_engine

    sql = (
        'SELECT DISTINCT ON ("Property.Property code", "Space number") '
        '       "Property.Property code" AS "Property Code", '
        '       "Space number"           AS "Space Number", '
        '       "Floor Name" '
        'FROM "SpaceUID" '
        'ORDER BY "Property.Property code", "Space number", "Floor Name"'
    )
    engine = create_engine(SPACEUID_DSN)
    try:
        return pd.read_sql(sql, engine)
    finally:
        engine.dispose()


def _strip_text(series: pd.Series) -> pd.Series:
    """Trim whitespace from string entries, leaving NaN untouched.

    Always returns an object (string) dtype Series so the column stays
    string-typed even when every value is missing.
    """
    return series.map(lambda v: v.strip() if isinstance(v, str) else v).astype(object)


def build_life_cycle(source=None) -> pd.DataFrame:
    """Read the workbook and return the filtered/typed DataFrame.

    ``source`` is the Excel workbook to read: a filesystem path or any
    file-like object (e.g. an ``io.BytesIO`` holding an uploaded file). It
    defaults to SOURCE_FILE so the CLI keeps reading the workbook that sits
    next to this script, while callers (e.g. the dashboard's refresh button)
    can point the pipeline at a file the user just chose.
    """
    # Read everything as text so nothing is implicitly coerced to numbers/dates.
    df = pd.read_excel(
        source if source is not None else SOURCE_FILE,
        sheet_name=0,
        usecols=KEEP_COLUMNS,
        dtype=str,
    )

    # Enforce the requested column order.
    df = df[KEEP_COLUMNS]

    # Normalize source column names to canonical output names up front, so the
    # filter and derived columns below all reference the canonical names.
    df = df.rename(columns=RENAME_COLUMNS)

    # Keep only the target asset group.
    df = df[df["Asset Group Code"] == ASSET_GROUP_CODE].copy()

    # Parse the date columns from their source values and render as MM/DD/YYYY.
    for col in DATE_COLUMNS:
        parsed = pd.to_datetime(df[col], errors="coerce")
        df[col] = parsed.dt.strftime("%m/%d/%Y")

    # Split GPS coordinates into Latitude / Longitude (on the comma), put the
    # two new columns where the GPS column was, then drop the original.
    gps_parts = df[GPS_COLUMN].str.split(",", n=1, expand=True).reindex(columns=[0, 1])
    insert_at = KEEP_COLUMNS.index(GPS_COLUMN)
    df.insert(insert_at, "Latitude", _strip_text(gps_parts[0]))
    df.insert(insert_at + 1, "Longitude", _strip_text(gps_parts[1]))
    df = df.drop(columns=[GPS_COLUMN])

    # Join Floor Name from SpaceUID on (Property Code, Space Number); left join
    # keeps every row (NULL Floor Name when unmatched). Placed after Space Name.
    df = df.merge(_floor_name_lookup(), on=["Property Code", "Space Number"], how="left")
    pos = list(df.columns).index("Space Name") + 1
    df.insert(pos, "Floor Name", df.pop("Floor Name"))

    # Derive age columns from Installation Date relative to today, using the
    # day difference (today - Installation Date). Missing Installation Date
    # -> NULL years/months. Int64 keeps months an integer while allowing NULL.
    today = pd.Timestamp.now().normalize()
    install_dt = pd.to_datetime(df["Installation Date"], format="%m/%d/%Y", errors="coerce")
    age_days = (today - install_dt).dt.days
    # years: float rounded to 2 decimals. months: nearest integer, ties up (floor(x+0.5)).
    df["years"] = (age_days / 360).round(2)
    df["months"] = np.floor(age_days / 30 + 0.5).astype("Int64")

    # Classify by years: <=8 -> G, (8,10) exclusive -> Y, >=10 -> R.
    # NaN years never satisfy a comparison, so they stay NULL.
    years = df["years"]
    classification = pd.Series(pd.NA, index=df.index, dtype="object")
    classification[years <= 8] = "G"
    classification[(years > 8) & (years < 10)] = "Y"
    classification[years >= 10] = "R"
    df["classification"] = classification

    return df.reset_index(drop=True)


if __name__ == "__main__":
    # Build from the default workbook only when run directly, so importing this
    # module (e.g. from the dashboard) has no side effects and never reads a
    # file the caller didn't ask for.
    life_cycle = build_life_cycle()
    print(
        f"life_cycle: {life_cycle.shape[0]} rows x "
        f"{life_cycle.shape[1]} columns "
        f"(Asset Group Code == {ASSET_GROUP_CODE})"
    )
    print(life_cycle.to_string(index=False))
