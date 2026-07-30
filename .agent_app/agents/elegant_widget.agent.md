# User Access Control (RBAC) — Implementation Reference

**Branch:** `create_user_access_levels`  
**Last updated:** 2026-05-07  
**Status:** Fully implemented and deployed to VM.

---

## Group Hierarchy

| Group | Description |
|---|---|
| **Admin** | Highest access level for the specific section/item where assigned. Any Admin-level grant qualifies the user for RBAC management routes, but does not grant access to unrelated items. |
| **Editor** | View + modify. Configured per section/item. |
| **Viewer** | View only. Configured per section/item. |

A user can hold mixed assignments simultaneously (Editor on item A, Viewer on item B, no access to item C). `None` on an item blocks that item even when the user is Admin on other items.

---

## Application Registry

Defined in `auth_service/app_registry.py`. Edit only this file to add new sections/items — no DB migration needed.

```
Application
  ├─ asset_capture_mobile   → "Asset Capture Mobile App"
  ├─ reviewer_mechanical    → "Asset Reviewer - Mechanical"
  ├─ reviewer_backflow      → "Asset Reviewer - Backflow"
  ├─ reviewer_electrical    → "Asset Reviewer - Electrical"
  └─ sdi_process            → "SDI Process Application"

Operations & Monitoring
  ├─ logs_pending           → "Logs & Pending"
  ├─ reviewer_kpis          → "Reviewer KPIs"
  ├─ cost_analysis          → "Performance Analysis"
  ├─ dashboard_overview     → "Dashboard Overview"
  ├─ audit_trail            → "Audit Trail"
  └─ reports                → "Reports"

Dictionary
  (empty — items to be added later via app_registry.py)
```

---

## Core Files

| File | Role |
|---|---|
| `auth_service/auth_model.py` | `UserAccess` model, `has_permission()`, `is_admin()`, `require_permission` decorator, `access_denied_response()` helper, `ensure_user_access_table()` |
| `auth_service/app_registry.py` | Central registry of all sections/items |
| `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` | RBAC enforcement + admin panel routes |
| `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html` | Current Users panel, permission matrix UI, `{% if can_edit %}` gating |
| `Dashboard/Asset_portal_dashboard.py` | Dashboard portal + permission management API (`/api/admin/permissions/*`) |
| `Dashboard/templates/dashboard.html` | Dashboard-level Current Users panel JS |

---

## Permission Enforcement Pattern

### Rule: HTML routes use inline check; API/mutation routes use decorator

**HTML page routes** — return the Access Denied page on failure:
```python
@app.route("/")
@login_required
def index():
    if not has_permission(current_user, "section_key", "item_key", "viewer"):
        return access_denied_response("App Label")
    # ... rest of route
```

**API / mutation routes** — return JSON 403 on failure:
```python
@app.route("/toggle_approved/<doc_id>", methods=["POST"])
@login_required
@require_permission("application", "reviewer_backflow", "editor")
def toggle_approved(doc_id):
    # ...
```

### `access_denied_response(app_label, logout_url="/logout")`

Defined in `auth_service/auth_model.py`. Returns the standardised HTML 403 page matching the Asset Capture Mobile App pattern (red "Access Denied" heading, permission message, Log Out button in UBC blue).

**`logout_url` note:** All reviewer apps and Dashboard register `auth_bp` **without** a URL prefix → logout is at `/logout`. The Asset Capture Mobile App registers with `url_prefix='/auth'` → logout is at `/auth/logout`. The mobile app uses its own inline HTML and is unaffected by this helper.

---

## Route Enforcement by Application

### Asset Capture Mobile App — `asset_capture_app_dev/app.py`
`auth_bp` registered with `url_prefix='/auth'`.

| Route | Guard | Level |
|---|---|---|
| `GET /` | inline `has_permission` + HTML page | `viewer` |
| `GET /api/locations` | `@require_permission` | `viewer` |
| `GET /api/check-qr` | `@require_permission` | `viewer` |
| `POST /api/update-parameters` | `@require_permission` | `editor` |
| `POST /api/get-temp-code` | `@require_permission` | `editor` |
| `POST /capture` | `@require_permission` | `editor` |
| `POST /submit` | `@require_permission` | `editor` |
| `GET /submit/success` | `@require_permission` | `viewer` |
| `GET /uploads/<filename>` | `@require_permission` | `viewer` |
| `POST /delete-upload` | `@require_permission` | `editor` |

### Asset Reviewer — Backflow — `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py`

| Route | Guard | Level |
|---|---|---|
| `GET /` | inline `has_permission` + HTML page | `viewer` |
| `POST /review/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_approved/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_sdi/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_ai_status/<doc_id>` | `@require_permission` | `editor` |
| `GET /api/admin/*` (5 routes) | `@admin_required` | admin |

Template (`dashboard.html`): toggle cells (`ai-status-cell`, `sdi-cell`, `approved-cell`) and Review links are wrapped with `{% if can_edit %}` — Viewer users see values but cannot interact.

### Asset Reviewer — Mechanical — `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py`

| Route | Guard | Level |
|---|---|---|
| `GET /` | inline `has_permission` + HTML page | `viewer` |
| `GET /review/<doc_id>` | inline `has_permission` + HTML page | `viewer` |
| `GET /check_sdi/<qr_code>` | `@require_permission` | `viewer` |
| `GET /api/user-activity` | `@require_permission` | `viewer` |
| `GET /images/<filename>` | `@require_permission` | `viewer` |
| `POST /review/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_approved/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_ai_status/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_sdi/<doc_id>` | `@require_permission` | `editor` |
| `GET /health` | unprotected (monitoring) | — |

### Asset Reviewer — Electrical — `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
`auth_bp` registered without prefix. `@login_required` applied globally via `@main_bp.before_request`.

| Route | Guard | Level |
|---|---|---|
| `GET /` | inline `has_permission` + HTML page | `viewer` |
| `GET /review-all` | inline `has_permission` + HTML page | `viewer` |
| `GET /review-distribution` | inline `has_permission` + HTML page | `viewer` |
| `GET /review/<doc_id>` | inline `has_permission` + HTML page | `viewer` |
| `GET /api/asset-dictionary` | `@require_permission` | `viewer` |
| `GET /check_sdi/<qr_code>` | `@require_permission` | `viewer` |
| `GET /api/user-activity` | `@require_permission` | `viewer` |
| `GET /images/<filename>` | `@require_permission` | `viewer` |
| `GET /api/fed_from_lookup/<building>/<supply_from>` | `@require_permission` | `viewer` |
| `POST /review/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_approved/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_ai_status/<doc_id>` | `@require_permission` | `editor` |
| `POST /toggle_sdi/<doc_id>` | `@require_permission` | `editor` |

### SDI Process — `SDI_process/app.py`

| Route | Guard | Level |
|---|---|---|
| `GET /` | inline `has_permission` + HTML page | `viewer` |
| `GET /api/validation_logs` | `@require_permission` | `viewer` |
| `GET /api/validation_log/<filename>` | `@require_permission` | `viewer` |
| `POST /export` | `@require_permission` | `editor` |
| `POST /exclude_package` | `@require_permission` | `editor` |
| `POST /move_to_archive` | `@require_permission` | `editor` |
| `POST /retrieve_from_archive` | `@require_permission` | `editor` |
| `POST /export-planon` | `@require_permission` | `editor` |
| `GET/POST /change-password` | `@login_required` only (user utility) | — |

### Asset Portal Dashboard — `Dashboard/Asset_portal_dashboard.py`

| Route | Guard | Level |
|---|---|---|
| `GET /` | inline `has_permission` + HTML page | `operations/dashboard_overview/viewer` |
| `GET /api/user-activity` | `@require_permission` | `operations/logs_pending/viewer` |
| `GET /api/reviewer-analysis/bar-hitboxes` | `@require_permission` | `operations/reviewer_kpis/viewer` |
| `GET /api/reviewer-analysis/hover` | `@require_permission` | `operations/reviewer_kpis/viewer` |
| `GET /api/admin/*` (5 routes) | `@dashboard_admin_required` | admin |
| Dictionary, FLS, map routes | `@login_required` only (not yet mapped to registry) | — |

---

## Permission Management UI

The "Current Users" admin panel lives in `Dashboard/templates/dashboard.html`. It is accessible only to users with any `admin`-level grant (`is_admin()` check).

The table exposes accessible sort controls on **ID**, **Username**, and **Name**. Username starts ascending; selecting the active header toggles direction, blank names remain last, and the implementation reorders existing rows so unsaved Name inputs are preserved. The permission matrix remains full width, with **Section / Item** sized to its longest section or item label and None, Viewer, and Editor sharing the remaining width.

**API endpoints** (in `Dashboard/Asset_portal_dashboard.py`):

| Method | URL | Action |
|---|---|---|
| `GET` | `/api/admin/registry` | Returns full APP_REGISTRY |
| `GET` | `/api/admin/permissions/users` | Lists all users + their grants |
| `GET` | `/api/admin/permissions/<username>` | Gets grants for one user |
| `PUT` | `/api/admin/permissions/<username>` | Atomically replaces all grants for user |
| `DELETE` | `/api/admin/permissions/<username>` | Removes all grants for user |

The BF Reviewer also exposes its own 5-route admin API at `/api/admin/users/*` (protected by `@admin_required`).

---

## DB Schema

Table: `user_access` (in `users.db`, shared across all apps via `auth_service`).

```sql
CREATE TABLE user_access (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    section_key  VARCHAR(80) NOT NULL,
    item_key     VARCHAR(80) NOT NULL DEFAULT '',
    access_level VARCHAR(20) NOT NULL,   -- 'admin' | 'editor' | 'viewer'
    UNIQUE(user_id, section_key, item_key)
);
CREATE INDEX ix_user_access_user_id ON user_access (user_id);
```

`item_key = ""` is reserved for section-level grants (not currently used by the UI).  
Permission resolution: exact `section_key + item_key` match only. Rank: `admin=3`, `editor=2`, `viewer=1`.

---

## Deployment Notes

The VM apps are **not in a single git repo**. Deployment is via `scp`:

| Service | systemd | VM path |
|---|---|---|
| BF Reviewer | `assetcap-bf` | `/home/developer/review/Asset_dasboard_browser_BF/` |
| ME Reviewer | `assetcap-reviewme` | `/home/developer/review/Asset_dasboard_browser_ME/` |
| EL Reviewer | `assetcap-el` | `/home/developer/review/Asset_dashboard_browser_EL/` |
| SDI Process | `sdi_process` | `/home/developer/SDI_process/` |
| Dashboard | `assetcap-dashboard` | `/home/developer/Dashboard/` |
| Auth service | shared | `/home/developer/auth_service/` |

The ME Reviewer has its own nested git repo (`dev_review_ME` branch) with no working GitHub remote — always deploy via `scp`.

After deploying `auth_model.py`, all services that import it must be restarted:
```bash
sudo systemctl restart assetcap-bf assetcap-reviewme assetcap-el sdi_process assetcap-dashboard
```

---

## Verification Checklist

1. User with `None` on `reviewer_backflow` → visits BF `GET /` → sees HTML "Access Denied" page with "Log Out" button linking to `/logout`.
2. Same user attempts `POST /toggle_approved/<id>` → receives JSON `{"success": false}` 403.
3. User with `Viewer` on `reviewer_backflow` → sees the dashboard but toggle cells have no pointer cursor and Review links are hidden.
4. User with `Editor` on `reviewer_backflow` → sees interactive toggle cells and Review links.
5. Admin user → "Current Users" nav button appears in BF and Dashboard.
6. Permission matrix saves correctly: select mixed roles, Save → DB contains exact rows, no extras.
7. Clear All → all rows for that user removed from `user_access`.
8. Restart app with `user_access` table deleted → `ensure_user_access_table()` recreates it automatically.
9. Add a new item to `app_registry.py` → restart → new row appears in permission matrix UI without any DB migration.
