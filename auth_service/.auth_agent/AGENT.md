# Shared Auth Service â€” Agent Instructions

Current documentation refresh: 2026-04-28.

## Application Identity

The **Auth Service** (`auth_service`) is a centralized authentication module shared across multiple Flask applications in the UBC Asset Management platform (Dashboard, Capture App, ME/BF/EL Plate Reviewers). 

It provides a single source of truth for user credentials, password hashing (bcrypt), session management (Flask-Login), and shared SQLAlchemy models.

**Location**: `/home/developer/auth_service/` (Production)

---

## Architecture Overview

```text
auth_service/
â”œâ”€â”€ .auth_agent/                 # Agent documentation (this directory)
â”œâ”€â”€ auth_controller.py           # Flask-Login initialization and user loader
â”œâ”€â”€ auth_model.py                # SQLAlchemy models (User, FLSAsset, Property, etc.)
â”œâ”€â”€ init_db.py                   # CLI script to initialize DB and create users
â”œâ”€â”€ reset_password.py            # CLI script to reset a user's password
â”œâ”€â”€ fix_user.py                  # CLI script to fix plain-text legacy passwords
â”œâ”€â”€ users.db                     # Primary SQLite user database
â””â”€â”€ venv/                        # Python virtual environment (if run standalone)
```

---

## Core Components

### 1. `auth_controller.py`
- Initializes the `LoginManager` from `flask_login`.
- Defines the `@login_manager.user_loader` decorator which queries `User.query.get(int(user_id))` on every authenticated request.
- Sets `login_manager.login_view = 'auth.login'` ensuring unauthenticated users are redirected to the local app's authentication blueprint.

### 2. `auth_model.py`
- Initializes decoupled SQLAlchemy (`db`) and Bcrypt (`bcrypt`) extensions.
- Defines the `User` model with `username`, `email`, and `password_hash`.
- Contains `set_password()` (generates bcrypt hash) and `check_password()` logic.
- **Shared Models**: Also hosts several other core data models used by the Dashboard (e.g., `FLSAsset`, `Property`, `Space`, `AssetGroup`, `DeviceTypeMap`).

### 3. CLI Management Scripts
Because there is no web frontend for user administration, all account creation and password resets happen via the command line on the server:
- `init_db.py`: Creates tables if missing and adds new users.
- `reset_password.py`: Resets the bcrypt hash for an existing username.
- `fix_user.py`: Specific repair script to delete a legacy plain-text user account and recreate it with a hashed password.

---

## Integration Patterm

Apps requiring authentication must do three things:
1. Include `auth_service` in their Python `sys.path`.
2. Import `db`, `bcrypt`, and `login_manager`.
3. Call `init_app(app)` on all three extensions during Flask initialization.

Each app implements its own local `auth` Blueprint to handle the actual `/login` and `/logout` rendering routes, but they all query the central `auth_service/users.db` via `auth_model.User`.
