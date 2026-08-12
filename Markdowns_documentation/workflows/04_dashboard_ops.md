# Workflow: Dashboard Operations

Current documentation refresh: 2026-06-11.

## Purpose

Operate the main dashboard views for AI process monitoring, review analytics, and operational reporting.

## Inputs

- PostgreSQL `qr_code_db`
- curated JSON payloads in `Output_jason_api/`
- Dashboard chart modules in `Dashboard/charts/`
- `/home/developer/ai_check.log` for the structured AI Check Runs view

## Main Steps

1. Open the dashboard application and load the requested operational page.
2. Use building and process filters to scope the analytics.
3. Review AI queue and summary tables derived from DB and JSON state.
4. Review the Operational Performance Analysis charts.
5. For data quality analytics, use the `All` / `Open Process` toggle in the chart header to control scope.
6. View FLS charts for FLS asset management visualization (requires Altair).
7. View the map chart for assets by building distribution.
8. View the SDI flow chart for flow quantity metrics.
9. Use the dictionary management UI to view or edit the mechanical dictionary.
10. Manage FLS assets through add, delete, bulk update, and read-only magnifying-glass detail views.
11. Open `System Logs -> AI Check Log -> Runs` to review the last 72 hours by wrapper routine and EL/BF/ME stage. Use status filtering or search for a QR code, model, host, PID, or message.

## Current Analytics Rules

- the operational data-quality view uses the merged `Data Quality Comparison` chart
- the chart compares weighted completeness and mean AI confidence by asset type on the same 0-100 scale
- completeness is recomputed from current JSON using discipline-aware rules, not stale fixed field counts
- AI confidence uses `Avg_ai_conf` first and falls back to normalized `confidence_scores` only when needed
- FLS charts use Altair for interactive visualization
- FLS New Device Flow hides Asset Group, Space, and Details in the main table; those fields remain available in Edit and magnifying-glass detail views
- FLS Attribute Set defaults to `FireAlarmDevice`; Planon-coded FLS rows remain editable, but delete and bulk selection stay blocked
- FLS Control Panel Code/Description is derived from `"UBC - Asset Data Master Info"` using the selected building's property code
- if a building has multiple Control Panel rows, the lowest Code row is displayed and the row/form is flagged
- map chart shows assets distributed by building location
- SDI Live Pipeline shows distinct QR review-state cards from `QR_code_assets.Col_process`: New Assets (`0`), Update Existing (`1`), and Manual Entry (`2`)
- SDI Live Pipeline lower flow shows `SDI Queue -> Requested -> Into Planon`; it intentionally omits Mechanical/Electrical discipline split cards
- SDI Live Pipeline is embedded as a single framed chart surface, left-aligned with the dashboard content, with no extra Dashboard card wrapper around the iframe
- SDI lower flow stage cards use modern step styling with captions, not bottom progress bars

## Outputs

- visual monitoring of AI throughput, review status, and data quality
- building-scoped and process-scoped analytics for operations
- FLS asset management operations
- dictionary updates
- deterministic AI Check routine visibility without OpenAI processing or additional API cost

## Verification

- confirm building filters update the operational charts
- confirm `Open Process` excludes approved rows while `All` includes them
- confirm empty states render as valid chart images instead of broken icons
- confirm FLS charts render when Altair is available
- confirm FLS Control Panel Code/Description updates when Property changes in New/Edit forms
- confirm multi-match buildings show the first Control Panel row and a warning flag
- confirm Planon-coded FLS rows can still open Edit but cannot be selected or deleted
- confirm hidden FLS table fields remain visible in Edit and magnifying-glass detail views
- confirm dictionary edits persist correctly
- confirm AI Check opens in Runs mode, newest routine is expanded, filters/search/pagination work, and warnings do not become failures when stage exits are zero
- confirm each routine exposes stage outcomes, processed assets, collapsed diagnostics, and chronological raw output
- confirm SDI Live Pipeline card totals match distinct base QR counts from `QR_code_assets.Col_process`
- confirm SDI Live Pipeline has no nested card wrapper and no Mechanical/Electrical stage cards
- confirm Summary and Raw remain available and Download returns the original complete log file

## Embedded Sub-App Tabs

The Dashboard now hosts ME, BF, EL, and SDI as in-page iframe tabs rather than launching them in a new browser tab. Each sub-app retains its own port, domain, and systemd service.

### Steps

1. Open a sub-app view via its hash URL (`#review-me-view`, `#review-bf-view`, `#review-el-view`, `#sdi-view`) — e.g. from an SLD-run link or a bookmarked hash. (The Overview's `Applications` launch cards were replaced by the `Key Performance Indicators` chart section on 2026-08-05; day-to-day app launching happens from the shared shell sidebar, which opens the standalone sub-app domains.) The Dashboard switches to the hash-routed view and the iframe loads `https://<sub-app>/?embedded=true` lazily on first activation.
2. Inside the embedded view, the sub-app's own top navbar / brand header / user dropdown is suppressed; functional controls (building selector, archive toggle, filters, meta-pills, approve toggle, sub-app's own back-to-list button) remain visible.
3. Use the `Dashboard` button in the central process-view-header (top of the embedded view) to return to the central main view. Use `Open full page` to open the sub-app standalone in a new browser tab.
4. Internal navigation inside the iframe (DataTable pagination, asset review drill-in, tab switching) preserves `?embedded=true` automatically.

### Verification

- confirm the Overview opens with the `Key Performance Indicators` section showing three animated Chart.js charts in order — `QR Codes by Asset Type` (ME/EL/BF bars with the Mechanical/Electrical/Backflow legend), `Performance Control KPI` gauge (center % + 90% target tick + `Approved` / `Remaining to target` legend), `Overall Approval` ring (center total + counted legend + percent labels on both segments, no top chip) — with no `Applications` launch cards, and that card titles render Title Case in the SDI-pipeline heading style (Arial bold, UBC blue).
- confirm hover tooltips show counts and percentages on all three charts, the charts stay sharp when browser zoom changes, and `GET /api/overview/kpis` returns JSON (login required).
- confirm the fallback: blocking the Chart.js CDN (DevTools network block) swaps all three canvases for the static SVG images.
- confirm the iframe loads only on first activation and does not reload when the user revisits the tab during the same session.
- confirm cookies on the sub-app domain show `SameSite=None; Secure; HttpOnly` (DevTools → Application → Cookies).
- confirm the sub-app's own login is not requested inside the iframe when the user is already logged into the Dashboard.
- confirm the `Dashboard` button in the process-view-header returns to the central main view without a full page reload.
- confirm the sub-app standalone URL (without `?embedded=true`) still renders its own navbar and chrome unchanged.

## Disposed Assets

The Dashboard hosts a `Disposed` tool that withdraws a QR-coded asset from the capture -> review -> SDI pipeline and keeps a full record of why. It is an ERP-style retirement: a posted transaction with a reason code, an actor and an immutable snapshot, reversed only by a documented counter-transaction. Reading the register requires only login; disposing and restoring require the `operations` / `disposed_assets` **editor** grant, given per user through the User Admin screen (`Perms` → Operations → Disposed Assets → Editor → Save). The platform `Admin` flag alone does not grant it: `has_permission()` resolves an exact section/item grant and never treats admin as a wildcard.

### Steps

1. Open `Operations & Monitoring -> Disposed` (tile `Disposed`, hash `#disposed`).
2. On the **Dispose Asset** tab, enter a QR code and press `Search`. The asset card shows its photos and extraction data; the **Eligibility** card shows the server's disposal gate check by check.
3. All four checks must pass: the QR exists, it is not already disposed, it is **not approved**, and it is not in an SDI package. A failed check explains why and the `Dispose asset` button stays disabled.
4. Choose a **Reason** (`Decommissioned`, `Duplicated`, `Wrong Asset`, `User Request`) - it is always required - and optionally add notes.
5. Press `Dispose asset`. The confirmation dialog asks `Dispose asset <QR>?` and restates the QR, discipline, building and reason; press `OK` to post it or `Cancel` to back out (the type-the-QR step was removed 2026-08-12). On success the view switches to the register.
6. The **Disposed Register** tab lists every disposal event with filters (search, reason, discipline, status, date range). `View` opens the full record: the disposal details, the extraction data, the curated row as it was at disposal, the QR record, and the photos. `Restore` (admin only) reinstates the asset.

### What disposal does

- Archives a full snapshot into `disposed_assets` and deletes the curated `sdi_dataset` / `sdi_dataset_EL` row, in one transaction.
- Hides the asset from the ME/BF/EL review dashboards (tabs and KPI counts), blocks approving it, blocks re-capturing it in the mobile app, and takes it out of the AI extraction queue.
- Touches **no files**: photos and `Output_jason_api` payloads stay on disk, which is what lets Restore rebuild the curated row later.
- Writes `audit_trail` rows (`app_name="dashboard_disposed"`, `source="human"`) inside the same transaction, so disposals appear in User Activity.

### Verification

- The disposed QR is gone from the relevant review dashboard, on every tab, and its KPI counts drop by one.
- `SELECT * FROM "disposed_assets" WHERE "qr_code" = '<QR>'` shows one row with `status = 'disposed'`.
- The curated row is gone: `SELECT * FROM "sdi_dataset" WHERE "QR Code" = '<QR>'` (or `sdi_dataset_EL`) returns nothing.
- The photos and JSON are still on disk.
- After `Restore`, the curated row is back and the asset reappears in the reviewer; the disposal row stays as `status = 'restored'`.

## Life Cycle Assessment

The Dashboard hosts a `Life Cycle Assessment` feature that surfaces asset age and life-cycle data. It runs as an in-process Flask Blueprint (`life_cycle`) mounted inside the Dashboard app at the `/life-cycle` prefix; it is not a separate service or port, and missing dependencies degrade to "feature absent" without crashing the portal. Access requires login plus the `lifecycle_assessment` viewer permission, granted per-user through the User Admin screen.

### Steps

1. From the shared shell sidebar, open `Operations -> Life Cycle Assessment` (directly below `FLS Devices`). It links to the standalone `/life-cycle/` full page.
2. Choose an asset group from the dropdown (default placeholder `Choose an asset group to begin`); the tables stay hidden until a group is selected. The current build targets the Mechanical group `ME.91.902.4817.5956` (Heating Water Storage Tanks).
3. Review rows split into `Complete` and `Incomplete` tabs. A row is `Complete` only when Make, Space Number, Serial Number, and Installation Date are all present; otherwise it is `Incomplete`.
4. Read the **Age Classification donut chart** for the selected group: `Good` (<= 8 yrs), `Caution` (8-10 yrs), `Critical` (>= 10 yrs), and `Unknown` (no installation date), each with a count and percentage. A companion **Life-cycle Expiry** bar chart plots assets by the year they reach 10 yrs of service (installation year + 10) against years in service, with a dashed 10-yr line and bars coloured by age band. Both are empty when no group is selected.
5. Click `Export` to download the filtered view as a styled XLSX file.
6. Use `Update Database` to upload an Excel workbook and rebuild the tables, then reload. This is destructive: it drops and rebuilds `life_cycle` and `space_floor`, so the `assetcap_app` DB user needs DDL privileges.

### Current Rules

- `life_cycle` is the main table, rebuilt on every `Update Database` run from the Excel workbook filtered to Asset Group Code `ME.91.902.4817.5956`, with Floor Name joined from the SpaceUID table.
- `space_floor` is a deduplicated reference table (Property Code, Space Number -> Floor Name) rebuilt on every load; `life_cycle` carries a composite FK to it.
- `life_cycle_meta` is a small key/value table that survives the rebuild and stores the `last_loaded` timestamp shown in the dashboard footer.
- at read time the page derives a `Captured` flag (QR present in `QR_codes` with a `date_set` — i.e. field-captured) and a `Capture Date` (`QR_codes.date_set`) per row from read-only inputs.
- the footer shows `Source: <db>.life_cycle` with the actual DB name parsed from the live DSN.

### Verification

- confirm the sidebar `Life Cycle Assessment` item opens the `/life-cycle/` full page and requires the `lifecycle_assessment` permission.
- confirm the tables stay hidden until an asset group is selected and the **Age Classification donut** + **Life-cycle Expiry** charts reflect the selected group.
- confirm `Complete` and `Incomplete` tabs split rows by Make, Space Number, Serial Number, and Installation Date completeness.
- confirm `Export` downloads the filtered view as a styled XLSX.
- confirm `Update Database` uploads a workbook, rebuilds `life_cycle` and `space_floor`, refreshes the `last_loaded` footer timestamp, and reloads.
