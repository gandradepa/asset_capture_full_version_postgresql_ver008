---
name: run_extraction
description: Executes asset data extraction scripts (ME, EL, BF) via command line with hybrid OCR and completeness guard.
---

# Run Extraction Skill

Current documentation refresh: 2026-04-28.

## Overview

This skill executes the core Python extraction scripts in the `API/` directory. Each script processes photos of industrial equipment nameplates from `Capture_photos_upload/`, extracts structured data using a hybrid OCR + LLM ensemble, and outputs validated JSON to `Output_jason_api/`.

## Scripts

| Asset Type | Script | Command |
|-----------|--------|---------|
| **Mechanical (ME)** | `API_interface_ME_ver00.py` | `python API/API_interface_ME_ver00.py` |
| **Electrical (EL)** | `API_interface_EL_ver00.py` | `python API/API_interface_EL_ver00.py` |
| **Backflow (BF)** | `API_interface_BF_ver00.py` | `python API/API_interface_BF_ver00.py` |

## CLI Arguments

All three scripts support:

| Argument | Example | Description |
|----------|---------|-------------|
| `--qr <code>` | `--qr 0000177289` | Process a single QR code only |
| `--debug` | `--debug` | Verbose logging with raw LLM responses |
| `--images-dir <path>` | `--images-dir /tmp/imgs` | Override input image directory |
| `--output-dir <path>` | `--output-dir /tmp/out` | Override JSON output directory |
| `--db <path>` | `--db /tmp/test.db` | Legacy rollback/testing SQLite path only; production uses `DB_BACKEND=postgres` / `QR_PG_DSN`. |

## Environment Variables

Ensure `.env` file exists (e.g., `API/OpenAI_key_giba.env`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | **Yes** | â€” | OpenAI API key |
| `DEV_PATH` | No | `/home/developer` | Root path for all operations |
| `ME_OCR_MODE` | No | `light` | ME OCR strategy: `off`, `light`, `full` |
| `BF_OCR_MODE` | No | `full` | BF OCR strategy |

## Extraction Pipeline

Each script follows this processing pipeline per asset:

```
1. Discover     â†’ Scan image folder, group by QR code + sequence
2. Filter       â†’ Skip if QR is already approved/flagged or ai_status=1
3. Preprocess   â†’ CLAHE + adaptive thresholding for dirty plates
4. OCR          â†’ Run Tesseract with multiple rotations + PSM modes
5. LLM Call     â†’ Send images + OCR context to GPT-4o with Pydantic schema
6. Validate     â†’ Normalize all fields via validators_shared.py
7. Guard        â†’ Compare completeness score with existing JSON
8. Save         â†’ Write JSON if new score â‰¥ existing score
9. Status       â†’ Mark ai_status=1 in database
```

## OCR Strategies

### Hybrid Context Injection (ME, EL)
OCR text is extracted from images and injected as context into the LLM prompt. The LLM uses both visual analysis and OCR hints to extract fields. Controlled by `*_OCR_MODE` env variable.

- **`off`**: No Tesseract calls, LLM vision only
- **`light`** (default): Run OCR only for fields where LLM output is weak or missing
- **`full`**: Always run OCR for every field

### Extreme Optimized OCR (BF)
Reduced to 4 OCR calls per image:
1. Adaptive threshold at 0Â° â†’ PSM 6 (block text)
2. Adaptive threshold at 0Â° â†’ PSM 11 (sparse/vertical)
3. Adaptive threshold at 90Â° CW â†’ PSM 6
4. Adaptive threshold at 90Â° CCW â†’ PSM 6

## Completeness Guard

The completeness guard prevents regressions:

```
existing_score = completeness_score(existing_json)
new_score      = completeness_score(new_extraction)

if new_score >= existing_score:
    SAVE new JSON (overwrite)
else:
    SKIP save, but still mark ai_status=1
```

This ensures corrections (even with equal completeness) are applied, while preventing lower-quality re-extractions from overwriting good data.

## Pydantic Validation

All LLM outputs are parsed through strict Pydantic models:

| Script | Model Class | Fields |
|--------|------------|--------|
| ME | `MEStructuredExtraction` | Manufacturer, Model, Serial Number, Year, UBC Tag, Technical Safety BC |
| EL | `ELStructuredExtraction` | UBC Asset Tag, Ampere, Volts, Fed From, Phase, Location |
| BF | `BFStructuredExtraction` | Manufacturer, Model, Serial Number, Diameter |

All models use `ConfigDict(extra="forbid")` to reject unexpected fields and `field_validator("*")` to coerce `None` to empty strings.

## Field Normalization Reference

| Function | Script(s) | Rule |
|----------|-----------|------|
| `normalize_manufacturer()` | ME, BF | Match against known set: Watts, Wilkins, Conbraco, Apollo, Bell & Gossett, Armstrong |
| `normalize_model()` | ME, BF | FCU M1â†’MI, MLâ†’MI correction; strip special chars; cap at 80 chars |
| `normalize_serial()` | ME, BF | Alphanumeric + dash only; cap at 64 chars |
| `normalize_year()` | ME | Extract 4-digit year in 1950â€“2026 range |
| `normalize_diameter()` | BF | Parse fractions/decimals; ensure `"` suffix |
| `normalize_ampere()` | EL | Extract digits + `A` suffix |
| `normalize_volts()` | EL | Parse compound voltages (208/120V, 600/347V); returns the bare value (`208/120`) with the unit in `Voltage Rating (UoM)` |
| `normalize_supply_from()` | EL | Whitespace normalization; cap at 80 chars |
| `normalize_panel_tag()` | EL | Enforce grammar: `CDP-6-N-1-L-1`, `TX-N-0-1` |

## Usage Examples

### 1. Run full ME batch (all pending)
```bash
python API/API_interface_ME_ver00.py
```

### 2. Debug a specific EL asset
```bash
python API/API_interface_EL_ver00.py --qr 0000183710 --debug
```

### 3. Override paths (Windows development)
```powershell
python API\API_interface_BF_ver00.py --images-dir "C:\Temp\Images" --output-dir "C:\Temp\Output"
```

### 4. Run everything via shell automation (Ubuntu)
```bash
bash API/auto_process_assets.sh
```

## Output Format

Successful extraction creates JSON files:

```json
{
  "qr_code": "0000177289",
  "building_number": "2043",
  "asset_type": "- ME",
  "structured_data": {
    "Manufacturer": "Armstrong",
    "Model": "DESIGN ENVELOPE 4302",
    "Serial Number": "1234567890AB",
    "Year": "2019",
    "UBC Tag": "HUM-5",
    "Technical Safety BC": ""
  },
  "completeness_score": 80.0
}
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `OPENAI_API_KEY is not set` | Missing `.env` file | Create/copy `.env` file with API key |
| `TesseractNotFoundError` | Tesseract not installed or not in PATH | Install Tesseract; check `setup_environment()` path resolution |
| `Database not found` | Wrong `DEV_PATH` | Set `DEV_PATH` env variable to correct root |
| All assets skipped | All QRs already have `ai_status=1` | Reset `ai_status` in DB or use `--qr` to force single asset |
| Empty JSON output | LLM returned no data | Run with `--debug` to inspect raw LLM response |
| Completeness guard skipping | Existing JSON has higher score | Expected behavior; use `--debug` to compare scores |
| FCU M1 instead of FCU MI | Handwriting OCR misread | Automatic via `normalize_model()` M1â†’MI correction |
