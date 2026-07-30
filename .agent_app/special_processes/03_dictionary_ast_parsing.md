# Special Process: AST Dictionary Parsing

Current documentation refresh: 2026-04-28.

## Why It Exists

The dictionary app edits Python-backed dictionary files. That is useful for the workers, but unsafe if handled with normal Python execution.

## Safe Read Flow

1. Load the dictionary file as text.
2. Parse with `ast.parse()`.
3. Find the assignment node for `ASSET_DICTIONARY`.
4. Read the value with `ast.literal_eval()`.

This avoids executing arbitrary code.

## Safe Write Flow

1. Normalize the incoming key set.
2. Serialize a pure data structure.
3. Write the Python file back as a deterministic assignment, not as evaluated code.

## Current Key Rule

- Composite keys are preferred:
  `TAG|TYPE`
- Legacy keys may still be read, but save operations should normalize them into the maintained format when applicable.

## Red Lines

- no `eval()`
- no dynamic import for mutation
- no user-controlled executable Python in the saved file
