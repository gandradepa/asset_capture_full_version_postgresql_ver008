# Incident - 2026-07-08 ME Manufacturer Whitelist Wipe (Gardner Denver) + Uncommitted VM Extraction Code

## Summary

QR `0000186422` (building 068, Gardner Denver C-DLR 150 compressor) extracted **Model
`C-DLR 150 (25)` (conf 92), Serial `SC10449228002` (conf 90), Year `2022` (conf 88)** from a
clean, legible seq `-0` plate — but **Manufacturer came back blank with confidence 0**. The
vision model read "Gardner Denver" correctly; the post-processing canonicalizer wiped it because
the name is absent from `ME_MANUFACTURER_REGEX_RULES` and the guarded generic fallback rejects
multi-token names without a legal suffix (`CO`/`INC`/`LTD`), `&`, or hyphen. Same failure class
as the 2026-06-22 Spirax Sarco miss (fixed then together with Siemens and the numeric-model
whitelist).

The investigation also found the VM's `API/API_interface_ME_ver00.py` (modified 2026-07-07
15:24) carried **~264 lines of newer extraction logic present in no git commit on any branch**
— serial-confabulation guards, manufacturer-aware serial-evidence checks, Siemens prompt rules —
while the Gardner Denver rule existed only in the local working copy and had never been
deployed. Neither side was a superset: deploying local over VM would have destroyed the newer
VM logic.

## Impact

- QR `0000186422` flagged `missing_manufacturer` + `low_completeness` (completeness 60, avg
  conf 54); requires a reviewer to enter "Gardner Denver" in the ME review app. No AI rerun
  needed — all other plate fields extracted correctly.
- Any ME asset whose plate manufacturer is a multi-token name absent from the regex/alias
  tables loses `Manufacturer` the same way. Signature in JSON: `"Manufacturer": ""` with
  confidence 0 while sibling seq `-0` fields are confident.
- Between ~2026-07-07 and 2026-07-08, git did not reflect the extraction code actually running
  in production (same governance gap class as the 2026-06-16 GPS feature, which shipped to the
  VM while uncommitted).

## Root Cause

1. **Whitelist-by-design:** `_canonicalize_manufacturer_candidate` resolves
   `ME_MANUFACTURER_REGEX_RULES` → compact alias lookup → shared `normalize_manufacturer()`
   whitelist → guarded generic fallback. The fallback deliberately rejects two-plus-token names
   lacking a legal suffix, `&`, or hyphen to block OCR noise — which equally rejects legitimate
   makers nobody added ("Gardner Denver", previously "Spirax Sarco"). There is no logging when
   the fallback wipes a candidate, so misses surface only as blank fields downstream.
2. **Deploy gap:** the Gardner Denver rule was authored locally in the 2026-06-22 session
   (after the user photographed the plate) but the deploy step was never confirmed, so the VM
   ran without it when this QR was processed 2026-07-07 10:24.
3. **Drift:** newer VM-side extraction work was deployed/edited on the VM without a matching
   commit, so no local checkout contained the production baseline to patch against.

## Fix

- Added `(r"\bGARDNER\s+DENVER\b", "Gardner Denver")` to `ME_MANUFACTURER_REGEX_RULES`,
  patched **onto the VM's current file** (not a local overwrite) to preserve the newer VM logic.
- Reconciled the drift: adopted the VM production file as the git baseline plus the Gardner
  rule — commit `7fc7b2f` on `New_improviments_01` (+265/−35). Local ≡ VM ≡ git.
- Documented the canonicalization order, wipe signature, and numeric-model whitelist in
  `rules/asset_extraction_api.rules.md` and `workflows/02_run_extraction_me_el_bf.md`.

## VM Deployment

- 2026-07-08 ~16:22: race check (VM file unchanged since 2026-07-07 15:24) → backup
  `/home/developer/API/API_interface_ME_ver00.py.bak_20260708_162232` → `scp` merged file
  (309,157 B) → `python3 -m py_compile` OK → markers verified (`GARDNER=1`, `SPIRAX=1`,
  `_manufacturer_allows_numeric_model=3`).
- No service restart required: extraction launches per run via `run_ai_and_sync.sh`.

## Validation

- Real-method harness (imports the module, no API cost): `_canonicalize_manufacturer_candidate`
  returns `Gardner Denver` for `'Gardner Denver'` and `'GARDNER DENVER compressor'`;
  Spirax/Siemens regressions pass; `_is_model_code_candidate` accepts Siemens numeric models
  `011749`/`012135` and `C-DLR 150 (25)`, still rejects all-numeric models for non-whitelisted
  makers and year/date shapes (`2024`, `20201010`). 9/9 cases pass on the merged file.
- QR `0000186422` remains flagged for reviewer completion (typing the manufacturer in the ME
  review app writes the audit trail correctly).

## Operational Note

- **Triage rule:** blank `Manufacturer` + confidence 0 with confident Model/Serial/Year from
  the same plate = canonicalizer wipe → add the maker to `ME_MANUFACTURER_REGEX_RULES` (zero
  per-run cost). Never escalate model tier for this signature (extraction cost constraint).
- **Deploy rule:** before any `scp` of `API_interface_ME_ver00.py` to the VM, diff against the
  VM's current file first — VM-side logic has now twice existed in no git commit. Commit the
  reconciled result the same day.
