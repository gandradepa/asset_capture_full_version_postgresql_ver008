import os
import ast
import json
import sqlite3
import sys
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION ---
PRIMARY_DICTIONARY_FILE_PATH = os.path.normpath(r"/home/developer/dictionary/mechanical_dictionary.py")
LOCAL_DICTIONARY_FILE_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "mechanical_dictionary.py"))
DB_FILE_PATH = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"

def get_candidate_paths():
    """Returns ordered list of possible dictionary paths."""
    env_path = os.environ.get("DICTIONARY_FILE_PATH")
    return [p for p in [env_path, PRIMARY_DICTIONARY_FILE_PATH, LOCAL_DICTIONARY_FILE_PATH] if p]

def resolve_write_path():
    """Choose the path to write: First existing candidate, or else the first configured one."""
    candidates = get_candidate_paths()
    for candidate in candidates:
        if os.path.exists(candidate):
            return os.path.normpath(candidate)
    return os.path.normpath(candidates[0]) if candidates else LOCAL_DICTIONARY_FILE_PATH

# --- HELPER: Database Connection ---
def get_db_connection():
    if not os.path.exists(DB_FILE_PATH):
        if os.path.exists("QR_codes.db"): return sqlite3.connect("QR_codes.db")
        return None
    try:
        conn = sqlite3.connect(DB_FILE_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"[ERROR] SQL Connect: {e}")
        return None

# --- HELPER: Dictionary IO ---
def read_dictionary():
    combined = {}
    any_existing = False
    for path in get_candidate_paths():
        if not os.path.exists(path):
            continue
        any_existing = True
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            tree = ast.parse(content)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'ASSET_DICTIONARY':
                            combined.update(ast.literal_eval(node.value))
                            break
        except Exception as e:
            print(f"[ERROR] Read Failed: {path} | {e}")
            return None 
    return combined

def save_dictionary(new_data):
    try:
        path = resolve_write_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sorted_data = dict(sorted(new_data.items()))
        content = "ASSET_DICTIONARY = " + json.dumps(sorted_data, indent=4)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"[ERROR] Save Failed: {e}")
        return False

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index_dictionary.html')

@app.route('/api/assets', methods=['GET'])
def get_assets():
    data = read_dictionary()
    if data is None:
        return jsonify([])

    assets_list = []
    for key, val in data.items():
        item = val.copy()
        # Split Key if Composite (Tag|Type)
        if '|' in key:
            code, atype = key.split('|', 1)
            item['asset_code'] = code
            item['asset_type'] = atype
        else:
            # Legacy Key Support
            item['asset_code'] = key
            item['asset_type'] = item.get('asset_type') or item.get('type') or ''

        item['unique_key'] = key 
        assets_list.append(item)
    return jsonify(assets_list)

@app.route('/api/assets', methods=['POST'])
def save_asset():
    logs = []  # Log collector to send back to browser
    def log(msg):
        print(msg)
        logs.append(msg)

    asset = request.json
    
    # 1. INPUT PROCESSING
    raw_key = (asset.get('asset_code') or '').strip()
    raw_type = (asset.get('asset_type') or '').strip()
    
    if not raw_key or not raw_type:
        return jsonify({'success': False, 'message': 'Tag and Type are required'}), 400

    new_tag = raw_key.upper()
    new_type = raw_type.upper()
    storage_key = f"{new_tag}|{new_type}" 
    
    is_edit = bool(asset.get('is_edit'))
    original_key = asset.get('original_key')

    log(f"--- START SAVE ---")
    log(f"Target: {storage_key}")
    log(f"Mode: {'EDIT' if is_edit else 'ADD'}")

    # 2. LOAD DATA
    current_data = read_dictionary()
    if current_data is None:
        return jsonify({'success': False, 'message': 'Critical Error: Cannot read dictionary file.', 'debug': logs}), 500

    # 3. MIGRATION & CLEANUP
    # We rebuild the dictionary. If we find old keys (e.g. "T-") we convert them to "T-|ME"
    # so they don't clash with the new "T-|EL".
    
    canonical_data = {}
    
    for k, v in current_data.items():
        # A. Remove old key if editing
        if is_edit and k == original_key:
            log(f"Removing original key: {original_key}")
            continue

        # B. Normalize/Migrate
        if '|' in k:
            canonical_key = k
            new_entry = v
        else:
            # Found a legacy key! Convert it.
            norm_tag = k.strip().upper()
            file_type = (v.get('asset_type') or v.get('type') or '').strip().upper()
            norm_type = file_type if file_type else 'ME'
            
            canonical_key = f"{norm_tag}|{norm_type}"
            log(f"Migrating Legacy Key: '{k}' -> '{canonical_key}'")
            
            new_entry = v.copy()
            new_entry['asset_type'] = norm_type
            new_entry['type'] = norm_type
        
        canonical_data[canonical_key] = new_entry

    current_data = canonical_data

    # 4. DUPLICATE CHECK
    if storage_key in current_data:
        log(f"ERROR: Key {storage_key} already exists.")
        return jsonify({'success': False, 'message': f'Error: The tag "{new_tag}" with type "{new_type}" already exists.', 'debug': logs}), 409

    # 5. SAVE NEW ENTRY
    current_data[storage_key] = {
        "attribute_set": asset.get('attribute_set', ''),
        "asset_group": asset.get('asset_group', ''),
        "main_asset": asset.get('main_asset', ''),
        "description": asset.get('description', ''),
        "asset_type": new_type,
        "type": new_type
    }
    
    target_file = resolve_write_path()
    log(f"Saving to: {target_file}")
    
    if save_dictionary(current_data):
        log("Save Successful.")
        return jsonify({'success': True, 'message': 'Asset saved successfully', 'debug': logs})
    
    log("Save Failed (File I/O Error).")
    return jsonify({'success': False, 'message': 'Failed to write to file', 'debug': logs}), 500

@app.route('/api/assets/delete', methods=['POST'])
def delete_asset():
    key = request.json.get('unique_key')
    data = read_dictionary()
    if data is None:
         return jsonify({'success': False, 'message': 'Failed to read dictionary file'}), 500

    if key in data:
        del data[key]
        if save_dictionary(data):
            return jsonify({'success': True, 'message': 'Asset deleted successfully'})
        return jsonify({'success': False, 'message': 'Failed to save changes to dictionary file'}), 500
    return jsonify({'success': False, 'message': f'Asset not found in dictionary (key: {key})'}), 404

# --- DROPDOWNS ---
@app.route('/api/main-assets', methods=['GET'])
def get_main():
    conn = get_db_connection()
    if not conn: return jsonify([])
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT main_asset FROM main_asset_description WHERE main_asset IS NOT NULL ORDER BY main_asset")
        return jsonify([r[0] for r in cur.fetchall()])
    except: return jsonify([])

@app.route('/api/asset-groups', methods=['GET'])
def get_groups():
    conn = get_db_connection()
    if not conn: return jsonify([])
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT Name FROM Asset_Group WHERE Name IS NOT NULL ORDER BY Name")
        return jsonify([r[0] for r in cur.fetchall()])
    except: return jsonify([])

@app.route('/api/attributes', methods=['GET'])
def get_attributes():
    return jsonify(["Receiver", "Fan", "Pump", "Motor", "Coil", "Filter", "Damper", "Valve", "Sensor"])

if __name__ == '__main__':
    print(f"--- SERVER STARTING ---")
    print(f"Target File: {resolve_write_path()}")
    app.run(debug=True, port=5000)