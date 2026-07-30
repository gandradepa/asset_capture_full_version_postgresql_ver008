# Asset Dictionary Agent

Current documentation refresh: 2026-04-28.
## Purpose
Permit human administrators to visually configure classification boundaries without triggering destructive Python syntax errors.
## Scope
**In-scope**: Secure AST evaluations, composite key validation (`TAG|TYPE`). 
**Out-of-scope**: Live database edits. The output is purely a `.py` file definition.
## Inputs
End-user classification mappings provided via UI mapping grid.
## Outputs
Rewrite target: `mechanical_dictionary.py` defining an `ASSET_DICTIONARY` constant.
## Key Paths & Env Vars
- `DICTIONARY_FILE_PATH`
## Critical Conventions
- Force uppercase on all user inputs.
- Legacy Key coercion (converting `AHU` to `AHU|ME`) is mandatory on all save loops.
- `ast.literal_eval()` protects against RCE vectors.
## Validation Checklist
- [ ] The generated dictionary is output using standard Python indentation (`indent=4`).
- [ ] Dropdowns gracefully ignore database connectivity exceptions.
