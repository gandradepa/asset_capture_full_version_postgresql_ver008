---
description: Create new users and reset passwords using the auth_service CLI tools.
---

# User Management Workflow

Current documentation refresh: 2026-04-28.

Because there is no web-based UI for user administration, all user accounts for the Dashboard, Review Apps, and Capture App must be managed from the server terminal using the CLI scripts in `auth_service`.

## Prerequisites
- SSH access to the production Ubuntu server (`developer@...`) or a local terminal on Windows.
- Python `venv` activated.

---

## 1. Create a New User

Use the `init_db.py` script to safely hash and store a new user sequence.

```bash
# 1. Navigate to directory
cd /home/developer/auth_service

# 2. Activate environment
source venv/bin/activate

# 3. Run creation script (Username, Email)
python3 init_db.py new_username user@ubc.ca
```

The script will prompt you:
`Enter password for new_username:`

Alternatively, pass the password directly via `-p` (not recommended on shared systems as it records in bash history):
```bash
python3 init_db.py new_username user@ubc.ca -p "safe_password"
```

---

## 2. Reset a Password

If a user forgets their password, use the `reset_password.py` standard script.

```bash
cd /home/developer/auth_service
source venv/bin/activate

# python reset_password.py <username> <new_password>
python3 reset_password.py john_doe "new_secure_pass"
```

A success message `Password for user 'john_doe' has been successfully reset.` will appear.

---

## 3. Verify Database Entries

If you need to verify a user was created or deleted directly in SQLite:

```bash
sqlite3 users.db

sqlite> .headers on
sqlite> .mode column
sqlite> SELECT id, username, email FROM user;
sqlite> .quit
```
