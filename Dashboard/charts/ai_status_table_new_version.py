import sqlite3
import pandas as pd
import os
import re
from datetime import datetime
import db as qrdb  # backend-agnostic QR_codes DB layer

# Define the database path in one place for easy access
DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"
ROOT_DEV_PATH = os.environ.get("DEV_PATH", "/home/developer")
JSON_OUTPUT_DIR = os.environ.get("JSON_OUTPUT_DIR", os.path.join(ROOT_DEV_PATH, "Output_jason_api"))
VALID_QR_CODE_PATTERN = re.compile(r"^(?:T\d+|\d+)$", re.IGNORECASE)
INVALID_TEXT_VALUES = {"", "none", "null", "nan", "undefined"}


def _normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()

def update_ai_status_in_db(assets_df, db_path):
    """
    Updates the 'ai_status' column in the QR_codes table based on the QR_code_ID.
    """
    if 'ai_status' not in assets_df.columns or 'code' not in assets_df.columns:
        print("❌ Error: DataFrame for DB update is missing 'ai_status' or 'code' (QR_code_ID) columns.")
        return

    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=db_path)
        cursor = conn.cursor()
        
        update_data = [
            (int(status), qr_id) 
            for status, qr_id in assets_df[['ai_status', 'code']].to_records(index=False)
        ]
        
        cursor.executemany('UPDATE "QR_codes" SET "ai_status" = ? WHERE "QR_code_ID" = ?', update_data)

        conn.commit()
        print(f"✅ Successfully updated 'ai_status' for {cursor.rowcount} records in the 'QR_codes' table.")

    except Exception as e:   # broadened from sqlite3.Error so PG (psycopg2) errors are caught too
        print(f"❌ Database error during AI status update in 'QR_codes' table: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

def get_processed_assets(db_path):
    """
    Loads and processes the QR_code_assets table to get a unique list of assets.
    """
    table_name = "QR_code_assets"
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=db_path)
        df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', qrdb.raw_conn(conn))
        
        if df.empty:
            print("⚠️ Warning: QR_code_assets table is empty.")
            return df
        
        split_columns = (
            df['code_assets']
            .fillna('')
            .str.split(' ', n=2, expand=True)
            .reindex(columns=range(3), fill_value='')
        )
        df[['code', 'building_code', 'type_of_asset']] = split_columns
        df['code'] = df['code'].map(_normalize_text)
        df['building_code'] = df['building_code'].map(_normalize_text)
        df['type_of_asset'] = (
            df['type_of_asset']
            .map(_normalize_text)
            .str.extract('([a-zA-Z]+)', expand=False)
            .fillna('')
        )

        asset_type_map = {"ME": "Mechanical", "BF": "Backflow", "EL": "Electrical"}
        df['type_of_asset'] = df['type_of_asset'].replace(asset_type_map)

        valid_mask = (
            df['code'].str.match(VALID_QR_CODE_PATTERN, na=False)
            & ~df['building_code'].str.lower().isin(INVALID_TEXT_VALUES)
            & ~df['type_of_asset'].str.lower().isin(INVALID_TEXT_VALUES)
        )
        dropped_rows = int((~valid_mask).sum())
        if dropped_rows:
            print(f"WARNING: Skipping {dropped_rows} invalid QR_code_assets rows with placeholder or malformed identifiers.")
        df = df.loc[valid_mask].copy()
        
        unique_assets_df = df.drop_duplicates(subset=['code', 'building_code', 'type_of_asset'], keep='first').copy()
        print(f"🔍 Diagnostic: Found {len(unique_assets_df)} unique assets after aggregation.")
        return unique_assets_df

    except Exception as e:
        print(f"❌ Error processing asset data: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_json_file_data(db_path):
    """
    Reads only the 'code' column from the json_files table to identify processed assets.
    """
    table_name = "json_files"
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=db_path)
        query = f'SELECT "code" FROM {table_name}'
        df = pd.read_sql_query(query, qrdb.raw_conn(conn))

        if df.empty:
            print("⚠️ Warning: json_files table is empty.")
            return df
            
        # Ensure code is string and stripped immediately
        df['code'] = df['code'].astype(str).str.strip()
        return df
    except Exception as e:
        print(f"❌ Error reading {table_name}: {e}")
        return pd.DataFrame(columns=['code'])
    finally:
        if conn:
            conn.close()

def get_json_codes_from_directory(json_dir):
    """
    Reads live JSON files from disk and returns a set of processed QR codes.
    This is the source of truth for ai_status assignment.
    """
    if not os.path.exists(json_dir):
        print(f"⚠️ Warning: JSON directory not found: {json_dir}")
        return set()

    code_pattern = re.compile(r"^(T\d+|\d+)")
    codes = set()
    try:
        for filename in os.listdir(json_dir):
            if not filename.lower().endswith(".json"):
                continue
            stem = os.path.splitext(filename)[0]
            match = code_pattern.match(stem)
            if not match:
                continue
            codes.add(str(match.group(1)).strip())
    except Exception as e:
        print(f"❌ Error scanning JSON directory '{json_dir}': {e}")
        return set()

    print(f"🔍 Diagnostic: Found {len(codes)} live JSON codes in directory '{json_dir}'.")
    return codes

def get_building_data(db_path):
    """
    Reads the 'Code' and 'Name' columns from the 'Buildings' table.
    """
    table_name = "Buildings"
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=db_path)
        query = f'SELECT "Code", "Name" FROM "{table_name}"'   # PG: mixed-case "Buildings" must be quoted
        df = pd.read_sql_query(query, qrdb.raw_conn(conn))
        return df
    except Exception as e:
        print(f"❌ Error reading {table_name}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_qr_codes_data(db_path):
    """
    Reads 'QR_code_ID', 'Location', and 'date_set' from the 'QR_codes' table.
    """
    table_name = "QR_codes"
    conn = None
    try:
        conn = qrdb.get_connection(sqlite_path=db_path)
        query = f'SELECT "QR_code_ID", "Location", "date_set" FROM "{table_name}"'   # PG: mixed-case "QR_codes" must be quoted
        df = pd.read_sql_query(query, qrdb.raw_conn(conn))
        
        if not df.empty:
            df['QR_code_ID'] = df['QR_code_ID'].astype(str).str.strip()
        return df
    except Exception as e:
        print(f"❌ Error reading {table_name}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_pending_assets():
    """
    Main function to orchestrate data loading, processing, and returning
    the summary and detailed pending asset dataframes.
    """
    all_assets_df = get_processed_assets(DB_PATH) 
    building_df = get_building_data(DB_PATH)
    qr_codes_df = get_qr_codes_data(DB_PATH)

    if all_assets_df is None or building_df is None or qr_codes_df is None:
        print("❌ One or more DataFrames failed to load. Aborting.")
        return None, None
        
    if all_assets_df.empty:
        print("ℹ️ No assets found in the source table. Returning empty results.")
        return pd.DataFrame(), pd.DataFrame()

    # --- MATCHING LOGIC ---
    # Assign ai_status based only on live JSON files currently on disk.
    json_codes = get_json_codes_from_directory(JSON_OUTPUT_DIR)
    
    all_assets_df['ai_status'] = all_assets_df['code'].isin(json_codes).astype(int)
    
    # Optional debug print to check specific asset
    if '0000183850' in all_assets_df['code'].values:
        status = all_assets_df.loc[all_assets_df['code'] == '0000183850', 'ai_status'].values[0]
        in_json = '0000183850' in json_codes
        print(f"DEBUG: Asset 0000183850 - Found in Asset Table. In JSON Set? {in_json}. Assigned Status: {status}")

    update_ai_status_in_db(all_assets_df, DB_PATH)
    
    merged_df = pd.merge(
        left=all_assets_df,
        right=building_df,
        how="left",
        left_on="building_code",
        right_on="Code"
    )
    
    merged_df = pd.merge(
        left=merged_df,
        right=qr_codes_df,
        how="left",
        left_on="code",
        right_on="QR_code_ID"
    )
    
    merged_df = merged_df.drop(columns=['Code', 'QR_code_ID'], errors='ignore')
    
    rename_map = {
        'code': 'QR_code_ID',
        'building_code': 'Code',
        'Name': 'Property', 
        'type_of_asset': 'Type of Asset',
        'date_set': 'Creation Date'
    }
    assets_with_status = merged_df.rename(columns=rename_map)
    
    status_map = {0: 'Pending', 1: 'Processed'}
    if 'ai_status' in assets_with_status.columns:
        assets_with_status['ai_status'] = assets_with_status['ai_status'].map(status_map)

    ai_asset_details = assets_with_status[assets_with_status['ai_status'] == 'Pending'].copy()
    
    print(f"🔍 Diagnostic: Found {len(ai_asset_details)} pending assets after filtering.")
    
    if ai_asset_details.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Format date and create new 'Time' column
    if 'Creation Date' in ai_asset_details.columns:
        dt_series = pd.to_datetime(ai_asset_details['Creation Date'], errors='coerce')
        ai_asset_details['Time'] = dt_series.dt.strftime('%I:%M:%S %p').str.lstrip('0')
        ai_asset_details['Creation Date'] = dt_series.dt.strftime('%m/%d/%Y')

    ai_asset_summary = ai_asset_details.groupby('Property').size().reset_index(name='Pendency QTY')
    
    desired_order = ['QR_code_ID', 'Property', 'Location', 'Type of Asset', 'Creation Date', 'Time', 'ai_status']
    existing_columns_in_order = [col for col in desired_order if col in ai_asset_details.columns]
    ai_asset_details = ai_asset_details[existing_columns_in_order]
    ai_asset_details = ai_asset_details.where(pd.notna(ai_asset_details), '')
    
    if 'Creation Date' in ai_asset_details.columns:
        ai_asset_details['Creation Date'] = ai_asset_details['Creation Date'].fillna('')
    if 'Time' in ai_asset_details.columns:
        ai_asset_details['Time'] = ai_asset_details['Time'].fillna('')
    
    return ai_asset_summary, ai_asset_details

if __name__ == "__main__":
    summary_df, details_df = get_pending_assets()

    if summary_df is not None and details_df is not None:
        print("✅ Data processing complete.")
        
        if not summary_df.empty:
            print("\n--- First 5 Rows of the 'ai_asset_summary' (Summary) Table ---")
            print(summary_df.head())
        else:
            print("\n--- Summary table is empty. ---")

        if not details_df.empty:
            print("\n--- First 5 Rows of the 'ai_asset_details' (Details) Table ---")
            print(details_df.head())
        else:
            print("\n--- Details table is empty. ---")
    else:
        print("❌ Failed to get pending asset data.")
