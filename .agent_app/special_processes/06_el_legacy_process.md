# EL Legacy Process — Consolidated Reference

Current documentation refresh: 2026-07-30.

Single reference for the electrical **Legacy** flow: what it is, every file that implements it, the rules currently in force, and the exact runbook for improving, fixing, or changing the process. Chronological detail lives in `attributes_changes.md`; binding field conventions live in `rules/review_apps.rules.md` → "EL Legacy Flow Rules" and `rules/asset_extraction_api.rules.md` → "EL Legacy Extraction Gate". If this doc and those rules docs ever disagree, the rules docs win — then fix this one.

## 1. What the Legacy process is

Most campus buildings carry old lamacoid panel plates (`DIST. CTRE. #1`, `PANEL U.P.S.1 ... FED FROM ...`) that do not follow the standard X-tag plate structure. The `"Buildings"."Process"` column decides, per building, which electrical flow runs:

| `Buildings.Process` | Behavior |
|---|---|
| `Standard` | Untouched standard EL flow (`dictionary/electrical.dictionary.py`). Byte-identical to pre-gate behavior. |
| `Legacy` | Legacy flow described in this document (`dictionary/electrical.dictionary_old.py`). |
| blank / NULL / DB unreachable | **Fail closed: skip and warn. Never default to Standard.** Review/SLD writes hard-409. |

Production state (deployed 2026-07-28): 323 buildings `Legacy`, 4 `Standard` (`217`, `314-1`, `459`, `750` — the buildings holding the pre-existing standard-shaped EL records). CHECK constraint `ck_buildings_process` enforces the value set. Reference building for legacy testing: `641` (six QRs `0000186127`–`0000186132`).

## 2. Component map

| Component | File | Legacy-specific parts |
|---|---|---|
| **Rules module (single source of truth)** | `review/Asset_dashboard_browser_EL/legacy_flow.py` | `get_building_process()`, `parse_legacy_ident()`, `parse_legacy_identity_text()`, `normalize_legacy_supply_from()`, `legacy_structured_from_raw()`, `compose_x_tag()`, `legacy_nameplate_specs()`, `is_legacy_transformer()`. Shared by the extraction API (importlib), the EL review app, and the SLD extractor. **Never fork its logic into a consumer.** |
| **Extraction API** | `API/API_interface_EL_ver00.py` | `_load_building_process_map()` gate; `_process_legacy_asset()` (verbatim-transcription `EL_LEGACY_PROMPT`, raw-preserving `ELLegacyStructuredExtraction` — no normalizing validators); `EL_LEGACY_RULE_VERSION` (currently **4**); `_existing_el_legacy_output_needs_rescore()`; legacy routing in `_load_ai_processed_qrs()`. |
| **Legacy dictionary (working copy)** | `dictionary/electrical.dictionary_old.py` | `panel_legacy` block, incl. `codes.ident_normalization` (`strip_chars`, `equivalences`) — schema-driven plate-typo/punctuation normalization. **All dictionary changes go here; `electrical.dictionary.py` is never touched without explicit request.** |
| **EL review app** | `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` | `_compute_el_upstream_fields()` Legacy branch (prefers stored Equipment ID / Equipment Type / Power Type over tag re-derivation); `_ensure_el_fed_from_equipment_id_column()` skips Legacy buildings (the standard normalizer mangles legacy identifiers). |
| **SLD blueprint** | `review/Asset_dashboard_browser_EL/sld_blueprint.py` | `swift_save_asset` / `reconcile_asset` resolve building process (409 on blank), gate `_apply_el_supply_from_formatting`, and pass `process=` to all four `_sync_db_from_structured` calls. |
| **Tests** | `test/test_el_legacy_nameplate.py` (19 tests: nameplate specs, amperage guard, source precedence, invariant 6) | Run with `python -m unittest discover -s test` from the repo root. **Corrected 2026-08-04:** this row previously cited `test/test_legacy_flow.py` (~199 tests) plus `test_el_extraction_legacy_gate.py`, `test_el_upstream_fields_equipment_id.py`, `test_el_fed_from_backfill_legacy_guard.py` and `test_el_sync_legacy_gate.py` — none of those files exist in the repository. Treat legacy rule coverage as the file named here until the missing suites are located or rewritten. |

Legacy JSON envelope (in `Output_jason_api/`): `"process": "Legacy"`, `"el_legacy_rule_version": <n>`, `structured_data.label_text` (verbatim **lamacoid** transcription from EL-1 — lets rules re-run without a new vision call and activates the ratings rules), `structured_data.nameplate_text` (verbatim **manufacturer nameplate** transcription from EL-0, added 2026-08-04; blank unless EL-0 is a manufacturer data plate).

## 3. Rules currently in force (rule version 4)

- **Identity / UBC Asset Tag:** the tag stores the plain equipment identity — `DCC-1`, `MDC-2`, `PNL-UPS1`, `MCC2`. Numbered MDC/DCC units always use the **hyphen form** (`DCC-1`, never `DCC #1`; the old form still parses forward). The X-composed structure (`DCC-2XXD1`, via `compose_x_tag()`) is **internal-only** — used to decode dictionary values such as voltage, never stored.
- **Supply From ≡ Fed From Equipment ID:** same feeder identifier, same form (`DCC-8`, `MDC`, `UPS`). Supply From may additionally carry a printed routing qualifier (`MDC via TX T1`, `DCC-1 via UPS`, `via ATS`).
- **UPS handling:** `FED FROM U.P.S. ...` → feeder `UPS`; `VIA UPS` → routing qualifier. The UPS primary-feeder check fires only after every more-specific identity fails — it must never hijack a specific tag (`FED FROM DCC-1 VIA UPS` keeps `DCC-1`).
- **Ident normalization (dictionary-driven):** `panel_legacy.codes.ident_normalization` strips `strip_chars` (periods: `U.P.S.1` → `UPS1`) and maps `equivalences` (plate typo `USP` → `UPS`). Future plate typos are **data additions to `electrical.dictionary_old.py`, not code changes**. An absent key is a no-op.
- **Description:** `<type word> - <Equipment ID>`; DCC/MDC are described as "Distribution" → `Distribution - DCC-1`. No fabricated descriptions for unparseable plates (blank instead).
- **Ratings:** values stored bare (`208/120`, `100`); units live in the `(UoM)` columns (`VLT`/`AMP` on SDI tables, `V`/`A` display in `electrical_building_schema`) — same convention as Standard.
- **Source precedence (user rule, 2026-08-04):** EL-1 (**lamacoid**) is the **primary** source; EL-0 (**Asset Plate, optional**) is **secondary**. For **transformers** it supplies the Power Rating pair and the HV/LV voltage; for **non-transformers** (2026-08-05, QR `0000186139`) the asset's *own* equipment label (e.g. a Siemens EQ loadcentre's `Amps 225` / `Volts AC/CA 120/240`) blank-fills Volts/Ampere via `legacy_ratings_from_label` — whose guards (winding-current context, RMS/SYM/INTERRUPTING, tap tables, same-line units, drawing-number line shapes like `3259A 18`) keep a mis-photographed transformer plate or part number from leaking values. **Power Rating remains transformer-only, unconditionally.** The two transcriptions live in separate fields (`label_text` vs `nameplate_text`) and are parsed by separate functions (`legacy_ratings_from_label` vs `legacy_nameplate_specs`), so a nameplate reading can never outrank or contaminate a lamacoid one. Both `legacy_structured_from_raw` and `apply_legacy_rules` fill from the nameplate **only where the lamacoid produced nothing**.
- **Manufacturer nameplates (2026-08-04).** `legacy_nameplate_specs()` reads an EL-0 data plate and emits:
  - **Power Rating** — the **base self-cooled** rating only. Preference order: the plate's declared impedance base (`SUR/ON 1500 KVA`), else the smallest explicit kVA not on a forced-air line (`AFN`/`FA`/`ONAF`), since forced-air and higher temperature-rise ratings are always larger than the base. Thousands separators are stripped first, because `normalize_power_rating_pair('1,500','KVA')` silently truncates to `500`. **Fractional plate sizes are truncated toward zero** (`112.5` → `112`, `37.5` → `37`) because that normalizer rejects decimals outright, and `sdi_dataset_EL` already stores `112` for a 112.5 kVA unit while `electrical_building_schema` keeps the exact `112.5`. UoM `KVA`. ⚠️ The kVA regexes carry a `(?<![\d.])` lookbehind for a reason: a bare `\b(\d{1,6})` word-boundaries onto the fraction and reads `112.5 KVA` as **5** — a *valid* pair that every downstream normalizer passes through unchanged, so it would reach Planon as a 5 kVA transformer. 37.5 / 75.0 / 112.5 / 167.5 are standard dry-type sizes, so this is the common case.
  - **Voltage** — composed primary-secondary pair, e.g. `12470-600Y/347`, matching the pair shape already stored for transformers in `sdi_dataset_EL` (`600-208Y/120`). A **decimal** secondary is the plate printing line/√3 exactly, and is mapped to its UBC-canonical integer nominal (`600Y/346.4` → `600Y/347`) because every stored row uses the nominal; the literal value stays recoverable in `nameplate_text`. A **printed integer** secondary is authoritative and is never substituted — rewriting split-phase `240/120` to `240/139` (240/√3) would invent a voltage printed on no plate, and `normalize_volts` rejects the whole pair, losing a usable reading.
  - **No amperage, ever.** A transformer plate prints per-tap, per-winding currents (five HV taps plus one LV value) that the single-column `Amperage Rating` cannot represent, and 24 of 26 transformer rows in `sdi_dataset_EL` are blank. `legacy_ratings_from_label` additionally **rejects** amp candidates in nameplate context (a `COURANT`/`CURRENT` header, a `%` tap row, or an `H.T.`/`B.T.`/`H.V.`/`L.V.` winding label) so a reformatted transcription such as `1443A` cannot leak in.
- **Plausibility tripwire (2026-08-05).** A parsed voltage must name a known UBC system (`is_plausible_voltage`: singles, wye pairs, transformer primary-secondary pairs incl. MV primaries) and a parsed amperage must be a standard NEMA/CSA rating size (`is_plausible_amperage`). Anything else is moved to a `*_rejected` key inside the scanners (`_apply_rating_rails`), composition stores **blank**, and extraction adds `unrecognized_voltage` / `implausible_amperage` to the manual-review reason codes via `structured_data.rating_plausibility_flags`. Rationale: every fabrication found in this workstream (225 V from an amps line, 3259 A from a drawing number, 139 V from 240/√3) was electrically implausible — the rails convert that whole class from silent Planon corruption into reviewer flags. Whitelists are generous by design; extending them is a code change to `_PLAUSIBLE_VOLTS_SINGLE` / `_STANDARD_AMP_RATINGS` with a fixture test.
- **Power Rating is transformer-only.** Gated by `is_legacy_transformer()` (tag or Equipment ID matching `TX*`, the drifted `T-X-` spelling, the `T1`/`T-1` unit naming (digit-suffix anchored — added 2026-08-05 for QR `0000186131`, per the dictionary's own `T-|EL` → Transformer classification), or a `Transformer` equipment type). A gate-proven transformer whose tag the ident grammar cannot parse still gets `Equipment ID` (the tag) and `Equipment Type` `Transformer`, in both `legacy_structured_from_raw` and the `apply_legacy_rules` blank-fill. A panel whose lamacoid quotes its upstream feeder — DCC-1's real plate prints `THROUGH TRANS. "T1" 112.5 K.V.A.` — must never inherit that rating; keeping nameplate text in its own field is what guarantees this.
- **⚠ Medium-voltage primaries are unvalidated against Planon.** No row ≥ 1000 V exists in `sdi_dataset_EL` or `electrical_building_schema`, so `12470` is a value class this system has never stored. The legacy path *can* hold it because it attaches none of the standard-flow validators (`normalize_volts`' whitelist tops out at 600 and would strip it) — **anyone re-running a legacy asset through the standard path will silently lose the primary.** Confirm Planon acceptance before relying on it; to store the secondary only, change the composition in `legacy_nameplate_specs`.
- **Golden examples:** QR `0000186130` → `DCC-1`, 208/120, 400, `Distribution - DCC-1`; QR `0000186128` → `PNL-UPS1`, Supply From = Fed From = `UPS`, 240/120, `Panel - PNL-UPS1`; QR `0000186132` (building 641, ABB dry-type) → `TX-MAIN`, Power Rating `1500` `KVA`, Volts `12470-600Y/347`, Ampere blank, completeness 100% (was 25%).

## 4. Runbook: how to improve, fix, or change the process

1. **Change the rules** in `legacy_flow.py` and/or the working-copy dictionary `electrical.dictionary_old.py` — never `electrical.dictionary.py`, never anything in the Standard flow (it must stay byte-identical).
2. **Test:** add/extend cases in `test/test_el_legacy_nameplate.py` and run the full suite with `python -m unittest discover -s test` from the repo root.
3. **Bump `EL_LEGACY_RULE_VERSION`** in `API/API_interface_EL_ver00.py` with a dated comment. This is the regeneration trigger: stale legacy JSONs are detected, `ai_status` resets, and re-extraction + resync happen on the next run. **Never bump `Config.EXTRACTION_RULE_VERSION` for legacy changes** — that forces fleet-wide Standard re-extraction (billable, wrong).
   - **Blast radius before you bump.** The check is a bare version comparison after the human-override short-circuits, so a 4→5 bump flags **every** legacy JSON below the new version stale at once. The `ai_status` reset is fleet-wide even on a `--qr` run, and regenerated JSONs reach `sdi_dataset_EL` on the next authenticated EL page load via the `before_request` sync — with no reviewer action. Take a `pg_dump` and copy every `process == "Legacy"` JSON first, then diff regenerated output field-by-field **before** anyone opens the EL dashboard.
   - **Nameplate change (2026-08-04) shipped deliberately WITHOUT a bump**, at the user's direction: the code and tests are in place but no re-extraction is triggered until the version is raised. Nothing changes for existing assets until then.
4. **If you change `EL_LEGACY_PROMPT`, change both models in the same commit.** `ELLegacyStructuredExtraction` and `ELLegacyConfidenceScores` are `extra="forbid"`; a prompt that asks for a key the models do not declare makes *every* legacy QR fail to parse and return `ERROR (Legacy extraction failed)`.
5. **Deploy to VM:** copy the changed files (`legacy_flow.py`, API file, dictionary), then reload gunicorn by HUP-ing the master process (the one with ppid 1 — never `pgrep | head`).
6. **Regenerate:** the `ai_check.sh` cron only fires on `ai_status=0` counts, so run the extraction manually with env sourced: `set -a; . /home/developer/db_backend.env; set +a` first (an env-less shell falls back to SQLite and the gate fails closed — everything skips). For a single weak read: `EL_OVERWRITE_EXISTING_JSON=true ... --qr <code>`.
7. **Sync:** the review-app batch JSON→DB sync only runs on authenticated requests; run `sync_json_directory_to_db_el()` headless from the app venv with env set. Verify results in `sdi_dataset_EL`.
8. **Document:** dated entry in `attributes_changes.md` (newest at top), update the rules sections in `review_apps.rules.md` / `asset_extraction_api.rules.md` and this file, then sync all four doc locations: canonical → `.agent_app/` mirrors → VM (`/home/developer/Markdowns_documentation/` + `/home/developer/.agent_app/`) → SecondBrain vault.

## 5. Invariants (do not break)

- **Human edits are authoritative:** JSONs with `modified=true`, `supply_from_manual_override=1`, or `volts_manual_override=1` are excluded from stale-flagging and overwrite (verified live: 0000186131). Approved / SDI-locked rows are never reset from `ai_status=1` to `0`.
- **Legacy JSONs route through the legacy rescore check** (`_existing_el_legacy_output_needs_rescore()`), never the standard one — the standard check misjudges legacy payloads and creates a permanent billable re-extraction loop.
- **Blank `Buildings.Process` fails closed** everywhere: extraction skips + warns; SLD swift-save/reconcile return 409.
- **Write paths must be process-aware:** any new code path that writes EL identifiers must resolve the building's process first (the 2026-07-29 hotfix exists because two process-blind paths mangled legacy identifiers).

## 6. Change history (detail in `attributes_changes.md`)

| Date | Rule ver | Key commits | Change |
|---|---|---|---|
| 2026-07-28 | — | `cf07bff`, `be2eb46` | `Buildings.Process` column + review/SLD-side gate; production classification 323 Legacy / 4 Standard. |
| 2026-07-29 | 1 | `779069d`…`5acf198` | Extraction-side gate + legacy path (verbatim prompt, raw model, envelope); building 641 extracted. |
| 2026-07-29 | 1 (hotfix) | `0f2143b` | Process-gated the fed-from backfill sweep and SLD swift-save/reconcile writes. |
| 2026-07-29 | 2 | `a3b0a5f` | Hyphen-form identifiers (`DCC-1`); Supply From ≡ Fed From; Description `Distribution - DCC-1`. |
| 2026-07-30 | 3 | `3d02ad7` | UBC Asset Tag = equipment identity; X-tag structure internal-only. |
| 2026-07-30 | 4 | `725b47a` | Dictionary-driven ident normalization (`U.P.S.`/`USP` → `UPS`); UPS as feeder and via qualifier. |

## 7. Rollback

- Extraction gate + later rule versions: `/home/developer/scripts/rollback_el_legacy_extraction.sh /home/developer/deploy_backups/el_legacy_extraction_20260729_191540` — restores the API file, `Asset_dashboard_EL.py`, `sld_blueprint.py`, and docs, but **keeps `legacy_flow.py` at the new revision** (the old revision cannot parse current stored forms like `MDC via TX T1` / `DCC-1`) and stamps `modified:true` on Legacy JSONs so old code cannot re-extract them.
- Review/SLD-side gate (2026-07-28 layer): `/home/developer/scripts/rollback_el_legacy_flow.sh /home/developer/deploy_backups/el_legacy_flow_20260728_141619 [--code-only|--full]`.
- VM DDL (e.g. touching the `Process` column) requires the owner role: `psql -h /tmp -p 5433 -d qr_code_db` as `developer` — the app role cannot ALTER TABLE.

## 8. Known follow-ups (surfaced, not yet requested)

- Transformer identity parsing (`T1` / `TX-MAIN` plates) — currently flagged for manual review; reviewer hand-edits stand.
- Asset Group mapping for DCC/MDC.
- Quota/auth telemetry on the legacy retry loop.
- Spaced `U P S` plate variant is unmatched by the ident normalizer.
- Targeted retry when a legacy vision pass returns a sparse result (e.g. one plate of two unread).
