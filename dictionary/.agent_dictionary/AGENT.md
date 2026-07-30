# Asset Dictionary Application â€” Agent Instructions

Current documentation refresh: 2026-04-28.

## Application Identity

The **Asset Dictionary** module consists of two components:
1. Static Python dictionary files (`mechanical_dictionary.py`, `electrical.dictionary.py`) used by the Review Apps and API extraction processes for metadata lookup.
2. A Flask-based CRUD web application (`dictionary_app.py`) that allows administrators to visually manage, add, edit, and safely deploy updates to the `mechanical_dictionary.py` file through a web UI, without requiring them to write raw Python code.

**Location**: `/home/developer/dictionary/` (Production)

---

## Architecture Overview

```text
dictionary/
â”œâ”€â”€ .agent_dictionary/          # This documentation directory
â”œâ”€â”€ dictionary_app.py           # Flask CRUD application
â”œâ”€â”€ mechanical_dictionary.py    # Generates ASSET_DICTIONARY (Managed by App)
â”œâ”€â”€ electrical.dictionary.py    # Generates label_schema (Static)
â”œâ”€â”€ templates/
â”‚   â””â”€â”€ index_dictionary.html   # Main SPA for dictionary management
â””â”€â”€ static/                     # CSS/JS
```

---

## Core Operational Logic (`dictionary_app.py`)

Unlike traditional Flask apps that connect to a SQL database, the Dictionary App connects directly to a `.py` file. It reads and writes Python source code.

### 1. File Resolution (Fallback Strategy)
The app resolves the dictionary path safely via `get_candidate_paths()`. Priority order:
1. Environment Variable: `DICTIONARY_FILE_PATH`
2. Server Absolute Path: `/home/developer/dictionary/mechanical_dictionary.py`
3. Local Relative Path: `mechanical_dictionary.py` (next to `dictionary_app.py`)

### 2. AST Reading (`read_dictionary()`)
Instead of executing the untrusted file, the app uses `ast.parse()` to build an Abstract Syntax Tree. It hunts for the `ASSET_DICTIONARY` variable assignment and securely evaluates the dictionary literal via `ast.literal_eval()`.

### 3. File Writing (`save_dictionary()`)
When saving, the backend serializes the updated dictionary using `json.dumps(..., indent=4)`, prepends `ASSET_DICTIONARY = `, and overwrites the `.py` file. This guarantees well-formatted, syntax-error-free Python dictionary structures.

### 4. Legacy Key Migration
The dictionary initially used simple keys like `AHU`. To support Electrical assets colliding with Mechanical prefixes (e.g., `T-`), the system transitioned to composite keys: `TAG|TYPE` (e.g., `AHU|ME`). 

When processing saves (`/api/assets` via POST), the backend actively scrubs and migrates legacy keys. If it finds `AHU`, it converts the stored key to `AHU|ME` during the save operation.
