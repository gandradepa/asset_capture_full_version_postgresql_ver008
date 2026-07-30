import sqlite3
import os
from flask import Blueprint, jsonify, current_app, render_template_string, request

# Define the Blueprint
gps_bp = Blueprint('gps_service', __name__)

GPS_TABLE_NAME = "UBC - All Properties List with GPS Coordinates"

def get_db_path():
    """Helper to safely get DB path from app config"""
    return current_app.config.get('DATABASE_FILE_PATH')

def get_building_gps(building_code, db_path):
    """
    Reusable function to fetch GPS coordinates.
    """
    if not os.path.exists(db_path) or not building_code:
        return None

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            query = f'SELECT "Latitude", "Longitude", "Name" FROM "{GPS_TABLE_NAME}" WHERE "Code" = ?'
            cur.execute(query, (building_code,))
            row = cur.fetchone()
            
            if row and row["Latitude"] and row["Longitude"]:
                return {
                    "lat": row["Latitude"],
                    "lng": row["Longitude"],
                    "name": row["Name"]
                }
    except Exception as e:
        print(f"GPS Service Error: {e}")
    
    return None

@gps_bp.route("/api/building_location/<building_code>")
def api_building_location(building_code):
    db_path = get_db_path()
    if not db_path:
        return jsonify({"success": False, "error": "DB path not configured"}), 500

    data = get_building_gps(building_code, db_path)
    if data:
        return jsonify({"success": True, "data": data})
    else:
        return jsonify({"success": False, "error": "No data found for this code"}), 404

# --- NEW DIAGNOSTICS ROUTE ---
@gps_bp.route("/gps/diagnostics")
def gps_diagnostics():
    """
    A simple HTML page to test the DB connection and list sample data.
    """
    db_path = get_db_path()
    status = "Unknown"
    sample_data = []
    error_msg = ""
    
    # 1. Check DB File
    if not db_path:
        status = "CRITICAL: 'DATABASE_FILE_PATH' is not set in app.config."
    elif not os.path.exists(db_path):
        status = f"CRITICAL: DB file not found at {db_path}"
    else:
        status = f"SUCCESS: DB file found at {db_path}"
        
        # 2. Try to Read Data
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                
                # Check if table exists
                cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{GPS_TABLE_NAME}'")
                if not cur.fetchone():
                    error_msg = f"Table '{GPS_TABLE_NAME}' does not exist in the database."
                else:
                    # Fetch sample rows
                    cur.execute(f'SELECT "Code", "Name", "Latitude", "Longitude" FROM "{GPS_TABLE_NAME}" LIMIT 5')
                    rows = cur.fetchall()
                    for r in rows:
                        sample_data.append(dict(r))
        except Exception as e:
            error_msg = f"Database Error: {str(e)}"

    # Simple HTML Template
    html = """
    <html>
        <head>
            <title>GPS Diagnostics</title>
            <style>
                body { font-family: sans-serif; padding: 20px; background: #f4f4f4; }
                .card { background: white; padding: 20px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                .success { color: green; font-weight: bold; }
                .error { color: red; font-weight: bold; }
                table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                th { background-color: #f2f2f2; }
            </style>
        </head>
        <body>
            <h1>GPS Service Diagnostics</h1>
            
            <div class="card">
                <h3>1. Database Connection</h3>
                <p>Status: <span class="{{ 'error' if 'CRITICAL' in status else 'success' }}">{{ status }}</span></p>
                {% if error_msg %}
                    <p class="error">{{ error_msg }}</p>
                {% endif %}
            </div>

            <div class="card">
                <h3>2. API Tester</h3>
                <p>Enter a Building Code to test the raw JSON response:</p>
                <form onsubmit="event.preventDefault(); window.location.href='/api/building_location/' + document.getElementById('code').value">
                    <input type="text" id="code" placeholder="e.g. MAIN" style="padding: 5px; width: 200px;">
                    <button type="submit" style="padding: 5px 15px; cursor: pointer;">Test API</button>
                </form>
            </div>

            <div class="card">
                <h3>3. Sample Data (First 5 Rows)</h3>
                {% if sample_data %}
                    <table>
                        <thead>
                            <tr>
                                <th>Code</th>
                                <th>Name</th>
                                <th>Latitude</th>
                                <th>Longitude</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for row in sample_data %}
                            <tr>
                                <td><strong>{{ row['Code'] }}</strong></td>
                                <td>{{ row['Name'] }}</td>
                                <td>{{ row['Latitude'] }}</td>
                                <td>{{ row['Longitude'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                {% else %}
                    <p>No data retrieved. Check table name or permissions.</p>
                {% endif %}
            </div>
        </body>
    </html>
    """
    return render_template_string(html, status=status, sample_data=sample_data, error_msg=error_msg)