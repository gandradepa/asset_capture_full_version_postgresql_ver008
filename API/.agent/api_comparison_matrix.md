# API Comparison Matrix (Updated)

Current documentation refresh: 2026-05-25.

This document compares the current state of the Mechanical (ME), Backflow (BF), and Electrical (EL) extraction scripts following the standardization refactor.

**All three scripts now share the same core architecture and best practices.**

## 1. High-Level Comparison

| Feature | ME (Mechanical) | BF (Backflow) | EL (Electrical) |
| :--- | :--- | :--- | :--- |
| **Script File** | `API_interface_ME_ver00.py` | `API_interface_BF_ver00.py` | `API_interface_EL_ver00.py` |
| **Lines of Code** | ~1,940 (ver04) | ~567 | ~532 |
| **Architecture** | **OOP** (Standardized Logging) | **OOP** (Standardized Logging) | **OOP** (Standardized Logging) |
| **Concurrency** | **ThreadPoolExecutor** (8) | **ThreadPoolExecutor** (8) | **ThreadPoolExecutor** (8) |
| **Config** | Class (`Config`) | Class (`Config`) | Class (`Config`) |
| **OCR Strategy** | **Hybrid** (Context Injection) | **Hybrid** (Extreme Optimized) | **Hybrid** (Context Injection) |
| **Guardrails** | **Completeness Check** | **Completeness Check** | **Completeness Check** |

## 2. Schema & Pydantic Models

| Detail | ME | BF | EL |
| :--- | :--- | :--- | :--- |
| **Pydantic Class** | `MEStructuredExtraction`, `MEReasonedExtraction` | `BFStructuredExtraction` | `ELStructuredExtraction` |
| **Key Fields** | Manufacturer, Model, Serial Number, Year, UBC Tag, Technical Safety BC | Manufacturer, Model, Serial Number, Diameter | UBC Asset Tag, Ampere, Volts, Fed From, Phase, Location |
| **Completeness Fields** | Manufacturer, Model, Serial Number, Year, UBC Tag | Manufacturer, Model, Serial Number, Diameter | UBC Asset Tag, Ampere, Volts, Fed From, Location |
| **Extra Config** | `extra="forbid"`, `str_strip_whitespace=True` | `extra="forbid"`, `str_strip_whitespace=True` | `extra="forbid"`, `str_strip_whitespace=True` |

## 3. Image Processing

| Detail | ME | BF | EL |
| :--- | :--- | :--- | :--- |
| **Image Sequences (LLM)** | `0` (Nameplate), `1` (UBC Tag), `3` (Tech Safety BC) | `0` (Nameplate), `1` (Context) | `0` (Asset Plate, optional), `1` (UBC Asset Tag), `2` (Panel Schedule) |
| **Captured Extra (excluded)** | `4` (**Extra Photo**) | `3` (**Extra Photo**) | `3` (**Extra Photo**) |
| **Valid Suffixes** | `{"0", "1", "3"}` | `{"0", "1"}` | `{"0", "1", "2"}` |
| **Filename Regex** (sequence class) | `[0-4]` | `[0-3]` | `[0-3]` |
| **Preprocessing** | CLAHE + Adaptive Threshold + Rotation (0Â°/90Â°CW/90Â°CCW) | Adaptive Threshold + Rotation | CLAHE + Adaptive Threshold |

The regex sequence class was widened to cover the optional Extra Photo so the file is discovered and logged as `invalid_seq` instead of `name_mismatch`. `VALID_SUFFIXES` is the actual LLM gate, and the Extra Photo's sequence is deliberately absent from it.

## 4. OCR Strategy Details

| Detail | ME | BF | EL |
| :--- | :--- | :--- | :--- |
| **Mode Env Var** | `ME_OCR_MODE` (default: `light`) | `BF_OCR_MODE` (default: `full`) | Follows ME pattern |
| **OCR Calls/Image** | Variable (**ver04:** Digital Rotation) | **4** (Extreme Optimized) | Variable (context injection) |
| **Context Injection** | OCR text â†’ LLM prompt as hint | OCR text â†’ LLM prompt | OCR text â†’ LLM prompt |
| **Rotation Strategy** | 0Â°, 90Â° CW, 90Â° CCW, 180Â° (ver04: seq 0,1,3) | 0Â°, 90Â° CW, 90Â° CCW, 180Â° | 0Â° primary |
| **PSM Modes** | PSM 6 (block), PSM 11 (sparse) | PSM 6 (block), PSM 11 (sparse) | PSM 6 (block) |

## 5. Validator Functions Used

| Function | ME | BF | EL |
| :--- | :---: | :---: | :---: |
| `normalize_manufacturer()` | âœ… | âœ… | â€” |
| `normalize_model()` | âœ… | âœ… | â€” |
| `normalize_serial()` | âœ… | âœ… | â€” |
| `looks_like_date_misread_serial()` | âœ… | âœ… | â€” |
| `normalize_year()` | âœ… | â€” | â€” |
| `normalize_ubc_tag()` | âœ… | â€” | âœ… |
| `normalize_diameter()` | â€” | âœ… | â€” |
| `normalize_ampere()` | â€” | â€” | âœ… |
| `normalize_volts()` | â€” | â€” | âœ… |
| `normalize_supply_from()` | â€” | â€” | âœ… |
| `normalize_panel_tag()` | â€” | â€” | âœ… |
| `completeness_score()` | âœ… | âœ… | âœ… |

## 6. Special Logic Differences

| Logic | ME | BF | EL |
| :--- | :--- | :--- | :--- |
| **Image Grouping (LLM-eligible)** | Regex catches `0..4`; `VALID_SUFFIXES` keeps Seq 0, 1, 3 | Regex catches `0..3`; `VALID_SUFFIXES` keeps Seq **0, 1** | Regex catches `0..3`; `VALID_SUFFIXES` keeps Seq 0, 1, 2 |
| **Special Rules** | **ver04 defenses**, FCU MI/M1/ML disambiguation, tag-vs-model guardrail, OCR recovery | Diameter suffix check (`"`), shared manufacturer validation | **PNL-** prefix enforcement, panel tag grammar |
| **Fallback Logic** | `_fallback_model_from_ocr()`, `_ocr_shows_mi()` | â€” | `_apply_tag_formatting()` |
| **Panel Abbreviations** | â€” | â€” | MDP, CDP, SPL, MCC, PNL, SWBD, ATS |

## 7. Workflow

All scripts now follow the identical workflow:
1. **Discover**: Scan `Capture_photos_upload/` for valid image groups.
2. **Filter**: Skip already processed QRs (checked against DB `ai_status`, `Approved`, `Flagged`).
3. **Process** (Parallel):
   - Preprocess images (CLAHE/Thresholding).
   - Run Tesseract OCR for context.
   - Call OpenAI with images + OCR context.
   - Validate & Clean data via `validators_shared.py`.
   - **Guard**: Check if existing JSON is **strictly better** (higher completeness); if so, skip save. (Allows update on equal score).
4. **Save**: Write JSON to `Output_jason_api/`.
5. **Update**: Mark `ai_status=1` in DB.

## 8. Shell Scripts & Automation

| Script | Covers | Notes |
| :--- | :--- | :--- |
| `auto_process_assets.sh` | EL â†’ ME â†’ DB sync | Cron automation; BF not included yet |
| `run_ai_and_sync.sh` | Any single type + DB sync | Dashboard launcher; passes script as `$1` |
| `run_interpreter.sh` | Any Python command | venv wrapper for ad-hoc execution |

## 9. Maintenance

- **Shared Code**: Any changes to `validators_shared.py` automatically update all 3 scripts.
- **Unified Config**: Environment variables (`OPENAI_API_KEY`, `DEV_PATH`) are consistent across all tools.
- **Output Schema**: All scripts produce JSON matching `response_schema.json` structure.
- **DB Sync**: `updating_process_database.py` handles all JSON -> PostgreSQL synchronization regardless of asset type.
