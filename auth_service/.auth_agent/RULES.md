# Auth Service Coding Rules & Standards

Current documentation refresh: 2026-04-28.

## Python Backend

### SQLAlchemy Extensions
- Extension objects (`db`, `bcrypt`, `login_manager`) must be initialized **without** an app context in `auth_service`.
- Host applications must import these decoupled extensions and execute `extension.init_app(app)` globally.
- Avoid circular imports by keeping the Flask `.route` logic completely separated into the host application's Blueprints.

### User Security
- **Never** store plain text passwords. All passwords must be hashed using `bcrypt.generate_password_hash()`.
- Password verification must exclusively use `bcrypt.check_password_hash()`.
- The `User` model must inherit `UserMixin` from `flask_login` to provide `.is_authenticated`, `.is_active`, and `.get_id()`.

### Database Path Fallbacks
- While specific host applications use environment variables for their specific DBs (like `QR_codes.db`), `auth_service` CLI scripts currently hardcode the production path for `users.db` (`sqlite:////home/developer/auth_service/users.db`).
- When modifying these CLI scripts for cross-platform local development, always introduce an `os.getenv("AUTH_DB_URI")` fallback.

### Shared Models
- Do not overload `auth_model.py` with app-specific queries. `auth_model.py` purely defines the table schemas.
- If a table is only ever used by a single application (e.g., the Capture app), it belongs in that app's directory, not in the shared `auth_service`. `auth_service` is only for models that must cross boundaries (Users, FLS Assets, Buildings).

## Command Line Administration
- Use `argparse` for CLI parameter collection (as seen in `init_db.py`).
- Fallback to `getpass()` if a password represents a sensitive input and shouldn't be recorded in bash history.
- Wrap all database operations in a temporary `with app.app_context():` block.
