# Life Cycle Assessment

Integrated and deployed to production: 2026-06-23.

## Overview

The **Life Cycle Assessment** feature is a dashboard that surfaces asset age and life-cycle data for the Mechanical asset group **`ME.91.902.4817.5956`** (Heating Water Storage Tanks). Rows are split into **Complete** and **Incomplete** tabs by data completeness, and the selected group is summarised by an **Age Classification donut chart** plus a **Life-cycle Expiry bar chart** (see [Charts](#charts)). The age buckets are:

- **Good** — `<= 8` years
- **Caution** — `8` to `10` years
- **Critical** — `>= 10` years
- **Unknown** — no installation date

## Integration

The feature is an **in-process Flask Blueprint** named `life_cycle`, mounted **inside** the existing Dashboard app (`Asset_portal_dashboard.py`) at `url_prefix="/life-cycle"`. It is **not** a separate service or port — it runs as part of the **`assetcap-dashboard`** systemd service (gunicorn, port `8002`, `dashboardprod.assetcap.facilities.ubc.ca`).

Blueprint registration is wrapped in `try/except` so that a missing dependency degrades to "feature absent" without crashing the portal.

## Code Layout

- **`Dashboard/life_cycle/`** package:
  - `__init__.py` — exports `life_cycle_bp`
  - `blueprint.py` — routes + data access
  - `excel_export.py` — styled XLSX export
  - `static/css/styles.css`
  - `static/js/dashboard.js`
  - `static/img/ubc-facilities_logo.jpg`
  - `templates/life_cycle/dashboard.html`
- **`life_cycle_pipeline/`** package (sibling of `Dashboard`; on the VM at `/home/developer/life_cycle_pipeline`):
  - `track_assets.py` — builds a DataFrame from the Excel workbook, joins Floor Name from `SpaceUID`
  - `load_life_cycle.py` — loads into PostgreSQL
  - `UBC - Asset Basic Info.xlsx` — default source workbook
  - `__init__.py`

## Routes

All routes are mounted under the `/life-cycle` prefix.

| Method | Route | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/life-cycle/` | Login + `lifecycle_assessment` viewer permission | HTML dashboard page |
| `POST` | `/life-cycle/export` | Login + permission | Styled XLSX of the visible rows |
| `POST` | `/life-cycle/refresh` | Login + permission | Uploads an Excel workbook and rebuilds the tables |
| `GET` | `/life-cycle/health` | Open (no auth) | Liveness probe |

Blueprint static assets are served at `/life-cycle/static/...`.

## Permissions (RBAC)

A new key was added to `auth_service/app_registry.py`:

- section: **`operations`**
- item: **`lifecycle_assessment`**

Routes enforce it via `has_permission` / `require_permission`, the **same model as FLS Devices**: enforced server-side; the sidebar link itself is **not** visibility-gated. Permission is granted per-user (viewer / editor) through the Dashboard **User Admin** screen.

## Navigation

A **"Life Cycle Assessment"** item lives in the shared shell sidebar (`Dashboard/static/shell/shell.js`) under the **"Operations"** group, directly below **"FLS Devices"**, with icon `activity`. It links to the standalone full page:

```
https://dashboardprod.assetcap.facilities.ubc.ca/life-cycle/
```

The nav entry and breadcrumb (**Home / Operations / Life Cycle Assessment**) were propagated to all five `shell.js` copies (Dashboard, ME / BF / EL reviewers, SDI Process). Shell assets are cache-busted via the `?v=` query in each app's `_shell.html`. The standalone `/life-cycle/` page also mounts the shared shell (`acshell-active "life"`).

## Database Tables

PostgreSQL: `qr_code_db` on `127.0.0.1:5433` in production; `qr_code_db_sandbox` on `:5432` in dev.

### `life_cycle`

Main table, **rebuilt on every "Update Database" run** via pandas `to_sql(if_exists="replace")`. All columns are `TEXT` except `years` (float) and `months` (integer). Built from the Excel workbook filtered to **Asset Group Code = `ME.91.902.4817.5956`**, with **Floor Name** joined from the `SpaceUID` table.

### `space_floor`

Deduplicated reference table (`Property Code`, `Space Number` -> `Floor Name`) built from `SpaceUID` with a `PRIMARY KEY`; rebuilt on every load. `life_cycle` carries a composite FK **`life_cycle_space_floor_fkey`** referencing `space_floor`.

### `life_cycle_meta`

Small key/value table that **survives** the `life_cycle` rebuild; stores the `last_loaded` timestamp shown in the dashboard footer.

### Read-time Captured flag and Capture Date

At read time the page also derives, per row:

- a **"Captured" flag** — QR present in `QR_codes` with a `date_set` (i.e. field-captured)
- a **"Capture Date"** — `QR_codes.date_set`

Those existing tables are **read-only inputs**.

## DB Configuration

The connection is derived from a **single source**, in this order of precedence:

1. env var **`LIFE_CYCLE_DSN`** (libpq DSN), if set
2. else the portal's **`QR_PG_DSN`**
3. else the **dev sandbox default**

The SQLAlchemy URL **`LIFE_CYCLE_SA_DSN`** (used by `track_assets.py` and `load_life_cycle.py`) is derived from that libpq DSN at blueprint import. `load_life_cycle.py`'s formerly hardcoded `DB_URL` is now read from `LIFE_CYCLE_SA_DSN`.

The footer line **`Source: <db>.life_cycle`** shows the actual DB name parsed from the live DSN (`qr_code_db` in prod).

## Dependencies

The blueprint and pipeline need the following on top of Flask: `pandas`, `numpy`, `sqlalchemy`, `openpyxl`, `psycopg2(-binary)`, `Pillow`. These were added to `Dashboard/requirements.txt`; on the VM only `openpyxl` was missing and was installed into the Dashboard venv.

## Completeness and Classification Rules

- **Completeness**: a row is **"Complete"** only when **Make**, **Space Number**, **Serial Number** AND **Installation Date** are all present; otherwise it is **Incomplete**.
- **Age Classification**: Good (`<= 8` yrs), Caution (`8`-`10` yrs), Critical (`>= 10` yrs), Unknown (no installation date).

## UI

- An **asset-group dropdown** gates the tables (default placeholder "Choose an asset group to begin").
- The **Age Classification donut** and the **Life-cycle Expiry** bar chart (see [Charts](#charts)) reflect the currently-selected group (empty when none selected).
- **Export** downloads the filtered view as XLSX.
- **Update Database** uploads an Excel workbook and rebuilds the tables, then reloads.

## Charts

Beside the asset-group selector, two linked charts summarise the selected group. Both are rendered client-side from the table rows (no extra endpoint) and update on every group change.

### Age Classification donut

An **SVG donut chart** (a ring with a centre total and a four-row legend) that **replaced** the original horizontal-bar measure. Each legend row shows the bucket's **count** and **percentage**:

- **Good** — `<= 8` yrs (green)
- **Caution** — `8`-`10` yrs (amber)
- **Critical** — `>= 10` yrs (red)
- **Unknown** — no installation date (grey)

The ring uses `r = 15.9155` so its circumference is exactly `100`, letting each bucket's percentage map straight to `stroke-dasharray`; segments chain clockwise from 12 o'clock.

### Life-cycle Expiry bar chart

A vertical bar chart that reads the life cycle as a timeline:

- **X-axis** = the **expiry year** = *installation year + 10* (the calendar year the asset reaches 10 years of service).
- **Y-axis** = **years in service** (0-30 scale, auto-extending for older cohorts), with a dashed horizontal **10-year expiry line**.
- Each bar rises to that cohort's current age and is **coloured by age band** (red `>= 10` already expired / expiring, amber `8`-`10`, green `<= 8` future expiry); the **asset count** is labelled on each bar.
- Bars left of "now" sit above the 10-year line (already expired); bars to the right sit below it (future expiry).

Assets with **no installation date** have no age or expiry year, so they appear only as the donut's **Unknown** bucket and are **omitted** from the expiry chart. Consistency check: the year bars sum to the donut's **Critical** count.

**Implementation:** `static/js/dashboard.js` — `updateMeasure()` draws the donut segments + legend and calls `renderAgeChart()`; `ageDataFor()` groups dated rows by `installYear + 10`. Styles live in `static/css/styles.css` (`.lc-donut*`, `.lc-legend*`, `.lc-agechart*`). Each table `<tr>` in `templates/life_cycle/dashboard.html` carries `data-years` and `data-install-year` so the charts can be built without a server round-trip.

## Update Database Flow

`POST /life-cycle/refresh` uploads an Excel workbook and rebuilds the tables, then reloads the page.

> **WARNING — destructive.** `/life-cycle/refresh` drops and rebuilds `life_cycle` + `space_floor`, and requires **DDL privileges** for the `assetcap_app` DB user.

## Ops / Deploy Notes

- Runs as part of the **`assetcap-dashboard`** systemd service (gunicorn, port `8002`, `dashboardprod.assetcap.facilities.ubc.ca`).
- Blueprint registration is wrapped in `try/except`, so a missing dependency degrades to "feature absent" rather than crashing the portal.
- The `/life-cycle/health` route is open (no auth) for liveness probing.
- Integrated and deployed to production on **2026-06-23**.
