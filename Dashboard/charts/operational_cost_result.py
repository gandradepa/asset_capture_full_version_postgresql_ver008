import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import io
import os
from matplotlib.lines import Line2D
import db as qrdb  # backend-agnostic QR_codes DB layer

# --- Configuration ---
DB_PATH_DEFAULT = r"/home/developer/asset_capture_app_dev/data/QR_codes.db"
DB_PATH = os.getenv("DASHBOARD_DB_PATH", DB_PATH_DEFAULT)
# Cost per unit for AI estimation
AI_UNIT_COST = 0.03
TABLE_QR_CODE_ASSETS = "QR_code_assets"
TABLE_BUILDINGS = "Buildings"
TABLE_QR_CODES = "QR_codes"

def _get_data(building: str = "All", year: str = "All", month: str = "All", user=None):
    """Fetches and processes data from the database with Year/Month/User filtering."""
    with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
        df_assets = pd.read_sql_query(f'SELECT * FROM "{TABLE_QR_CODE_ASSETS}"', qrdb.raw_conn(conn))
        df_buildings = pd.read_sql_query(f'SELECT "Code", "Name" FROM "{TABLE_BUILDINGS}"', qrdb.raw_conn(conn))
        df_qr_codes = pd.read_sql_query(f'SELECT * FROM "{TABLE_QR_CODES}"', qrdb.raw_conn(conn))

    if user and "user" in df_assets.columns:
        df_assets = df_assets[df_assets["user"].astype(str).str.strip() == str(user).strip()].copy()

    processed_df = df_assets[['code_assets']].copy()
    processed_df['QR_code_ID'] = processed_df['code_assets'].str[:10]
    split_data = processed_df['code_assets'].str.split(' ', n=2, expand=True)
    processed_df['Code'] = split_data[1]
    
    qr_building = pd.merge(processed_df, df_buildings, on='Code', how='inner')
    final_df = qr_building[['QR_code_ID', 'Code', 'Name']].drop_duplicates().reset_index(drop=True)
    
    operational_cost = pd.merge(df_qr_codes, final_df, on='QR_code_ID', how='inner')
    operational_cost['date_set'] = pd.to_datetime(operational_cost['date_set'], errors='coerce')
    operational_cost.dropna(subset=['date_set'], inplace=True)

    # --- Filtering Logic ---
    # If specific filters are applied, use them. Otherwise, default to last 12 months.
    if year != "All" or month != "All":
        if year != "All":
            operational_cost = operational_cost[operational_cost['date_set'].dt.year == int(year)]
        if month != "All":
            operational_cost = operational_cost[operational_cost['date_set'].dt.month == int(month)]
    else:
        # Default behavior: Last 12 months
        start_date = pd.Timestamp.now() - pd.DateOffset(months=12)
        operational_cost = operational_cost[operational_cost['date_set'] >= start_date].copy()
    
    operational_cost['date'] = operational_cost['date_set'].dt.date
    operational_cost['time'] = operational_cost['date_set'].dt.time

    # --- Process Elapsetime ---
    def parse_elapsetime(t_str):
        if not t_str or not isinstance(t_str, str): return 0
        try:
            parts = t_str.split(':')
            if len(parts) == 2:
                m, s = map(int, parts)
                return m * 60 + s
            return 0
        except:
            return 0
            
    if 'elapsetime' in operational_cost.columns:
        operational_cost['elapsetime_seconds'] = operational_cost['elapsetime'].apply(parse_elapsetime)
    else:
        operational_cost['elapsetime_seconds'] = 0

    operational_cost.rename(columns={'Name': 'Property'}, inplace=True)
    
    if building != "All":
        operational_cost = operational_cost[operational_cost['Property'] == building]
    
    operational_cost_grouped = operational_cost.groupby(['date', 'Property']).agg(
        qty_asset=('Code', 'count'),
        min_time=('time', 'min'),
        max_time=('time', 'max'),
        total_elapsetime_seconds=('elapsetime_seconds', 'sum')
    ).reset_index()

    dummy_date = pd.to_datetime('1970-01-01').date()
    max_dt = operational_cost_grouped['max_time'].apply(lambda t: pd.to_datetime(f"{dummy_date} {t}"))
    min_dt = operational_cost_grouped['min_time'].apply(lambda t: pd.to_datetime(f"{dummy_date} {t}"))
    
    operational_cost_grouped['total_seconds'] = (max_dt - min_dt).dt.total_seconds()
    operational_cost_grouped = operational_cost_grouped[operational_cost_grouped['min_time'] != operational_cost_grouped['max_time']].copy()
    
    return operational_cost_grouped

# --- [NEW] Elapsetime Integration ---
import json
import glob
import os
import sqlite3

# Resolve paths relative to this file: .../Dashboard/charts/operational_cost_result.py
# Root is 2 levels up from 'charts': .../Dashboard/charts -> .../Dashboard -> .../asset_full_dummy_version
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))

# Correct path to Output_jason_api
OUTPUT_JSON_API_DIR = os.path.join(ROOT_DIR, "Output_jason_api")
ASSET_CAPTURE_DATA_DIR = os.path.join(ROOT_DIR, "asset_capture_app_dev", "data")

# Correct path to DB (Override default if not set consistently)
# Only override if the default Linux path is still there and we are likely on Windows/Dev
if "/home/developer" in DB_PATH:
    POSSIBLE_DB = os.path.join(ROOT_DIR, "asset_capture_app_dev", "data", "QR_codes.db")
    if os.path.exists(POSSIBLE_DB):
        DB_PATH = POSSIBLE_DB

def _ensure_elapsetime_column():
    """Ensures 'elapsetime' column exists in QR_codes table."""
    try:
        # Use the resolved DB_PATH
        with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
            cursor = conn.cursor()
            columns = qrdb.table_columns(conn, "QR_codes")  # backend-agnostic (was PRAGMA table_info)
            if "elapsetime" not in columns:
                print(f"Adding 'elapsetime' column to QR_codes table at {DB_PATH}...")
                cursor.execute('ALTER TABLE "QR_codes" ADD COLUMN "elapsetime" TEXT')
                conn.commit()
                print("'elapsetime' column added successfully.")
            else:
                # Debug print
                pass
    except sqlite3.Error as e:
        print(f"Error checking/adding column to {DB_PATH}: {e}")

def _sync_elapsetime_from_json():
    """Reads *_et.json files from known stores and updates QR_codes.elapsetime."""
    candidate_dirs = [OUTPUT_JSON_API_DIR, ASSET_CAPTURE_DATA_DIR]
    json_files = []
    for candidate_dir in candidate_dirs:
        if os.path.exists(candidate_dir):
            json_files.extend(glob.glob(os.path.join(candidate_dir, "*_et.json")))

    if not json_files:
        print("No *_et.json files found.")
        return

    latest_by_qr = {}
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            qr_code = str(data.get("qr_code") or "").strip()
            elapsetime = str(data.get("elapsetime") or "").strip()
            if not qr_code or not elapsetime:
                continue

            current = latest_by_qr.get(qr_code)
            file_mtime = os.path.getmtime(file_path)
            if current is None or file_mtime >= current[0]:
                latest_by_qr[qr_code] = (file_mtime, elapsetime)
        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    if latest_by_qr:
        try:
            with qrdb.get_connection(sqlite_path=DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.executemany(
                    'UPDATE "QR_codes" SET "elapsetime" = ? WHERE "QR_code_ID" = ?',
                    [(payload[1], qr_code) for qr_code, payload in latest_by_qr.items()]
                )
                conn.commit()
        except sqlite3.Error as e:
            print(f"Database update error: {e}")


# Run sync on module load (or call explicitly if needed)
_ensure_elapsetime_column()
_sync_elapsetime_from_json()

def _draw_combo_chart(ax, data, metric="duration", building="All"):
    """Draws the daily operational combo chart."""
    if data.empty:
        ax.text(0.5, 0.5, "No data available for this chart.", ha='center', va='center')
        ax.axis('off')
        return

    chart_df = data.copy()
    chart_df['date'] = pd.to_datetime(chart_df['date'])
    chart_df.sort_values('date', inplace=True)

    daily_summary = chart_df.groupby('date').agg(
        total_qty=('qty_asset', 'sum'),
        total_time_seconds=('total_seconds', 'sum'),
        total_elapsetime_seconds=('total_elapsetime_seconds', 'sum')
    ).reset_index()
    
    # Determine which time metric to use
    if metric == "elapsetime":
        # [NEW] Filter out records where elapsetime is 0
        daily_summary = daily_summary[daily_summary['total_elapsetime_seconds'] > 0].copy()
        
        # Use simple sum of elapsetime, or weighted average if strict average is needed?
        # Logic: (Total Elapsetime) / (Total Qty)
        time_numerator = daily_summary['total_elapsetime_seconds']
        metric_label = "Asset Time"
    else:
        # Default: Site Duration
        time_numerator = daily_summary['total_time_seconds']
        metric_label = "Site Duration"

    daily_summary['weighted_avg_time_minutes'] = (time_numerator / daily_summary['total_qty'].replace(0, 1)) / 60

    lollipop_color = '#240046'
    line_color = '#7b2cbf'
    fill_color = '#e0c3fc'
    
    x_pos = range(len(daily_summary['date']))

    # --- Area Chart (ax for Average Time) ---
    ax.plot(x_pos, daily_summary['weighted_avg_time_minutes'], color=line_color, linewidth=2.5, zorder=10)
    ax.fill_between(x_pos, daily_summary['weighted_avg_time_minutes'], color=fill_color, alpha=0.45, zorder=5)

    # --- Lollipop Chart (ax2 for Qty of Assets) ---
    ax2 = ax.twinx()
    
    ax2.vlines(x=x_pos, ymin=0, ymax=daily_summary['total_qty'], color=lollipop_color, lw=1.2, zorder=1)

    ax2.scatter(x_pos, daily_summary['total_qty'], s=500, color=lollipop_color, zorder=15)
    ax2.scatter(x_pos, daily_summary['total_qty'], s=350, color='white', zorder=16)

    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)
    
    # --- Title (Left Aligned) ---
    title_text = f'Operational Asset Data Capture Performance ({metric_label}) - {building}'
    ax.set_title(title_text, fontsize=16, fontweight='bold', color='#240046', pad=60, loc='left')
    
    # --- Axes and Ticks ---
    ax.set_xlabel('')
    ax.set_xticks(x_pos)
    # [UPDATED] Set rotation to 90 for vertical labels
    ax.set_xticklabels(daily_summary['date'].dt.strftime('%Y-%m-%d'), rotation=90, ha='center')
    
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['bottom'].set_color('#B0B0B0')
    
    for spine in ax2.spines.values():
        spine.set_visible(False)
    
    ax.tick_params(axis='y', length=0)
    ax.tick_params(axis='x', length=0)
    ax2.tick_params(axis='y', length=0)
    ax.set_yticklabels([])
    ax2.set_yticklabels([])

    # --- Data Labels ---
    for i, v in enumerate(daily_summary['total_qty']):
        if v > 0:
            ax2.text(i, v, f'{int(v)}', ha='center', va='center', color=lollipop_color, fontsize=10, fontweight='bold', zorder=17)
    
    y_offset = (ax.get_ylim()[1] if not daily_summary.empty else 1) * 0.03
    for i, v in enumerate(daily_summary['weighted_avg_time_minutes']):
        ax.text(i, v + y_offset, f'{v:.2f}', ha='center', va='bottom', color=line_color, fontsize=10, fontweight='bold', zorder=20)
            
    # --- Custom Legend ---
    legend_elements = [
        Line2D([0], [0], color=line_color, lw=2, label='Average Time in Min'),
        Line2D([0], [0], marker='o', color='w', label='Qty of Assets',
               markerfacecolor=lollipop_color, markersize=10)
    ]
    ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(-0.01, 1.12), ncol=2, frameon=False, fontsize=12.5)


def _draw_card(ax, value, title, note=None, note_fontsize=8):
    """Draws a card-style chart with optional footer note."""
    
    # Layout positions (all within 0-1 axes coordinates)
    if note:
        # Card WITH note: title top, value upper, note bottom (extra room)
        title_y = 0.95
        value_y = 0.72
    else:
        # Card WITHOUT note: centered layout
        title_y = 0.95
        value_y = 0.55

    ax.text(0.5, title_y, title, ha='center', va='top', fontsize=14, color='gray', linespacing=1.3)
    ax.text(0.5, value_y, value, ha='center', va='center', fontsize=48, fontweight='bold', color='#03045e')
    
    if note:
        # Note pinned to bottom-left of axes
        ax.text(0.02, 0.02, note, ha='left', va='bottom', fontsize=note_fontsize, color='#555', linespacing=1.4, family='monospace',
                bbox=dict(facecolor='white', alpha=0.9, edgecolor='#e5e7eb', boxstyle='round,pad=0.5'))
    
    ax.axis('off')


def render_chart_png(chart_type: str = "combo", building: str = "All", year: str = "All", month: str = "All", metric: str = "duration", user=None) -> bytes:
    """Renders the specified operational cost chart to a PNG byte string."""
    try:
        _sync_elapsetime_from_json()
        data = _get_data(building=building, year=year, month=month, user=user)
    except Exception as e:
        print(f"Error getting data for operational cost chart: {e}")
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, "Error: Could not load data for this chart.", ha='center', va='center')
        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True); plt.close(fig); buf.seek(0)
        return buf.read()

    fig, ax = plt.subplots(figsize=(14, 8))
    fig.patch.set_facecolor('none')
    ax.set_facecolor('none')
    
    if chart_type == "combo":
        _draw_combo_chart(ax, data, metric=metric, building=building)
    elif chart_type in ["weighted_avg_time", "ai_cost"]:
        plt.close(fig) 
        # AI Cost card: wider (6) and taller (4) to fit 16pt note text
        # Weighted Avg card: standard compact size
        if chart_type == "ai_cost":
            fig, ax = plt.subplots(figsize=(6, 5))
        else:
            fig, ax = plt.subplots(figsize=(4, 2.5))
        fig.patch.set_facecolor('none')
        ax.set_facecolor('none')
        
        # [NEW] Filter strict for card if metric is elapsetime
        if chart_type == "weighted_avg_time" and metric == "elapsetime" and 'total_elapsetime_seconds' in data:
             data = data[data['total_elapsetime_seconds'] > 0].copy()

        total_qty = data['qty_asset'].sum()
        total_seconds = data['total_seconds'].sum()
        total_elapsetime_seconds = data['total_elapsetime_seconds'].sum() if 'total_elapsetime_seconds' in data else 0

        if chart_type == "weighted_avg_time":
            # Select numerator based on metric
            if metric == "elapsetime":
                numerator = total_elapsetime_seconds
                title_suffix = "(Asset Time)"
            else:
                numerator = total_seconds
                title_suffix = "(Site Duration)"

            avg_seconds = numerator / total_qty if total_qty > 0 else 0
            m_avg = int(avg_seconds // 60)
            s_avg = int(avg_seconds % 60)
            value = f"{m_avg:02d}:{s_avg:02d}"
            _draw_card(ax, value, f"Weighted Avg Time to Capture\n{title_suffix}")
            
        elif chart_type == "ai_cost":
            ai_est_cost = total_qty * AI_UNIT_COST
            value = f"${ai_est_cost:.2f}"
            
            # Dynamic note content
            if not data.empty and 'date' in data.columns:
                start_date = data['date'].min()
            else:
                start_date = "N/A"
                
            note_text = (
                f"Note:\n"
                f"1. Estimated AI Unit Cost ($): {AI_UNIT_COST}\n"
                f"2. Quantity of Assets: {int(total_qty)}\n"
                f"3. Period starts from: {start_date}"
            )
            
            _draw_card(ax, value, "Annual AI Estimated Cost\n(Filtered Period)", note=note_text, note_fontsize=19.2)

    plt.tight_layout()
    buf = io.BytesIO()
    # bbox_inches='tight' ensures no text is clipped outside the figure boundaries
    fig.savefig(buf, format="png", dpi=120, transparent=True, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf.read()
