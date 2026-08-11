---
description: How to add, edit, or remove an asset prefix definition using the web-based dictionary tool.
---

# Manage Asset Dictionary

Current documentation refresh: 2026-08-10.

This workflow describes how reviewers use the UI to securely update the active asset dictionary, which governs how metadata is applied to nameplates in the CMMS pipeline.

## Prerequisites
- The **Dashboard** app (`/dictionary`, port `8002` locally, `dashboardprod.assetcap.facilities.ubc.ca/dictionary` in production). This is the live UI. The standalone `dictionary/dictionary_app.py` on port 5000 is legacy reference code and is not deployed.
- A `dictionary/dictionary` permission grant, issued through Dashboard → User Admin:
  - **viewer** — read-only table. No Add button, no row actions.
  - **editor** — full add / edit / delete.

---

## 1. Viewing and Filtering the Dictionary

1. Open **Dictionary** from the Operations group in the sidebar.
2. The table lists every entry from `mechanical_dictionary.py`. Composite keys (e.g. `AHU|ME`) are split into readable **UBC Tag (Key)** and **Type** columns; Type renders as a colored badge (ME blue, EL green, BF amber).
3. Header chips show the totals: overall plus a per-discipline count.
4. The filter toolbar narrows the list:
    - **Search all** — matches any column.
    - **UBC Tag** — substring match on the tag only.
    - **Asset Type** — All / ME / EL / BF.
    - **Attribute Set** and **Asset Group** — searchable multi-selects. Their options are faceted against the other active filters, so they only ever offer values that would actually return rows.
5. Active filters appear as chips beneath the toolbar; click a chip's `×` to drop just that one, or **Reset** to clear everything. The count on the right reads `Showing X of Y entries`.
6. Any column header sorts (click, or focus and press Enter/Space).

---

## 2. Adding a New Asset Definition

1. Click **Add New Entry**.
2. Fill out the required parameters:
    - **UBC Tag (Key)**: The sequence letters found on nameplates (e.g. `RTU`, `CH`, `P-`).
    - **Asset Type**: The engineering discipline — `ME`, `EL`, or `BF`. These three are the only accepted values; the server rejects anything else with a `400`.
    - **Attribute Set**: (e.g. Mechanical, Tank, Electrical).
    - **Asset Group**: Select from the list (populated from PostgreSQL `qr_code_db` through the shared DB layer to prevent typos). Off-list values are rejected.
    - **Main Asset**: Optional.
    - **Description**: The default readable label given to these assets.
3. Click **Save Changes**.
4. The Flask backend validates the input, generates a composite key (`PREFIX|TYPE`), checks for duplicates, and rewrites the underlying `.py` file to disk immediately.

---

## 3. Editing an Existing Asset

1. Find the target asset row and click the **Edit** icon.
2. The modal form opens, prepopulated with the existing values.
3. **Note on Migration**: If you are editing an older entry that is still using a legacy key (no pipe character, e.g. `AHU`), saving the edit will automatically convert its underlying dictionary key to the modern `TAG|TYPE` standard (`AHU|ME`).

---

## 4. Deleting an Asset

1. Click the **Delete** icon on the row.
2. The confirmation dialog requires you to retype the UBC tag exactly before the Delete button enables. This is deliberate: extraction immediately stops deriving Asset Group and Attribute Set from a deleted tag.

---

## 5. Audit Trail

Every save and delete writes one `audit_trail` row per changed field, recording the logged-in user, timestamp, and old/new values (`app_name = dashboard_dictionary`, `record_pk = TAG|TYPE`). Renames are recorded under the synthetic field `dictionary_key`. Auditing is best-effort — if the audit DB is unreachable the dictionary write still succeeds and the failure is logged server-side.

---

## 6. Operational Impact

Once a dictionary save is committed, the updated Python dictionary is immediately active.
- The next time the API Extraction script processes an image, it will import the fresh dictionary.
- The next time a Plate Review App refreshes, it will apply the new dictionary descriptions to new unreviewed assets.
