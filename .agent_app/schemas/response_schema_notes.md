# Response Schema Notes

Current documentation refresh: 2026-05-25.

## Top-Level JSON Shape

All extraction outputs should use this top-level structure:

- `qr_code`
- `building_number`
- `asset_type`
- `structured_data`
- `completeness_score`
- `confidence_scores`
- `Avg_ai_conf`
- `modified`

## Discipline Field Notes

### ME

Expected core fields:

- `Manufacturer`
- `Model`
- `Serial Number`
- `Year`
- `UBC Tag`
- `Technical Safety BC`

Rules:

- `Technical Safety BC` is only part of completeness and AI confidence when seq `-3` exists.
- blank final fields must not keep a stale non-zero confidence value.

### BF

Expected core fields:

- `Manufacturer`
- `Model`
- `Serial Number`
- `Diameter`

### EL

Expected raw/review JSON fields:

- `UBC Asset Tag`
- `Branch Panel`
- `Ampere`
- `Supply From`
- `Volts`
- `Location`
- `Power Rating`
- `Power Rating (UoM)`

Rules:

- completeness uses only `UBC Asset Tag`, `Ampere`, and `Supply From`
- AI confidence averages exclude `Volts`, `Location`, and `Branch Panel`
- legacy `Fed From` and `Fed` values should be normalized into `Supply From`
- transformer power rating is retained only with explicit transformer tag/source evidence
- Planon-facing canonical EL fields are stored in curated DB/package rows, while raw extraction still keeps compatibility fields such as `Ampere`, `Volts`, and `Supply From`

## Confidence Rules

- `confidence_scores` is field-level evidence, not a guarantee that the value is approved
- `Avg_ai_conf` should be based on the discipline-aware included fields only
- blank final fields should resolve to `0` confidence, not a retained prior score
- when source confidence is missing, synthesized confidence must come from current evidence, not from invented defaults

## Extra Photo Sequence (Out of Schema)

The optional **Extra Photo** sequence (ME `-4`, BF `-3`, EL `-3`) does not contribute to any extraction-schema field. The pipeline's `VALID_SUFFIXES` excludes it, so the LLM never sees the file and the resulting JSON cannot reference any value derived from it. Schema validators do not need to add any field for the Extra Photo.

## Curated Field Notes

Review may add or preserve curated fields such as:

- `Asset Group`
- `Attribute`
- `Description`
- `Main Asset`
- `ExcludeSDI`
- `Approved`
- `Flagged`

These may be absent in raw extraction output and added later during review.

## Current Curated EL Fields

EL review and SDI sync currently maintain canonical Planon-facing fields in `sdi_dataset_EL` and package tables, including:

- `Amperage Rating`
- `Amperage Rating (UoM)`
- `Voltage Rating`
- `Voltage Rating (UoM)`
- `Equipment ID`
- `Equipment Type`
- `Fed From Equipment ID`
- `Fed From Amperage Rating`
- `Fed From Amperage Rating (UoM)`
- `Power Type`
- `Power Rating`
- `Power Rating (UoM)`

Note (2026-06-12): `Fed From Amperage Rating` (+ UoM) is derived from the SLD diagram table (`electrical_building_schema`, active rows) by matching `Supply From` against the SLD `Equipment ID` in the same building. It stays blank when the building has no SLD data. It is not AI-extracted and not copied from sibling `sdi_dataset_EL` rows.
