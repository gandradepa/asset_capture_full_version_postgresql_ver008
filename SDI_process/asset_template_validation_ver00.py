import pandas as pd
import numpy as np
import os
import json
from datetime import datetime

def validate_asset_data(file_path, output_dir=None, override_filename=None, return_summary=False):
    print(f"Loading file: {file_path}...")
    
    try:
        # Load RAW data (No type conversion)
        df_raw = pd.read_excel(file_path, header=None)
    except FileNotFoundError:
        return "Error: File not found. Please check the path."
    except Exception as e:
        return f"Error reading file: {e}"

    # --- 1. Parse Specifications ---
    spec_block = df_raw.iloc[0:8, 1:]
    spec_labels = df_raw.iloc[0:8, 0].values
    
    # Extract Headers and Clean them
    header_row = df_raw.iloc[8, 1:].astype(str).str.strip()
    
    # Extract Data Rows
    data_rows = df_raw.iloc[9:, 1:].copy()
    data_rows = data_rows.dropna(how='all')

    # Compile Specifications Map
    col_rules = {}
    for col_idx in spec_block.columns:
        rules = dict(zip(spec_labels, spec_block[col_idx].values))
        rules['Header Name'] = header_row[col_idx]
        col_rules[col_idx] = rules

    errors = []

    print("Running Smart Validation (Lenient on Booleans, Strict on Strings)...")

    # --- 2. Iterate Columns ---
    for col_idx, rules in col_rules.items():
        col_name = rules.get('Header Name', f'Column {col_idx}')
        if pd.isna(col_name) or str(col_name).lower() == 'nan': continue

        field_type = str(rules.get('Field type', 'String'))
        is_mandatory = str(rules.get('Mandatory', '')).lower() == 'true'
        
        series = data_rows[col_idx]

        for row_idx, val in series.items():
            # Check Empty
            if pd.isna(val) or str(val).strip() == "":
                if is_mandatory:
                    errors.append({'Row': row_idx + 1, 'Column': col_name, 'Value': 'EMPTY', 'Issue': 'Mandatory field is empty'})
                continue

            val_str = str(val).strip()
            
            # Step 1: Content Validation
            is_content_valid = True
            if field_type == 'Boolean':
                if val_str.lower() not in ['true', 'false', '1', '0', 'yes', 'no']:
                    is_content_valid = False
                    errors.append({'Row': row_idx + 1, 'Column': col_name, 'Value': val, 'Issue': 'Invalid Boolean'})
            elif field_type in ['Integer', 'Double', 'BigDecimal']:
                try: float(val_str)
                except: 
                    is_content_valid = False
                    errors.append({'Row': row_idx + 1, 'Column': col_name, 'Value': val, 'Issue': 'Invalid Number'})

            # Step 2: Storage Validation (Lenient on Booleans)
            if is_content_valid and field_type != 'Boolean':
                if not isinstance(val, str):
                    errors.append({
                        'Row': row_idx + 1, 'Column': col_name, 'Value': val, 
                        'Actual Type': type(val).__name__,
                        'Issue': 'Format Error: Valid value, but NOT stored as String (Text) in Excel.'
                    })
            
            # Step 3: Length Check (Skip Booleans)
            if field_type != 'Boolean':
                max_len_val = rules.get('Planon input length')
                if pd.notna(max_len_val) and str(max_len_val).lower() != 'nan':
                    try:
                        max_len = int(float(max_len_val))
                        if len(val_str) > max_len:
                            errors.append({'Row': row_idx + 1, 'Column': col_name, 'Value': val, 'Issue': f'Length {len(val_str)} exceeds limit {max_len}'})
                    except: pass

    # --- Bulk Unique Check ---
    for col_idx, rules in col_rules.items():
        if str(rules.get('Unique', '')).lower() == 'true':
            series = data_rows[col_idx]
            vals = series.astype(str).str.strip()
            vals = vals[vals != 'nan']
            vals = vals[vals != '']
            if vals.duplicated().any():
                duplicates = vals[vals.duplicated(keep=False)]
                for idx, val in duplicates.items():
                     errors.append({'Row': idx + 1, 'Column': rules['Header Name'], 'Value': val, 'Issue': 'Duplicate value in Unique field'})

    # --- 3. Cross-Field Validation ---
    header_to_idx = {rules['Header Name']: idx for idx, rules in col_rules.items()}
    space_cols = ["Space.Floor.Property", "Space.Floor.Floor", "Space.Space number"]
    if all(k in header_to_idx for k in space_cols):
        idx_p, idx_f, idx_s = [header_to_idx[k] for k in space_cols]
        for r in data_rows.index:
            vals = [str(data_rows.at[r, idx_p]), str(data_rows.at[r, idx_f]), str(data_rows.at[r, idx_s])]
            present = sum(1 for v in vals if v.lower() != 'nan' and v.strip() != '')
            if 0 < present < 3:
                 errors.append({'Row': r + 1, 'Column': 'Space Group', 'Value': 'Incomplete', 'Issue': 'Incomplete Location'})

    # --- 3.1 Power Type Validation ---
    if 'Power Type' in header_to_idx:
        pwr_idx = header_to_idx['Power Type']
        valid_power_types = {'N', 'E', 'S', 'ES', 'NE', 'NES', 'NS'}
        
        for r in data_rows.index:
            val = str(data_rows.at[r, pwr_idx]).strip()
            # Skip empty values (assuming not mandatory or handled by mandatory check)
            if val and val.lower() != 'nan':
                 if val not in valid_power_types:
                     errors.append({
                         'Row': r + 1, 
                         'Column': 'Power Type', 
                         'Value': val, 
                         'Issue': f'Invalid Power Type. Allowed: {", ".join(sorted(valid_power_types))}'
                     })

    # --- 4. Statistics Calculation ---
    total_rows_count = len(data_rows)
    unique_error_rows = set(e['Row'] for e in errors)
    total_wrong = len(unique_error_rows)
    total_correct = total_rows_count - total_wrong
    
    if total_rows_count > 0:
        pct_correct = (total_correct / total_rows_count) * 100
        pct_wrong = (total_wrong / total_rows_count) * 100
    else:
        pct_correct = 0
        pct_wrong = 0

    print("\n" + "="*35)
    print("        STATISTIC SUMMARY")
    print("="*35)
    print(f"Total of correct rows: {total_correct}")
    print(f"Total of wrong rows:   {total_wrong}")
    print(f"% of Correct rows:     {pct_correct:.2f}%")
    print(f"% of wrong rows:       {pct_wrong:.2f}%")
    print("="*35 + "\n")
    
    summary = {
        "total_rows": total_rows_count,
        "total_correct": total_correct,
        "total_wrong": total_wrong,
        "pct_correct": round(pct_correct, 2),
        "pct_wrong": round(pct_wrong, 2),
    }

    # --- 5. Output Management ---
    
    # Define Output Path
    if output_dir:
        output_folder = output_dir
    else:
        output_folder = r"S:\MaintOpsPlan\AssetMgt\Asset Management Process\Database\8. New Assets\Git_control\SDI Process\sdi_json_output"
    
    # Fallback to current directory if S: drive is not accessible AND no custom output_dir was provided
    if not output_dir:
        if not os.path.exists(os.path.dirname(output_folder)): 
            pass 
            
    if not os.path.exists(output_folder):
        try:
            os.makedirs(output_folder, exist_ok=True)
            print(f"📂 Created directory: {output_folder}")
        except OSError:
            if not output_dir: # Only fallback if using default
                print(f"⚠️ Warning: Could not access path {output_folder}. Saving to local folder instead.")
                output_folder = "sdi_json_output"
                if not os.path.exists(output_folder): os.makedirs(output_folder)
            else:
                 print(f"⚠️ Warning: Could not access path {output_folder}.")

    # Get clean base filename (without .xlsx extension)
    if override_filename:
        base_name = os.path.splitext(os.path.basename(override_filename))[0]
    else:
        base_name = os.path.splitext(os.path.basename(file_path))[0]
    
    # Helper to clean data for JSON
    def convert_types(obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.bool_): return bool(obj)
        if pd.isna(obj): return None
        return str(obj)

    if errors:
        # --- CASE A: ERRORS FOUND (Save Errors JSON) ---
        json_filename = f"errors_{base_name}.json"
        json_full_path = os.path.join(output_folder, json_filename)
        
        try:
            with open(json_full_path, 'w', encoding='utf-8') as f:
                json.dump(errors, f, indent=4, default=convert_types)
            print(f"💾 ERRORS FOUND: Report saved to: {json_full_path}")
        except Exception as e:
            print(f"❌ Failed to save JSON file: {e}")

        # Display Errors in Terminal
        err_df = pd.DataFrame(errors)
        cols = [c for c in ['Row', 'Column', 'Value', 'Actual Type', 'Issue'] if c in err_df.columns]
        err_df = err_df[cols].sort_values(['Row', 'Column'])
        
        pd.set_option('display.width', 1000)
        pd.set_option('display.max_columns', 10)
        pd.set_option('display.max_rows', 100)
        pd.set_option('display.expand_frame_repr', False)
        
        print(f"❌ Found {len(err_df)} Total Errors.")
        print(err_df.head(50).to_string(index=False))
        if return_summary:
            return err_df, summary
        return err_df
        
    else:
        # --- CASE B: NO ERRORS (Save Valid Data JSON) ---
        print("✅ Validation Successful! No errors found.")
        
        json_filename = f"{base_name}.json"
        json_full_path = os.path.join(output_folder, json_filename)
        
        # Prepare Data for JSON (Map Data Rows to Header Names)
        # 1. Create a copy of the valid data
        clean_df = data_rows.copy()
        
        # 2. Map integer columns to Header Strings (from row 8)
        clean_df.columns = header_row.values
        
        # 3. Reset index to be clean (0, 1, 2...)
        clean_df.reset_index(drop=True, inplace=True)
        
        try:
            # Convert to dictionary with 'records' orientation
            data_dict = clean_df.to_dict(orient='records')
            
            with open(json_full_path, 'w', encoding='utf-8') as f:
                json.dump(data_dict, f, indent=4, default=convert_types)
            print(f"💾 SUCCESS: Valid data converted and saved to: {json_full_path}")
        except Exception as e:
            print(f"❌ Failed to save Data JSON file: {e}")

        if return_summary:
            return None, summary
        return None

# --- EXECUTION ---
if __name__ == "__main__":
    target_dir = r"C:\Users\gandrade\Downloads"
    file_name = "SDI_Process_SDI-00005_11_27_2025_217 - Copy.xlsx"
    server_file_path = os.path.join(target_dir, file_name)
    
    validate_asset_data(server_file_path)
