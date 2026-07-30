---
name: dictionary_app
description: Developer skill guide for modifying the AST-based CRUD operations in the Dictionary management app.
---

# Asset Dictionary Application Skill

Current documentation refresh: 2026-04-28.

## Use this skill when
- Modifying how the `mechanical_dictionary.py` file is parsed or saved.
- Adding new fields to the schema of a dictionary entry.
- Modifying the legacy key migration logic inside `api/assets` (POST).
- Troubleshooting `ast.literal_eval()` failures.

## Do not use this skill when
- Modifying the `electrical.dictionary.py` structure (the `label_schema`), as the CRUD app does not manage it.
- Modifying how the API Extractors *consume* this dictionary (see `API/.agent`).

## Instructions

The Dictionary App operates on a sensitive feedback loop. It modifies a `.py` file that is imported natively by the API Extraction Scripts and the Plate Review Apps. 

If `save_dictionary()` introduces a syntax error into `mechanical_dictionary.py`, it will instantly break the extraction pipeline and crash the Plate Review web apps.

Always test saves locally. Look at the output of the local `mechanical_dictionary.py` after you hit the "Save" endpoint and verify that `ASSET_DICTIONARY = { ... }` is perfectly valid Python code.

## Modifying Entry Schemas
If you want to add a new property to all dictionary entries (for example, `maintenance_schedule`), you must:
1. Add it to the Javascript UI in `index_dictionary.html`.
2. Allow it in the backend save payload parser in `app.route('/api/assets', methods=['POST'])`:
```python
current_data[storage_key] = {
    "attribute_set": asset.get('attribute_set', ''),
    "asset_group": asset.get('asset_group', ''),
    "main_asset": asset.get('main_asset', ''),
    "description": asset.get('description', ''),
    "asset_type": new_type,
    "type": new_type,
    "maintenance_schedule": asset.get('maintenance_schedule', '') # NEW FIELD
}
```
