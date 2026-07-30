---
description: Manage FLS (Fire & Life Safety) assets in the Dashboard â€” add, edit, delete, bulk update, and inline field updates.
---

# Manage FLS Assets

Current documentation refresh: 2026-04-28.

## Overview

The FLS Assets view provides CRUD operations on the `new_device` table in PostgreSQL `qr_code_db`. Assets represent devices tracked through the Planon checklist workflow. `Attribute Set` defaults to `FireAlarmDevice`; rows with `Planon Code` remain editable for corrections, but delete and bulk selection are blocked.

---

## Data Model

### Table: `new_device`

| Column | Type | Description |
|--------|------|-------------|
| `tag` | TEXT (PK) | Unique device tag identifier |
| `building` | TEXT | Building code |
| `status` | TEXT | Current status (e.g., Active, Pending) |
| `trade` | TEXT | Trade category |
| `description` | TEXT | Device description |
| `space` | TEXT | Room/space identifier |
| `floor` | TEXT | Floor number |
| `asset_group` | TEXT | Asset classification group |
| `priority` | TEXT | Priority level |
| `notes` | TEXT | Free-text notes |
| `planon_*` columns | INTEGER (0/1) | Planon checklist boolean fields |

New columns are auto-added via `_ensure_new_device_columns()` at app startup.

---

## API Endpoints

### List All Assets
```
GET /api/fls-assets
```
Returns JSON array of all `new_device` records, enriched with building names from the `Buildings` table.

### Add / Update Asset
```
POST /api/fls-assets/add
Content-Type: application/json

{
    "tag": "FLS-001",
    "building": "1234",
    "status": "Pending",
    "trade": "HVAC",
    "description": "Smoke Detector",
    "space": "Room 101",
    "floor": "1",
    "asset_group": "Fire Safety",
    "priority": "High",
    "notes": ""
}
```
Uses `INSERT ... ON CONFLICT(tag)` for upsert behavior.

### Delete Asset
```
POST /api/fls-assets/delete
Content-Type: application/json

{
    "tag": "FLS-001"
}
```

### Bulk Update
```
POST /api/fls-assets/bulk-update
Content-Type: application/json

{
    "tags": ["FLS-001", "FLS-002", "FLS-003"],
    "updates": {
        "status": "Active",
        "priority": "Medium"
    }
}
```
Only updates columns that are explicitly in the allowed whitelist.

### Inline Field Update
```
POST /api/fls-assets/update-field
Content-Type: application/json

{
    "tag": "FLS-001",
    "field": "planon_registered",
    "value": 1
}
```
Used for checkbox toggles in the table (Planon checklist columns).

---

## Frontend Workflow

### Viewing Assets
1. Click **"FLS Assets"** in the sidebar â†’ `showView('fls-assets-view')`
2. JavaScript fetches `GET /api/fls-assets` and populates the table
3. Table supports sorting, filtering, and pagination
   - **Note**: The "Planon" filter defaults to **"NO"** to highlight pending items. Change to "All" to see everything.

### Adding an Asset
1. Click **"Add Asset"** button â†’ opens Bootstrap modal
2. Fill in the form fields
3. Click **Save** â†’ `POST /api/fls-assets/add`
4. Table refreshes automatically

### Editing an Asset
1. Click the **edit icon** (pencil) on a table row
2. Modal opens pre-populated with current values
3. Modify fields and click **Save** â†’ `POST /api/fls-assets/add` (upsert)

Rows with a populated `Planon Code` still allow this edit path.

### Deleting an Asset
1. Click the **delete icon** (trash) on a table row
2. Confirm deletion dialog
3. `POST /api/fls-assets/delete` with the tag

Rows with a populated `Planon Code` must not be deletable.

### Bulk Update
1. Select multiple rows via checkboxes
2. Click **"Bulk Update"** button
3. Choose fields to update and new values
4. `POST /api/fls-assets/bulk-update`

Rows with a populated `Planon Code` must not be selectable for bulk update.

### Inline Checkbox Update
1. Click any Planon checklist checkbox in the table
2. `POST /api/fls-assets/update-field` fires immediately
3. Visual feedback confirms the update

---

## Adding a New Planon Checklist Column

1. **Database**: Add column name to `_ensure_new_device_columns()` in `Asset_portal_dashboard.py`
2. **Backend**: Include the column in `add_fls_assets()` INSERT/UPDATE query
3. **Backend**: Include the column in `get_fls_asset_data()` SELECT query
4. **Frontend**: Add `<th>` header in the FLS table in `dashboard.html`
5. **Frontend**: Add checkbox cell in the row-building JavaScript
6. **Frontend**: Hook the checkbox to `update_fls_asset_field()` API call

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Missing columns in response | Run the app once to trigger `_ensure_new_device_columns()` migration |
| Upsert not working | Verify `tag` is the PRIMARY KEY in the `new_device` table |
| Bulk update ignored columns | Check the allowed-columns whitelist in `bulk_update_assets()` |
| Checkbox not saving | Verify the `field` parameter matches an actual column name |
