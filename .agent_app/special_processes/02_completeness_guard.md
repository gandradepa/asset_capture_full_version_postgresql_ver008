# Special Process: Completeness Guard

Current documentation refresh: 2026-05-25.

## Purpose

The completeness guard prevents a later AI extraction pass from overwriting a better existing JSON payload with a weaker result.

## Current Save Rule

- If the new extraction is more complete than the existing JSON, save it.
- If the new extraction is equally complete, saving is still allowed.
- If the new extraction is less complete, skip the save.
- Even when skipping the save, update AI-processing state so the asset does not get stuck in a reprocessing loop.

## Discipline-Specific Required Fields

### ME

- `Manufacturer`
- `Model`
- `Serial Number`
- `Year`
- `UBC Tag`
- add `Technical Safety BC` only when seq `-3` exists

### BF

- `Manufacturer`
- `Model`
- `Serial Number`
- `Diameter`

### EL

- `UBC Asset Tag`
- `Ampere`
- `Supply From`

## Extra Photo Exclusion

The optional **Extra Photo** sequence (ME `-4`, BF `-3`, EL `-3`) is never part of completeness. It owns no fields and is excluded from each pipeline's `VALID_SUFFIXES`, so the LLM never sees it and the saved JSON cannot inherit any field values from it. Its presence or absence must also never trigger the "Missed Photo" flag in the review dashboards.

## Important Distinction

Completeness and `Avg_ai_conf` are related but separate. A field can count for completeness while using a different confidence inclusion rule, especially in EL and conditional ME cases.

## Human Review Override

The completeness guard is for extraction workers. Human review saves are allowed to replace the payload even when the result is less complete, because human curation is the higher-trust layer.
