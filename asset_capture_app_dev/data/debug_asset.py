import sqlite3
import pandas as pd
import os

# Path to your database
DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"
TARGET_ASSET_ID = "183797" # We search for the core ID to catch both 183797 and 0000183797

def diagnose_db():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        return

    print(f"--- Diagnosing Asset: {TARGET_ASSET_ID} ---")
    
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Check QR_code_assets (Source of Truth)
    print("\n1. Checking 'QR_code_assets' table...")
    try:
        # Fetch rows that resemble the ID
        query = f"SELECT * FROM QR_code_assets WHERE code_assets LIKE '%{TARGET_ASSET_ID}%'"
        df_assets = pd.read_sql_query(query, conn)
        
        if df_assets.empty:
            print("   ❌ Asset NOT FOUND in QR_code_assets table.")
        else:
            print(f"   ✅ Found {len(df_assets)} row(s).")
            for i, row in df_assets.iterrows():
                raw_val = row['code_assets']
                print(f"   Row {i} Raw Value: '{raw_val}'")
                
                # Test the current split logic (Split by single space)
                split_space = str(raw_val).split(' ', 2)
                print(f"      -> Current Logic (split(' ')): {split_space}")
                
                # Test robust split logic (Split by any whitespace)
                split_robust = str(raw_val).split(None, 2)
                print(f"      -> Robust Logic  (split()):    {split_robust}")
                
                if len(split_space) > 0:
                    print(f"      -> Extracted Code: '{split_space[0]}'")
    except Exception as e:
        print(f"   ❌ Error querying QR_code_assets: {e}")

    # 2. Check json_files (Processed files)
    print("\n2. Checking 'json_files' table...")
    try:
        # Fetch rows that resemble the ID
        query = f"SELECT * FROM json_files WHERE code LIKE '%{TARGET_ASSET_ID}%'"
        df_json = pd.read_sql_query(query, conn)
        
        if df_json.empty:
            print("   ❌ Asset NOT FOUND in json_files table.")
            print("   (This implies the JSON file has not been processed into the DB yet.)")
            print("   (Did you run the 'Update DB from Photos & JSON' task on the dashboard?)")
        else:
            print(f"   ✅ Found {len(df_json)} row(s).")
            for i, row in df_json.iterrows():
                code_val = row['code']
                print(f"   Row {i} Code Column: '{code_val}' (Type: {type(code_val)})")
                
                # Check match against target
                if "0000183793" == str(code_val).strip():
                    print("   ✅ Exact match confirmed.")
                else:
                    print(f"   ⚠️ Mismatch detected. DB has '{code_val}' vs Target '0000183797'")
    except Exception as e:
        print(f"   ❌ Error querying json_files: {e}")

    conn.close()
    print("\n--- End Diagnosis ---")

if __name__ == "__main__":
    diagnose_db()