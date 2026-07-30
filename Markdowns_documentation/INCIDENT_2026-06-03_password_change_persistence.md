# Incident - 2026-06-03 Password Change Persistence

## Summary

A user password reset from Dashboard **User Administration -> Reset Password** succeeded, but the user's later self-service password change did not persist. After logout, the user could not log in with the newly chosen password.

## Impact

- Affected self-service password changes from Dashboard `/change-password`.
- The duplicate SDI Process `/change-password` route had the same persistence bug.
- Admin reset and CLI reset were not broken because both used `User.set_password()`.
- A user already logged in after an admin reset could keep using the active Flask-Login session until logout/session expiry, masking the failed later self-change.

## Root Cause

The shared auth model persists passwords only in `User.password_hash`:

```python
password_hash = db.Column(db.String(128), nullable=False)
```

The broken self-service routes assigned an unmapped attribute:

```python
current_user.password = bcrypt.generate_password_hash(new_password).decode("utf-8")
```

SQLAlchemy ignored that transient attribute during commit. The route flashed success, but the stored `password_hash` remained unchanged.

## Fix

Both self-service routes now use the model helper that writes the mapped column:

```python
current_user.set_password(new_password)
db.session.commit()
```

Changed production paths:

- `/home/developer/Dashboard/Asset_portal_dashboard.py`
- `/home/developer/SDI_process/app.py`

Local repository paths now match the VM byte-for-byte:

- `Dashboard/Asset_portal_dashboard.py` SHA256 `03ddcbc024bb3be3a6a53ee96455c9beee85022ce923f5a3c1d783229eb6be10`
- `SDI_process/app.py` SHA256 `11453a7a4851e32c712d43fa9aef69bce0036bf3a9f08e0775c4324b720b3b30`

## VM Deployment

Backups created on the VM:

- `/home/developer/Dashboard/Asset_portal_dashboard.py.bak_20260603_103640_pre_password_change_fix`
- `/home/developer/SDI_process/app.py.bak_20260603_103640_pre_password_change_fix`

Validation:

- `python3 -m py_compile` passed for Dashboard, SDI Process, and `auth_service/auth_model.py`.
- Remote scan found `bad_assignment_count 0` in both changed files.
- `assetcap-dashboard` and `sdi_process` Gunicorn masters were reloaded with `HUP` because passworded `sudo systemctl restart` was unavailable.
- New workers started, both services remained `active`, and local VM HTTP probes on ports 8002 and 8003 returned expected `302` login redirects.

## Regression Coverage

Local regression test:

- `test/test_password_change_persistence.py`

The test parses Dashboard and SDI source and asserts the self-service routes call `current_user.set_password(new_password)` and do not assign `current_user.password`.

Full local test command:

```bash
python -m unittest discover -s test
```

Result: 19 tests passed.

## Operational Note

For any user who attempted a self-service password change before this fix, reset the user's password again from Dashboard User Administration or `/home/developer/auth_service/reset_password.py`, then have the user change it again through the fixed flow.
