---
description: End-to-end asset review session â€” from login through dashboard filtering, asset review, field editing, approval, and navigation.
---

# Review Asset Session

Current documentation refresh: 2026-06-25.

This workflow describes the complete reviewer experience for inspecting, correcting, and approving extracted asset data.

---

## 1. Login

1. Navigate to the review app URL (e.g., `http://127.0.0.1:5002` for ME)
2. Enter credentials on the login page
3. On success, redirect to the main dashboard (`/`)

---

## 2. Dashboard Overview

The dashboard displays assets in three tabs, each corresponding to a `Col_process` value:

| Tab | Content | Default Filter |
|-----|---------|----------------|
| **New** | Freshly extracted assets | Pending (not approved) |
| **Update** | Assets returned for corrections | Pending (not approved) |
| **Manual Entry** | Assets flagged for manual data entry | All statuses |

Each tab shows a table with columns: QR Code, Building, UBC Tag, Asset Group, Approved status, Photos Summary, Flagged, Modified, Capture Date.

The **Photo** column shows the required-photo ratio (`{present}/{required}`) in a red/green pill, plus a small `+1` chip next to it when the optional **Extra Photo** is present (ME `-4`, BF `-3`, EL `-3`). The chip is informational only — Extra Photo never counts toward the red "Missed Photo" pill or KPI.

---

## 3. Apply Filters

Use the filter controls above the table to narrow the asset list:

1. **Building**: Pick one or more buildings from the searchable checkbox dropdown (ME/BF; EL is single-select) to show only their assets
2. **Review Status**: Choose Approved / Pending / All
3. **QR Code**: Type a partial QR code for substring matching
4. **UBC Tag**: Type a partial tag (searches both `UBC Asset Tag` and `UBC Tag` keys)
5. **Asset Group**: Pick one or more groups from the searchable checkbox dropdown (ME/BF — rows matching any checked group are shown; EL keeps a simple single-value select)
6. **Capture Date**: Filter by date prefix
7. **Quick Filters**: Toggle Flagged, Modified, or Missed Photos badges

> **Tip**: Filters **and the column sort** are preserved across navigation. Save & Next / Save & Previous walk the dashboard's visible, filtered + sorted order (captured to `localStorage('reviewOrder')`), and the "Show Archive" toggle persists through the round-trip back to the dashboard.

---

## 4. Open an Asset for Review

Click any row in the dashboard table to open the review page (`/review/<doc_id>`).

The review page displays:
- **Asset photos**: thumbnail strip per discipline:
  - ME: `-0` Asset Plate, `-1` UBC Tag, `-2` Main Picture, `-3` TSBC, `-4` **Extra Photo** (optional)
  - BF: `-0` Asset Plate, `-1` Asset Plate (Opt), `-2` Main Asset, `-3` **Extra Photo** (optional)
  - EL: `-0` Asset Plate (Opt), `-1` UBC Asset Tag, `-2` Full Interior Panel, `-3` **Extra Photo** (optional)
  - An absent Extra Photo renders the neutral "Missing" placeholder — never a red/error state.
- **Editable fields**: All structured data fields (varies by type â€” see AGENT.md for field matrix)
- **Status indicators**: Approved badge, Flagged checkbox, Modified indicator, SDI status
- **Pagination**: Current position (`X of Y`) within the dashboard's filtered + sorted order
- **Next Asset Preview**: Thumbnail and basic info of the next asset in that order (fetched via `GET /api/asset-preview/<doc_id>`)

---

## 5. Review and Edit Fields

1. **Inspect photos** â€” Click thumbnails to view full-size images
2. **Verify extracted data** â€” Compare field values against the nameplate photos
3. **Correct errors** â€” Edit fields directly in the form
4. **Flag for attention** â€” Check the "Flagged" checkbox if the asset needs special attention
5. **Change QR Code** â€” For temporary codes (starting with `T`), enter the permanent QR code in the rename field

---

## 6. Save and Navigate

| Action | Button | Behavior |
|--------|--------|----------|
| **Save & Return** | Save button | Saves and returns to dashboard |
| **Save & Next** | Next arrow | Saves and navigates to the next asset in the dashboard's filtered + sorted order |
| **Save & Previous** | Previous arrow | Saves and navigates to the previous asset in that same order |

On save:
- JSON file is updated with new field values
- Dictionary rules are re-applied (Asset Group, Description)
- `sdi_dataset` is updated in the database
- If QR was renamed: JSON file, images, and all DB references are atomically updated

> **Asset Review Sheet (PDF / Export):** the review header also carries **PDF** and **Export** buttons (ME / BF / EL) that produce a one-page, self-contained report for the open asset — `review_print` (auto-print → Save-as-PDF) and `review_export` (download a portable `.html` with photos/logo inlined). Read-only; Description is shown above Identity. See `review/.agent/AGENT.md` and `Markdowns_documentation/rules/review_apps.rules.md` ("Asset Review Sheet").

---

## 7. Toggle Actions (from Dashboard or Review Page)

### Approve
- Click the approval badge to toggle between Approved and Pending
- Updates both JSON (`structured_data.Approved`) and DB (`QR_codes.Approved`)

### Toggle AI Status
- Click the AI status icon to mark/unmark for AI reprocessing
- Updates `QR_codes.ai_status` column (0 â†” 1)

### Toggle SDI Exclude
- Click the SDI icon to exclude/include from SDI printout
- Updates `QR_codes.sdi` column (0 â†” 1)
- **ME app**: Also moves the asset between tabs by updating `Col_process` (0 â†” 2)
- **BF/EL apps**: SDI toggle is decoupled from tab status

---

## 8. Move Asset Between Tabs

Assets move between tabs based on `Col_process` value in `QR_code_assets`:
- An admin or automated process can update `Col_process` to move assets
- The SDI toggle (ME) automatically moves assets: 0â†’2 (Newâ†’Manual) or 2â†’0 (Manualâ†’New)

---

## 9. Batch Review Tips

- Set your filters first (e.g., Building = "MAIN", Status = "Pending")
- Use **Save & Next** to review assets sequentially without returning to the dashboard
- The filter context is preserved â€” you'll always stay within your filtered queue
- Approve assets as you go to track progress
- Use the "Flagged" badge filter to quickly revisit flagged items later
