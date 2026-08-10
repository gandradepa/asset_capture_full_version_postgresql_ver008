# Asset Dictionary Rules

Current documentation refresh: 2026-08-10.

## Purpose

The dictionary application edits Python-backed dictionary files without executing them.

## Read / Write Rules

- Read dictionary content with `ast.parse()` and `ast.literal_eval()`.
- Do not import the dictionary module to mutate its contents.
- Write files with `encoding='utf-8'`.
- Save deterministic output so diffs stay readable.

## Key Rules

- Composite keys are the preferred format:
  `TAG|TYPE`
- Normalize keys to uppercase before save.
- Support legacy simple keys for read compatibility, but migrate them to composite form on save when appropriate.

## Source Files

- Mechanical and electrical dictionaries are Python files in `dictionary/`.
- The UI layer in `dictionary_app.py` is responsible for safe parsing and safe overwrite behavior.

## DB-Backed Helper Data

- Dropdown helper values may be fetched from PostgreSQL `qr_code_db` through the shared DB layer.
- The dictionary app must not crash if the shared operational DB is temporarily unavailable.

## Safety Rules

- Never use `eval()`.
- Never allow arbitrary Python expressions from user input into the saved dictionary file.
- Preserve valid Python syntax on every write.

## Dashboard Dictionary Editing

- The Dashboard provides a UI for editing the mechanical dictionary via `read_dictionary()` / `save_dictionary()`.
- These functions use the same AST-safe approach: read with `ast.parse()` + `ast.literal_eval()`, write with `json.dumps()`.
- Updates from the Dashboard target `dictionary/mechanical_dictionary.py`.
- Output is sorted deterministically so diffs stay readable.
- Editing happens in a modal (`#assetModal`); deletion is type-to-confirm. There is no inline row editing.
- Asset Type is restricted to **ME / EL / BF**, enforced server-side by `DICTIONARY_ALLOWED_TYPES` (`400` on anything else) as well as in the modal dropdown. `BP` is not a platform discipline code and must not be reintroduced.
- Access splits by the `dictionary/dictionary` permission: **viewer** gets a read-only table (no Add button, no Actions column), **editor** gets full CRUD. The API decorators remain authoritative; the UI flag only hides actions that would fail.
- Every save and delete writes `audit_trail` rows (`app_name="dashboard_dictionary"`, `table_name="mechanical_dictionary"`, `record_pk="<TAG|TYPE>"`, `source="human"`), one row per changed field plus a synthetic `dictionary_key` field for renames. Auditing is best-effort on its own connection and never fails the write. Details: `dashboard.rules.md` → "Dictionary Audit Rules".

Despite the filename, `mechanical_dictionary.py` holds **all** disciplines (ME, EL, BF) in one `ASSET_DICTIONARY` literal — there is no per-discipline dictionary file. `dictionary/electrical.dictionary.py` is a different structure (EL panel label schema) and is not managed by this UI.
