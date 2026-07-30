---
description: Debug a single asset extraction by QR code with verbose logging and step-by-step analysis.
---

# Debug Single Asset Extraction

Current documentation refresh: 2026-04-28.

This workflow walks through debugging extraction failures or unexpected results for a specific QR code.

## Prerequisites

1. **Identify the QR code** â€” e.g., `0000177289`
2. **Identify the asset type** â€” ME, EL, or BF
3. **Ensure images exist** in `Capture_photos_upload/` matching the pattern `<QR> <Building> <Type>-<Seq>.jpg`

## Step 1: Verify Image Files Exist

Check that the expected image files are present and correctly named.

```bash
ls Capture_photos_upload/ | grep "0000177289"
```

Expected output (example for ME):
```
0000177289 2043 ME-0.jpg    # Nameplate
0000177289 2043 ME-1.jpg    # UBC Tag
0000177289 2043 ME-3.jpg    # Technical Safety BC
```

## Step 2: Check Database Status

Verify the asset has not already been processed or approved.

```bash
python -c "
import sys
sys.path.insert(0, 'asset_capture_app_dev')
import db
conn = db.get_connection()
cur = conn.cursor()
# Check ai_status
cur.execute('SELECT QR_code_ID, ai_status FROM QR_codes WHERE QR_code_ID = ?', ('0000177289',))
print('QR_codes:', cur.fetchall())
# Check approval status
cur.execute('SELECT \"QR Code\", Approved, Flagged FROM sdi_dataset WHERE \"QR Code\" = ?', ('0000177289',))
print('sdi_dataset:', cur.fetchall())
conn.close()
"
```

> [!TIP]
> If `ai_status=1`, the script will skip this QR. Use `--qr` flag to bypass this filter.

## Step 3: Run Extraction with Debug Flag

Run the appropriate script with `--qr` and `--debug` flags.

```bash
# For Mechanical assets:
python API/API_interface_ME_ver00.py --qr 0000177289 --debug

# For Electrical assets:
python API/API_interface_EL_ver00.py --qr 0000183710 --debug

# For Backflow assets:
python API/API_interface_BF_ver00.py --qr 0000177289 --debug
```

## Step 4: Analyze Debug Output

Look for these key sections in the verbose output:

1. **Image discovery**: Confirm correct files are found and grouped
2. **OCR context**: Check raw Tesseract text for useful data
3. **LLM prompt**: Verify the prompt includes OCR context (if hybrid mode is enabled)
4. **Raw LLM response**: Check the JSON returned by the model
5. **Pydantic validation**: Look for `ValidationError` messages
6. **Normalization**: Check which normalizers modified the data
7. **Completeness guard**: Check if the save was skipped due to existing higher score

## Step 5: Check Existing JSON Output

If a JSON file already exists, compare it with the new extraction.

```bash
cat Output_jason_api/0000177289_ME_2043.json | python -m json.tool
```

## Step 6: Force Re-extraction (if needed)

To force re-extraction when `ai_status=1`, reset the status:

```bash
python -c "
import sys
sys.path.insert(0, 'asset_capture_app_dev')
import db
conn = db.get_connection()
conn.execute('UPDATE QR_codes SET ai_status = 0 WHERE QR_code_ID = ?', ('0000177289',))
conn.commit()
conn.close()
print('Reset ai_status for 0000177289')
"
```

Then re-run the extraction script (Step 3).

## Common Issues

| Issue | Debug Check | Resolution |
|-------|-----------|------------|
| QR not discovered | Image filename regex doesn't match | Rename file to match `<QR> <Building> <Type>-<Seq>.jpg` |
| Empty LLM response | Check `--debug` for API errors | Verify `OPENAI_API_KEY`, check rate limits |
| Wrong manufacturer | Check `normalize_manufacturer()` output | Add to `VALID_MANUFACTURERS` set in `validators_shared.py` |
| Model shows tag value | `_is_tag_like_model_candidate()` allowed it | Adjust the tag detection regex in the ME script |
| Low completeness | Many fields empty after normalization | Check if OCR context is being injected (`ME_OCR_MODE=full`) |
