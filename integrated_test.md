# Integrated Test — Issue Tracker

**Started:** 2026-04-29
**Last updated:** 2026-06-12
**Branch:** Integrated_Test
**Tester:** Gibandrade

This document maps issues found during comprehensive end-to-end testing of the UBC Asset Capture platform. Issues are grouped by module, following the workflow:

```
Capture App -> Extraction API -> Review Apps -> SDI Process -> Planon Export
                      \              /
                       \            /
                        Dashboard
```

---

## How to use this tracker

Add a new issue under the relevant module section using the template below. Keep one issue per entry. When fixed, move it to **Resolved Issues** at the bottom and add the resolution note + commit hash.

### Issue template

```
### [MODULE-###] Short title
- **Severity:** Blocker | High | Medium | Low
- **Status:** Open | In progress | Blocked | Resolved
- **Found:** YYYY-MM-DD
- **Environment:** local / staging / prod, OS, browser
- **Steps to reproduce:**
  1.
  2.
- **Expected:**
- **Actual:**
- **Logs / screenshots:**
- **Suspected file(s):** path/to/file.py:LINE
- **Notes:**
```

**Severity guide**
- **Blocker** — workflow cannot proceed; data loss; security exposure.
- **High** — feature broken but workaround exists.
- **Medium** — incorrect behavior, degraded UX, non-critical path.
- **Low** — cosmetic, copy, minor inconsistency.

---

## Test Coverage Checklist

Mark off as each area is exercised end-to-end.

### 1. Capture App — `asset_capture_app_dev/` (port 5001)
- [ ] App boots without errors
- [ ] Login / auth_service flow
- [ ] QR intake (scan + manual)
- [ ] Image upload (single, multiple, large files)
- [x] User / timestamp audit fields recorded *(verified via QR_code_assets — `user`, `date_hour` populated; surfaced in review.html as "Captured by / Date / Hour")*
- [ ] Elapsed-time JSON written
- [ ] Parameter updates persist
- [x] DB writes to PostgreSQL `qr_code_db` via `db.py` / `DB_BACKEND=postgres` *(SQLite `QR_codes.db` is rollback/reference only)*
- [ ] Images land in `Capture_photos_upload/`

### 2. Extraction API — `API/`
- [x] ME extraction pipeline runs end-to-end *(verified via ai_check.log; succeeds when quota present)*
- [ ] BF extraction pipeline runs end-to-end *(no recent activity in audit window)*
- [x] EL extraction pipeline runs end-to-end *(verified; description prefix fixed via [EL-002])*
- [x] OpenAI vision call succeeds + retries on failure *(audited; lacking retry budget — see [AI-001])*
- [ ] OCR helpers produce expected output
- [ ] Shared validators reject invalid payloads *(known gap — see [EL-005])*
- [x] JSON outputs written to `Output_jason_api/`
- [x] `run_ai_and_sync.sh` completes AI → DB sync chain
- [x] Errors surface to logs (not silent) *(audited via ai_check.log)*

### 3. Review Apps
#### ME Review — `review/Asset_dasboard_browser_ME/` (port 5002)
- [x] App boots
- [x] Loads pending items
- [x] Field correction saves *(after [REVIEW-002] fix)*
- [x] Approval writes to `sdi_dataset`
- [ ] SDI inclusion/exclusion toggles
- [ ] QR replacement flow
- [ ] Confidence filtering

#### BF Review — `review/Asset_dasboard_browser_BF/` (port 5004)
- [x] App boots
- [ ] Loads pending items
- [ ] Approval writes to `sdi_dataset`
- [ ] BF-specific review logic

#### EL Review — `review/Asset_dashboard_browser_EL/` (port 8005)
- [x] App boots
- [x] Approval writes to `sdi_dataset_EL`
- [x] Canonical Planon fields present (Amperage Rating, Voltage Rating, Equipment ID, Equipment Type, Fed From Equipment ID, Power Type)
- [x] Dashboard tab badges reflect selected building, with all-building totals when no building is selected *(see [EL-007])*

### 4. Dashboard — `Dashboard/Asset_portal_dashboard.py` (port 8002)
- [x] App boots / unified entry opens main dashboard *(commit `0380a2a`)*
- [ ] Charts render with real data
- [ ] Extraction launcher triggers `run_ai_and_sync.sh`
- [x] Logs viewer *(extended with SLD Extraction Runs queue — see [DASH-004])*
- [ ] Dictionary editor (AST-safe parsing)
- [ ] FLS asset CRUD
- [ ] Map views
- [x] SDI flow views *(redesigned SDI Live Pipeline deployed to VM — see [DASH-005])*
- [ ] Asset-photo lookup
- [x] Cross-module navigation *(EL/ME/BF/SDI tabs verified — see [DASH-002])*
- [x] AI Process Queue → System Logs → SLD Extraction Runs row appears for each `<run_id>.jsonl` in `/home/developer/sld_extract_feedback/` *(see [DASH-004])*
- [x] SLD run drilldown shows run summary + asset rows + model_call telemetry *(see [DASH-004])*
- [x] "Open in EL Reviewer" deep-links into embedded EL iframe with the run's building pre-selected *(see [DASH-004])*
- [x] "Re-run extraction" admin button proxies to EL Reviewer's loopback `/sld/api/rerun/<run_id>` *(see [DASH-004])*

### 5. SDI Process — `SDI_process/app.py` (port 8003)
- [ ] App boots
- [ ] Package creation
- [ ] Archive operation
- [ ] Retrieve operation
- [ ] Exclude operation
- [ ] Validation log review
- [ ] Planon export
- [ ] `Col_process` / JSON `ExcludeSDI` / `QR_codes.sdi` stay aligned

### 6. Auth Service — `auth_service/`
- [ ] Login
- [x] Password reset / change *(admin reset and Dashboard/SDI self-service persistence fixed; see [AUTH-001])*
- [ ] Session expiry
- [ ] Cross-app session sharing
- [ ] `auth_service.env` / `DATABASE_URI` correctly resolved

### 7. Dictionary Tooling — `dictionary/`
- [x] Loads current dictionary *(used by [EL-002] fix)*
- [ ] AST-safe edits don't corrupt file
- [ ] Mechanical dictionary edits via Dashboard

### 8. Cross-cutting
- [x] Shared PostgreSQL `qr_code_db` consistency across apps *(SQLite `QR_codes.db` is historical rollback/reference only)*
- [x] File-system store consistency (`Capture_photos_upload/`, `Output_jason_api/`) *(1326 JSONs match VM↔local)*
- [ ] Permissions / ACLs on shared stores
- [ ] Concurrent writes don't corrupt state
- [x] Logs roll over and are readable *(ai_check.log audited end-to-end)*

---

## Open Issues

### Capture App

### [CAPTURE-001] Capture app saves literal `"None"` as QR when scan fails
- **Severity:** Medium (data integrity; produces unprocessable rows in `QR_codes` / `QR_code_assets` / `sdi_dataset` and orphan photo files)
- **Status:** Open
- **Found:** 2026-04-30 (during stuck-pool investigation)
- **Evidence:** Row created 2026-03-03 by user `shawn`: `QR_code_ID='None'`, `Building Code='None'`, 3 photos `None None ME - 0/1/2.jpg`, malformed JSON filename `None_et.json`. DB rows + files cleaned up via `purge_qr_from_db.py` + manual file deletion (see [DATA-001]).
- **Recommendation:** In the capture app, refuse to save when the QR scanner returns no value. Validate against literal `"None"` / empty / whitespace-only.

### Extraction API

### [AI-001] ME extractor retry loop on persistently failing assets (`insufficient_quota`)
- **Severity:** Medium (resource waste; would become billable if quota refilled while the loop persisted on real failures)
- **Status:** Resolved root condition (quota restored 2026-04-29 ~15:13). Structural risk remains — see Recommendation.
- **Found:** 2026-04-30 (audit of `ai_check.log`)
- **Symptoms:** 11,532 `insufficient_quota` 429 responses across 2026-04-25 → 2026-04-29. 6 ME QRs (`0000186079`, `0000186082`, `0000084162`, `0000154401`, `0000154402`, `0000154403`) re-processed every cron tick. 3,852 `ME_ASSET_START` events; 8 successful saves; 3,844 `no_json` failures.
- **Cost impact:** Zero billable tokens — OpenAI does not bill 429 `insufficient_quota` errors. Once quota was restored, the 6 stuck assets all processed successfully and exited the loop.
- **Recommendation:** Add a retry budget. After N consecutive failures (or specifically on `insufficient_quota` / persistent error class), tombstone with a different `ai_status` value (e.g. `'2'`) so the asset stops being reprocessed every 4 minutes. Provide a "reset to retry" path in the dashboard.

### [ME-002] Auto-flag ME extractions for manual review on low completeness/confidence
- **Severity:** Feature (data quality)
- **Status:** In progress (local edits, not yet committed/deployed)
- **Found:** 2026-05-01
- **Location:** [API_interface_ME_ver00.py:5531-5587](API/API_interface_ME_ver00.py#L5531-L5587), [API_interface_ME_ver00.py:5641-5660](API/API_interface_ME_ver00.py#L5641-L5660)
- **Detail:** New `_build_manual_review_metadata` helper writes a `manual_review` block to the saved JSON containing `flag_for_review`, `reason_codes`, `ocr_assisted_rescue`, `ocr_mode`, and the threshold values used. Reason codes cover: low completeness (`< MANUAL_REVIEW_MIN_SCORE`, default 95), missing required fields (`missing_<field>`), low per-field confidence on critical fields — Manufacturer, Model, Serial Number, Year, UBC Tag, optional Technical Safety BC — (`< MANUAL_REVIEW_MIN_CONFIDENCE`, default 70), and `?` uncertain-character markers. When triggered, sets `structured["Flagged"]="true"` and clears `Approved` so the row routes back through review.
- **Side changes in same diff:**
  - Model tier shift: `PRIMARY_MODELS` `gpt-5.2,gpt-5.1,gpt-4o` → `gpt-5.4,gpt-5.2`; `FALLBACK_MODELS` `gpt-5.1,gpt-5.2` → `gpt-5.5`. `OPENAI_MODEL` / `PRIMARY_MODEL` `gpt-4o` → `gpt-5.4`. `FALLBACK_MIN_SCORE` 80 → 95.
  - New `NORMAL_REASONING_EFFORT` / `HARD_REASONING_EFFORT` env tiers. Reasoning-effort gating moved from `model.startswith("o")` to a `_reasoning_effort_for_model` helper that supports both `gpt-5*` and `o*` families and routes `hard=` based on whether the model is in `FALLBACK_MODELS`.
  - OCR fallback policy regression fixed (the previous edit unconditionally re-enabled OCR even when `OCR_MODE=off`).
  - `_should_use_o_series_for_ocr` now scans all `EXPECTED_FIELDS` for `?` markers, not just Model/Serial.

### Review Apps (ME / BF / EL)

### [EL-003] Hardcoded Windows debug-log path in EL review handler
- **Severity:** Low (silent in prod; dev-only artifacts on local Windows box)
- **Status:** Open
- **Location:** [Asset_dashboard_EL.py:2486](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L2486)
- **Detail:** Writes a debug log to `c:/Users/gandrade/.gemini/antigravity/brain/.../debug_log.txt` on every `review` GET request. Wrapped in try/except so prod is unaffected, but it's leftover developer instrumentation that should be removed or gated.

### [EL-004] JS `applyDictionary` can rewrite Description on page load
- **Severity:** Low (visual flicker; backend defends on save)
- **Status:** Open
- **Location:** [review.html:1845-1880](review/Asset_dashboard_browser_EL/review_asset_templates/review.html#L1845-L1880)
- **Detail:** When `descAutoEnabled` evaluates true on a reloaded page, `applyDictionary` rewrites `descField.value` from the dictionary. With the [EL-001] backend hardening, the user's saved Description still wins on the next save, but the visible field can flicker to a dict-derived value mid-session.

### [EL-005] `normalize_power_rating_pair` silently drops partial entries
- **Severity:** Medium (silent data loss when user fills only Value or only UoM)
- **Status:** Open
- **Location:** [Asset_dashboard_EL.py:74-93](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L74-L93) (also `API/validators_shared.py`, `API/API_interface_EL_ver00.py`)
- **Detail:** Regex requires `\d{1,5}\s*(KVA|KW|VA)` together; otherwise returns `("", "")`. Cases that silently zero out both fields:
  - User types Power Rating without UoM (or vice versa).
  - Decimal values like `50.5 KVA` truncate to `5 KVA`.
  - Stray characters / non-canonical UoM (`KVAr`, `kVa `).
- **Recommendation:** Preserve the user's literal value when normalization fails; surface a validation hint instead of silently nulling both fields.

### [EL-006] Asset Group preservation now gated on persisted `asset_group_manual` flag (refines [EL-001])
- **Severity:** Medium (refinement of [EL-001] resolution)
- **Status:** In progress (local edits, not yet committed/deployed)
- **Found:** 2026-05-01
- **Location:** [Asset_dashboard_EL.py:1710-1727](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L1710-L1727), [Asset_dashboard_EL.py:2003-2019](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L2003-L2019), [Asset_dashboard_EL.py:2525-2545](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L2525-L2545), [Asset_dashboard_EL.py:2809-2828](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L2809-L2828), [review.html:1050](review/Asset_dashboard_browser_EL/review_asset_templates/review.html#L1050), [review.html:1918-1933](review/Asset_dashboard_browser_EL/review_asset_templates/review.html#L1918-L1933)
- **Why:** Under [EL-001], any non-empty saved `Asset Group` was preserved over the dictionary unconditionally. That blocked retroactive dictionary updates (e.g. adding `SWBD|EL` — see [DICT-001]) from flowing into rows that had previously saved the AI-default value `"Panels"`.
- **Resolution:** Replaces the unconditional snapshot/restore with a gate on the persisted `asset_group_manual` flag in structured JSON. When `"1"` (set by JS the moment the user touches the dropdown), the saved value sticks; when `"0"`/missing, the dictionary value flows through. `save_review` now persists the flag into structured. JS `initAssetGroupAutoState` now reads the flag directly instead of inferring "manual" from current vs. derived value (unreliable when dict entries are added retroactively).
- **Notes:** Description preservation still uses the legacy `Panel - <tag>` placeholder detector (unchanged). [EL-001] resolution stays in effect for explicit user edits.

### [ME-001] Single-photo ME captures land in DB but are never processable
- **Severity:** Medium
- **Status:** Open
- **Detail:** ME extractor requires images `-0` (Asset Plate) and `-1` (UBC Tag). When only `-2` (Main Picture) exists, discovery rejects the asset (`accepted_qrs=0`) and it sits at `ai_status='0'` permanently. Today's pool had 2 such cases — `0000084088` (purged) and `0000184542` (still pending decision).
- **Recommendation:** Either (a) capture app must enforce -0/-1 before saving, (b) extractor should fall back to single-image extraction with reduced confidence, or (c) admin path to mark the asset for manual entry only.

### Dashboard

### [DASH-004] FLS New Device Flow Control Panel lookup and detail view

- **Found:** 2026-06-03 after request to simplify the `1. New FLS Device Flow` table and surface building Control Panel data.
- **Resolution:** Dashboard FLS data now derives Control Panel `Code` and `Description` from `"UBC - Asset Data Master Info"` by building `Property code`. The lookup is display-only and does not change `new_device`.
- **UI behavior:** The primary New FLS Device Flow table hides `Asset Group`, `Space`, and `Details`; a magnifying-glass row action opens a read-only modal with the same fields available in Edit. New/Edit/detail views show a Control Panel section.
- **Multi-match behavior:** When a property has more than one matching Control Panel row, Dashboard displays the lowest `Code` row and flags that multiple matches exist.
- **Validation target:** Confirm `/api/fls-assets` returns `property_control_panel_map`, row-level control-panel fields, and match flags; verify a multi-match property such as `466` shows a warning while using the first Code.

### [DASH-003] Parent Dashboard refresh sent embedded review iframes back to their landing page
- **Severity:** Medium (UX — workflow interruption)
- **Status:** In progress (local edits, not yet committed/deployed)
- **Found:** 2026-05-01
- **Symptom:** Refreshing the parent unified Dashboard always reloaded the EL iframe at its `data-src` landing page, dropping the user out of `/review-distribution` (or wherever they had navigated inside the iframe).
- **Resolution:** Each EL template ([dashboard.html:3369-3382](review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html#L3369-L3382), [landing.html:561-574](review/Asset_dashboard_browser_EL/review_asset_templates/landing.html#L561-L574), [review.html:2203-2216](review/Asset_dashboard_browser_EL/review_asset_templates/review.html#L2203-L2216)) now `postMessage`s `{type:'review-iframe-nav', iframe:'iframe-review-el', href:location.href}` to the parent on load. Parent ([Dashboard/templates/dashboard.html:3042-3070](Dashboard/templates/dashboard.html#L3042-L3070)) caches the most recent href per `iframe.id` in `localStorage` (`lastIframeUrl_<id>`). `maybeLoadIframe` prefers the remembered URL over `data-src`.
- **Notes:** Wired for EL only. ME/BF templates would need the same `postMessage` for parity if requested.

### SDI Process
_No open issues — see Resolved [SDI-001], [SDI-002], [SDI-003]._

### Auth Service
_No open issues - see Resolved [AUTH-001]._

### Dictionary Tooling

### [DICT-002] Added `MDC` to EL extractor abbreviations + fixed trailing-dash bug; recovered QR `0000184961`
- **Severity:** High (silent data corruption — every plate labeled `MDC` was tagged `PNL-MDC` and classified as `Panels`)
- **Status:** Resolved (deployed to VM 2026-05-04 13:28; QR `0000184961` re-extracted and synced 13:34)
- **Found:** 2026-05-04 (user investigated why "PNL-MDC" was assigned to QR `0000184961` building 750)
- **Symptom:** AI read the asset plate's branch field as `MDC` (high confidence: 95) but `_apply_tag_formatting` in [API_interface_EL_ver00.py:483-507](API/API_interface_EL_ver00.py#L483-L507) didn't recognize `MDC` as an equipment-type abbreviation, so it fell through to the catch-all `f"PNL-{clean_tag}"` and produced `PNL-MDC`. Description fell to default `Panel - PNL-MDC`; Asset Group classified as `Panels` because the dictionary lookup matched `PNL|EL` (the bogus prefix), not `MDC|EL` (which has existed at [mechanical_dictionary.py:330](dictionary/mechanical_dictionary.py#L330) all along: `Other Service and Distribution / Electrical Service and Distribution / Main Distribution`).
- **Root cause:** `MDC` (Main Distribution Cabinet) was missing from every prefix list in the EL pipeline:
  - [`validators_shared.PANEL_ABBR`](API/validators_shared.py#L8) — canonical (drives `_SUPPLY_FROM_JOINABLE_PREFIXES`, the decimal-floor regex, and per-row classification).
  - [`API_interface_EL_ver00.py:171`](API/API_interface_EL_ver00.py#L171) — fallback `_SUPPLY_FROM_JOINABLE_PREFIXES` (used when `validators_shared` import fails).
  - [`API_interface_EL_ver00.py:228`](API/API_interface_EL_ver00.py#L228) — fallback prefix tuple in `normalize_el_supply_from_tag`.
  - [`Config.ABBREVIATIONS` dict at API_interface_EL_ver00.py:346`](API/API_interface_EL_ver00.py#L346) — used by `_apply_tag_formatting`. Note: the AI prompt itself ([line 1724](API/API_interface_EL_ver00.py#L1724)) already mentioned `MDC` as a valid Fed-From identifier — only the post-processor was inconsistent.
- **Resolution (4 file edits):**
  1. Added `"MDC"` to `validators_shared.PANEL_ABBR` set.
  2. Added `"MDC"` to `_SUPPLY_FROM_JOINABLE_PREFIXES` fallback set.
  3. Added `"MDC"` to the prefix tuple in `normalize_el_supply_from_tag` fallback.
  4. Added `"MDC": "MAIN DISTRIBUTION CABINET"` to `Config.ABBREVIATIONS`.
  5. **Bonus** — fixed a pre-existing trailing-dash bug at [API_interface_EL_ver00.py:505](API/API_interface_EL_ver00.py#L505): `f"{found_abbr}-{remainder}"` → `f"{found_abbr}-{remainder}" if remainder else found_abbr`. Without this guard, just adding `MDC` would have produced `MDC-` (trailing dash) when the AI read a bare `MDC` with no suffix. The fallback `normalize_el_supply_from_tag` already had this guard ([line 231](API/API_interface_EL_ver00.py#L231)); brings the main path into line. The bug was latent for every other prefix too — e.g. a bare `SWBD` plate would have produced `SWBD-`.
- **No dictionary change needed:** the `MDC|EL` entry already existed at [mechanical_dictionary.py:330](dictionary/mechanical_dictionary.py#L330). With the extractor preserving `MDC` as the tag, the dictionary classifies it correctly automatically — same shape as [DICT-001]'s `SWBD|EL`.
- **Re-extraction of QR `0000184961` (path B):**
  - First attempt skipped saving (`Existing completeness (75.0%) >= New (75.0%)` — `_existing_el_output_needs_rescore` safety rule).
  - Resolved by removing the existing JSON (after backup), re-running `python3 API_interface_EL_ver00.py --qr 0000184961`. The `--qr` flag bypasses the `ai_processed_qrs` filter at [line 1361](API/API_interface_EL_ver00.py#L1361).
  - New JSON: `UBC Asset Tag: "MDC"`, `Description: "Main Distribution - MDC"`, `Branch Panel: "MDC"`, `Ampere: "1200"`, `Volts: "600Y/347V"`, `Flagged: "true"`, `manual_review.reason_codes: ["low_completeness","missing_supply_from"]`. Confidence on `UBC Asset Tag` = 96.
  - Manual `_sync_db_from_structured` call (since the API extractor only writes the JSON; the EL Review watcher syncs to `sdi_dataset_EL` on `before_request`). Sync log confirmed dictionary hit: `[EL-DICT-MATCH] Exact composite key: MDC|EL`.
  - Post-sync DB row: `UBC Asset Tag=MDC`, `Equipment ID=MDC`, `Description=Main Distribution - MDC`, `Asset Group=Other Service and Distribution`, `Main Asset=Electrical Service and Distribution`, `Attribute=Electrical`, `Flagged=1`, `ID_check=750 | MDC |`. `Approved` left empty so the row routes through human review (correctly flagged).
  - Path C (one-shot cleanup script) was queued as a backstop and skipped — re-extraction produced canonical values directly. Writing a hardcoded overwrite script would have fought future legitimate AI re-runs.
- **Existing-data scope:** 1 row (`0000184961`) had the `PNL-MDC` synthesis. 6 other rows reference `MDC` correctly in `Supply From` (no fix needed). The fix prevents recurrence on future captures.
- **Validation:**
  - Local + VM `python3 -m py_compile validators_shared.py API_interface_EL_ver00.py` passed.
  - Local + VM SHA256 matched on both deployed files.
  - Re-extraction trace through new code, AI input `"MDC"`: `_apply_tag_formatting → found_abbr="MDC", remainder="" → returns bare "MDC"` ✓ (with the trailing-dash guard).
  - End-to-end DB+JSON spot-check on QR `0000184961` confirmed canonical post-state.
- **VM backups (rollback set):**
  - `/home/developer/API/validators_shared.py.bak_20260504_140000_pre_mdc`
  - `/home/developer/API/API_interface_EL_ver00.py.bak_20260504_140000_pre_mdc`
  - `/home/developer/Output_jason_api/0000184961_EL_750.json.bak_20260504_140000_pre_re_extract`
  - `/tmp/0000184961_predump_20260504_140000.txt` (DB rows pre-sync)
- **Rollback:** `ssh developer@142.103.68.1 'cd /home/developer/API && cp validators_shared.py.bak_20260504_140000_pre_mdc validators_shared.py && cp API_interface_EL_ver00.py.bak_20260504_140000_pre_mdc API_interface_EL_ver00.py && cp /home/developer/Output_jason_api/0000184961_EL_750.json.bak_20260504_140000_pre_re_extract /home/developer/Output_jason_api/0000184961_EL_750.json'` (DB row sync would also need to be re-run from the restored JSON via the same one-liner used for the forward sync).
- **User action remaining (path A):** open QR `0000184961` in EL Review, confirm the values, save & approve.

### [DICT-001] Added `SWBD|EL` entry to mechanical dictionary
- **Severity:** Low (Planon classification gap)
- **Status:** In progress (local edits, not yet committed/deployed)
- **Found:** 2026-05-01
- **Location:** [mechanical_dictionary.py:474-481](dictionary/mechanical_dictionary.py#L474-L481)
- **Detail:** New entry `SWBD|EL` → `attribute_set: Electrical`, `asset_group: Other Service and Distribution`, `main_asset: Electrical Service and Distribution`, `description: Distribution`, `asset_type/type: EL`. Pairs with [EL-006] flag-gating so SWBD-prefixed EL rows that previously saved `"Panels"` will pick up the new dictionary classification on next render.

### Cross-cutting / Integration

### [DATA-002] EL pipeline never bumps `QR_codes.ai_status` to `'1'`
- **Severity:** Medium (causes recurring log noise; misleads future audits of "what AI processed")
- **Status:** Open
- **Detail:** Only the ME pipeline updates `ai_status`. EL/BF process-and-approve cycles leave it at `'0'`, so EL's `_load_ai_processed_qrs` safety rule repeatedly resets any backfilled rows back to `'0'`. Today's Cat-A 11-row backfill (see [DATA-001]) needed JSON `el_extraction_rule_version` bumps to make the `ai_status='1'` change stick.
- **Recommendation:** Make EL (and BF) update `ai_status='1'` on successful save_review, mirroring ME. Alternatively, refactor `ai_status` to be discipline-aware (`ai_status_me`, `ai_status_el`, `ai_status_bf`).

### [DATA-003] New `Main Asset` column on `sdi_dataset` / `sdi_dataset_EL` (Planon alignment)
- **Severity:** Feature (Planon export alignment)
- **Status:** In progress — write paths wired locally; backfill script ready but not yet executed; not yet committed/deployed
- **Found:** 2026-05-01
- **Three pieces:**
  1. **EL writer** ([Asset_dashboard_EL.py:1681](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L1681), [Asset_dashboard_EL.py:1760](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L1760), [Asset_dashboard_EL.py:1797](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L1797), [Asset_dashboard_EL.py:2812](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L2812)) — adds `"Main Asset"` to the EL upsert column list, ensures the column on `sdi_dataset_EL`, and persists `(sd.get("Main Asset") or "").strip()` on save.
  2. **BF writer** ([asset_plate_reviewer_bf.py:1107](review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py#L1107), [asset_plate_reviewer_bf.py:1126](review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py#L1126)) — hardcoded constant `"Domestic Water Distribution Equipment"` (BF review form has no Main Asset input); ensures the column on `sdi_dataset`.
  3. **Backfill script** ([scripts/backfill_main_asset.py](scripts/backfill_main_asset.py)) — one-shot. EL rows: prefer `structured_data["Main Asset"]` from the matching JSON, else mechanical-dictionary lookup at `<prefix>|EL` (composite-prefix → plain-prefix fallback). ME rows: same logic with `<prefix>|ME`. BF rows: the constant. Rows with no resolution are left empty (surfaces dictionary gaps rather than inventing values). `--dry-run` flag previews; live mode takes a timestamped DB backup first; idempotent (only fills NULL/empty cells).
- **Out of scope (intentional):** `sdi_print_out` / `sdi_print_out_arch` (Planon export tables) — separate follow-up once Planon's expected schema is confirmed.

---

## Resolved Issues

### [AUTH-001] Self-service password change reported success but did not persist
- **Severity:** High (account access failure after logout)
- **Status:** Resolved (deployed to VM 2026-06-03; local source files match VM SHA256)
- **Found:** 2026-06-03 after a Dashboard admin reset followed by user self-service password change.
- **Root cause:** Dashboard and SDI `/change-password` routes assigned `current_user.password = bcrypt.generate_password_hash(...)`. The shared `User` model persists only `password_hash`, so SQLAlchemy ignored the unmapped attribute and the route flashed success without changing the login password.
- **Resolution:** Both routes now call `current_user.set_password(new_password)` before `db.session.commit()`. Admin reset and CLI reset already used the correct helper.
- **Validation:** VM `py_compile` passed; remote scan found zero bad assignments; Dashboard and SDI Gunicorn masters reloaded with `HUP`; both services active and returned expected 302 login redirects. Local `python -m unittest discover -s test` passed with password persistence regression coverage.
- **VM backups:** `/home/developer/Dashboard/Asset_portal_dashboard.py.bak_20260603_103640_pre_password_change_fix`, `/home/developer/SDI_process/app.py.bak_20260603_103640_pre_password_change_fix`.

### [EL-001] Manual edits to "Asset Group" and "Description" silently overwritten by dictionary
- **Severity:** High
- **Status:** Resolved (deployed to VM `assetcap-el`, verified on prod 2026-04-29)
- **Found:** 2026-04-29
- **Environment:** VM (`reviewel.assetcap.facilities.ubc.ca`), EL Review
- **Steps to reproduce:**
  1. Open QR `0000183791` (Building 217) in EL Review.
  2. Change `Asset Group` from "Panels" → "Interior Distribution Transformers".
  3. Change `Description` from "Panel - PNL-2S4D1" → "Distribution - PNL-2S4D1".
  4. Save, then reopen the record (or re-save without changes).
- **Expected:** New values persist in the JSON, `sdi_dataset_EL`, and the rendered form across reloads and idempotent re-saves.
- **Actual (before fix):** Both fields reverted to "Panels" / "Panel - PNL-2S4D1" — the dictionary entry `PNL|EL` was reapplied and clobbered user input.
- **Root cause:** `_apply_mechanical_fallback` ([Asset_dashboard_EL.py:846-863](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L846-L863)) unconditionally overwrites `Asset Group` and `Description` from the dictionary. Reached on **four** paths in EL: `save_review` (POST), `_sync_db_from_structured` (DB upsert), `review` GET handler (page render), and the list/feed iteration. The pre-existing `asset_group_manual` form flag was honored only on the tag-changed branch. An initial fix that gated restoration on "did the user change the value this submission" silently regressed on idempotent re-saves.
- **Resolution (Option B — save+render hardened):** Always restore non-empty form `Asset Group` / `Description` over the dictionary-derived value (no "did the user change it" gate); snapshot+restore around `_apply_tag_dictionary_first` in `_sync_db_from_structured`, the GET handler, and the list/feed iteration. Verified end-to-end on the VM.
- **Notes:** Same shape recommended for ME and BF if reproduced. Related JS hazard tracked separately as [EL-004].

### [EL-002] AI extractor hardcoded "Panel" prefix; review app couldn't auto-correct after [EL-001]
- **Severity:** High
- **Status:** Resolved (API + review deployed 2026-04-30)
- **Found:** 2026-04-30
- **Environment:** VM, QR `0000183856` (CDP-6N0M1)
- **Symptom:** Description always rendered as "Panel - <tag>" regardless of tag prefix family (CDP, TX, ATS).
- **Root cause:** [API_interface_EL_ver00.py:1507](API/API_interface_EL_ver00.py#L1507) hardcoded `f"Panel - {final_tag}"`. Pre-[EL-001], `_apply_mechanical_fallback` corrected this on render; post-[EL-001] the JSON's existing AI-default value was preserved as if it were a user edit.
- **Resolution:**
  1. **API:** added `_load_mechanical_asset_dictionary` + `_el_description_prefix_from_tag` helpers. Replaced line 1507 with a dict lookup that returns the correct prefix (`Distribution` for CDP, `Transformer` for TX/T-, `Panel` for PNL, etc.; falls back to `Panel`).
  2. **Review:** added `_is_ai_default_description(desc, tag)` placeholder detector in `Asset_dashboard_EL.py`; wired into all four snapshot+restore sites so the dict-derived value wins when description matches the legacy `Panel - <tag>` placeholder. Existing user edits still preserved.

### [REVIEW-001] Save & Next redirected to dashboard after toggling Approved on a Pending-only filtered view
- **Severity:** Medium
- **Status:** Resolved (ME/EL/BF deployed 2026-04-30)
- **Found:** 2026-04-30
- **Steps to reproduce:**
  1. Filter dashboard to Pending only.
  2. Open a pending asset; click the Pending pill (toggles to Approved via AJAX).
  3. Click Save & Next.
- **Expected:** Navigate to next pending asset.
- **Actual (before fix):** Redirected to dashboard.
- **Root cause:** Toggle-approved AJAX persisted Approved=True before save_review ran; `nav_idx` lookup excluded the just-approved doc → fell through to dashboard redirect.
- **Resolution:** Added `_resolve_neighbor[_doc]` helper in each backend that retries with a broader scope (`approved=""` to disable the filter — `None` falls back to the Pending-only default) when the doc is filtered out, finds the doc in the broader list, then picks the closest still-eligible neighbor.

### [REVIEW-002] Pending toggle button discarded in-form edits
- **Severity:** High
- **Status:** Resolved (ME/EL/BF deployed 2026-04-30)
- **Found:** 2026-04-30
- **Steps to reproduce:**
  1. Edit a field in the review form.
  2. Click the Pending pill (without first clicking Save).
- **Expected:** Edits + new Approved state both persist.
- **Actual (before fix):** Only the Approved flag persisted via the AJAX call. Subsequent Save & Next skipped the field merge (early-return for already-Approved records), and form-disabled fields weren't submitted anyway → edits lost.
- **Root cause:** Toggle was a separate AJAX endpoint that ignored form data. JS then disabled fields, blocking subsequent form submission.
- **Resolution:** Replaced AJAX toggle with form submit using a new action `save_toggle`. Backend bypasses the Approved-record early-return for `save_toggle`, merges the form, persists, then redirects back to the same review page so the lock state takes effect on reload. Identical shape applied in ME, EL, BF.

### [REVIEW-003] User Activity Log fields below Description in review.html (ME/EL/BF)
- **Severity:** Feature
- **Status:** Resolved (deployed 2026-04-30)
- **Resolution:** Added `_fetch_capture_info(qr, building, discipline)` helper in each backend (queries `QR_code_assets` for latest capture user/`date_hour`, splits on `T`). Added a "User Activity Log" section to each review.html with read-only **Captured by**, **Date**, **Hour** fields. Empty when no capture record exists.

### [REVIEW-004] Building filter dropdown showed only the code (no name)
- **Severity:** Low (UX)
- **Status:** Resolved (ME/BF deployed 2026-04-30)
- **Resolution:** Added `_get_buildings_name_map()` helper to ME/BF backends (`{Code: Name}` from `Buildings` table). Exposed as `window.BUILDING_NAME_MAP` in templates. `populateDropdowns()` now formats option labels as `Code - Name` while keeping `<option value>` as the bare code so existing column filters continue to work.

### [EL-007] EL dashboard tab badges ignored selected building
- **Severity:** Medium (dashboard counts misleading when reviewing a single building)
- **Status:** Resolved (deployed to VM 2026-05-04 11:24; verified working by user)
- **Found:** 2026-05-04 (user screenshot of EL Distribution view where tab badge counts did not reflect the global building dropdown)
- **Environment:** VM (`reviewel.assetcap.facilities.ubc.ca`), EL Review / Distribution dashboard
- **Expected:** When `-- Select a building --` is selected, tab badges show all-building totals. When a real building is selected, New Assets / Manual Entry / hidden Update badge counts and related quick-filter counts reflect only that building.
- **Actual (before fix):** The page tables were scoped by `filter_building`, but `count_unapproved_*` and `count_missed_*` were computed from each process base dataset (`data_*_base`) without applying `selected_building_code`, so badges stayed at all-building counts.
- **Root cause:** `_render_dashboard_view()` in [Asset_dashboard_EL.py](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py) built the badge/quick-filter counters from the unscoped base sets after table data had already been scoped for the selected building.
- **Resolution:** Added `scope_counts_to_selected_building(ds)` in `_render_dashboard_view()` and now feeds `get_counts()` plus `count_unapproved_new/manual/update` from the building-scoped list when `selected_building_code` is non-empty. Empty dropdown value keeps the original base list, preserving all-building totals for the placeholder.
- **Validation:** Local and VM `python3 -m py_compile Asset_dashboard_EL.py` passed. Local and VM SHA256 matched (`b0af1d5c7e978d8e1d934806460aae1382741233376e73ce370fcb67d930b758`). `assetcap-el` Gunicorn workers reloaded with `HUP`; service remained active and local VM HTTP check returned `302` to login. User confirmed the behavior works properly in production.
- **VM backups:**
  - `/home/developer/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py.bak_20260504_112403_pre_tab_counts`
  - `/home/developer/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py.bak_20260504_112411_pre_tab_counts`
- **Rollback:** `cp /home/developer/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py.bak_20260504_112403_pre_tab_counts /home/developer/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py && kill -HUP $(ps -o pid,ppid,args -C gunicorn | awk '$2==1 && /Asset_dashboard_EL/ {print $1; exit}')`.

### [DASH-001] User Activity Log lacked a Date filter (ME/BF/EL per-discipline review dashboards)
- **Severity:** Low (UX)
- **Status:** Resolved (deployed 2026-04-30)
- **Resolution:** Added `<input type="date" id="ua-date-filter">` between the QR Code and Reset controls on each discipline's User Activity Log view. Wired into `renderUserActivityTable()` and the reset handler. Compares `row.date` (already `YYYY-MM-DD`) to the selected date.

### [DASH-002] "Dashboard" / "Main Dashboard" label inconsistencies in EL embedded view
- **Severity:** Low (UX)
- **Status:** Resolved (deployed 2026-04-29)
- **Resolution:**
  - Removed the redundant "Main Dashboard" back-button inside the EL embedded reviewer (sat before the building selector).
  - Renamed the top-bar "Dashboard" button (next to "Open full page") in the EL tab of the main Dashboard to "Main Dashboard".

### [DASH-004] AI Process Queue — System Logs / SLD Extraction Runs visibility + admin re-run
- **Severity:** Feature
- **Status:** Resolved (deployed 2026-05-05)
- **Found:** 2026-05-05 (UX request — ops users couldn't see AI extraction history or react to bad runs without leaving the dashboard)
- **Environment:** Dashboard `#qr-pending-view` + EL Reviewer `sld_blueprint.py`
- **Changes:**
  1. **Dashboard**: new "SLD Extraction Runs" sub-section under existing "System Logs" lists every run found in `/home/developer/sld_extract_feedback/sld_*.jsonl` (newest first, default limit 20). Columns: short run_id, time, building, PDF, status badge (Success / Error / Timeout / Running), asset count, duration, action buttons.
  2. **Drilldown page** (`GET /sld-logs/runs/<run_id>`) renders the run summary card, the rows from `electrical_building_schema WHERE sld_extract_run_id = ?`, and a collapsible Model Calls table (label, model, latency_ms, ok/error).
  3. **Action: View** — links to drilldown.
  4. **Action: Open in EL Reviewer** — uses the existing iframe hash-view pattern (`#review-el-view`); a click interceptor in `dashboard.html` updates `#iframe-review-el`'s `src` to include `?building=<code>` before switching the hash. Sub-app `sld.js` accepts both `?filter_building=` and `?building=`.
  5. **Action: Re-run extraction** (admin only — gated by `is_dashboard_admin()` / `dashboard_admin_required`): button POSTs to a new Dashboard route `/sld-logs/runs/<run_id>/rerun`, which loopback-POSTs (`urllib`, no `requests` dep) to `http://127.0.0.1:8005/sld/api/rerun/<run_id>` forwarding the user's session cookie. Avoids CORS on the cross-domain origin.
  6. **EL Reviewer refactor**: extracted the body of `api_process` into a private helper `_run_extraction(filename, building_code, replace)` so both `POST /sld/api/process` and the new `POST /sld/api/rerun/<run_id>` reuse the same Channel A/B logging + backup/restore pipeline. New helpers `_is_dashboard_admin()` (mirrors Dashboard's via `DASHBOARD_ADMIN_USERS` env, both apps load `auth_service.env`) and `_read_run_meta(run_id)`.
  7. **Refresh button** on the SLD Runs section calls `GET /sld-logs/runs` (JSON) and re-renders the tbody without a full page reload.
  8. **Empty state** — "No SLD extraction runs yet." when the feedback dir is empty.
- **Files added:**
  - `Dashboard/templates/sld_log_detail.html` (drilldown page)
- **Files modified:**
  - `Dashboard/Asset_portal_dashboard.py` — added `SLD_FEEDBACK_DIR` / `SLD_REVIEW_BASE_URL` / `SLD_REVIEW_INTERNAL_BASE`, helpers `_sld_runs_index`, `_sld_run_detail`, `_resolve_db_path`, `_parse_sld_jsonl`, `_sld_run_status_from_records`; routes `GET /sld-logs/runs`, `GET /sld-logs/runs/<run_id>`, `POST /sld-logs/runs/<run_id>/rerun`; passes `sld_runs` + `sld_review_base_url` into `dashboard()` template context.
  - `Dashboard/templates/dashboard.html` — new "SLD Extraction Runs" section under the existing System Logs block (~lines 1620-1700); JS block at file-end binds Refresh / Re-run / Open-in-EL handlers.
  - `review/Asset_dashboard_browser_EL/sld_blueprint.py` — new `_is_dashboard_admin`, `_read_run_meta`, `_run_extraction` helpers; `api_process` reduced to a thin wrapper; new `api_rerun(run_id)` route.
  - `review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js` — accept `?building=` alongside the existing `?filter_building=` URL param.
- **Verification (2026-05-05):**
  - Local AST + Jinja2 parse checks: PASS for both Python files and both templates.
  - VM AST parse via `python3 -c "ast.parse(...)"`: PASS for both deployed Python files.
  - Backup files written on VM: `*.bak_20260505_123832` for the four overwritten production files.
  - Service restart: `assetcap-dashboard` and `assetcap-el` both `active` after restart; both serve HTTP 302 to login on root path (expected).
  - Route registration probes (HTTP 302 = route exists, login-gated):
    - `GET  http://127.0.0.1:8002/sld-logs/runs` → 302 ✓
    - `POST http://127.0.0.1:8002/sld-logs/runs/<run_id>/rerun` → 302 ✓
    - `POST http://127.0.0.1:8005/sld/api/rerun/<run_id>` → 302 ✓
  - Feedback dir on VM contains 3 prior SLD runs + `corrections.jsonl`, so the queue UI will populate immediately on first authenticated visit.
  - Browser walkthrough of 6 plan steps (View / Open / admin Re-run / non-admin gate / failure case / empty state): pending human verification.
- **Notes:**
  - Plan deliberately deferred to v1: rollback / delete-this-run, acknowledge / dismiss tracking, live-tail of in-progress runs, surfacing `corrections.jsonl` as its own view.
  - The cross-service POST originally proposed in the plan was changed mid-implementation to a server-side reverse-proxy when CORS check showed the EL Reviewer has no `Access-Control-Allow-*` configuration. Net effect: simpler frontend (`credentials: 'same-origin'`), no new dependency, admin gate enforced twice (Dashboard + EL).

### [DASH-005] SDI Live Pipeline distinct QR cards + modern flow redesign
- **Severity:** Low (UX / operational clarity)
- **Status:** Resolved (deployed to VM 2026-05-26)
- **Found:** 2026-05-26 (Dashboard SDI flow card was hard to read and counted process rows rather than distinct review-state QR codes)
- **Environment:** Dashboard main view, `Pipeline Overview & Shortcuts`, route `/chart/sdi_flow`
- **Expected:** SDI Live Pipeline should show high-signal review-state totals and a clear SDI progression without duplicated/nested card chrome.
- **Actual (before fix):** D3/SVG flow showed Mechanical and Electrical discipline cards, relied on external D3/Google Font assets, and was embedded inside an extra Dashboard card wrapper. The requested New / Update Existing / Manual Entry totals were not shown.
- **Resolution:**
  1. `Dashboard/charts/flow_quantity_chart.py` now returns a structured contract with KPI cards, total distinct QR count, unclassified count, and simplified flow stages.
  2. KPI cards count distinct base QR codes from `QR_code_assets.Col_process`: `0` New Assets, `1` Update Existing, `2` Manual Entry. Multiple photo rows for one QR count once.
  3. Lower flow intentionally omits Mechanical/Electrical cards and shows only `SDI Queue -> Requested -> Into Planon`.
  4. `Dashboard/templates/sdi_label.html` rebuilt as semantic HTML/CSS; no D3, no Google Fonts. Stage cards use modern numbered styling, captions, and connectors; bottom progress bars were removed by final UX request.
  5. `Dashboard/templates/dashboard.html` embeds the iframe without an extra card wrapper and keeps the chart left-aligned with the Dashboard content.
- **Files modified:**
  - `Dashboard/charts/flow_quantity_chart.py`
  - `Dashboard/templates/sdi_label.html`
  - `Dashboard/templates/dashboard.html`
  - `test/test_sdi_flow_counts.py`
  - `Markdowns_documentation/rules/dashboard.rules.md`
  - `.agent_app/rules/dashboard.rules.md`
- **Verification:**
  - Local and VM `python3 test/test_sdi_flow_counts.py`: PASS.
  - Local and VM Jinja parse checks for `dashboard.html` / `sdi_label.html`: PASS.
  - VM SHA256 matched local for deployed files during deploy.
  - VM Dashboard Gunicorn reloaded with `HUP`; `assetcap-dashboard` active; `/chart/sdi_flow` returns HTTP 302 login redirect.
- **VM rollback backups:**
  - `/home/developer/deploy_backups/sdi_live_pipeline_20260526_100658`
  - `/home/developer/deploy_backups/sdi_live_pipeline_visual_20260526_102006`
  - `/home/developer/deploy_backups/sdi_live_pipeline_left_align_20260526_102650`
  - `/home/developer/deploy_backups/sdi_live_pipeline_flow_caption_20260526_103809`

### [EL-UI-001] "Swift Over" → "Switch Over" rename + tab-row cleanup
- **Severity:** Low (UX/typo)
- **Status:** Resolved (deployed 2026-04-30)
- **Resolution:** SLD panel `<h4>Swift Over &mdash; inline editor</h4>` → `<h4>Switch Over - Inline Editor</h4>`. Title font 15px → 18px (+20%). Tab button label "Swift Over Editor" → "Switch Over Editor". Underlying CSS classes/IDs unchanged.

### [SLD-001] Switch Over Inline Editor — header redesign + Hierarchy card + table grouping + PDF/Excel
- **Severity:** Feature
- **Status:** Resolved (deployed 2026-05-01)
- **Found:** 2026-05-01 (UX request)
- **Environment:** EL Review → Distribution → "Switch Over to Table Format"
- **Changes:**
  1. **Stat cards moved beside the "Find by" filter** (previously stacked above). Wrapped both in a flex `.sld-search-row`.
  2. **New "Hierarchies" stat card** showing the count of distinct `Hierarchy` values in the loaded building. `updateAssetStats()` now feeds `#sld-stat-hierarchy` from `new Set(allAssets.map(a => String(a.Hierarchy ?? '').trim()).filter(Boolean)).size`.
  3. **Auto-fit table columns** to the widest content — `applySwiftColumnWidths()` max-char caps relaxed (Hierarchy 14 → 80, Equipment ID 28 → 80, Room 32 → 80, etc.).
  4. **Hierarchy group shadow** — each `<tr>` carries `data-hierarchy=…`; new `tagSwiftHierarchyGroups()` marks the first/last row of each contiguous run with `is-hierarchy-group-start` / `is-hierarchy-group-end`. CSS draws a subtle inset top/bottom shadow on those marker rows. Re-runs after any in-place row replace inside save flow so live edits to Hierarchy reshape the bands correctly.
  5. **Excel export** — replaced "stripe every other row" with **alternating fill on Hierarchy boundaries**; per-column auto-fit to widest cell content (header included) with sensible floors.
  6. **PDF export** — when the Switch Over toggle is on, the PDF button now exports the **table** via the new `exportSwiftTablePdf()` (jsPDF + jspdf-autotable plugin loaded via CDN) with `cellWidth: 'wrap'` auto-sizing and per-Hierarchy banding via `didParseCell`. Diagram export path unchanged when toggle is off.
- **Files:** `review/Asset_dashboard_browser_EL/review_asset_templates/sld/sld_panel.html`, `review_asset_templates/static/sld/sld.css`, `review_asset_templates/static/sld/sld.js`, `review_asset_templates/dashboard.html` (new `jspdf-autotable` script tag).

### [SLD-002] Stat-card visual refresh + Find By position + no-wrap
- **Severity:** Feature (UX)
- **Status:** Resolved (deployed 2026-05-01)
- **Changes:**
  - **Order swap:** Find by filter now precedes the stat cards in `.sld-search-row`.
  - **Find-by single-line lock:** `.sld-find-bar` flipped from `flex-wrap: wrap` to `flex-wrap: nowrap` + `white-space: nowrap`, with `flex-shrink: 0` on each child. The value input stays elastic via `flex: 1 1 auto; min-width: 200px`.
  - **Card restyle** modeled on the supplied template — white background, 12 px radius, soft shadow, 20 px bold colored figure on the left, label below (uppercase, muted), 22 px Bootstrap icon on the right. Per-variant accent colors: Assets teal `#2e6ea6` (`bi-collection`), Matched green (`bi-check-circle`), Unmatched red (`bi-x-circle`), Ambiguous orange (`bi-exclamation-triangle`), Hierarchies indigo `#6366f1` (`bi-diagram-3`).

### [SLD-003] SLD control buttons converted to icon-only with hover tooltips
- **Severity:** Feature (UX)
- **Status:** Resolved (deployed 2026-05-01)
- **Changes:** Five buttons (Layout, Collapse, Expand All, Create a New Diagram, + New Asset) replaced their text labels with Bootstrap Icons. Original descriptions preserved in `title=""` (browser-native hover tooltip) and `aria-label` (assistive tech). Orientation button JS (`updateOrientationButton`) now swaps the icon class (`bi-arrow-down-up` ↔ `bi-arrow-left-right`) instead of rewriting `textContent`; title also flips to describe the *next* state. New CSS class `.sld-icon-btn` gives the buttons a square footprint with centered glyph.
- **Follow-up:** icon font size later bumped from 16 px → 22 px (+37.5%) per request.

### [SLD-004] d3 tree label overlap on rotated layout
- **Severity:** Medium (legibility)
- **Status:** Resolved (deployed 2026-05-01)
- **Found:** 2026-05-01 (visible on horizontal/rotated SLD layouts where children stack vertically beside the parent)
- **Symptom:** QR-code label of one sibling overlapped the rating line (`600V | 400A | 3W`) of the previous sibling.
- **Root cause:** Each node draws a 50 px box plus a QR label (~17 px above) and a rating label (~22 px below) — total visual span ~89 px per node — but `d3.tree().nodeSize([dx, dy])` was set to `[90, 220]` in the rotated orientation, leaving zero clearance.
- **Resolution:** Bumped `nodeSize` — rotated `[90, 220]` → `[120, 240]`; top-down `[160, 140]` → `[180, 160]` for label-safety in both orientations. File: `review_asset_templates/static/sld/sld.js` (`buildTree`).

### [SDI-001] SDI dashboard gated on building selection
- **Severity:** Feature (UX + perf)
- **Status:** Resolved (deployed to VM 2026-05-01 14:28 — bundled with [SDI-002])
- **Found:** 2026-05-01
- **Why:** Loading the dashboard with no `building_code` previously built `unpackaged_df` / `packaged_df` across every building. Process actions also posted against an empty `selected_building` hidden input.
- **Resolution:** [SDI_process/app.py:1142-1213](SDI_process/app.py#L1142-L1213) — when `building_code` is empty, route returns empty datasets and `requires_building=True`. [SDI_process/template/dashboard.html:185-209](SDI_process/template/dashboard.html#L185-L209), [dashboard.html:565-650](SDI_process/template/dashboard.html#L565-L650) — template renders a centered "Select a Building to Continue" placeholder card and skips the entire tabs/tables block. Building dropdown change handler moved above the DataTables init guard so it still works in the gated state. Default option label changed `-- All Buildings --` → `-- Select a building --`.
- **VM backup:** `/home/developer/SDI_process/app.py.bak_20260501_142758_pre_main_asset` (pre-deploy snapshot of the live `app.py`, also covers rollback for [SDI-002]).
- **Rollback:** `cp <backup> /home/developer/SDI_process/app.py && kill -HUP $(ps -o pid,ppid -C gunicorn | awk '/SDI_process/ && $2==1 {print $1; exit}')`.

### [SDI-002] Planon export "Main Asset" cell populated via SUST System List lookup
- **Severity:** Feature (Planon export correctness)
- **Status:** Resolved (deployed to VM 2026-05-01 14:28 — bundled with [SDI-001])
- **Found:** 2026-05-01
- **Why:** Template column `AQ` ("Main Asset") in `Import Assets-TEMPLATE-082923.xlsx` requires the SUST Asset Code (e.g. `SYS0032098`), not the descriptive value (e.g. `"Distribution Systems"`). Previously the cell was always blank: `sdi_print_out` doesn't carry `Main Asset`, no df column ever mapped to header AQ.
- **Resolution:** New helper [_resolve_main_asset_codes()](SDI_process/app.py) added above `export_to_planon()`. Per QR: joins back to `sdi_dataset` (or `sdi_dataset_EL` when `Attribute='electrical'`) on `QR Code` to recover the descriptive Main Asset, then looks up `(Property.Property code = Building, Description = descriptive)` in `SUST - System List` → returns `Asset Code`. Call site sits between the attribute-set filter block and the template-open; the existing template-header normalization (`_normalize_name`) maps `df2["Main Asset"]` → AQ automatically. Edge cases: empty descriptive/Building → blank cell; no match → blank + console warning listing affected QRs; multiple matches → hard block (mirrors the existing duplicated-Asset-Group guard).
- **Validation:** Read-only standalone harness ([scripts/_vm_test_main_asset_lookup.py](scripts/_vm_test_main_asset_lookup.py)) confirmed 233/233 rows in `sdi_print_out` resolve, 0 unresolved, 0 duplicates. End-to-end xlsx generation harness ([scripts/_vm_test_export_aq.py](scripts/_vm_test_export_aq.py)) generated `/tmp/test_export_aq.xlsx` for `SDI-00017` (217 rows); read-back confirmed cell `AQ10` for QR `0000184869` = `'SYS0032098'` (matches the user-provided spec).
- **VM backup:** `/home/developer/SDI_process/app.py.bak_20260501_142758_pre_main_asset` (shared with [SDI-001]).
- **Depends on:** [DATA-003] `Main Asset` columns on `sdi_dataset` / `sdi_dataset_EL` (live writers wired earlier the same day) and [DATA-004] `SUST - System List` CSV→DB sync.

### [SLD-011] SLD PDF import preview confirmation
- **Severity:** Feature / workflow correction
- **Status:** Resolved (implemented locally and deployed to VM 2026-05-21)
- **Found:** 2026-05-21 (the old import flow required the PDF filename to begin with a building-derived 3-digit code and used a second replace confirmation prompt)
- **Resolution:**
  1. **Building source of truth:** the current SLD/global building selector now supplies `building_code`. `/sld/api/upload` receives it in multipart form data and no longer derives building identity from the filename.
  2. **Validation:** frontend and backend keep PDF-only validation. The 3-digit filename prefix requirement was removed.
  3. **Preview confirmation:** selecting a PDF opens a PDF.js preview inside the import modal before upload. The modal shows the selected building, selected filename, and an inline warning when the building already has an active SLD.
  4. **Preview controls:** page navigation, zoom in/out, rotate clockwise, and mouse drag-to-pan are supported. Rotating auto-fits the page to the preview area, and zoom can go below 50% when needed to avoid cutting large rotated drawings.
  5. **Confirmation semantics:** `Upload & Process` is the final confirmation and calls `/sld/api/process` with `replace: true`; the old `Replace & Process` prompt was removed. `Cancel` closes the modal and clears preview/render state.
- **Files:** [review/Asset_dashboard_browser_EL/sld_blueprint.py](review/Asset_dashboard_browser_EL/sld_blueprint.py), [review/Asset_dashboard_browser_EL/review_asset_templates/sld/sld_panel.html](review/Asset_dashboard_browser_EL/review_asset_templates/sld/sld_panel.html), [review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js), [review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css), [review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html](review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html).
- **VM deployment notes:** initial deploy created post-copy backups with timestamp `20260521_110455_deployed_pdf_preview` after a pre-copy backup quoting error. Follow-up preview-fit backup timestamp: `20260521_111425_pre_pdf_fit`. Drag-pan backup timestamp: `20260521_112014_pre_pdf_pan`. Gunicorn master `31418` was HUPed after each deploy; `assetcap-el` stayed active and the SLD route returned `302` (login redirect, healthy).
- **Validation:** local `python -m py_compile sld_blueprint.py`, local `node --check sld.js`, VM `python3 -m py_compile sld_blueprint.py`, local/VM SHA256 matches on deployed files, and HTTP health probe on `127.0.0.1:8005/review-distribution?tab=sld`.

### [SLD-012] Breaker-label amperage misattributed to transformers after silent all-AI-failure run
- **Severity:** High (wrong Planon-facing data stored looking clean; propagated into children's `Fed From Amperage Rating`)
- **Status:** Resolved (code + data fix deployed to VM 2026-06-12)
- **Found:** 2026-06-12 (user noticed TX-N01/N11/N21/N31 in building 750 carried `Amperage Rating` 200/90/90/90 A although the drawing shows no transformer amperage)
- **Root cause (two stacked bugs, run `sld_20260608_174333_b750_989c76`):**
  1. **Silent total-AI-failure:** every one of the run's 30 OpenAI calls failed with `RateLimitError 429` (quota exhausted; the API key was replaced 2026-06-11). The run summary recorded `raw_asset_count: 0` / `no_assets_extracted`, yet `write_payload_to_db` unconditionally merged the `build_text_layer_asset_payload()` supplemental scrape, stored 25 unverified rows with no manual-review markers, and exited 0.
  2. **Text-layer harvest over-reach:** `extract_text_layer_attributes` / `enrich_attributes_from_pdf_text` take the first amperage-looking line within ~7 PDF text lines after each tag. After each TX tag the 750 text layer reads `75kVA` / `600V:208Y/120V` / `200A-3P` (or `90A-3P`) / `LSI` — the **feeder breaker label** (trip amps + pole count + LSI trip unit), which the harvester attributed to the transformer. (90 A is the standard 125% primary breaker for a 75 kVA 600 V TX — real drawing text, wrong equipment.)
- **Resolution:**
  1. **Breaker-label guard:** new `BREAKER_NOTATION_PATTERN` (`200A-3P`, `90A-3P`, `100AT-3P`, `225AF-2P`…) — lines matching it are never harvested as `rating`; and `TX-` assets never harvest bare amperage from the text layer at all (transformers are kVA-rated; nearby A-values are protective devices). Applied to both text-harvest sites; the LLM path and panel lines like `225A, 208Y/120V, 3PH, 4W` are unaffected.
  2. **Fail-loud:** `_MODEL_CALL_SUCCESSES` counter; when the extraction payload is empty AND zero model calls succeeded, `main()` aborts with exit 1 **before** `write_payload_to_db` (archival/recreate live inside the write path, so the previously active SLD stays untouched). The blueprint surfaces the failure to the user instead of storing a clean-looking scrape.
  3. **Data fix (VM PG, audited):** cleared `Amperage Rating` (+UoM) on the four TX rows (row_ids 3758-3761; kVA/voltage preserved), wrote 8 `audit_trail` rows (`source='system'`, `modified_by='maintenance'`), and re-ran the building-750 Fed From derivation so CDP-2N0/2N1/2N2/2N3 now carry blank `Fed From Amperage Rating` — correct, since the SLD genuinely has no transformer amperage.
- **Files:** [review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py](review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py).
- **VM deployment:** backup `extract_electrical_schema.py.bak_20260612_141921`; SHA256 verified; EL-venv `py_compile` passed. No gunicorn HUP needed (the script is spawned per extraction run).
- **Validation:** local harness against the real 750 PDF text layer (PyMuPDF): TX tags harvest no amperage but keep `75kVA` + voltage; CDP-2N1/2N2/2N3 still harvest `225A, 208Y/120V, 3PH, 4W`; breaker lines rejected by the new pattern while panel rating lines are not. Live fail-loud test on the VM with a bogus API key + scratch DB: exit 1, "Aborting without writing", scratch DB never created. Before/after SELECTs for the data fix recorded in session.
- **Known residual:** the same 7-line window can still attach a *neighbour's* wire/voltage lines to a TX (e.g. `wire_rating` "4W" from the next asset's panel line) — cosmetic relative to amperage; revisit if it bothers reviewers.

### [SDI-003] Iframe-safe alert / confirm dialogs (replaces native `alert()` / `confirm()`)
- **Severity:** Medium (workflow blocker when SDI is embedded — silent in iframe)
- **Status:** Resolved (deployed to VM 2026-05-01 14:42 [alerts] + 2026-05-01 14:50 [confirms])
- **Found:** 2026-05-01 (user screenshot of `"Select an SDI Print Control ID first."` browser-native alert; second screenshot of `"QR Codes already exported: …"` confirm)
- **Why:** Chrome 92+ (and equivalent in Firefox/Safari) silently blocks `window.alert()` / `confirm()` / `prompt()` inside cross-origin iframes unless the parent passes `allow-modals` in the `sandbox=` attribute. The unified Dashboard's iframe sources the SDI app from `sdiprocess.assetcap.facilities.ubc.ca`, a different origin, so every native modal fired from the SDI dashboard never rendered when embedded — only "Open full page" worked.
- **Resolution:** [SDI_process/template/dashboard.html](SDI_process/template/dashboard.html) — added two reusable Bootstrap 5 modals (`#sdiAlertModal`, `#sdiConfirmModal`) with severity-aware icon, title, message body (with `white-space: pre-wrap` so `\n` line breaks render and long QR-code lists wrap cleanly), and color-themed buttons. Two helpers:
  - `window.showSdiAlert(message, opts)` — replaces 6 native `alert()` call sites. Severity drives icon + button color: warning (validation prompts) or danger (export errors).
  - `window.showSdiConfirm(message, opts)` — returns `Promise<boolean>`. Resolves `true` only on the OK click; resolves `false` on Cancel, Esc, X, or backdrop click. Replaces 3 native `confirm()` call sites. The exclude/archive form callsite uses a `data-sdi-confirmed='1'` bypass flag and `requestSubmit(submitter)` to preserve the archive-vs-exclude `formaction` after the user OKs the modal.
- **VM backups:**
  - Pre-alert deploy: `/home/developer/SDI_process/template/dashboard.html.bak_20260501_144232_pre_alert_modal` (predates both alert and confirm rounds).
- **Rollback:** `cp <backup> /home/developer/SDI_process/template/dashboard.html && kill -HUP $(ps -o pid,ppid -C gunicorn | awk '/SDI_process/ && $2==1 {print $1; exit}')`.

### [SLD-005] SLD extraction feedback corpus (v1 + human_correction capture)
- **Severity:** Feature (AI improvement loop foundation)
- **Status:** Resolved (deployed to VM 2026-05-04 08:36)
- **Found:** 2026-05-04 (user request to make AI logs detailed enough to retrofeed prompt-engineering and fine-tuning)
- **Why:** Output of `extract_electrical_schema.py` was previously captured by `subprocess.run(capture_output=True)` with stdout dropped on success and only the last 2,000 chars of stderr surfaced on failure (via `current_app.logger.error("[sld] extraction failed ...")`). Nothing landed in `ai_check.log` (different invocation path). With no telemetry, no prompt improvement or fine-tune corpus is possible.
- **Resolution:** Two-channel logging plus a human-correction capture path so every extraction is replayable and every human edit is joinable to its origin run.
  - **Channel A — `/home/developer/sld_extract.log`:** human-readable START/END envelopes plus captured stdout/stderr per run. Same shape as `ai_check.log` for grep parity.
  - **Channel B — `/home/developer/sld_extract_feedback/<run_id>.jsonl`:** structured JSONL events per run. `run_id` format: `sld_<UTC>_b<building>_<pdf_sha1[:6]>`.
  - **Event kinds shipped (v1):** `run_meta` (wrapper, pre-subprocess: pdf+script sha1s, paths), `model_call` (all 4 `client.responses.create()` sites in the script via `_traced_create()`: model, instructions sha1+preview, input with first image inline + remaining images hash-only, output_text, usage tokens, latency, ok/error), `wrapper_event` (timeout, missing script, non-zero exit), `run_summary` (asset/hierarchy counts, model_used, manual review reasons, duration).
  - **Event kind shipped (v2):** `human_correction` (EL review `update_asset` and `swift_save_asset` paths emit one event per changed field, joined by `ai_run_id`). Written append-only to `/home/developer/sld_extract_feedback/corrections.jsonl`.
  - **Schema:** new columns `sld_extract_run_id TEXT` and `sld_ai_extract_payload TEXT` on `electrical_building_schema`. Idempotent ALTER in `ensure_sld_schema()` (added on app startup; verified 125/125 existing rows updated). Inserts in `recreate_table()` + `write_payload_to_db()` populate both. `restore_archived_rows()` updated to preserve them across re-extractions.
  - **Kill-switch:** `SLD_FEEDBACK_DISABLED=1` env disables both channels (no-op emitter). `SLD_FEEDBACK_DIR` and `SLD_EXTRACT_LOG` overridable.
  - **Image redaction policy:** first input image of each run inlined as full data URL; subsequent images replaced by `{image_sha1, image_bytes, image_omitted: true}` to keep corpus size sub-MB per run.
  - **Best-effort writes:** every file append wrapped in try/except so a logging failure never breaks extraction or a user-facing save.
- **Files:**
  - New: [review/Asset_dashboard_browser_EL/sld/_feedback.py](review/Asset_dashboard_browser_EL/sld/_feedback.py) (360 lines — `FeedbackEmitter`, `traced_responses_create`, `redact_input`, `write_corrections_for_changed_fields`, `make_run_id`, `parse_summary_from_stdout`).
  - Modified: [review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py](review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py) (4 `client.responses.create` sites wrapped, new `--feedback-file` and `--run-id` CLI args, schema updated, `__SLD_RUN_SUMMARY__` line emitted at exit).
  - Modified: [review/Asset_dashboard_browser_EL/sld_blueprint.py](review/Asset_dashboard_browser_EL/sld_blueprint.py) (`api_process` route writes envelopes + passes args; `update_asset` and `swift_save_asset` emit `human_correction` events; `ensure_sld_schema` and `restore_archived_rows` updated).
- **Validation:** [scripts/_test_sld_feedback.py](scripts/_test_sld_feedback.py) — 32 unit-style checks across 8 scenarios (write, disable-by-arg, disable-by-env, image redaction policy, traced model_call shape, summary line round-trip, correction emission, run_id format). All pass locally.
- **VM backups (rollback):**
  - `/home/developer/review/Asset_dashboard_browser_EL/sld_blueprint.py.bak_20260504_083602_pre_feedback`
  - `/home/developer/review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py.bak_20260504_083602_pre_feedback`
- **Rollback:** `ssh developer@142.103.68.1 'cd /home/developer/review/Asset_dashboard_browser_EL && cp sld_blueprint.py.bak_20260504_083602_pre_feedback sld_blueprint.py && cp sld/extract_electrical_schema.py.bak_20260504_083602_pre_feedback sld/extract_electrical_schema.py && rm -f sld/_feedback.py && kill -HUP $(ps -o pid,ppid,args -C gunicorn | awk \$2==1 && /Asset_dashboard_EL/ {print \$1; exit})'`. The two new DB columns are harmless if left in place after rollback (just unpopulated going forward).
- **Deferred to v3:** fine-tuning pipeline scripts (`build_sld_finetune_corpus.py`, `launch_sld_finetune.py`, `eval_sld_model.py`) — gate on ≥50 `human_correction` events accumulated in the corpus and on vision FT availability for the target base model. Until then the corpus drives prompt engineering: top-N few-shot examples in the system prompt, failure-mode taxonomy from `human_correction.field` clusters, dictionary updates from `dictionary_event` misses (latter event kind is itself v3-deferred).

### [SLD-006] Find-by warning text overlapped neighbouring stat cards
- **Severity:** Low (UX — warning text rendered into adjacent layout)
- **Status:** Resolved (deployed to VM 2026-05-04 10:05)
- **Found:** 2026-05-04 (user screenshot; orange `"No Equipment ID match in this diagram!"` text spilling past the find-bar into the ASSETS / MATCHED / UNMATCHED stat cards)
- **Root cause:** `.sld-find-status` lived inside `.sld-find-bar` which is `flex-wrap: nowrap` + `white-space: nowrap` with `flex-shrink: 0` on every child. The status text could neither wrap nor shrink, so it overflowed the bar's `max-width: min(620px, 100%)` boundary into the neighbouring `.sld-stats` flex column in `.sld-search-row`.
- **Resolution:**
  - [review_asset_templates/sld/sld_panel.html](review/Asset_dashboard_browser_EL/review_asset_templates/sld/sld_panel.html) — moved the `<span class="sld-find-status" id="sld-find-status">` *out* of `.sld-find-bar` and made it a sibling under `.sld-find-wrap` (between the bar and the results dropdown).
  - [review_asset_templates/static/sld/sld.css](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css) — `.sld-find-status` is now `display: block; margin-top: 4px; white-space: normal; word-break: break-word;` so it sits on its own row beneath the bar and wraps if it ever gets long.
  - JS unchanged — `setFindStatus()` queries by ID; the move is invisible to it.
- **VM backups:**
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/sld/sld_panel.html.bak_20260504_100544_pre_status_fix`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css.bak_20260504_100544_pre_status_fix`

### [SLD-007] `restore_archived_rows` silently archived OTHER buildings on every Process call
- **Severity:** High (every SLD `Process` for one building wiped the active diagram for every other building)
- **Status:** Resolved (deployed to VM 2026-05-04 10:21; building 750 recovered 10:17)
- **Found:** 2026-05-04 (user reported empty SLD diagram for building 750 even though 27 SDI assets were recorded for it)
- **Symptom:** Building 750 had 25 SLD rows in `electrical_building_schema` but ALL `new_draw='FALSE'`. The SLD page filters to active rows only, so the diagram rendered empty even though the data was there.
- **Root cause:** The `api_process` route at the previous [sld_blueprint.py:1645](review/Asset_dashboard_browser_EL/sld_blueprint.py#L1645) called `restore_archived_rows([r for r in old_rows_to_archive if r["Building"] != building_code])` to re-insert other buildings' rows after the table-drop. But `restore_archived_rows` always inserts with `new_draw='FALSE'` — so OTHER buildings' previously-active rows were silently archived on every Process. Pre-existing bug, not introduced today.
- **Resolution (two parts):**
  1. **Recovery (DB UPDATE):** flipped 750's 25 archived rows back to `new_draw='TRUE'`. Pre-state: 750 = 0 active / 25 archived. Post-state: 750 = 25 active / 0 archived. Building 217 untouched (108 / 125). DB backup taken first: `QR_codes.bak_20260504_101732_pre_750_recover.db`.
  2. **Code fix:** refactored `restore_archived_rows` to delegate to a private `_insert_rows_into_schema(rows, new_draw_value)` helper, then added `restore_active_rows(rows)` that inserts with `new_draw='TRUE'`. Updated the `api_process` other-buildings restore to call `restore_active_rows` instead of `restore_archived_rows`. The other two `restore_archived_rows` callsites (already-archived rows; this-building's prior active rows being superseded) remain correct as archive operations. File: [review/Asset_dashboard_browser_EL/sld_blueprint.py](review/Asset_dashboard_browser_EL/sld_blueprint.py).
- **VM backup:** `/home/developer/review/Asset_dashboard_browser_EL/sld_blueprint.py.bak_20260504_102132_pre_restore_active_fix`

### [SLD-008] SLD xlsx export failed in Edge with `Couldn't download — Network issue`
- **Severity:** High (xlsx export workflow blocked)
- **Status:** Resolved (deployed to VM 2026-05-04 10:45 + 10:57; verified working)
- **Found:** 2026-05-04 (user screenshot of multiple failed xlsx downloads; PDF export from same iframe context succeeding)
- **Root cause:** The previous xlsx export was client-side: ExcelJS → Blob → `URL.createObjectURL` → simulated `<a download>` click → revoke. Edge's iframe download manager selectively blocks Office MIME types delivered via blob URLs (the same iframe successfully downloads PDFs via jsPDF, which uses a similar pattern internally — but jsPDF's bundled FileSaver and the Office-MIME-specific scanner path differ in Edge). Three speculative client-side fixes (longer revoke timeout, externally-loaded FileSaver.js, `<a download>` with `target='_blank'`) all failed. Server-side direct test of the new endpoint via Flask test request confirmed `Status 200, Content-Type xlsx, Content-Disposition attachment` were correct, narrowing the cause to client-side handling of blob URLs in the iframe.
- **Resolution:** New server-side endpoint `GET /sld/api/download-xlsx?building=<code>` in [sld_blueprint.py](review/Asset_dashboard_browser_EL/sld_blueprint.py). Builds the xlsx with openpyxl using the same data path (`get_all_assets`) the page already uses, with feature parity to the client export: 9 columns (Hierarchy / QR Code / Equipment ID / Fed From / Room / Voltage / Amperage / Power / Check), white-on-UBC-blue header, alternating hierarchy bands, ✓/✗ check coloring, frozen header row, auto-fit column widths. Returns via `flask.send_file(as_attachment=True, download_name="EL_Assets_<bld>.xlsx")`. Client `exportSwiftExcel` in [sld.js](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js) now triggers a synthetic `<a download>` click on the real HTTP URL — same path jsPDF uses internally for PDFs (which already worked). The legacy ExcelJS-based code is preserved as `exportSwiftExcelClientLegacy` (not bound) for emergency rollback.
- **New runtime dependency:** `openpyxl 3.1.5` installed in the EL venv at `/home/developer/review/Asset_dashboard_browser_EL/venv/`. `requirements.txt` not updated — should be added in a follow-up commit.
- **VM backups:**
  - `/home/developer/review/Asset_dashboard_browser_EL/sld_blueprint.py.bak_20260504_104549_pre_xlsx_endpoint`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js.bak_20260504_104549_pre_xlsx_endpoint`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html.bak_20260504_103204_pre_filesaver` (from the FileSaver.js attempt — that script tag remains in dashboard.html, harmless and may still be useful to other code)
- **Note for retrospective:** the diagnostic loop was longer than necessary because the early speculative deploys (revoke timeout bump, FileSaver.js) chased the wrong layer. Lesson: when the same code path that works for one MIME type fails for another in the same browser/iframe, jump straight to server-side delivery rather than iterating on client-side variants.
- **Update 2026-06-12 — amperage warning highlight, gridlines off, column layout + embedded Expand-All diagram:** the endpoint emits 14 columns — own-asset block (navy): Hierarchy / Equip QR Code / Equipment ID / Room / Voltage / Amperage / Power / Power Type / Wire; fed-from (parent) asset block (beige): Fed QR Code / Fed From / Fed From Amp Rating / Power; then Check. When own amperage exceeds the matched parent amperage, the own `Amperage` cell is red-highlighted; no separate flag column is exported. Worksheet gridlines are disabled while table borders remain. `Power Type` comes from the matched `sdi_dataset_EL` row via `_enrich_asset_display_fields`. The endpoint also accepts an optional `&diagram_token=<token>` and embeds the client-captured Expand-All diagram PNG below the last table row; the token comes from the new `POST /sld/api/diagram-image` endpoint (single-use, filesystem-backed cache in `<tempdir>/sld_xlsx_diagram_cache` with 120 s TTL — filesystem because the EL service runs 3 gunicorn workers). Capture/upload failures fall back to the table-only spreadsheet. `pillow>=10` added to `requirements.txt`. `exportSwiftTablePdf` (PDF of the Swift table) still renders the previous 11-column layout — known divergence.

### [SLD-010] "Add to SLD" from "Missing from SLD" tab failed with "QR Code is required"
- **Severity:** High (workflow blocker — every Add to SLD attempt from this tab returned 400)
- **Status:** Resolved (deployed to VM 2026-05-04 14:25)
- **Found:** 2026-05-04 (user clicked "Add to SLD" on QR `0000184443` in building 750 / "Missing from SLD" tab and received a warn toast that prevented the action)
- **Symptom:** Server `/sld/api/assets` POST returned `{"error": "QR Code is required"}` (400) for every row in the "Missing from SLD" list.
- **Root cause:** The JS handler `addSdiMissingAssetToSld` in [sld.js:1453-1464](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L1453-L1464) built the request body without a `QR Code` field. The server's [`api_assets_create` endpoint at sld_blueprint.py:1675-1678](review/Asset_dashboard_browser_EL/sld_blueprint.py#L1675-L1678) reads `body.get("QR Code")` and calls `validate_qr_in_sdi(qr_code, building)` ([line 690](review/Asset_dashboard_browser_EL/sld_blueprint.py#L690)) which returns `(False, "QR Code is required")` for empty input. The `row` dict in the JS handler did carry `QR Code` (from `sdiNotInSldAssets`, populated by `get_sdi_not_in_sld_assets` which selects `"QR Code"` per [sld_blueprint.py:534](review/Asset_dashboard_browser_EL/sld_blueprint.py#L534)) — it just wasn't being forwarded.
- **Resolution:** Added `"QR Code": (row['QR Code'] || '').trim()` to the request body. The sibling handler `addMissedAssetToSld` (used by the "Missed Assets" tab, posting to `/missed-assets/add-to-sld`) already forwarded the QR — only the SDI-missing path was broken.
- **Why this slipped through earlier:** The "Missing from SLD" view itself is recent. The two add-to-SLD paths use different endpoints (`/missed-assets/add-to-sld` vs `/api/assets`); the QR-required validation was added to `/api/assets` for the standalone "+ New Asset" form path, and the Missing-from-SLD handler hit the same endpoint without the matching field.
- **Cache-buster:** bumped `?v=20260504-photo1` → `?v=20260504-addqr` on `sld.js` and `sld.css` script tags in [dashboard.html](review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html).
- **Validation:**
  - Local + VM SHA256 matched on the two deployed files.
  - Gunicorn HUPed; HTTP probe `302` (login redirect, healthy).
  - End-to-end re-trace with QR `0000184443` building 750: row carries `QR Code: "0000184443"` and `Equipment ID: "PNL-2E0P1"`. JS pre-check at [line 1448](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L1448) doesn't trigger (PNL-2E0P1 is in building 217's active SLD but NOT in 750's). Server `validate_qr_in_sdi("0000184443","750")` now passes; `active_asset_exists("PNL-2E0P1","750")` returns False; `create_asset` proceeds.
- **Note on cross-building tag collision:** PNL-2E0P1 also exists as an active SLD row for building 217 (row_id 63, QR `0000183851`). `active_asset_exists` is building-scoped at [line 664-666](review/Asset_dashboard_browser_EL/sld_blueprint.py#L664-L666), so this collision does not block the building-750 add — physically distinct panels can share a tag across buildings.
- **VM backups:**
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js.bak_20260504_142500_pre_addqr_fix`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html.bak_20260504_142500_pre_addqr_fix`

### [SLD-009] Hover-and-pin asset photo popover on QR codes (Diagram / Switch Over / Missing-from-SLD)
- **Severity:** Feature (review UX — quick verification of physical asset without leaving the SLD tab)
- **Status:** Resolved (deployed to VM 2026-05-04 13:00; helper round-trip verified on real EL QR)
- **Found:** 2026-05-04 (user request; reviewers had to leave the SLD tab and open the per-asset review page just to confirm what the equipment looks like)
- **Why:** The three QR-bearing surfaces in the EL Review SLD tab (the d3 diagram, the Switch Over inline editor, and the "Missing from SLD" table) all surfaced the QR text but offered no way to glance at the captured photo. With 3-4 captures per QR (`-0` Asset Plate, `-1` UBC Tag, `-2` Main Picture, sometimes `-3`), reviewers wanted a hover preview with rotate + zoom to confirm physical state in-context.
- **User-confirmed scope:** hover opens, click pins; close on Escape, X button, outside-click, or scroll. Default photo is the `-1` capture (UBC Tag); prev/next arrows navigate all photos for the QR with wrap-around. Inside the popover: zoom (±0.2 clamped 0.2-4.0) + rotate (90° steps).
- **Resolution:**
  1. **Backend** ([sld_blueprint.py](review/Asset_dashboard_browser_EL/sld_blueprint.py)) — new helper `_find_photos_for_qr(qr_code, discipline="EL")` modeled on [`find_photo_for_qr` in Asset_portal_dashboard.py:1722](Dashboard/Asset_portal_dashboard.py#L1722) but returning the *full list* sorted by trailing `- <int>` index. Soft discipline filter: when at least one filename for a QR contains ` EL ` (space-bounded), restrict the result to those; otherwise return all matches (legacy files lacking the discipline token). New endpoint `GET /sld/api/photos/<qr>` (login-required, matches the rest of `/sld/api/*`) returns `{qr, default_idx_in_list, photos: [{index, url}]}`. `default_idx_in_list` points at the `-1` photo when present, else 0. Filenames URL-quoted because they contain spaces. 404 with empty `photos[]` when nothing matches; 400 on empty/missing QR.
  2. **Photo serving** — reuses the existing public [`/images/<filename>` route at Asset_dashboard_EL.py:3277](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L3277) (no auth, same-origin to port 8005, no CORS). No new file-serving route.
  3. **Config wiring** — `app.config["IMG_DIR"] = IMG_DIR` registered at [Asset_dashboard_EL.py:288](review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py#L288) so the blueprint helper reads it via `current_app.config`.
  4. **Frontend module** ([sld.js](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js)) — new `assetPhotoPopover` IIFE with state machine (`closed → hovering → pinned`), 150 ms hover-debounce + 200 ms close-grace, AbortController + per-QR `Map` cache, position-anchored via `getBoundingClientRect()` with overflow-mirror fallback (mirror to right side / flip above when popover would overflow viewport, clamp to 8 px viewport margins). Single popover element appended to `<body>` so it floats above tab panes / iframes; lazily created on first open. Exposes `attach(triggerEl, qrCode)` for plain DOM and `attachD3(selection, qrFn)` for d3 selections.
  5. **Three integration points:**
      - **Diagram** — `attachD3` on the `<text class="node-qr-code">` selection inside `renderDiagram` ([sld.js around line 1924](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L1924)). `renderDiagram` calls `assetPhotoPopover.close()` first ([line 1748](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L1748)) so re-renders don't strand the popover anchored to a removed node. The d3 click handler calls `event.stopPropagation()` so it doesn't bubble up to the `<g>` and open the edit panel.
      - **Switch Over inline editor** — per-row `attach` on `.sld-swift-qr` inside `buildSwiftRow` ([sld.js around line 2589](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L2589)).
      - **Missing from SLD** — wraps the QR `<td>` with `<span class="sld-sdi-qr">` and per-row `attach` inside `renderSdiNotInSldAssets` ([sld.js around line 1424](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L1424)).
  6. **CSS** ([sld.css](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css)) — new `.sld-photo-popover` block (top-level, NOT scoped under `.sld-pane`, since the popover lives on `<body>`); `position: fixed; z-index: 1075; width: 360px; max-width: 90vw;`; stage 280 px high with dark backdrop; `.sld-pp-img { transform-origin: center; transition: transform 80ms linear; }`. Also relaxed `.node-qr-code` `pointer-events: none → auto` and added `cursor: zoom-in` so the SVG QR text receives its own hover events.
  7. **Cache-buster** bumped to `?v=20260504-photo1` on `sld.css` and `sld.js` script tags in [dashboard.html](review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html) (lines 15 and 2332).
- **Reused existing pieces:**
  - `find_photo_for_qr` glob/sanitize logic at [Asset_portal_dashboard.py:1722](Dashboard/Asset_portal_dashboard.py#L1722) — ported verbatim, extended to return all matches sorted by index.
  - BF Review zoom/rotate increments and clamps at [review/Asset_dasboard_browser_BF/review_asset_templates/review.html:925-950](review/Asset_dasboard_browser_BF/review_asset_templates/review.html#L925-L950) — copied verbatim (±0.2 zoom, max 4, min 0.2; ±90° rotate; reset on photo switch).
  - Cursor-follow tooltip lifecycle at [sld.js:1515-1537](review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js#L1515-L1537) — modeled the open/close states on it (extended with click-to-pin + grace-period transitions).
  - `escapeHtml` and `getQrCodeText` already inside the IIFE.
- **Validation:**
  - Local + VM `python3 -m py_compile sld_blueprint.py Asset_dashboard_EL.py` passed.
  - Local + VM SHA256 matched on all 5 deployed files (sld_blueprint.py, Asset_dashboard_EL.py, sld.js, sld.css, dashboard.html).
  - `assetcap-el` Gunicorn HUPed; service stayed active; HTTP probe on `127.0.0.1:8005/` returned `302` (login redirect, expected).
  - `curl /sld/api/photos/0000182390` returns `302 → /login` (auth gate working).
  - Helper round-trip via `python -c "from sld_blueprint import _find_photos_for_qr; ..."` against real EL QR `0000182390` returned the three EL captures (`- 0`, `- 1`, `- 2`) in correct index order.
- **VM backups (rollback set, all `*.bak_20260504_130000_pre_photo_popover`):**
  - `/home/developer/review/Asset_dashboard_browser_EL/sld_blueprint.py.bak_20260504_130000_pre_photo_popover`
  - `/home/developer/review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py.bak_20260504_130000_pre_photo_popover`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js.bak_20260504_130000_pre_photo_popover`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css.bak_20260504_130000_pre_photo_popover`
  - `/home/developer/review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html.bak_20260504_130000_pre_photo_popover`
- **Rollback:** `ssh developer@142.103.68.1 'cd /home/developer/review/Asset_dashboard_browser_EL && for f in sld_blueprint.py Asset_dashboard_EL.py review_asset_templates/static/sld/sld.js review_asset_templates/static/sld/sld.css review_asset_templates/dashboard.html; do cp "$f.bak_20260504_130000_pre_photo_popover" "$f"; done && kill -HUP $(ps -o pid,ppid,args -C gunicorn | awk "\$2==1 && /Asset_dashboard_EL/ {print \$1; exit}")'`
- **Open follow-ups:** dual-tooltip stacking on the diagram QR text (the existing `#sld-tooltip` on the parent `<g>` and the new photo popover on the `<text>` child can both be visible). Currently considered acceptable because they anchor differently (cursor-follow vs. element-anchored). Suppress the `<g>` tooltip while the photo popover is open if reviewers find it busy.

### [DATA-004] `SUST - System List` CSV→DB sync (insert-only)
- **Severity:** Feature (Planon reference data refresh)
- **Status:** Resolved (applied on VM 2026-05-01)
- **Found:** 2026-05-01 (Planon export carried codes not yet in `SUST - System List`)
- **Inputs:** `/home/developer/asset_capture_app_dev/data/SUST - System List.csv` (UTF-8 BOM, `;` delimiter, 21,327 distinct rows) → `/home/developer/asset_capture_app_dev/data/QR_codes.db` table `SUST - System List`.
- **Pre-state:** 26,618 DB rows, 719 overlap with CSV, 20,608 CSV-only codes, 25,899 DB-only codes (CSV and DB largely disjoint populations — investigated and confirmed expected before applying).
- **Schema mapping:** positional 1:1 except CSV col 6 `Asset Group.Item group` → DB col `Asset Group.Classification group` (renamed in export, semantically identical — sample values like `EL.21.301` confirm).
- **Action:** Insert-only (mode A). DB-only rows untouched.
- **Post-state:** 47,226 rows / 47,226 distinct Asset Codes. Re-diff: 0 CSV-only codes remain. Spot-check on `SYS0011447` / `SYS0011448` / `SYS0011463` confirms columns landed correctly.
- **Backups (both verified at the pre-state 26,618 rows):**
  - `/home/developer/asset_capture_app_dev/data/QR_codes.bak_20260501_132524_pre_sust_sync.db`
  - `/home/developer/asset_capture_app_dev/data/QR_codes.bak_20260501_135423_pre_sust_sync.db`
- **Scripts:** [scripts/sync_sust_system_list.py](scripts/sync_sust_system_list.py) (dry-run / apply), [scripts/_vm_backup_qr_codes_db.sh](scripts/_vm_backup_qr_codes_db.sh) (sqlite3 `.backup` helper). Both untracked locally.

### [DATA-001] Stuck-pool cleanup (15 → 1 row at `ai_status='0'`)
- **Severity:** Low (no API cost; pure log noise)
- **Status:** Resolved (cleanup applied 2026-04-30)
- **Categories cleared:**
  - **A — 11 EL rows already approved + JSON exists** (`0000184439`, `184441`, `184445`, `184478`, `184479`, `184480`, `184485`, `184489`, `184557`, `184558`, `184865`). Round-1 backfill reverted by `_existing_el_output_needs_rescore` safety rule (older `el_extraction_rule_version` + missing Power Rating fields). Round-2 fix: `refresh_el_stale_jsons.py` updated 11 JSONs (set `el_extraction_rule_version=15`, added empty `Power Rating` / `Power Rating (UoM)` keys), then `ai_status=1`. Per-JSON backups + DB backup at `2026-04-30 15:39:03`.
  - **B — `0000084088`** (real ME, only `-2` photo): purged 4 DB rows across `QR_codes` / `QR_code_assets` / `process_type` / `sdi_dataset` via `purge_qr_from_db.py`.
  - **C — `0000184919`** (ghost row, no associated data): single `QR_codes` row deleted.
  - **D — `None`** (literal-string QR from capture-app bug — see [CAPTURE-001]): purged 6 DB rows + deleted 4 filesystem artifacts (3 photos, `None_et.json`).
- **Remaining:** `0000184542` (Category B) — single `-2` photo, awaiting decision (re-capture vs purge).
- **Scripts created:** `scripts/diagnose_stuck_qrs.py`, `trace_qr_in_db.py`, `dump_qr_rows.py`, `purge_qr_from_db.py`, `backfill_ai_status_el_done.py`, `inspect_el_stale_jsons.py`, `refresh_el_stale_jsons.py`.

---

## Notes & Observations

Use this section for non-issue observations: behaviors that are surprising but intentional, areas worth deeper review, or follow-up questions for the team.

- **2026-04-30 — VM ↔ local sync.** Pulled VM as source-of-truth. Code drift across `API/`, `Dashboard/`, `review/`, `SDI_process/`, `dictionary/`, `auth_service/`, `scripts/`, `asset_capture_app_dev/`: 182 files identical, 0 modified post-pull, 0 only-on-VM. Two minor chart HTMLs refreshed (`devices_in_process_monitor.html`, `priority_chart.html`). Pulled `Output_jason_api/` (1326 JSONs) and the consolidated `QR_codes.db` (WAL-checkpointed first → 965 pages flushed). Sync utilities in `scripts/manifest_tree.py` + `scripts/diff_manifests.py`. Local backups at `Output_jason_api.bak_20260430_154908/` and `asset_capture_app_dev/data/QR_codes.bak_20260430_154908_pre_vm_sync.db`.
- **2026-04-30 — `SpaceUID` row insert.** Added building 217 floor 7 "Elevator Room" with `Space number=7200A`. Column name on disk is lowercase `Space number` (not `Space Number`); `Floor Code` is INTEGER.
- **`Capture_photos_upload/` (~2.8 GB on VM)** intentionally not synced — option 1 minimal sync. Switch to incremental (option 3) if/when that becomes useful.
- **Reset to "fully reprocess" path is missing.** When an asset was previously approved and the user wants the AI to re-extract it (e.g. after dictionary updates), there's no obvious admin button. Could fold into the [AI-001] retry-budget UI work.
- **[EL-001] post-fix structural risk:** the dictionary still owns the description prefix on AI ingest (now via [EL-002]) — but the review app's "preserve user value" path treats *any* non-empty Description as a user edit unless it matches the exact legacy "Panel - <tag>" placeholder. If/when the AI default ever changes again, the placeholder detector ([EL-002] resolution part 2) will need a corresponding update.
- **2026-05-01 — local ↔ VM parity confirmed.** Re-ran the hash manifest after the SLD redesign deploys ([SLD-001] through [SLD-004]). All 184 production code files identical across `API/`, `Dashboard/`, `review/`, `SDI_process/`, `dictionary/`, `auth_service/`, `scripts/`, `asset_capture_app_dev/`. Only local-only files: `scripts/manifest_tree.py` and `scripts/diff_manifests.py` (sync utilities, never deployed). Today's edits all followed the local-edit → `scp` → restart pattern, so no VM→local pull was needed.
- **2026-05-01 — local-only changes pending commit on `Integrated_Test` branch.** Working tree carries [ME-002], [EL-006], [DASH-003], [DICT-001], and [DATA-003] (write paths + backfill script). [SDI-001], [SDI-002], [SDI-003] are deployed to the VM but the corresponding edits in `SDI_process/app.py` and `SDI_process/template/dashboard.html` are still local-only on this branch. Affected files: `API/API_interface_ME_ver00.py`, `Dashboard/templates/dashboard.html`, `SDI_process/app.py`, `SDI_process/template/dashboard.html`, `dictionary/mechanical_dictionary.py`, `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py`, `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`, `review/Asset_dashboard_browser_EL/review_asset_templates/{dashboard,landing,review}.html`, plus untracked `scripts/backfill_main_asset.py`, `scripts/sync_sust_system_list.py`, `scripts/_vm_backup_qr_codes_db.sh`, `scripts/_vm_drop_sust_asset_rows.sh`, `scripts/_vm_test_main_asset_lookup.py`, `scripts/_vm_test_export_aq.py`. `asset_capture_app_dev/data/QR_codes.db` also dirty in the working tree.
- **2026-05-18 — [SLD-005] corpus audit due.** Run `bash scripts/audit_sld_corpus.sh` to inspect the SLD feedback corpus, count human_correction events, and apply the [SLD-005] deferral criteria (≥50 corrections → greenlight v3 / <50 → stay on prompts / 0 → investigate wiring). Script is self-contained and read-only against the VM.
- **2026-05-04 — [SLD-005] deployed to VM but still local-only on this branch.** New: `review/Asset_dashboard_browser_EL/sld/_feedback.py`, untracked `scripts/_test_sld_feedback.py`. Modified: `review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py`, `review/Asset_dashboard_browser_EL/sld_blueprint.py`. VM master gunicorn `497183` reloaded via SIGHUP; `electrical_building_schema` table now carries `sld_extract_run_id` and `sld_ai_extract_payload` columns (idempotent ALTER on app startup). Feedback dir `/home/developer/sld_extract_feedback/` created. First `<run_id>.jsonl` will appear on the next SLD upload.
- **2026-05-04 — [SLD-005] audit script installed on VM.** Canonical Python at `/home/developer/scripts/audit_sld_corpus.py` (also in repo at [scripts/audit_sld_corpus.py](scripts/audit_sld_corpus.py)). Local thin wrapper at [scripts/audit_sld_corpus.sh](scripts/audit_sld_corpus.sh) is now a one-line `ssh ... 'python3 /home/developer/scripts/audit_sld_corpus.py'`. Reminder: re-run on **2026-05-18** to apply the [SLD-005] deferral criteria (≥50 corrections → greenlight v3 / <50 → stay on prompts / 0 → investigate wiring).
- **2026-05-04 — [SLD-006] / [SLD-007] / [SLD-008] deployed to VM but still local-only on this branch.** Modified: `review/Asset_dashboard_browser_EL/sld_blueprint.py` (SLD-007 + SLD-008 endpoint), `review/Asset_dashboard_browser_EL/review_asset_templates/sld/sld_panel.html` (SLD-006), `review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.css` (SLD-006), `review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js` (SLD-008), `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html` (FileSaver.js script tag from the SLD-008 diagnostic loop — harmless to keep). New runtime dep: `openpyxl 3.1.5` in the EL venv (no `requirements.txt` update yet). DB write: building 750's 25 SLD rows flipped to `new_draw='TRUE'` (recovery, see [SLD-007]).
- **2026-05-04 — [EL-007] deployed to VM and verified.** Modified: `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` only. VM copy hash matches local after deploy; Gunicorn workers reloaded via `HUP` because passwordless `sudo systemctl restart assetcap-el` was unavailable. User confirmed tab badges now scope to the selected building and fall back to all-building totals when no building is selected.
- **2026-05-01 — VM /tmp/ artifacts left from today's work** (cleanable when convenient): `_vm_backup_qr_codes_db.sh`, `_vm_drop_sust_asset_rows.sh`, `sync_sust_system_list.py`, `_vm_test_main_asset_lookup.py`, `_vm_test_export_aq.py`, `test_export_aq.xlsx`. VM-side production backups (keep): `QR_codes.bak_20260501_132524_pre_sust_sync.db`, `QR_codes.bak_20260501_135423_pre_sust_sync.db`, `QR_codes.bak_20260501_140906_pre_sust_asset_delete.db`, `SDI_process/app.py.bak_20260501_142758_pre_main_asset`, `SDI_process/template/dashboard.html.bak_20260501_144232_pre_alert_modal`.
## 2026-07-10 — Optional Installation Date in ME/BF/EL Review and SDI

- **Behavior:** all three review forms read `QR_codes.installation_date`, display/edit `DD/MM/YYYY`, reject invalid/future values, permit clearing, store `YYYY-MM-DD`, and write human audit rows. The field stays out of extraction JSON and completeness/confidence.
- **SDI:** package lookup joins the QR-level value, active/archive tables carry `Installation Date`, invalid/absent values normalize blank, and the Planon workbook writes ISO text to template column 35.
- **Validation:** 8 focused unit/contract tests passed; all Python files compiled; all three Jinja templates parsed. Production ME/BF/EL set-read-clear checks returned `02/01/2020` for stored `2020-01-02`, then restored original blanks. SDI lookup/normalization returned `['2020-01-02', '', '']`. Four services remained active and returned HTTP 302 after HUP reload.
- **Backup:** `/home/developer/deploy_backups/installation_date_20260710_091010` includes a 1.5 MB custom-format PostgreSQL dump and pre-deploy files.
- **UI refinement (same day, deployed):** label is plain "Installation Date" (no "(optional)" suffix or DD/MM/YYYY helper note); typing applies an auto-slash `DD/MM/YYYY` mask; a calendar button opens the native picker (hidden unnamed `type=date` input, `showPicker()`, `max`=today) that fills the text field; EL field moved from below the Location card into the Identity card. Blocks are identical across the three review.html copies and honor the `data-lock-editable` approve-lock. Validation: 8 unit tests re-passed, three templates Jinja-parsed, 10/10 headless-Chromium checks on the shipped markup/JS (mask, paste re-slash, picker round-trip, lock, form submission keys). Deploy: templates scp'd with `.bak_20260710_095325` in-place backups, sha256 verified, ME/BF/EL gunicorn masters HUP-reloaded (fresh workers), ports 8001/8004/8005 returned HTTP 302.
- **Follow-up fix (EL only, deployed):** on approved/locked assets the empty Installation Date field painted "DD/MM/YYYY" over its label — EL's disabled-input rule (`-webkit-text-fill-color: #5a7694`) repaints the placeholder Bootstrap's floating labels keep transparent (Chromium: text-fill-color beats `color: transparent`). Reproduced and fix verified headlessly; new rule `.form-floating>.form-control:disabled::placeholder { color/-webkit-text-fill-color: transparent }` also cures the same latent overlap on other empty locked floating fields (e.g. Power Rating/UoM). ME/BF lack the tint rule and were unaffected. Deployed with `.bak_20260710_100338` backup, sha256 verified, EL HUP-reloaded, HTTP 302.
