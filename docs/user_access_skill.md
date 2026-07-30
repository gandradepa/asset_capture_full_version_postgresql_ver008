# User Access Control — Technical Specification

This document describes the RBAC (Role-Based Access Control) system implemented in the AssetCapture platform.

---

## 1. Data Model

### Table: `user_access` (in `users.db`)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `user_id` | INTEGER FK | References `user.id` ON DELETE CASCADE |
| `section_key` | VARCHAR(80) | e.g. `"application"`, `"operations"` |
| `item_key` | VARCHAR(80) | e.g. `"reviewer_backflow"`. Empty string `""` = section-level grant |
| `access_level` | VARCHAR(20) | One of: `"admin"`, `"editor"`, `"viewer"` |

**Unique constraint**: `(user_id, section_key, item_key)` — one grant per user per position.

**Convention**: `item_key = ""` is reserved for section-level grants. Never define an item with key `""` in `app_registry.py`.

**Migration**: `ensure_user_access_table()` in `auth_service/auth_model.py` creates this table idempotently on startup.

---

## 2. Application Registry

**File**: `auth_service/app_registry.py`

This is the single source of truth for all sections and items displayed in the permission matrix.

```python
APP_REGISTRY = [
    {
        "key": "application",
        "label": "Application",
        "items": [
            {"key": "asset_capture_mobile", "label": "Asset Capture Mobile App"},
            ...
        ],
    },
    ...
]
```

### To add a new section
Append a new dict to `APP_REGISTRY`. Restart the server. No DB migration required.

### To add a new item
Append `{"key": "my_key", "label": "My Label"}` to the matching section's `"items"` list. Restart the server.

---

## 3. Permission Resolution Algorithm

Defined in `auth_service/auth_model.py` as `has_permission(user, section_key, item_key, required_level)`.

**Rank table**: `admin=3`, `editor=2`, `viewer=1`, absent=`0`

**Resolution order** (highest rank wins):

1. If the user has **any** row with `access_level='admin'` → **GRANTED** immediately (global admin).
2. Scan all grants for this user where `section_key` matches:
   - If `item_key = ""` (section-level): apply its rank.
   - If `item_key` exactly matches the requested item: apply its rank.
3. If `best_rank >= required_rank` → **GRANTED**, else **DENIED**.

**Shorthand helper**: `is_admin(user)` returns True if any admin grant exists for the user.

---

## 4. API Contract

All routes require `@login_required` and `@admin_required` (403 if not admin).

| Method | URL | Body | Response |
|---|---|---|---|
| GET | `/api/admin/registry` | — | `{success, registry: [...]}` |
| GET | `/api/admin/users` | — | `{success, users: [{id, username, name, email, grants}]}` |
| GET | `/api/admin/users/<username>/permissions` | — | `{success, username, grants}` |
| PUT | `/api/admin/users/<username>/permissions` | `{grants: [{section_key, item_key, access_level}]}` | `{success, message, grants}` |
| DELETE | `/api/admin/users/<username>/permissions` | — | `{success, message}` |

**PUT semantics**: Atomic replace — all existing grants for the user are deleted, then the new set is inserted in a single DB transaction. This prevents partial state.

**Error responses**: `401` if not authenticated, `403` if not admin, `400` if an invalid section/item key or access_level is provided.

---

## 5. Enforcement Patterns

### `admin_required` decorator
Gates admin management routes. Returns 403 JSON if `is_admin(current_user)` is False.

### `require_permission(section_key, item_key, level)` decorator factory
Gates specific mutation routes. Example:
```python
@app.route("/toggle_approved/<doc_id>", methods=["POST"])
@login_required
@require_permission("application", "reviewer_backflow", "editor")
def toggle_approved(doc_id): ...
```

### `has_permission(user, section_key, item_key, level)` helper
Use inline for context checks. Example:
```python
can_edit = has_permission(current_user, "application", "reviewer_backflow", "editor")
return render_template("dashboard.html", can_edit=can_edit, ...)
```

### Template-side gating (Jinja2)
```html
{% if can_edit %}
<button ...>Approve</button>
{% endif %}

{% if is_admin %}
<button id="show-current-users-btn">Current Users</button>
{% endif %}
```

---

## 6. Initial Admin Bootstrap

On every startup of `asset_plate_reviewer_bf.py`, the function `_seed_admin("gandrade")` runs inside `with app.app_context()`. It inserts an admin grant for `gandrade` if none exists, so the designated admin always retains access without manual SQL.

If `gandrade` is accidentally cleared via the UI, simply restart the server to restore the admin grant.

---

## 7. Files Changed

| File | Change |
|---|---|
| `auth_service/auth_model.py` | Added `UserAccess` model, `ensure_user_access_table()`, `has_permission()`, `is_admin()` |
| `auth_service/app_registry.py` | New file — central section/item registry |
| `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` | Added imports, `admin_bp` Blueprint, 5 API routes, `admin_required` + `require_permission` decorators, startup migration, `_seed_admin()`, `is_admin`/`can_edit` in template context |
| `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html` | Added "Current Users" nav button (admin-gated), `#current-users-view` panel, full JS block, fixed `mainContainer` selector |
| `Dashboard/Asset_portal_dashboard.py` | Added `ensure_user_access_table` import + call |
| `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` | Added `ensure_user_access_table` import + call |
| `SDI_process/app.py` | Added `ensure_user_access_table` import + startup block |
| `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` | Added `ensure_user_access_table` import + startup block |
| `docs/user_access_workflow.md` | New file — admin-facing usage guide |
| `docs/user_access_skill.md` | This file — technical specification |

---

## 8. Security Notes

- **Atomic replace**: The PUT endpoint deletes all grants and inserts fresh ones in a single `db.session.commit()`, preventing inconsistent intermediate states.
- **CSRF**: Mutation routes (PUT, DELETE) are called via same-origin AJAX with a session cookie (`SameSite=None; Secure; HttpOnly`). For production, consider adding a CSRF token header if the cookie policy changes.
- **Admin self-lockout**: An admin can clear their own grants via the UI. The `_seed_admin()` startup function mitigates this for `gandrade`. For other admins, manual re-grant via `gandrade` is needed.
- **Unique constraint**: The DB-level `UNIQUE(user_id, section_key, item_key)` constraint prevents duplicate grants even under concurrent requests.
- **Input validation**: All PUT payloads are validated against `app_registry.is_valid_item()` before any DB write. Unknown section/item keys return 400.
