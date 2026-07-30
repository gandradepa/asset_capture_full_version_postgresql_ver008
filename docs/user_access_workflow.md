# User Access Management — Workflow Guide

This guide is for **Administrators** of the AssetCapture platform. It explains how to navigate to the Current Users panel, assign and revoke permissions, and understand the role hierarchy.

---

## 1. Prerequisites

- You must be logged in as user **`gandrade`** or another account that has been granted Admin access.
- The "Current Users" button is **only visible to Admins**. Other users will not see it.

---

## 2. Navigating to the Current Users Panel

1. Log in to the **Asset Reviewer – Backflow** dashboard.
2. In the top-right header bar, locate the **"Current Users"** button (icon: group of people, filled).
3. Click it. The dashboard content is replaced by the Current Users admin panel.

---

## 3. The Current Users Panel

The panel shows a table of **all registered platform users** with the following columns:

| Column | Description |
|---|---|
| ID | Numeric user identifier |
| Username | Login username |
| Name | Editable display name (if set) |
| Email | Registered email address |
| Active | Activates or deactivates the account |
| Admin | Grants or removes site-administrator access |
| Actions | Saves user details or opens the permission matrix |

Select **ID**, **Username**, or **Name** in the table header to sort that column. Select the active header again to switch between ascending and descending order. Username is sorted ascending when the panel first loads.

---

## 4. Assigning Permissions

1. Click **"Perms"** on any user row.
2. The **Permission Matrix** appears below the table.
3. For each section and item, select one of:
   - **None** — user cannot see or access this item (default).
   - **Viewer** — user can view data but cannot save changes.
   - **Editor** — user can view and save/modify data.
4. Click **"Save"** to apply. A green banner confirms success.
5. Changes take effect on the user's **next page load** (current session is not interrupted).

The **Section / Item** column is sized to its longest label. The None, Viewer, and Editor columns share the remaining table width.

---

## 5. Role Descriptions

### Admin
- Full access to all sections, items, and the Current Users panel.
- No further item-level configuration needed.
- Only `gandrade` holds this role by default on first startup.

### Editor
- Can view data **and** save changes (approve assets, toggle AI status, save reviews).
- Scoped to specific sections/items. Example: Editor on "Asset Reviewer – Backflow" only.

### Viewer
- Can view data but all save/modify controls are hidden or disabled.
- Scoped to specific sections/items.

### None (default)
- User cannot access the section or item at all.
- Attempting to call a protected route directly will return a 403 error.

---

## 6. Mixed Permissions (Editor + Viewer)

A user can hold different levels for different items simultaneously. Example:

| Section | Item | Access |
|---|---|---|
| Application | Asset Capture Mobile App | Editor |
| Application | Asset Reviewer – Mechanical | Viewer |
| Application | Asset Reviewer – Backflow | None |
| Application | Asset Reviewer – Electrical | None |
| Application | SDI Process Application | None |
| Operations & Monitoring | *(all items)* | None |

---

## 7. Step-by-Step Example: Configuring `jjeon99`

1. Open the Current Users panel.
2. Click "Edit Permissions" for `jjeon99`.
3. Under **Application > Asset Capture Mobile App**: select **Editor**.
4. Under **Application > Asset Reviewer – Mechanical**: select **Viewer**.
5. Leave all other items as **None**.
6. Click **Save**. Confirm the green banner appears.
7. Log in as `jjeon99` to verify:
   - Asset Capture Mobile App shows edit/save controls.
   - Asset Reviewer – Mechanical shows a read-only view.
   - All other sections and items are inaccessible.

---

## 8. Revoking All Permissions

1. Click "Edit Permissions" for the target user.
2. Click the **"Clear All"** button (red, trash icon).
3. Confirm the dialog.
4. All grants for that user are removed immediately. The user will be unable to access any section on their next page load.

---

## 9. Frequently Asked Questions

**Q: Can a user be both Editor on one item and Viewer on another?**
A: Yes. The permission matrix assigns a level per item independently.

**Q: Does changing permissions log out the affected user?**
A: No. Changes take effect the next time the user loads a page or makes an API request.

**Q: Can I accidentally lock myself (gandrade) out?**
A: Yes — clicking "Clear All" on your own account removes your admin grant. If this happens, restart the app server; `_seed_admin("gandrade")` runs at startup and will re-grant admin access.

**Q: Who can access the Current Users panel?**
A: Only users with any Admin-level grant. Admins can grant Admin to other users from this panel.

**Q: What happens if I add a new app or section?**
A: Edit `auth_service/app_registry.py` to add the new section or item. It will appear in the permission matrix on the next server restart. No database migration needed.
