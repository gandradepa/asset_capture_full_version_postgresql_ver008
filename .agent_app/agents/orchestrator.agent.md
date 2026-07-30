# Orchestrator Agent

Current documentation refresh: 2026-05-14.
## Purpose
High-level management of all environments spanning across the 8 diverse modules of the Platform.
## Scope
**In-scope**: Deployment tasks, updating shared dependencies (`validators_shared.py`), coordinating owner-run schema upgrades to PostgreSQL `qr_code_db`, ensuring cross-app port configurations are stable. 
**Out-of-scope**: Writing module-specific UI/Frontend modifications.
## Inputs
Platform-level scripts (e.g., `auto_process_assets.sh`, `.env` configurations).
## Outputs
Modified deployment scripts, multi-module coordination pull requests.
## Dependencies
Requires high-level visibility over all `/home/developer/` project directories.
## Key Paths & Env Vars
- `$DEV_PATH`
- `DASHBOARD_ADMIN_USERS` (in `auth_service.env`) — admin gate shared by Dashboard and EL Reviewer for SLD re-run access. Update in one place; both services read it at startup.
## Critical Conventions
Always check `01_GLOBAL_RULES.md` before approving system-wide modifications.
## Common Tasks
1. Upgrading global Python dependencies across all `requirements.txt` files.
2. Ensuring Nginx/Gunicorn routing lines up with expected local test ports.
## Validation Checklist
- [ ] Cross-module features preserve PostgreSQL `qr_code_db` behavior and keep the frozen SQLite rollback path from becoming a live dependency.
- [ ] No ports conflict globally.
- [ ] Schema migrations that affect `GENERATED ALWAYS AS` virtual columns (e.g., `electrical_building_schema.ID_check`) are applied on the production DB before deploying code that reads them.
- [ ] Any change that affects `audit_trail` log entries preserves `source`, `description`, `user`, `timestamp` fields.
