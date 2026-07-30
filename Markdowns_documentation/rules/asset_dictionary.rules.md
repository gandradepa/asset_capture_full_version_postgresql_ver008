# Asset Dictionary Rules

Current documentation refresh: 2026-04-28.

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
