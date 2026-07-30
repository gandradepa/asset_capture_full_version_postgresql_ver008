import sqlite3
import pandas as pd
from pathlib import Path
import db as qrdb  # backend-agnostic QR_codes DB layer

# Columns to keep from asset tables (Building retained for the join only)
ASSET_COLS = [
    "QR Code",
    "Description",
    "UBC Tag",
    "Asset Group",
    "Space",
    "Building",
]

# Final output columns expected after the Buildings join
OUTPUT_COLS = [
    "QR Code",
    "Description",
    "UBC Tag",
    "Asset Group",
    "Space",
    "Property",
]

def _load_and_trim_assets(conn: sqlite3.Connection, table: str) -> pd.DataFrame:
    """Load an asset table, normalize column names, and keep only ASSET_COLS."""
    df = pd.read_sql_query(f'SELECT * FROM "{table}";', qrdb.raw_conn(conn))
    df.columns = df.columns.astype(str).str.strip()
    missing = [c for c in ASSET_COLS if c not in df.columns]
    if missing:
        print(f"WARNING [{table}]: Missing expected columns: {missing}")
    keep = [c for c in ASSET_COLS if c in df.columns]
    if not keep:
        raise ValueError(f"No requested columns found in table '{table}'.")
    return df[keep].copy()

def _load_buildings(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load Buildings table and trim column-name whitespace."""
    bldg = pd.read_sql_query('SELECT * FROM "Buildings";', qrdb.raw_conn(conn))
    bldg.columns = bldg.columns.astype(str).str.strip()
    # Ensure the key exists
    if "Code" not in bldg.columns:
        raise ValueError("Expected 'Code' column not found in Buildings table.")
    return bldg

def map_new_assets_all(db_path: str) -> pd.DataFrame:
    """
    Loads assets (current + archived), keeps key columns, concatenates,
    then LEFT-joins Buildings on Building (assets) == Code (Buildings).
    Returns the merged DataFrame.
    """
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(f"DB not found at: {db_file}")

    with qrdb.get_connection(sqlite_path=str(db_file)) as conn:
        # Load and trim both asset tables
        current_assets = _load_and_trim_assets(conn, "sdi_print_out")
        arch_assets = _load_and_trim_assets(conn, "sdi_print_out_arch")

        # Combine assets
        assets = pd.concat([current_assets, arch_assets], ignore_index=True)

        # Load Buildings
        buildings = _load_buildings(conn)

    if "Building" not in assets.columns:
        raise ValueError("Expected 'Building' column not found in asset data.")

    # Left-join: assets left, buildings right
    merged = assets.merge(
        buildings,
        how="left",
        left_on="Building",
        right_on="Code",
        suffixes=("", "_bldg"),  # avoid collisions if any
    )

    # Rename building name to Property and keep only the requested output columns
    if "Name" in merged.columns:
        merged = merged.rename(columns={"Name": "Property"})
    else:
        print("WARNING: Expected 'Name' column not found in Buildings data.")

    missing_final = [c for c in OUTPUT_COLS if c not in merged.columns]
    if missing_final:
        print(f"WARNING: Missing expected output columns after merge: {missing_final}")
    keep_final = [c for c in OUTPUT_COLS if c in merged.columns]
    if not keep_final:
        raise ValueError("No requested output columns found after merge.")

    return merged[keep_final].copy()

# --- Example usage ---
if __name__ == "__main__":
    DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"
    df = map_new_assets_all(DB_PATH)
    print("Merged shape:", df.shape)
    print(df.info())
