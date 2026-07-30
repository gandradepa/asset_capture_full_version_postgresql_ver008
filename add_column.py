import sqlite3

# Update this path to match your actual DB path
DB_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"

def add_sdi_column():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            # Try to add the column. If it exists, this will throw an error which we catch.
            try:
                cur.execute('ALTER TABLE "QR_codes" ADD COLUMN "sdi" INTEGER DEFAULT 0')
                print("Column 'sdi' added successfully.")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e):
                    print("Column 'sdi' already exists.")
                else:
                    raise e
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    add_sdi_column()