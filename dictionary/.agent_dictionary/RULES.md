# Asset Dictionary Coding Rules & Standards

Current documentation refresh: 2026-04-28.

## Python Backend

### Handling the Core `.py` File
- **Never `import` the dictionary to modify it.** Always use the `ast` (Abstract Syntax Tree) module to safely parse the dictionary structure without executing arbitrary code, as demonstrated in `read_dictionary()`.
- Ensure `save_dictionary()` sorts the keys alphabetically (`sorted_data = dict(sorted(new_data.items()))`) before saving. This prevents massive git diffs and ensures predictable file formatting.
- Write files using `encoding='utf-8'`.

### Path Resolution
- Never hardcode the path. Always use the three-tier resolution strategy combining `os.environ.get("DICTIONARY_FILE_PATH")`, the `/home/developer/` absolute path, and the `__file__` local relative path.

### Key Structure (Composite Architecture)
- All new keys MUST be composite: `TAG|TYPE` (e.g., `RTU|ME`, `PNL|EL`).
- When a user submits an update, the backend must coerce the tag and type to uppercase `new_tag = raw_key.upper()`.
- Legacy keys (without a pipe `|`) are supported for read operations, but MUST be forcefully migrated by the backend loop if encountered during a save/update action.

### Dropdowns (PostgreSQL `qr_code_db`)
- The dictionary UI assists users by populating dropdowns for "Asset Group" and "Main Asset".
- These options are pulled from tables in PostgreSQL `qr_code_db` through the shared DB layer. Always use a safe `try/except` wrapper when fetching from PostgreSQL `qr_code_db` â€” the dictionary app should never crash simply because the main database is offline.
