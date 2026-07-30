---
description: How the atomic parameter update service works â€” file renames, DB updates, JSON updates, and rollback on failure.
---

# Parameter Update Workflow

Current documentation refresh: 2026-04-28.

When a technician changes Building, Location, or Asset Type for an existing QR code during a capture session, the `parameter_update_service.py` module handles all updates atomically with rollback support.

---

## When This Is Triggered

- **Route**: `POST /api/update-parameters`
- **Payload**: `{ qr_code, new_building, new_location, new_asset_type }`
- **Context**: User updates fields on the capture page after photos already exist

---

## Pipeline Steps

### 1. Detect Changes
```python
changes = detect_parameter_changes(old_params, new_params)
# Returns: { changed, building_changed, location_changed, 
#            asset_type_changed, requires_file_rename }
```
- If nothing changed â†’ return early (no-op)
- If building or asset type changed â†’ files need renaming

### 2. Get Affected Files
```python
files = get_affected_files(upload_dir, qr_code)
# Finds all files matching the QR code in the upload directory
```

### 3. Create Backup
```python
backup_dir = backup_files(files)
# Copies all affected files to a temp directory
```

### 4. Rename Files (if required)
```python
renames = rename_files_atomic(upload_dir, files, qr_code, new_building, new_asset_type)
# Old: "0000177276 314-1 BF - 0.jpg"
# New: "0000177276 217 ME - 0.jpg"
# Returns: [(old_path, new_path, old_base, new_base), ...]
```

### 5. Update QR_codes Table
```python
update_qr_codes_table(conn, qr_code, new_building, new_location, new_asset_type)
# - Ensures Building Code, asset_type columns exist
# - Updates building, location, asset type, date_set
# - SQLite triggers auto-update: Space, Floor, Space Details, Floor Code
```

### 6. Update QR_code_assets Table
```python
update_assets_table(conn, file_renames)
# Updates code_assets column for each renamed file
```

### 7. Clean Up Orphan Records
```python
purge_orphan_asset_rows(conn, qr_code)
delete_exact_asset_row(conn, qr_code)
# Removes rows where code_assets is just the QR (no file suffix)
```

### 8. Update sdi_dataset Tables
```python
update_sdi_dataset_table(conn, qr_code, new_building, new_location, new_asset_type)
# Updates sdi_dataset AND sdi_dataset_EL if rows exist for this QR
# Uses UPDATE only â€” never creates duplicate rows
```

### 9. Update JSON Files
```python
update_json_files(qr_code, old_building, new_building, old_asset_type, new_asset_type)
# - Renames JSON files: old_building â†’ new_building in filename
# - Edits JSON content: updates building_number and asset_type fields
```

---

## Rollback Procedure

If **any step fails** after the backup is created:

```python
rollback_file_changes(backup_dir, upload_dir, original_files, renames)
# 1. Delete any renamed files
# 2. Restore original files from backup
# 3. Log the rollback with error details
```

The database rollback is handled by SQLite's transaction mechanism â€” changes are only committed if all operations succeed.

---

## Filename Transformation

| Component | Old | New |
|-----------|-----|-----|
| QR Code | `0000177276` | `0000177276` (unchanged) |
| Building | `314-1` | `217` |
| Asset Type | `BF` | `ME` |
| Sequence | `0` | `0` (unchanged) |
| **Full Name** | `0000177276 314-1 BF - 0.jpg` | `0000177276 217 ME - 0.jpg` |

---

## Tables Modified

| Table | Operation | Condition |
|-------|-----------|-----------|
| `QR_codes` | UPDATE | Always |
| `QR_code_assets` | UPDATE | When files renamed |
| `sdi_dataset` | UPDATE | If QR exists in ME/BF dataset |
| `sdi_dataset_EL` | UPDATE | If QR exists in EL dataset |

## Files Modified

| Location | Operation | Condition |
|----------|-----------|-----------|
| Upload dir | RENAME | When building or asset type changed |
| JSON dir | RENAME + EDIT | When building or asset type changed |

---

## Error Scenarios

| Scenario | Handling |
|----------|----------|
| File rename fails (permissions, disk full) | Rollback from backup |
| DB update fails | Transaction rollback, file rollback |
| JSON file missing | Skipped gracefully (logged, not fatal) |
| Backup creation fails | Abort before any changes |
| Partial rename | Rollback already-renamed files from backup |
