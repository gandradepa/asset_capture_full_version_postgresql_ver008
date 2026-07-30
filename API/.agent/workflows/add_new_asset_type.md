---
description: Guide for extending the extraction pipeline to support a new asset type (e.g., Plumbing, Fire Alarm).
---

# Add New Asset Type to Extraction Pipeline

Current documentation refresh: 2026-05-25.

This workflow guides you through adding a new asset type (e.g., "PL" for Plumbing) to the extraction pipeline, following the established architecture patterns.

## Prerequisites

- Understand the new asset type's nameplate fields
- Have sample images to test with
- Review `API_interface_ME_ver00.py` as the reference implementation

---

## Step 1: Define the Schema

Create the Pydantic model for the new type. Follow the existing pattern:

```python
class PLStructuredExtraction(BaseModel):
    """Strict schema for PL asset extraction."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    manufacturer: str = Field(default="", alias="Manufacturer")
    model: str = Field(default="", alias="Model")
    serial_number: str = Field(default="", alias="Serial Number")
    # Add type-specific fields:
    pipe_size: str = Field(default="", alias="Pipe Size")

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_to_string(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()
```

## Step 2: Create the Script

Copy and adapt one of the existing scripts:

1. **Copy template**: `cp API_interface_ME_ver00.py API_interface_PL_ver00.py`
2. **Update Config class**:
   - Change `VALID_SUFFIXES` to match expected image sequences. **Do not include the optional Extra Photo sequence** — by platform convention, the Extra Photo is captured/displayed in review but never enters the LLM. Each existing pipeline reserves the highest seq for the Extra Photo (`-4` for ME, `-3` for BF/EL) and keeps it out of `VALID_SUFFIXES`.
   - Set `COMPLETENESS_SCORE_FIELDS` for the new type's key fields
   - Update `FIELD_SOURCES` mapping (which images contain which fields)
   - Set appropriate `OCR_MODE` env variable name
   - Widen `FILENAME_PATTERN`'s sequence character class to cover the Extra Photo index so the file is logged as `invalid_seq` rather than `name_mismatch`.
3. **Replace schema**: Use your new `PLStructuredExtraction` model
4. **Update `discover_assets()`**: Filter for `asset_type.upper() == "PL"`
5. **Update `process_single_asset()`**: Adjust normalization calls for new fields
6. **Update logging/summary**: Change type label from `ME` to `PL`

## Step 3: Add Validators (if needed)

If the new type has unique fields, add normalizer functions to `validators_shared.py`:

```python
def normalize_pipe_size(val: str) -> str:
    """Parse and normalize pipe size values."""
    # ... implementation
    return normalized_value
```

Then import it in the new script:

```python
from validators_shared import (
    normalize_manufacturer,
    normalize_model,
    normalize_serial,
    normalize_pipe_size,  # New
    completeness_score,
)
```

## Step 4: Update Image Filename Pattern

Ensure images for the new type follow the naming convention:

```
<QR> <Building> PL-0.jpg    # Nameplate
<QR> <Building> PL-1.jpg    # Context photo
```

The existing regex pattern already supports any 2-letter type code:
```regex
^([T]?\d+)\s+(\d+(?:-\d+)?)\s+([A-Z]{2})\s*-\s*([0-3])$
```

## Step 5: Update Shell Scripts

### `auto_process_assets.sh`
Add the new script to the automation pipeline:

```bash
# --- STEP X: Run Plumbing AI Interpreter ---
echo "[X/N] Running Plumbing AI..." >> "$LOG_FILE"
$VENV_PYTHON "$API_DIR/API_interface_PL_ver00.py" >> "$LOG_FILE" 2>&1
```

### Dashboard Integration
If the Dashboard should be able to trigger this script, add a new task key in `Asset_portal_dashboard.py`:

```python
# In the TASKS dictionary:
"run_pl": {"script": "API_interface_PL_ver00.py", "label": "Plumbing AI"},
```

## Step 6: Update Documentation

1. **`AGENT.md`**: Add the new script to the architecture overview and extraction scripts table
2. **`SKILL.md`**: Add the new Pydantic model and CLI examples
3. **`api_comparison_matrix.md`**: Add a new column for the asset type
4. **`RULES.md`**: Update if any new coding patterns are introduced

## Step 7: Test

```bash
# Test with a single known QR code
python API/API_interface_PL_ver00.py --qr 0000199999 --debug

# Verify JSON output
cat Output_jason_api/0000199999_PL_100.json | python -m json.tool

# Run DB sync
python API/updating_process_database.py
```

## Checklist

- [ ] Pydantic schema defined with all required fields
- [ ] Config class updated (paths, sequences, completeness fields)
- [ ] `discover_assets()` filters for new type code
- [ ] Normalization functions added to `validators_shared.py` (if needed)
- [ ] Image filename pattern matches existing regex
- [ ] Shell automation scripts updated
- [ ] Dashboard task runner updated (if applicable)
- [ ] All documentation updated
- [ ] Tested with `--debug` on sample images
