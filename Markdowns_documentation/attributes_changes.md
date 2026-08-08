# Attribute Changes

Current documentation refresh: 2026-08-07.

## 2026-08-07 (follow-up): EL General Capacity field + General listing column drop

### Summary

Two follow-ups to the EL General nameplate form shipped earlier the same day. (1) The General review form's Identity card gains an optional `Capacity` + `Capacity (UoM)` pair (bare value, unit as printed — no fixed code list unlike `AMP`/`VLT`), captured by AI from the `-0` Asset Plate for all EL assets, stored for General rows only, and exported through SDI packaging to the Planon template columns `Capacity` (CH) / `Capacity UoM` (CI). Capacity is deliberately **excluded** from review completeness scoring, the hover checklist, and the traffic light. (2) The General "Review Electrical Assets" listing (`/review-all`) now hides the Supply From, Amperage Rating, Volts, and Location columns (DataTables `visible:false, searchable:true`, same mechanism the Distribution listing already used for Amperage/Volts/Location); the Distribution listing and the dashboard XLSX export are unchanged.

### Scope

- **DB:** owner-run migration `scripts/migrations/2026-08-07_el_capacity_columns.sql` adds `Capacity`, `Capacity (UoM)` (`TEXT`) to `sdi_dataset_EL`, `sdi_print_out`, and `sdi_print_out_arch` (packaging INSERTs every `PRINT_OUT_COLS` column). `_ensure_package_amperage_columns` raises with the migration name if a package table misses them on PostgreSQL.
- **Review app:** `EL_CAPACITY_FIELDS` constant (NOT part of `EL_NAMEPLATE_FIELDS` — scoring unchanged); DB sync writes the pair for General rows, `''` for Distribution; `keep_blank` additions; General Identity card input pair (modeled on the Distribution Power Rating pair); review sheet Nameplate section shows Capacity as value+UoM joined.
- **AI extraction:** `Capacity`/`Capacity (UoM)` added to `STRUCTURED_FIELDS`, both schema families, and the API-side `Config.EL_NAMEPLATE_FIELDS` (inherits the retry/fallback-gate exclusions, legacy raw-copy, and non-blank-only confidence projection). `_normalize_el_capacity_pair` splits a combined "75 kVA" reading into value+unit; no unit whitelist. **No rule-version bump** — existing JSONs are not re-extracted.
- **SDI\Planon:** pair added to `PACKAGE_ONLY_COLS`; `ATTRIBUTE_SETS.py` lists `Capacity`/`Capacity UoM` under `Electrical` so the attribute-set filter keeps them for EL rows (the file mirrors Planon's configuration). The punctuation-insensitive template header match lands `Capacity (UoM)` in `Capacity UoM` with no rename entry. No UoM default-fill at export.
- **Listing:** `generalHiddenColumns = [Supply From (7), Amperage Rating (8), Volts (9), Location (10)]`; view-selected via `hiddenColumns` alongside the existing `distributionHiddenColumns` (8/9/10).

## 2026-08-07: EL General nameplate review form (ME-style) + Planon nameplate flow

### Summary

General "Electrical Assets" (`elec_dist_setup='N'` groups) now review through an ME-style nameplate form instead of the electrical tech-card form. `review.html` renders one of two server-selected variants via the new `_el_form_variant(asset_group)` (data-driven off `get_distribution_asset_groups()`; the client-supplied `base_route` stays pagination-only; blank/unknown group → Distribution). The General variant captures Manufacturer, Model, Serial Number, Year (+ Installation Date) with a Classification card (UBC Asset Tag / Asset Group / readonly Attribute / Main Asset), keeps Location and auto-Description, and drops TSBC and all electrical tech cards; Equipment ID / Equipment Type / Power Type are still derived server-side and stored. The Distribution variant is byte-identical to the previous form.

### Scope

- **DB:** owner-run migration `scripts/migrations/2026-08-07_sdi_dataset_el_nameplate_columns.sql` adds `Manufacturer`, `Model`, `Serial Number`, `Year` (`TEXT`, JSON-key names) to `sdi_dataset_EL`. The sync writes them only for General rows; Distribution rows stay blank by contract.
- **Review app:** variant-aware review scoring (`EL_REVIEW_GENERAL_SCORING_FIELDS` = UBC Asset Tag + the four nameplate fields; stored group beats tag-derived group), General entries in the dashboard hover checklist, variant-aware print/export sheet (Nameplate column replaces Technical Details for General). The hidden `form_variant` input is a diagnostic echo in `ignored_form_fields` — save derivations never branch on it. Same change fixes a pre-existing leak: `nav_prev`/`nav_next` no longer merge into `structured_data`.
- **AI extraction:** `API_interface_EL_ver00.py` reads the four fields from the `-0` Asset Plate for ALL EL assets (standard + legacy flows) — General cannot be classified pre-extraction. Normalizers: `normalize_year` / `normalize_serial` (+ `looks_like_date_misread_serial` rejection) / `normalize_model`; `normalize_manufacturer` falls back to `_clean_raw_manufacturer` because the shared validator is a BF whitelist that would blank electrical brands. **No `EXTRACTION_RULE_VERSION` / `EL_LEGACY_RULE_VERSION` bump**, and confidences project only when non-blank — both deliberate, to avoid mass-flagging the existing corpus stale (a corpus-wide billable re-extraction). Backfill of existing General assets is targeted per-QR `FORCE_REPROCESS`. Note: `Avg_ai_conf` on newly extracted EL JSONs now includes nameplate confidences whenever a plate is read.
- **SDI/Planon:** Manufacturer/Model/Year already flow through packaging (`MASTER_COLS`); `build_sdi_dataset()` gained the EL-only `Serial Number` → `Serial` rename so the serial survives the `PRINT_OUT_COLS` projection. EL General rows now export Make (J), Model (K), Serial Number (M), Date Of Manufacture Or Construction (AN, via `format_year_to_date`). No package-table schema change; archive/retrieve unchanged.

## 2026-08-04: EL General/Distribution split driven by `Asset_Group.elec_dist_setup` — deployed same day

### Summary

The "Electrical Assets" vs "Electrical Assets - Distribution" dashboard split is now data-driven: an asset lands in the Distribution view when its `Asset Group` matches an `Asset_Group` row with `elec_dist_setup = 'Y'` (`CHAR(1) NOT NULL DEFAULT 'N'` + `ck_asset_group_elec_dist_setup` check constraint; owner-run migrations `scripts/migrations/2026-08-04_asset_group_elec_dist_setup*.sql`, applied to Local and VM — 8 seeded `Y` rows including both Panels records and the newly added Main Transformers, with matching `audit_trail` rows).

App side: `get_distribution_asset_groups()` in `Asset_dashboard_EL.py` (60s TTL cache) feeds the two views, the review-page amp-warning gating, and the XLSX export (`build_workbook` / `has_el_amperage_warning` gain an optional `distribution_groups` parameter — three-copy rule kept, ME/BF behavior unchanged); the SLD Switch Over query reads the flag via `_distribution_asset_groups()` in `sld_blueprint.py`. The static `EL_DISTRIBUTION_ASSET_GROUPS` frozenset (and its `sld_blueprint.py` tuple mirror) remains only as the DB-unavailable fallback (e.g. the frozen local SQLite copy). Precursor, same day: "Main Transformers" was added to the static set and seeded `Y`.

Deployed to the VM same day: file copy with `.bak_20260804_103822` backups, then gunicorn SIGHUP graceful reload of `assetcap-el` / `assetcap-reviewme` / `assetcap-bf` (units have `Restart=no`, so masters were HUPed, not killed). Moving a group between views is now an audited `UPDATE "Asset_Group" SET elec_dist_setup = ...`, not a code change; the dashboards pick it up within ~60 seconds.

## 2026-07-30: Legacy ident normalization (U.P.S./USP → UPS) + UPS feeder — deployed same day

### Summary

Root cause of QR 0000186128 recording `PNL-USP1`: the asset's beige lamacoid prints the transposition typo `USP1` while its blue plate prints `U.P.S.1`, and the `panel_legacy` ident pattern had no period handling — even a correct `U.P.S.1` read would have fallen through verbatim. Fixes (commit `725b47a`, `EL_LEGACY_RULE_VERSION` 3 → 4):

- **Schema-driven ident normalization** — new `panel_legacy.codes.ident_normalization` in `dictionary/electrical.dictionary_old.py` (working copy): `strip_chars: "."` (`'U.P.S.1'` → `'UPS1'`) and `equivalences: {"USP": "UPS"}` (known plate typo). Applied by `parse_legacy_ident` before the pattern match; absent key = no-op (older dictionaries keep pre-existing behavior).
- **UPS as a fed-from feeder** — `FED FROM U.P.S. MAIN ELECTRICAL RM. 0060` → Supply From/Fed From Equipment ID `UPS` (source room `0060`). UPS also works as a `via` qualifier like ATS/TX (`DCC-1 via UPS`); the primary-feeder check runs only after every more-specific identity fails, so it can never hijack a hyphenated feeder tag (review finding, fixed pre-merge + span-based VIA-clause exclusion from re-review).

Verified live: 0000186128 = `PNL-UPS1` / Branch Panel `UPS1` / Supply From `UPS` = Fed From `UPS` / Volts `240/120`. QR 0000186131 (`modified: true` — human-edited in the review app) was correctly excluded from regeneration by the invariant-6 protections.

## 2026-07-30: Legacy UBC Asset Tag = equipment identity (user rule) — deployed same day

### Summary

User rule (commit `3d02ad7`, `EL_LEGACY_RULE_VERSION` 2 → 3): the Legacy `UBC Asset Tag` stores the equipment identity — `"DCC-1"`, `"PNL-U"`, `"MCC2"` — the same value as `Equipment ID`, instead of the X-composed standard structure (`"DCC-2XXD1"`). The X-tag remains the internal dictionary-decode/display structure (`compose_x_tag` retained; its segment codes still map voltage/system/type through the standard `label_schema`) but is never stored as the tag. The version bump regenerated the six building-641 Legacy JSONs through the designed rescore mechanism (verified: stale-detect → reset → re-extract → resync, fully automatic). Verified live: 0000186130 = `UBC Asset Tag "DCC-1"` / `Equipment ID "DCC-1"` / `"Distribution - DCC-1"` / Supply From `"MDC via TX T1"` / Fed From `"MDC"` / `208/120` / `400`.

## 2026-07-29: Legacy identifier + Description normalization (user rules) — deployed same day

### Summary

User-directed normalization of the legacy composition rules (commit `a3b0a5f`, `EL_LEGACY_RULE_VERSION` 1 → 2 — supersedes the identifier shapes shown in the two entries below):

- **Hyphen-form unit identifiers.** `Equipment ID` for numbered MDC/DCC units is `"DCC-1"` (was `"DCC #1"`). The old `#` form still parses and normalizes forward on read (stored data self-heals through `normalize_legacy_supply_from` / `parse_legacy_identity_text`).
- **Supply From ≡ Fed From Equipment ID.** Both fields mean the upstream feeder and carry the same hyphenated identifier (`DCC-1`, `DCC-8`, `MDC`); Supply From may additionally carry a printed `via ...` qualifier.
- **Description = `"<type word> - <Equipment ID>"`.** A DCC/MDC is described as a `Distribution` → `"Distribution - DCC-1"` (identifier is the Equipment ID, not the X-tag). Panels unchanged (`"Panel - PNL-W"`).

The version bump made the six existing building-641 Legacy JSONs stale; they re-extracted automatically through the designed rescore mechanism and re-synced to `sdi_dataset_EL`. Verified live: 0000186130 = `DCC-2XXD1` / `DCC-1` / `"Distribution - DCC-1"` / Supply From `MDC` = Fed From `MDC`; PNL-W fed from `DCC-1` cross-references the DCC's Equipment ID. The hyphen collision surface (`DCC-1` unit vs `DCC-2XXD1` X-tag) is regex-guarded and test-pinned (`test/test_legacy_flow.py`). Note: `UBC Asset Tag` keeps the X-tag (`DCC-2XXD1`) — unchanged by this rule.

## 2026-07-29: EL legacy extraction gate — PRODUCTION DEPLOY to VM + same-day hotfix

### Summary

The EL legacy extraction gate (entry below; feature/el-legacy-extraction-gate, merged to main at `5b76d8d`) was deployed to the production VM (142.103.68.1): `API/API_interface_EL_ver00.py`, `legacy_flow.py`, `Asset_dashboard_EL.py`, docs, `scripts/rollback_el_legacy_extraction.sh` (new). No DB schema changes. All six building-641 (Legacy) QRs — `0000186127`–`0000186132` — were re-extracted through the new legacy path (`EL_OVERWRITE_EXISTING_JSON=true --qr <code>`) and batch-synced to `sdi_dataset_EL`. The golden asset 0000186130 now records `DCC-2XXD1` / `Equipment ID "DCC #1"` / `Supply From "MDC via TX T1"` / `Fed From Equipment ID "MDC"` / `208/120` / `400` (was `PNL-DIST.CTRE.1` / `DIST.` / `208`). PNL-W (186129) → `Fed From Equipment ID "DCC #1"` now cross-references the DCC's `Equipment ID` — the SLD linking works live. The two transformer plates (`T1`, `TX-MAIN`) were kept verbatim (no PNL fabrication) and flagged for manual review — transformer identity parsing is a known follow-up.

### Same-day hotfix (commit `0f2143b`) — discovered during live verification

First sync of real legacy rows exposed two pre-existing, process-blind standard-flow write paths that mangled the freshly-written legacy values:

- **`_ensure_el_fed_from_equipment_id_column` (Asset_dashboard_EL.py).** Unlike its blank-fill-only siblings, this ensure-backfill force-rewrote EVERY `sdi_dataset_EL` row on EVERY `_sync_db_from_structured` call to keep `Fed From Equipment ID` in lockstep with the STANDARD derivation from `Supply From` — observed live: `MDC` → `MDC-VIA`, `DCC #8` → `DCC`. Now skips rows in Legacy-classified buildings (probes `Buildings."Process"` via metadata first so a missing column can never abort a PG transaction; environments without the column keep pre-existing behavior — they cannot contain legacy-composed rows). Test: `test/test_el_fed_from_backfill_legacy_guard.py` (behavioral, real in-memory DB, all four quadrants).
- **SLD swift-save / reconcile (`sld_blueprint.py`).** All four `_sync_db_from_structured` call sites omitted `process=` (→ Standard branch) and standard-formatted the submitted/reconciled `Supply From`. Both endpoints now resolve `Buildings.Process` once per request (blank → 409 stop-and-warn, same semantic as `toggle_approved`), keep Legacy `Supply From` verbatim, and thread `process=` through every sync call. Guard tests: `test/test_el_sync_legacy_gate.py::SldBlueprintProcessGateTests`.

Note: a blank/NULL `Buildings.Process` now hard-409s swift-save/reconcile where it previously (ungated) silently succeeded via the Standard branch — intentional per the never-silently-Standard invariant; no live blast radius (0 blank buildings).

The mangled rows were repaired by bumping the six JSONs' mtimes and re-running the (now-guarded) batch sync; each of the six sweeps re-ran over the full table and the legacy values survived — the guard is verified by construction in production.

### Rollback

- Pre-deploy bundle (replaced files incl. pre-hotfix `sld_blueprint.py` + full pg_dump): `/home/developer/deploy_backups/el_legacy_extraction_20260729_191540/` (MANIFEST inside).
- One-command rollback: `/home/developer/scripts/rollback_el_legacy_extraction.sh <bundle>` — restores `API_interface_EL_ver00.py`, `Asset_dashboard_EL.py`, `sld_blueprint.py` + docs, **deliberately keeps `legacy_flow.py` at the new revision** (the pre-deploy revision cannot read back `"MDC via TX T1"` / `"DCC #1"` values — it would blank Fed From on every legacy record's review page), stamps `"modified": true` on `process=Legacy` JSONs (manifest written into the bundle) so the ungated old code cannot re-extract/overwrite them, and graceful-HUPs assetcap-el. Full-dump disaster restore documented in the script header (manual by design).

## 2026-07-29: EL legacy extraction gate (API/API_interface_EL_ver00.py)

### Summary

Root cause: QR 0000186130 (building 641, Legacy) prints a legacy lamacoid identity (`DIST. CTRE. #1 400A-120/208V - 3PH - 4W` / `FED FROM MAIN DIST. CTRE. TRANS. RM. 0060 THROUGH TRANS. "T1" 112.5 K.V.A.`). Before this change the EL extraction API had no `Buildings.Process` gate at all — every building, Legacy included, ran the standard extraction prompt and `_apply_tag_formatting`, which prefixed the digit-adjacent identity with `PNL-` and mangled the rest, recording `UBC Asset Tag = "PNL-DIST.CTRE.1"`, `Supply From = "DIST."`, `Volts = "208"`: a fabricated standard-shaped tag for a building that was never tagged that way, plus a lost fed-from/rating record. The review-app-side gate (`legacy_flow.py`, deployed 2026-07-28, see entries below) could not fix this on its own — by the time a Legacy-building JSON reached the reviewer, the fabrication had already happened at extraction time and the un-fabricated raw text was gone.

This closes that gap: `API/API_interface_EL_ver00.py` now reads the same `"Buildings"."Process"` column the review app and SLD extractor already gate on, and Legacy buildings get a dedicated extraction path instead of the standard one.

### Behavior

- **Gate (`AssetProcessor.__init__` / `process_single_asset`).** `_load_building_process_map()` loads the whole `Buildings` table (`Code` → `Process`) once per run. `process_single_asset` checks it before any billing work: `'Standard'` → existing extraction body, byte-identical; `'Legacy'` → `_process_legacy_asset(qr, info)`; blank/missing/any other value → the QR is skipped with a warning, no JSON written, `ai_status` untouched. If the `Buildings` table itself is unreachable, the map load fails safe to `{}`, which skips every QR in the run rather than defaulting anything to Standard.
- **Legacy extraction path (`_process_legacy_asset`).** Prompts with a new verbatim-transcription-only prompt (`EL_LEGACY_PROMPT`) and a raw-preserving Pydantic model (`ELLegacyStructuredExtraction`) that carries none of the standard flow's normalizing field validators. The raw model output is post-processed through `el_legacy_flow.legacy_structured_from_raw()` (`review/Asset_dashboard_browser_EL/legacy_flow.py`, the same module the review app and SLD extractor already share) instead of the standard `_apply_tag_formatting`, so a legacy identity is either recognized and normalized to its legacy-correct shape (`"DIST. CTRE. #1"` → `Equipment ID "DCC #1"`, `UBC Asset Tag "DCC-2XXD1"`) or left verbatim — never fabricated into a `PNL-` tag. Completeness scores against a 4-field legacy set (`EL_LEGACY_SCORING_FIELDS`) instead of the standard field list.
- **Legacy JSON envelope.** Adds `"process": "Legacy"`, `"el_legacy_rule_version"` (independent of `Config.EXTRACTION_RULE_VERSION`, currently `1`), and `structured_data.label_text` (the verbatim plate transcription) — the latter now activates the previously-dormant ratings rules (Field-Gap Map rules 2–3) in the review app's `apply_legacy_rules` for newly-extracted Legacy JSONs; see `Markdowns_documentation/rules/review_apps.rules.md` → "EL Legacy Flow Rules" → "Extraction-side gate (2026-07-29)".
- **Staleness/overwrite protections.** A Legacy payload's skip-if-exists check routes through a legacy-appropriate rescore function (`_existing_el_legacy_output_needs_rescore`) instead of the standard one, which would otherwise flag every valid Legacy JSON stale forever. A human-reviewed or manually-overridden existing Legacy JSON (`modified`, `supply_from_manual_override`, `volts_manual_override`) is refused an overwrite regardless of `EL_OVERWRITE_EXISTING_JSON` (invariant 6).
- **Golden asset target (QR 0000186130):** `UBC Asset Tag = "DCC-2XXD1"`, `Equipment ID = "DCC #1"`, `Equipment Type = "Distribution Centre"`, `Supply From = "MDC via TX T1"`, `Fed From Equipment ID = "MDC"`, `Volts = "208/120"`, `Ampere = "400"`, `Location = ""`.

### Operational caveat

Pre-feature Legacy-building EL JSONs (extracted before this gate existed, with no top-level `"process"` key) do not auto-convert to the Legacy envelope. They fall through `_process_legacy_asset`'s existing-JSON check to the standard rescore function (`_existing_el_output_needs_rescore`, a multi-condition check — rule-version comparison, completeness/confidence recomputation, tag-decimal normalization — not a hardcoded result), which currently evaluates as "not stale" for all such payloads in the present fleet, so they are skipped indefinitely rather than re-extracted. Not a live problem today — the only buildings holding existing EL records (`217`, `314-1`, `459`, `750`) are all classified `'Standard'` — but if a building holding existing Standard-flow JSONs is ever reclassified to `'Legacy'`, its JSONs need a one-time `EL_OVERWRITE_EXISTING_JSON=true` re-run to pick up the Legacy behavior.

### Validation

- New tests: `test/test_el_extraction_legacy_gate.py` (AST-structural guards: gate ordering, never-defaults-to-Standard, no `_apply_tag_formatting` call, legacy envelope keys, raw-preserving model). Extended: `test/test_legacy_flow.py` (identity parsing, X-tag type segment, fed-from round-trip, `legacy_structured_from_raw` composer, including the golden-asset case).
- Code review round 1 fixed: hyphenated fed-from tag truncation, an identity/fed-from-hijack edge case, idempotency/overwrite gaps, and a description-fabrication risk (commit `2390b12`).

## 2026-07-28: EL legacy flow — PRODUCTION DEPLOY to VM

### Summary

The EL legacy flow (feature/el-legacy-flow, merged to main at `cf07bff` + MDC promotion `a241e98`) was deployed to the production VM (142.103.68.1). Files: `legacy_flow.py` (new), `Asset_dashboard_EL.py`, `sld/extract_electrical_schema.py`, `dictionary/electrical.dictionary.py` (MDC), `dictionary/electrical.dictionary_old.py` (new), `scripts/migrate_buildings_process_column.py`, `scripts/rollback_el_legacy_flow.sh` (new), docs. DB: `Buildings."Process"` column + `ck_buildings_process` added via owner role (`developer` over the `/tmp` socket, port 5433 — the app role `assetcap_app` cannot run DDL); backfilled 327 rows to `'Legacy'`, then re-classified the 4 buildings holding existing EL records (`217`, `314-1`, `459`, `750`) to `'Standard'`. Service reloaded via graceful HUP to the gunicorn master (no-sudo path; master = gunicorn process with ppid 1 — never `pgrep | head`). Verified: py_compile, prod dictionary `panel_legacy` load (`PANEL U` -> `PNL-U`), fresh workers, HTTP 302 on :8005, nginx 301 on the reviewel host, SLD extractor `--help`.

### Rollback

- Pre-deploy bundle (files + full pg_dump): `/home/developer/deploy_backups/el_legacy_flow_20260728_141619/` (MANIFEST inside).
- One-command rollback: `/home/developer/scripts/rollback_el_legacy_flow.sh <bundle> [--code-only|--full]` — `--code-only` (default) restores replaced files, deletes introduced files, graceful-reloads assetcap-el, leaves the additive `Process` column in place; `--full` also drops the constraint + column. Full-dump disaster restore documented in the script header (manual by design).

## 2026-07-28: EL legacy flow: Buildings.Process gate in EL review + SLD

### Summary

Completed the electrical legacy-flow gate rolled out earlier the same day (see the "Buildings Process flow-decision column" entry below): `"Buildings"."Process"` now actively steers both consumers of legacy panel identities instead of just existing as a schema column.

- **Task 6 (EL review app, `Asset_dashboard_EL.py`):** every call site that derives Equipment ID / Equipment Type / Power Type / Supply From / Fed From Equipment ID, and the final dictionary-priority/save-review sync, looks up `Buildings.Process` once per record via `legacy_flow.get_building_process(conn, building)` and reuses the result (`process`) for every downstream decision in that request. `'Standard'` buildings run the existing `_apply_dictionary_priority(...)` path unchanged (zero behavior change). `'Legacy'` buildings route through `legacy_flow.apply_legacy_rules(data)` and `_compute_el_upstream_fields(data, process)` instead, so legacy idents (`PANEL U`, `MCC2`, `ATS`, ...) get parsed/normalized and blank fields get filled without ever overwriting a non-blank reviewer value. Full detail already lives in `Markdowns_documentation/rules/review_apps.rules.md` -> "EL Legacy Flow Rules (2026-07-28)" — not duplicated here.
- **Task 7 (SLD extractor, `review/Asset_dashboard_browser_EL/sld/extract_electrical_schema.py`):** `main()` now performs the same `Buildings.Process` lookup, using the same (`--building-code` override, else filename-derived) precedence `write_payload_to_db` already used for the persisted `"Building"` column, so the gate can never disagree with what gets written. A blank/missing `Process` raises `legacy_flow.BuildingProcessError`, caught and routed through the extractor's existing `fail()` convention (stderr + non-zero exit). For `'Legacy'` buildings only, the freshly loaded SLD dictionary module (`electrical.dictionay_single_line_diagrama.py`, itself untouched) is wrapped in a new `legacy_flow.legacy_dictionary_shim(dictionary_module)`: legacy idents are accepted/normalized (`is_dictionary_id`/`normalize_dictionary_id`) via `parse_legacy_ident`, everything else delegates unchanged to the wrapped module. `'Standard'` buildings never get the shim installed — `dictionary_module` is passed through byte-identical to before. The "not modeled" identifier failure (`validate_dictionary_identifiers`) and the normalization call sites feeding it now transparently accept legacy panel names for Legacy buildings, with no other extractor changes.

### Validation

- `test/test_legacy_flow.py::test_legacy_dictionary_shim` (new, TDD): delegated ids, legacy-accepted ids, rejected junk, legacy normalization, delegated normalization, and `label_schema` pass-through, all verified against a fake SLD-dictionary double.
- Full suite: `test/test_legacy_flow.py` + `test/test_el_sync_legacy_gate.py` (66) and the full `test/` directory (105 + 6 subtests) pass.
- Manual verification (no OpenAI key/PDF available in this environment for a true end-to-end extractor run): imported `extract_electrical_schema` and exercised the gate against a scratch `Buildings` table plus the real production SLD dictionary module — a `'Standard'` building leaves `dictionary_module` untouched (same bound methods); a `'Legacy'` building installs the shim, `PANEL U` normalizes to `PNL-U` and is accepted, standard `CDP-...` ids still delegate identically to the unwrapped module, and junk is still rejected; a blank `Process` row raises `BuildingProcessError` and `fail()` exits non-zero with the expected stderr message.

## 2026-07-28: Buildings "Process" flow-decision column (local dev DB)

### Summary

New per-building gate for the electrical extraction flow: `"Buildings"."Process"` decides which dictionary flow a capture goes through — `'Standard'` -> core flow (`dictionary/electrical.dictionary.py`), `'Legacy'` -> legacy flow (`dictionary/electrical.dictionary_old.py`), blank/NULL -> the process must STOP and warn the user. Applied to the **local dev PostgreSQL copy only** (127.0.0.1:5432) via the new idempotent migration `scripts/migrate_buildings_process_column.py`; production gets the same script at cutover. Column is TEXT with CHECK constraint `ck_buildings_process` (`Standard`/`Legacy`, NULL/'' representing the stop-and-warn state); all 327 rows initially backfilled to `'Legacy'`. Backup taken first: `db_backups/backup_qr_code_db_local_20260728_100008.sql`. Flow-gate enforcement in application code is the next workstream step (not yet implemented).

## 2026-07-28: Electrical dictionary working copy — DCC abbreviation

### Summary

Added panel abbreviation `DCC` -> `Distribution Centre` to the working copy `dictionary/electrical.dictionary_old.py` (not promoted to production). Printed label variations map to it: "DISTRIBUTION CENTRE", "DIST. CTRE.", "DIST CTRE", "DIST. CENTRE" — but only when NOT preceded by "MAIN"; "MAIN DIST. CENTRE" continues to map to `MDC` (Main Distribution Centre). Production `electrical.dictionary.py` unchanged (still only the promoted MDC delta from baseline).

## 2026-07-27: Electrical dictionary working copy with MDC abbreviation and X-placeholder convention

### Summary

New working-copy process for the electrical dictionary: `dictionary/electrical.dictionary_old.py` (new file) now receives all electrical dictionary updates, while the production `dictionary/electrical.dictionary.py` — the file actually loaded by the EL review app and extraction API — stays intact and must not be modified unless explicitly requested. Changes are promoted from the working copy to production only on explicit request.

The working copy carries two adopted conventions not present in production yet:

- Panel abbreviation `MDC` -> `Main Distribution Centre` (synonym of Main Distribution Panel; field lamacoids read "MAIN DIST. CENTRE" / "DIST. CTRE.").
- X-placeholder convention (header comment): when composing a standard tag from field-captured data, segments with no printed/verified value are written as `X` (e.g. `MCC-6XXX2`, `PNL-2XXXX`); `X` is not a decodable code and parsers must treat such segments as unknown.
- `panel_legacy` schema (added same day, working copy only): models legacy identity-style panel labels — `PNL <IDENT>` (also printed `PANEL <IDENT>`), where IDENT is typically 1–2 letters with an optional trailing number (up to 3 letters and slash-joined compounds occur), e.g. `PNL D`, `PNL U`, `PNL EM`, `PNL NPH`, `PNL QQ`, `PNL EM3`, `PNL UPS/CM`. Includes an ident regex (`^[A-Z]{1,3}[0-9]{0,2}(?:/[A-Z]{1,3}[0-9]{0,2})?$`), and `system_hints` (N -> Normal, E/EM -> Life Safety, UPS -> UPS supply) that are hints only and require corroboration. Identity codes are names, not decodable segments; because idents start with letters and standard compressed suffixes start with a digit, the two panel schemas cannot collide. Caution: the live review-app compressed parser can misread digit-ending idents (e.g. `EM3` -> "Level 3" location) — a known pre-existing behavior to address if/when `panel_legacy` is promoted.

### Behavior

- **`MDC` promoted to production on explicit request (2026-07-27, same day):** `electrical.dictionary.py` now carries `MDC` -> `Main Distribution Centre` as its only delta from the committed baseline. The EL review app and extraction API pick it up on next load; the API's `Config.ABBREVIATIONS` already contained an `MDC` prefix, so tag-prefix recognition and the dictionary are aligned.
- The X-placeholder convention remains documented in the working copy only (not promoted).
- No change to `electrical_equipment_rules.py` equipment-type derivation or the SLD dictionary variant (`electrical.dictionay_single_line_diagrama.py`) — an `MDC-...` asset still gets no derived Equipment Type in Planon export until that map is extended.

### Files updated

- `dictionary/electrical.dictionary_old.py` (new working copy; holds MDC + X convention)
- `dictionary/electrical.dictionary.py` (MDC entry promoted on explicit request)

## 2026-07-23: ME long-model and Model/Serial collision defense

### Summary

Mechanical extraction now preserves long OEM model configuration strings and prevents a Serial Number from being promoted into the Model field. This fixes QR `0000186301` (Trane fan-coil unit), whose 40-character model was rejected by the old 32-character model gate; fast OCR then selected the shorter serial `T03M77537` as Model because the word `MODEL` appeared elsewhere in the full OCR text.

### Behavior

- Valid code-like ME models may contain up to 64 compact characters; long candidates remain subject to stricter character, letter/digit, and word-count gates.
- Model parsing and model evidence are label-local. OCR fallback candidates must immediately follow a recognized model label and cannot come from an unrelated Serial, Sales Order, rating, or instruction row.
- Identical compact Model and Serial values invalidate the candidate, force model recovery, and produce the `model_serial_collision` manual-review reason if a collision was encountered.
- Merge and final-save guards prevent a duplicate Model/Serial pair from being persisted.
- Production simple mode now reads upright grayscale before thresholded variants and treats a label-local value missing only one or two trailing zeroes as corroboration of the longer zero-padded OEM Model.
- Fast OCR no longer lets a longer rotated/noisy model or serial automatically replace a complete upright read.

### Validation

- Added `test/test_me_long_model_serial_collision.py` for long-model acceptance, label-local evidence, fast-OCR separation, collision recovery, and LLM-candidate rejection.
- Patched fast OCR returns Model `BCHC090H1A0A2AF7P000000B0000000000000000` and Serial Number `T03M77537` from the QR `0000186301` production nameplate image.

## 2026-07-14: Modern score capsules for ME, BF, and EL review dashboards

### Summary

The "Avg AI Conf" and "Comp Score" columns now use a compact semantic score capsule in every New, Update, and Manual listing table across the ME, BF, and EL reviewers. The previous all-green gradient bar and floating triangle marker were replaced by a bold percentage, status dot, and five discrete 20-point segments. Column order, score calculations, filters, sorting, exports, JSON, and database behavior are unchanged.

### Presentation rules

- Headers use the existing Bootstrap Icons library: `bi-stars` for Avg AI Conf and `bi-check-circle` for Comp Score.
- Scores below 70 render red; 70–79.99 render amber; 80–100 render green.
- Segments activate at 1, 21, 41, 61, and 81. Zero shows no active segment; missing values remain neutral `N/A`.
- Each capsule exposes an ARIA meter value and a Low/Medium/High text alternative, with a visually hidden level label so state is not conveyed by color alone.
- Raw numeric `data-order` values, existing tooltips, and DataTables saved-state versions are preserved.

### Files updated

- ME, BF, and EL `review_asset_templates/dashboard.html` templates
- `Markdowns_documentation/rules/review_apps.rules.md` and its `.agent_app` mirror
- SecondBrain review rules and attribute change history

## 2026-07-09: EL review-report SLD — display units instead of Planon UoM codes (fixes "208/120VLT | 100AMP")

### Summary

The SLD embedded in the EL Asset Review Sheet (server-rendered SVG in `review_print.html`) showed raw Planon UoM codes for diagram rows that had been copied from the SDI side — `208/120VLT | 100AMP` instead of `208/120V | 100A` (observed on QR 0000184443 / PNL-2E0P1). The SDI tables and their `VLT`/`AMP` codes are intentionally unchanged.

### Problem

`_sld_rating_text()` (Asset_dashboard_EL.py) concatenated the value with the raw `(UoM)` column. `electrical_building_schema` natively stores display units (`V`/`A`), but 6 rows copied from `sdi_dataset_EL` carried the Planon codes `VLT`/`AMP`, which only the print report rendered verbatim (the interactive sld.js always draws `V`/`A`).

### Behavior

- `_sld_rating_text()` now maps UoM codes to display units for rendering (`VLT` -> `V`, `AMP` -> `A`; blank falls back to `V`/`A` for voltage/amperage) so the report SLD matches the interactive diagram regardless of row provenance.
- Schema-table writes normalize UoM the same way (`_schema_display_uom` in sld_blueprint.py, applied in `create_asset` and the `swift_save_asset` schema UPDATE). SDI-bound payloads keep the raw UoM — SDI behavior is untouched.
- `scripts/normalize_el_voltage_values.py` extended: on `electrical_building_schema` ONLY it also normalizes `Voltage Rating (UoM)` / `Amperage Rating (UoM)` (`VLT`->`V`, `AMP`->`A`). Applied 2026-07-09: 6 rows; SDI tables and JSONs reported 0 changes.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` (`_sld_rating_text`)
- `review/Asset_dashboard_browser_EL/sld_blueprint.py` (`_schema_display_uom`, `create_asset`, `swift_save_asset`)
- `scripts/normalize_el_voltage_values.py`

## 2026-07-08: EL Voltage Rating — bare value canonical form (fixes SLD "208/120VV" double unit)

### Summary

EL voltage values are now stored **without** unit letters (`208/120`, `600-208Y/120`, `208Y/120`, `600`); the unit continues to live in `Voltage Rating (UoM)` (`VLT` on SDI tables, `V` on `electrical_building_schema`). This matches the amperage convention (bare digits + `AMP`) and fixes the SLD diagram rendering `208/120VV` — the value already embedded a `V` and the renderer appended another.

### Problem

`normalize_volts()` returned values with a `V` suffix (`208/120V`), and the electrical dictionary voltage map carries the same suffixed text. The SLD front end (`sld.js`) appends a hardcoded `V`/`A` to node labels, tooltips, and the edit panel, so every suffixed value displayed a doubled unit (observed on QR 0000184443 / PNL-2E0P1). 170+ `sdi_dataset_EL` rows and 5 `electrical_building_schema` rows carried embedded units.

### Behavior

- `normalize_volts()` (shared `API/validators_shared.py` and the fallback copy in `API/API_interface_EL_ver00.py`) returns the bare value. `_derive_dictionary_panel_volts()` already routes dictionary text through it, so dictionary-derived volts and the stale-JSON check stay consistent.
- Save boundaries strip unit letters defensively (regex `(?<=\d)\s*V(?:AC|DC|OLTS?)?\b`):
  - EL reviewer save (`Asset_dashboard_EL.py` `_strip_el_voltage_unit_letters`, applied to the resolved `Volts`/`Voltage Rating` before the SDI write)
  - SLD `swift_save_asset`, `create_asset`, and `add_missed_asset_to_sld` (`sld_blueprint.py` `_strip_voltage_unit_letters`)
  - SDI Process `_normalize_voltage_columns` and the export-shaping frame (`SDI_process/app.py`)
- `sld.js` no longer doubles units: new `withRatingUnit(value, unit)` appends `V`/`A` only when the value does not already end with the unit (also tolerates `VAC`/`VDC`). Applied to node rating labels, hover tooltips, and the edit panel; `?v=` cache-bust bumped in `dashboard.html`.
- UoM semantics unchanged: `VLT`/`AMP` remain the canonical Planon-facing UoM codes.

### Data migration

`scripts/normalize_el_voltage_values.py` (dry-run by `--dry-run`, writes an old/new snapshot to `logs/`) strips embedded unit letters from `"Voltage Rating"`/`"Volts"` in `sdi_dataset_EL`, `sdi_print_out`, `sdi_print_out_arch`, `electrical_building_schema`, and from `structured_data.Volts` in `Output_jason_api/*_EL_*.json`. Applied locally 2026-07-08: 172 + 24 + 111 + 5 rows and 172 JSONs; idempotent re-run reports 0 changes.

### Files updated

- `API/validators_shared.py`, `API/API_interface_EL_ver00.py`
- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`, `sld_blueprint.py`
- `review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js`, `dashboard.html`
- `SDI_process/app.py`
- `scripts/normalize_el_voltage_values.py` (new)

## 2026-07-07: Capture App — optional Notes and Installation Date fields

### Summary

The mobile Capture App pre-submit screen (`capture.html`) now offers two optional fields: **Notes** (multi-line textarea, 200-char limit with live counter) and **Installation Date** (native date picker with an explicit ✓ confirm step). Values persist to new `QR_codes` columns `capture_notes` / `installation_date` and to the `{qr}_et.json` payload. Deployed to production 2026-07-07.

### Behavior

- Both fields are optional, best-effort capture metadata — an empty field never blocks a save.
- Notes: trimmed and clamped to 200 chars server-side in `/submit` and again in `upsert_qr_codes()`; client `maxlength` is not trusted.
- Installation Date: the visible picker carries no `name`; the submitted value lives in a hidden input populated only when the user taps the ✓ button (prevents iOS Safari from silently saving an auto-filled "today" when the calendar opens). ✕ clears it. Server validates strict ISO `YYYY-MM-DD` via `normalize_iso_date()`; invalid values are logged and dropped, never a 400.
- Overwrite semantics: latest non-empty submission wins; an empty resubmission never erases a stored value (Invariant #6). Clearing is a review-layer job.
- `{qr}_et.json` gains keys `capture_notes` and `installation_date` (always present, empty string when unset). Downstream `_et.json` readers are key-tolerant.
- `capture_notes` is displayed read-only in the ME review dashboard/detail page; it is not propagated to the AI-extraction JSON, BF/EL review dashboards, or `sdi_dataset` / `sdi_dataset_EL`.
- New columns created by owner-run migration `scripts/migrations/2026-07-06_qr_codes_capture_notes_install_date.sql` (migration-first deploy order: `upsert_qr_codes()` feature-detects the columns and silently drops values if the app ships first).

### Files updated

- `asset_capture_app_dev/app.py` (`normalize_iso_date()`, `/submit` reads, `upsert_qr_codes()`, `write_elapsed_time_json()`)
- `asset_capture_app_dev/templates/capture.html` (fields + confirm-flow JS) and `templates/base.html` (CSS cache-bust)
- `asset_capture_app_dev/static/css/styles.css` (date-input width cap, `.install-date-row` / `.install-date-btn`)
- `scripts/migrations/2026-07-06_qr_codes_capture_notes_install_date.sql`

## 2026-07-07: ME Year policy — printed evidence only; Bradford White manufacturer canonicalization

### Summary

ME extraction saves a `Year` only when it is corroborated by printed evidence on the seq-0 plate (`_has_year_evidence`). Model-inferred years — including serial letter-code decodes — are never saved. A new manufacturer regex rule canonicalizes "Bradford White" (with or without the "Corporation" suffix) to "Bradford White Corporation".

### Problem

Bradford White tank nameplates carry no printed year, and their manufacturer block is faint dot-matrix print that OCR often misses. The vision model inferred years from the serial letter date code and the pipeline kept them (confidence ~10, `low_confidence_year`); a suffix-less "Bradford White" read was rejected by the two-token-no-legal-suffix gate (`missing_manufacturer`). Observed on QRs 0000081369/70/71 (building 068) and 0000081480.

**Rejected approach (same day):** a deterministic Bradford White serial date-code decoder (corroborate + fill) was deployed and reverted within hours. A single misread serial letter shifts the decode by decades — on QR 0000081369 the serial was misread `CJ...` instead of `GJ...` and the fill asserted Year 2026 at confidence 85. Field decision: an empty Year is always preferable to an inferred one.

### Behavior

- `Year` is kept only when `_has_year_evidence` confirms it against seq-0 OCR/ROI evidence.
- Targeted seq-0 Year rereads (`_reread_year_from_nameplate_llm`) are themselves model outputs; their result is now accepted **only** when it also passes `_has_year_evidence` — in both the simple-mode guardrail and the UI-parity guardrail/fill paths. Unevidenced rereads are logged (`Discarding unevidenced Year reread`) and dropped.
- Plates without a printed year (e.g. Bradford White storage tanks) correctly save `Year = ""` and flag `missing_year` for the reviewer.
- New manufacturer regex rule `\bBRADFORD\s+WHITE(?:\s+CORPORATION)?\b` → "Bradford White Corporation" (retained from the reverted change; recovers the faint dot-matrix manufacturer block when the vision model reads it without the suffix).

## 2026-07-07: Review "Save" button (save and stay) in ME/BF/EL

### Summary

All three review apps now have a dedicated Save button beside the Pending/Approved pill (header and footer) that saves the form and returns to the same review page, instead of navigating back to the review dashboard.

### Behavior

- New `save_stay` form action: merges the submitted form exactly like a normal save (JSON + SDI table sync), then redirects back to the same review page with a "Changes saved." flash.
- The button is a blue pill (`.save-stay-btn`, `bi-floppy2-fill` icon) rendered beside both Pending/Approved pills; visually consistent with the existing pill styling.
- Disabled while the record is Approved (read-only form) or package-locked, mirroring the other save buttons; the disable state follows the Pending/Approved toggle live.
- Defensive server-side handling: `save_stay` on an approved record redirects back without saving; package-locked records flash the lock message (existing behavior).

### Files updated

- `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` + `review_asset_templates/review.html`
- `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` + `review_asset_templates/review.html`
- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` + `review_asset_templates/review.html`

## 2026-07-07: ME/BF manual-edit protection for Asset Group and Attribute

### Summary

Reviewer edits to `Asset Group` and `Attribute` in the ME and BF review apps are now recorded as manual overrides and are no longer clobbered by the mechanical-dictionary re-application. This extends the EL app's existing `asset_group_manual` pattern to ME and BF, and adds an `attribute_manual` flag.

### Behavior

- Two flags persisted in the structured JSON (`Output_jason_api/` `structured_data`): `asset_group_manual` and `attribute_manual` (`"1"`/`"0"`, absent = `"0"`). No new DB columns.
- On save, each flag is computed server-side: `"1"` when the reviewer submitted a non-blank value different from the current dictionary derivation for the tag; `"0"` when the submission is blank or matches the dictionary (returns the field to dictionary control).
- `apply_dictionary_rules` in both apps skips overwriting a flagged, non-blank field on every path (save, JSON→DB sync, list render, review render, print render).
- Once flagged manual, the value survives UBC Tag changes (matches EL behavior).
- Legacy JSONs without flags behave exactly as before: dictionary-controlled.
- ME `Main Asset` remains dictionary-owned (read-only in the form). ME's Approved blank-only backfill and BF's blank-only `Description` fill are unchanged.

### Files updated

- `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` — new `_find_dictionary_entry()` and `_update_manual_field_flags()`; flag checks in `apply_dictionary_rules()`; flag computation in `save_review` before the dictionary re-apply; flag keys excluded from the generic form merge.
- `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` — same helpers; the three duplicated match branches of `apply_dictionary_rules()` consolidated through `_find_dictionary_entry()`; flag computation in `save_review` after the form merge loops.
- EL is unchanged (`asset_group_manual` already existed; EL's `Attribute` field is read-only in its UI).

## 2026-06-29: FLS Attribute Set default and Planon-coded edit rule

### Summary

FLS devices now use stored Attribute code `FireAlarmDevice` for `new_device."Attribute Set"`, backed by `"Attribute"."Attribute" = Electrical/FLS - Fire Alarm Device`.

### Behavior

- New FLS Device Flow defaults `Attribute Set` to `FireAlarmDevice`.
- Existing `new_device` rows are normalized to `FireAlarmDevice`.
- Dashboard save logic enforces `FireAlarmDevice` when API callers submit a blank FLS `attribute_set`.
- Rows with a populated `Planon Code` remain editable for metadata corrections.
- Rows with a populated `Planon Code` cannot be deleted or selected for bulk updates.

## 2026-06-25: ME ASME pressure-vessel plate rescue

### Summary

ME AI extraction now has a local, deterministic rescue for seq `-0` ASME-style pressure-vessel plates with embossed text such as `CERTIFIED BY`, `S/N`, `NB`, `MAWP`, `MDMT`, and `YEAR`.

### Behavior

- `CERTIFIED BY <company>` can fill `Manufacturer` when no separate maker/brand is visible.
- `S/N <value>` can fill `Serial Number`, and `YEAR <yyyy>` can fill `Year`.
- `NB`, `MAWP`, `MDMT`, pressure ratings, and shell/head thickness values are explicitly blocked from becoming `Model`.
- The rescue uses bounded local OCR/preprocessing and parser logic only; it does not enable fallback models, premium models, or additional OpenAI calls.

## 2026-06-03: FLS New Device Flow Control Panel lookup

### Summary

Dashboard `1. New FLS Device Flow` now derives Control Panel `Code` and `Description` from `"UBC - Asset Data Master Info"` by selected building `Property code`.

### Behavior

- The lookup is display-only; no `new_device` schema change or persisted Control Panel snapshot was added.
- If a property has multiple matching Control Panel rows, Dashboard displays the lowest `Code` row and flags the row/form as multi-match.
- The primary FLS table hides `Asset Group`, `Space`, and `Details` in the New Device Flow view.
- Those hidden fields remain available in Edit and the magnifying-glass details modal.

## 2026-06-03: Asset system code text normalization

### Summary

The VM `Asset_System_info` view now exposes asset master `"Code"` values as 10-character zero-padded text.

### Behavior

- `Asset_System_info` is a view; the source column is `"UBC - Asset Data Master Info"."Code"`.
- The source column was rebuilt from `INTEGER` to `TEXT`.
- All 150 source rows were normalized to 10 characters, including `54137 -> 0000054137` and `154409 -> 0000154409`.
- `PRAGMA integrity_check` returned `ok` after the migration and VM-side `VACUUM`.
- Current production DB: PostgreSQL `qr_code_db` on the VM (`127.0.0.1:5433`) via `/home/developer/db_backend.env`; the legacy SQLite `QR_codes.db` is rollback/reference only.
- Backup files under `/home/developer/asset_capture_app_dev/data/`, `/home/developer/backup_app/`, and deployment backup folders can still show the old integer values and are not current production state.

### VM backups

- `/home/developer/asset_capture_app_dev/data/QR_codes.bak_20260603_152011_asset_system_code_text.db`
- `/home/developer/asset_capture_app_dev/data/QR_codes.bak_20260603_152154_before_vacuum_after_asset_system_code_text.db`

## 2026-06-03: Review Photo column left alignment

### Summary

The Photo value cells in the ME, BF, and EL review dashboard listing tables now align their photo-status pills to the left.

### Behavior

- Applies to the Photo column in all three review dashboard tabs: New, Update, and Manual.
- The cell class changed from `text-center` to `text-start` for the Photo value cells only.
- The Photo header, Review action column, status columns, JSON/DB state, SDI behavior, and photo-count logic are unchanged.
- VM deployment backup: `/home/developer/deploy_backups/review_photo_left_align_20260603_131754`.

### Files updated

- ME review template: `review/Asset_dasboard_browser_ME/review_asset_templates/dashboard.html`
- BF review template: `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html`
- EL review template: `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`
- Review rules and workflow docs updated in canonical and mirrored documentation.

## 2026-06-03: Sidebar Performance Analysis label

### Summary

The shared application shell now labels the former `Cost Analysis` navigation item as `Performance Analysis` and uses a gauge-style icon to better match the operational data-quality chart.

### Behavior

- The shared shell sidebar label changed from `Cost Analysis` to `Performance Analysis` in Dashboard, SDI Process, ME Review, BF Review, and EL Review.
- The icon changed from the dollar symbol to a Lucide-style gauge icon.
- The historical route key remains `cost`, and the RBAC item key remains `cost_analysis`; only the visible label changed.
- The Dashboard monitoring card and RBAC registry label were aligned to the new visible text.
- VM deployment backup: `/home/developer/deploy_backups/sidebar_performance_analysis_20260603_111550`.

### Files updated

- Shared shell JS:
  - `Dashboard/static/shell/shell.js`
  - `SDI_process/static/shell/shell.js`
  - `review/Asset_dasboard_browser_ME/review_asset_templates/static/shell/shell.js`
  - `review/Asset_dasboard_browser_BF/review_asset_templates/static/shell/shell.js`
  - `review/Asset_dashboard_browser_EL/review_asset_templates/static/shell/shell.js`
- Dashboard template: `Dashboard/templates/dashboard.html`
- RBAC registry: `auth_service/app_registry.py`
- Canonical and mirrored documentation updated to use `Operational Performance Analysis`.

## 2026-05-29: Centered modal dialogs in review apps and dashboard dictionary

### Summary

All Bootstrap modal dialogs in the ME, BF, and EL review apps and the Dashboard dictionary-management view now render vertically centered (`modal-dialog-centered`) instead of pinned to the top of the viewport.

### Behavior

- Previously the review-app modals (`#planonModal`, `#infoModal`, `#confirmModal`) and the dictionary `#deleteModal` used a plain `<div class="modal-dialog">`, so Bootstrap positioned them near the top of the scroll area.
- When a review app runs embedded in the Dashboard iframe, that top-aligned position tucked the dialog header under the Dashboard's sticky top bar (for example, the "Confirm Bulk Action" dialog was partially hidden).
- Each affected modal now uses `<div class="modal-dialog modal-dialog-centered">`, so the dialog sits in the vertical center of the iframe panel, clear of the top bar. The iframe panels are fixed height (`calc(100vh - 130px)`), so centering lands inside the visible area.
- This aligns the review apps and dictionary view with the SDI Process, main Dashboard, and Capture app, which already used `modal-dialog-centered`.
- Presentation-only change: no SQL, discipline, completeness, confidence, or SDI-sync behavior is affected.

### Files updated

- ME review template: `review/Asset_dasboard_browser_ME/review_asset_templates/dashboard.html`
- BF review template: `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html`
- EL review template: `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`
- Dashboard dictionary template: `Dashboard/templates/index_dictionary.html`
- Review rules: `Markdowns_documentation/rules/review_apps.rules.md`
- Dashboard rules: `Markdowns_documentation/rules/dashboard.rules.md`

## 2026-05-28: EL amperage reviewer override preservation

### Summary

EL review saves now preserve a reviewer-entered `Ampere` / `Amperage Rating` value when the editable `Ampere` field differs from the existing Planon-facing `Amperage Rating` alias.

### Behavior

- The EL amperage resolver now treats `Ampere` as the editable source of truth and `Amperage Rating` as the fallback alias.
- A stale `Amperage Rating` value can no longer overwrite a submitted `Ampere` value during save.
- A submitted blank `Ampere` value is now treated as an intentional clear and blanks both `Ampere` and `Amperage Rating`; it no longer falls back to the previous alias value.
- On save, both `Ampere` and `Amperage Rating` are still synchronized to the same numeric value and `Amperage Rating (UoM)` is still derived as `AMP` when present.
- This is the same class of issue as the EL `Supply From` cleanup bug: internal normalization overwrote a reviewer correction. The specific mechanism is alias precedence, not AI stale-output reprocessing.

### Files updated

- EL review/save: `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- Regression test: `test/test_el_amperage_alias.py`

## 2026-05-28: EL Distribution filter reset preserves building scope

### Summary

The `Reset` control in the EL Distribution review dashboard now clears table filters without dropping the currently selected building or leaving the `/review-distribution` page.

### Behavior

- Reset keeps the global building selector value by restoring both `building` and `filter_building` in the clean query string.
- Reset still clears QR, date, UBC tag, asset group, captured-by, approved, confidence, traffic-light, quick-view, and archive filters.
- The reload target is rebuilt from the current route, preserving `?embedded=true` when present and avoiding navigation through browser history.

### Files updated

- EL dashboard template: `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`

## 2026-05-28: EL Supply From reviewer override preservation

### Summary

EL review saves now preserve a human-entered `Supply From` value instead of always replacing it with the AI/extraction normalizer result.

### Behavior

- If a reviewer enters a non-empty `Supply From` value that differs from the normalized equipment ID, the JSON stores `supply_from_manual_override=1` and keeps the reviewer text.
- `Fed From Equipment ID` remains normalized from `Supply From` and is still used for parent amperage lookup / export alignment.
- EL DB sync no longer bulk-rewrites `sdi_dataset_EL."Supply From"` during derived-field maintenance; it updates `Fed From Equipment ID` separately.
- EL review saves refresh `completeness_score`, `confidence_scores`, and `Avg_ai_conf` metadata after reviewer edits so the AI checker does not classify the saved JSON as stale.
- The EL AI stale-output detector treats `modified=true`, `supply_from_manual_override=1`, and `volts_manual_override=1` JSON payloads as human-reviewed and does not reprocess them.
- ME and BF do not have the EL `Supply From` cleanup path. Their primary reviewer fields are still saved directly, with existing dictionary-derived behavior for Asset Group / Description unchanged.

### Files updated

- EL review/save: `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- EL extraction: `API/API_interface_EL_ver00.py`
- Review rules: `Markdowns_documentation/rules/review_apps.rules.md`

## 2026-05-27: Review dashboard bulk toggles and EL Distribution presentation

### Summary

The review dashboards were aligned so BF and EL have the same Manual / Approved header-checkbox bulk actions already used by ME. EL Distribution also received presentation-only cleanup for the listing table and shared shell behavior.

### Review dashboard bulk actions

- BF and EL New / Update / Manual tables now render `.select-all-manual` and `.select-all-approved` header checkboxes in the Manual and Approved columns.
- Bulk actions are client-side queues through existing endpoints only: `POST /toggle_sdi/<doc_id>`, `POST /toggle_approved/<doc_id>`, and `GET /check_sdi/<qr_code>`.
- Bulk Manual applies only to currently filtered DataTables rows whose Manual state differs from the target, and skips Approved rows.
- Bulk Approved applies only to currently filtered rows whose Approved state differs from the target. Unchecking Approved calls `/check_sdi/<qr_code>` first and skips exported / Planon-locked rows.
- BF header checkboxes render only when `can_edit` is true. EL keeps its existing endpoint-enforced permissions.
- Manual / Approved header labels were vertically centered with the checkbox using `.review-bulk-header` and table-header vertical alignment.

### EL Distribution presentation

- EL `dashboard.html` now includes `_shell.html`, matching ME and BF standalone sidebar/topbar behavior. The shared shell still suppresses itself inside iframe / embedded contexts.
- The "Review Electrical Assets - Distribution" listing view hides Amperage Rating, Volts, and Location from its New / Update / Manual DataTables. The hide is scoped to Distribution and is presentation-only; `review.html`, `/review-all`, JSON, DB state, SDI packaging, and Planon export still carry those fields.
- EL required-field checklist popovers now set `.el-required-popover` above the shared shell sidebar z-index so QR hover cards are not covered by the shell.
- EL review saves now preserve an explicit reviewer-entered `Volts` value with a `volts_manual_override` marker so tag-derived voltage defaults do not overwrite human edits during JSON save or `sdi_dataset_EL` sync.

### EL AI status stale JSON repair

- EL `ai_status=1` rows are only treated as processed when a current usable EL JSON exists. Stale JSON rows are reset to `ai_status=0` so the AI worker can refresh them under the current extraction rules.
- The EL processor no longer applies the existing-JSON skip to stale payloads. When `_existing_el_output_needs_rescore()` flags the JSON, extraction continues and rewrites the stale payload even when `EL_OVERWRITE_EXISTING_JSON=false`; current JSON files still keep the skip guard.
- Approved / SDI-locked EL rows are exempt from stale-JSON resets because the AI worker deliberately excludes approved rows from reprocessing.
- This prevents rows such as QR `0000184448` from repeatedly returning to `AI Status = FALSE` after an operator toggles them true.

### Files updated

- BF dashboard: `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html`
- EL dashboard: `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`
- EL review/save: `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- EL extraction: `API/API_interface_EL_ver00.py`

## 2026-05-25: Photo orientation EXIF transpose

### Summary

`save_image_file()` in `asset_capture_app_dev/app.py` now applies `PIL.ImageOps.exif_transpose()` immediately after `Image.open()`. Phone photos that arrive as landscape-pixel JPEGs with an EXIF Orientation tag (typically `Orientation: 6 - rotate 90 CW`) are now physically rotated to portrait before being written to `Capture_photos_upload/`, and the orientation tag is removed so downstream viewers do not double-rotate.

### Scope of change

- Single change in `save_image_file()`: `from PIL import Image, ImageOps`, then `img = ImageOps.exif_transpose(img)` before the existing save branches.
- Catches every upload path (camera modal, gallery pick, future API client) because all paths flow through `save_image_file()`.
- No client-side change; `capture.html` `ensurePortraitOrientation()` and `previewImage()` are unchanged.
- No backfill — historical files captured before 2026-05-25 remain in their original orientation on disk. Only new uploads are corrected.

### Why not client-side

- The browser's `img.width / img.height` does not auto-read EXIF, so the existing JS heuristic could not see the orientation hint.
- The gallery-pick path bypassed the JS orientation logic entirely.
- A server-side fix is browser-agnostic and authoritative for any future upload route.

### Files updated

- `asset_capture_app_dev/app.py` (`save_image_file()`)

## 2026-05-21: Extra Photo slot (ME, BF, EL)

### Summary

Each discipline now exposes one optional **Extra Photo** capture slot: ME `-4`, BF `-3`, EL `-3`. The slot is visible in capture and review but excluded from completeness, AI confidence, AI extraction, and the "Missed Photo" count. In review dashboards it renders as a small `+1` chip next to the existing required-photo ratio in the Photo column.

### Capture app

- New tile "Extra Photo (Optional)" added to all four asset-type field lists in `capture.html`.
- The Extra Photo `<input type="file">` carries `data-optional="true"`; `updateCompletionState()` filters by `:not([data-optional="true"])` so the green "all required captured" toast fires once the required tiles are filled.
- Submit loop in `app.py` now iterates seqs `0..4`; missing files in any slot are skipped by the existing `continue` guard.
- `seq_to_label()` returns "Extra Photo" for the new index in every asset map.

### Review dashboards (ME / BF / EL)

- `SEQ_SHOW` (ME) / `ALL_SHOW` (EL) widened to include the new index. `SEQ_CHECK` / `REQUIRED` unchanged — the new slot never counts toward "Missed Photo".
- `IMG_NAME_RE` regexes widened from `[0-3]` (ME) / `[0-2]` (EL) to include the new index. BF was already `[0-3]`.
- Each item dict carries a new `Extra Photo` boolean populated by `find_image(qr, building, <extra-seq>)`.
- The Photo column cell adds `{% if item['Extra Photo'] %}<span class="v2-photo-extra-chip">+1</span>{% endif %}` in all three tab panes (New / Update / Manual). A new `.v2-photo-extra-chip` CSS rule was added to each dashboard stylesheet.
- The thumbnail-strip label map in `review.html` adds the Extra Photo label.
- Pagination preview's `label_map` was extended in the same way.

### AI extraction pipelines (`API/`)

- `FILENAME_PATTERN` regex widened: ME `[0-3]` → `[0-4]`, BF `[01]` → `[0-3]`, EL `[012]` → `[0-3]`. This lets discovery see the Extra Photo file and log it as `invalid_seq` rather than the noisier `name_mismatch`.
- `VALID_SUFFIXES` was deliberately **not** changed. The Extra Photo's sequence remains absent from each pipeline's `VALID_SUFFIXES`, so the file is discovered, logged, and skipped before being added to `info["images"]`. The LLM never sees it.
- ME's optional `role_map` got `"4": "Extra Photo"` entries in `_llm_multi_image()` and `_llm_multi_image_simple()` for log readability if discovery ever surfaces the file.

### Files updated

- Capture: `asset_capture_app_dev/app.py`, `asset_capture_app_dev/templates/capture.html`
- Review ME: `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py`, `review_asset_templates/dashboard.html`, `review_asset_templates/review.html`
- Review BF: `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py`, `review_asset_templates/dashboard.html`, `review_asset_templates/review.html`
- Review EL: `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`, `review_asset_templates/dashboard.html`, `review_asset_templates/review.html`
- AI: `API/API_interface_ME_ver00.py`, `API/API_interface_BF_ver00.py`, `API/API_interface_EL_ver00.py`

## 2026-03-31: EL amperage storage shift

### Summary

Electrical amperage is now stored canonically in `sdi_dataset_EL."Amperage Rating"`.
The legacy `sdi_dataset_EL."Ampere"` column remains in place as a compatibility mirror during the transition.

This phase does not rename the EL extractor JSON contract or the EL review form.
Those surfaces still use `Ampere`, and the EL sync layer maps the value into both database columns.

### Scope of change

- `sdi_dataset_EL."Amperage Rating"` is now the curated EL source of truth.
- `sdi_dataset_EL."Ampere"` is still populated with the same value for compatibility.
- EL JSON payloads still use `structured_data.Ampere`.
- EL review UI still shows and edits `Ampere`.
- SDI export now prefers the canonical EL amperage value and falls back safely to the legacy one.

### Amperage format rule

EL amperage values now store integer-only text with no unit suffix.

Examples:

- `225A` -> `225`
- `600 amps` -> `600`
- `1200 A` -> `1200`

The unit is handled separately by downstream fields such as `Amperage Rating (UoM)`.

### Implementation notes

#### EL review and DB sync

- Added EL amperage normalization in the EL review/sync service.
- Sync now derives one amperage value from:
  - `structured_data["Amperage Rating"]` if present
  - otherwise `structured_data["Ampere"]`
- Sync dual-writes:
  - `sdi_dataset_EL."Amperage Rating"`
  - `sdi_dataset_EL."Ampere"`

#### Database migration and backfill

- Added an idempotent migration path to ensure `sdi_dataset_EL."Amperage Rating"` exists.
- Backfilled blank `Amperage Rating` values from legacy `Ampere`.
- Added and populated `sdi_dataset_EL."Amperage Rating (UoM)"` using the rule:
  - `AMP` when `Amperage Rating` is present
  - blank when `Amperage Rating` is blank
- Normalized existing EL DB values so amperage is stored without `A` or other letter suffixes.

#### EL extraction

- EL extraction still returns the `Ampere` key.
- Shared amperage normalization now strips unit letters and keeps only the integer value.
- EL extraction prompt was updated to request integer-only amperage.

#### SDI export

- EL rows are normalized before export so `Amperage Rating` is the active SDI Process field.
- SDI Process package handling now uses `Amperage Rating` as its internal amperage column.
- SDI Process now carries `Amperage Rating (UoM)` in package tables and uses the stored value for template fill.
- Package tables keep compatibility with older `Ampere` data by backfilling `Amperage Rating` where needed.

### Files updated

- `API/validators_shared.py`
- `API/API_interface_EL_ver00.py`
- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `01_GLOBAL_RULES.md`
- `UBC_Asset_Capture_Application_Documentation.md`
- `UBC_Asset_Capture_Application_Server_Documentation.md`
- `UBC_Asset_Capture_Application_Technical_Documentation.md`
- `review/.agent/AGENT.md`
- `asset_capture_app_dev/.agent/QR_codes_db_schema.md`

### Data updates applied

#### SQLite database

- Updated live EL rows in `asset_capture_app_dev/data/QR_codes.db`
- Backfilled `Amperage Rating` from `Ampere`
- Normalized EL amperage values to integer-only text

#### EL JSON payloads

- Normalized existing `Output_jason_api/*_EL_*.json` amperage values to integer-only text
- Kept `Ampere` as the active JSON field
- Wrote `Amperage Rating` where needed for compatibility with the new DB sync rule

### Backups created

- `asset_capture_app_dev/data/QR_codes.db.bak_20260331_1540`
- `asset_capture_app_dev/data/QR_codes.db.bak_20260331_1622_amperage_shift`
- `asset_capture_app_dev/data/QR_codes.bak_20260331_160514_amp_numeric.db`
- `Output_jason_api_backup_20260331_160514_amp_numeric/`

### Validation completed

- Python compile checks passed for the updated EL/API/export modules.
- `sdi_dataset_EL` row count remained unchanged.
- Existing EL amperage values were copied into `Amperage Rating`.
- EL DB rows with letter suffixes in amperage fields: `0`
- EL JSON payloads with letter suffixes in amperage fields: `0`

### Compatibility notes

- `Ampere` is still the visible EL review field.
- `Ampere` is still the active EL extraction JSON key.
- `Amperage Rating` is now the canonical curated DB field.
- Both DB columns currently store the same integer-only value.

### Follow-up options

- Full EL field rename from `Ampere` to `Amperage Rating` in JSON and UI
- Removal of the `Ampere` compatibility mirror after all downstream consumers are updated
- Additional audit for any external integrations that may still expect values like `225A`

## 2026-04-01: Production-safe SQLite amperage migration script

### Summary

Added a standalone SQLite migration script so production can be backfilled immediately after deployment, without waiting for EL review traffic or SDI package flows to touch the rows.

### Script

- `scripts/backfill_amperage_columns_sqlite.py`

### What it does

- Creates a consistent SQLite backup before making changes
- Ensures these columns exist where needed:
  - `Equipment ID`
  - `Equipment Type`
  - `Amperage Rating`
  - `Amperage Rating (UoM)`
- Backfills `Equipment ID` from the curated UBC tag source
- Backfills `Equipment Type` from `Equipment ID` or the UBC tag source using the shared EL prefix mapping
- Applies the current amperage normalization rule:
  - keep only the integer part of amperage values
- Backfills and synchronizes these tables:
  - `sdi_dataset_EL`
  - `sdi_print_out`
  - `sdi_print_out_arch`
- Sets `Amperage Rating (UoM)` to `AMP` when `Amperage Rating` is populated, else blank
- Mirrors the normalized value into legacy `Ampere` where that column exists

### Intended use

- Run once on the deployed SQLite database after the new application code is in place
- Recommended first step:
  - `python scripts/backfill_amperage_columns_sqlite.py --db /path/to/QR_codes.db --dry-run`
- Apply:
  - `python scripts/backfill_amperage_columns_sqlite.py --db /path/to/QR_codes.db`

### Notes

- The script is idempotent and can be re-run safely
- It is intended as the immediate production backfill step so the DB is fully updated even before users touch the EL review or SDI package routes

## 2026-04-01: EL Equipment ID source shift

### Summary

`sdi_dataset_EL."Equipment ID"` is now the canonical EL source for Planon `Equipment ID`.
The value remains auto-derived from the curated EL tag source rather than being user-entered.

### Implementation notes

- EL review/DB sync now writes `Equipment ID` into `sdi_dataset_EL` on every sync.
- The current derivation rule is preserved:
  - `Equipment ID` is copied from the curated EL `UBC Asset Tag` value.
- SDI package tables now carry `Equipment ID` so the value survives package creation, archive, and retrieval.
- Planon export now reads stored `Equipment ID` first and falls back to legacy `UBC Tag` only when the canonical field is blank.
- The SQLite migration script now backfills `Equipment ID` in `sdi_dataset_EL`, `sdi_print_out`, and `sdi_print_out_arch`.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

## 2026-04-02: EL `Fed From Amperage Rating (UoM)` standardization

### Summary

`Fed From Amperage Rating (UoM)` is now stored as a derived standard field instead of remaining unmapped.

### Rule

- If `Fed From Amperage Rating` has a value:
  - set `Fed From Amperage Rating (UoM) = A`
- If `Fed From Amperage Rating` is blank:
  - keep `Fed From Amperage Rating (UoM)` blank

### Scope

- EL DB sync in `sdi_dataset_EL`
- EL historical backfill in `sdi_dataset_EL`
- SDI package tables:
  - `sdi_print_out`
  - `sdi_print_out_arch`
- Planon export/template fill from the stored field
- EL review form display as a read-only `UoM` field beside `Fed From Amp Rating`

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `review/Asset_dashboard_browser_EL/review_asset_templates/review.html`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

## 2026-04-07: SDI carry-through for EL `Power Rating`

### Summary

The SDI process now carries stored EL `Power Rating` and `Power Rating (UoM)` values from `sdi_dataset_EL` into package tables and the export template.

### Rule

- Source of truth is `sdi_dataset_EL`
- SDI package tables preserve:
  - `Power Rating`
  - `Power Rating (UoM)`
- Export uses the stored DB values directly
- If `Power Rating` is blank, `Power Rating (UoM)` stays blank
- No SDI-side derivation or inference is added

### Scope

- SDI package creation
- `sdi_print_out`
- `sdi_print_out_arch`
- SDI export/template fill
- SQLite migration/backfill script for existing package rows

### Files updated

- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

## 2026-04-07: EL `Power Type` source-of-truth shift

### Summary

`Power Type` is now treated as a stored EL field instead of existing only as an SDI export-time derived value.

### Rule

- Canonical stored value is the short system code:
  - `N`
  - `E`
  - `S`
  - `NE`
  - `ES`
  - `NS`
  - `NES`
- Derive `Power Type` from:
  - `Equipment ID`
  - fallback `UBC Asset Tag`
- Export uses the stored field first and only falls back to parsing `Equipment ID` when the stored value is blank.

### Scope

- EL DB sync in `sdi_dataset_EL`
- EL historical backfill in `sdi_dataset_EL`
- SDI package tables:
  - `sdi_print_out`
  - `sdi_print_out_arch`
- Planon export/template fill from the stored field

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

### Additional UI update

- The EL review form now shows `Power Type` in the `Technical Details` section as a read-only derived field.
- The displayed value comes from the same canonical short-code logic stored in `sdi_dataset_EL`.

### Review UI

- The EL review form now shows `Fed From Amperage Rating` in the `Technical Details` section.
- The field is read-only because it is DB-derived from the same-building `Supply From -> UBC Asset Tag` lookup.
- Review load and save both recompute the displayed value so it stays aligned with the current `Supply From` value.

### Additional file updated

- `review/Asset_dashboard_browser_EL/review_asset_templates/review.html`
- `UBC_Asset_Capture_Application_Technical_Documentation.md`
- `asset_capture_app_dev/.agent/QR_codes_db_schema.md`

### Compatibility notes

- The EL review UI still does not expose `Equipment ID` as an editable field.
- Users continue to edit `UBC Asset Tag`; `Equipment ID` is derived in the backend.
- The existing `Power Type` and `Equipment Type` parsing logic is unchanged and still uses `Equipment ID` as its input.

### Local migration and validation

- Updated local SQLite DB: `asset_capture_app_dev/data/QR_codes.db`
- Backup created: `asset_capture_app_dev/data/QR_codes.bak_20260401_094215_amperage_columns_migration.db`
- Post-backfill counts:
  - `sdi_dataset_EL`: `176` total, `171` nonblank `Equipment ID`
  - `sdi_print_out`: `16` total, `0` nonblank `Equipment ID`
  - `sdi_print_out_arch`: `388` total, `141` nonblank `Equipment ID` (matching current archived electrical rows)

## 2026-04-01: EL Equipment Type source shift

### Summary

`sdi_dataset_EL."Equipment Type"` is now the canonical EL source for Planon `Equipment Type`.
The value remains auto-derived from the EL tag pattern rather than being user-entered.

### Implementation notes

- EL review/DB sync now writes `Equipment Type` into `sdi_dataset_EL` on every sync.
- The current derivation rule is preserved and shared across EL sync, package backfill, and Planon export:
  - `MDP` -> `Main Distribution Panel`
  - `CDP` -> `Central Distribution Panel`
  - `SPL` -> `Splitter`
  - `MCC` -> `Motor Control Center`
  - `PNL` -> `Panel`
  - `SWBD` -> `Switchboard`
  - `ATS` -> `Automatic Transfer Switch`
  - `TX` -> `Transformer`
- SDI package tables now carry `Equipment Type` so the value survives package creation, archive, and retrieval.
- Planon export now reads stored `Equipment Type` first and falls back to parsing `Equipment ID` only when the canonical field is blank.
- The SQLite migration script now backfills `Equipment Type` in `sdi_dataset_EL`, `sdi_print_out`, and `sdi_print_out_arch`.

### Files updated

- `electrical_equipment_rules.py`
- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`
- `UBC_Asset_Capture_Application_Technical_Documentation.md`
- `asset_capture_app_dev/.agent/QR_codes_db_schema.md`

### Local migration and validation

- Updated local SQLite DB: `asset_capture_app_dev/data/QR_codes.db`
- Backup created: `asset_capture_app_dev/data/QR_codes.bak_20260401_134004_amperage_columns_migration.db`
- Post-backfill counts:
  - `sdi_dataset_EL`: `176` total, `171` nonblank `Equipment ID`, `163` nonblank `Equipment Type`
  - `sdi_print_out`: `16` total, `0` nonblank `Equipment ID`, `0` nonblank `Equipment Type`
  - `sdi_print_out_arch`: `388` total, `141` nonblank `Equipment ID`, `139` nonblank `Equipment Type`

## 2026-04-01: EL Power Rating capture and DB sync

### Summary

EL extraction now captures `Power Rating` and `Power Rating (UoM)` from the AI output and syncs both fields into `sdi_dataset_EL`.

Update:
- As of `2026-04-11`, EL AI extraction keeps these fields only for transformer tags with explicit proof text.
- Panel assets now save both fields blank.
- See `2026-04-11: EL transformer-only power rating proof rule`.

The intended capture rule is strict:
- the unit must be `KVA`, `KW`, or `VA`
- the unit must be immediately preceded or followed by a whole number
- voltage text such as `600V`, `208Y/120V`, or `600V-208Y/120V` must be ignored

### Implementation notes

- Extended the EL API extraction schema to include:
  - `Power Rating`
  - `Power Rating (UoM)`
- Added shared normalization so inputs like `75KVA`, `75 KVA`, and `KVA 75` normalize to:
  - `Power Rating = 75`
  - `Power Rating (UoM) = KVA`
- Updated the EL extraction prompt to treat image `-1` as the primary source and image `-0` as the fallback source for power rating.
- Updated the EL completeness-guard rescore logic so older EL JSON outputs missing the new fields are treated as stale and can be refreshed on rerun.
- Updated the EL review/save path so `Power Rating` fields are preserved even though they are not yet exposed in the review form.
- Updated EL DB sync so `sdi_dataset_EL` now stores:
  - `Power Rating`
  - `Power Rating (UoM)`

### Files updated

- `API/API_interface_EL_ver00.py`
- `API/validators_shared.py`

## 2026-04-13: EL dashboard attribute-status traffic-light indicator and filter

### Summary

The EL dashboard now shows a traffic-light attribute-status indicator beside each QR code value, based on the required-fields checklist already derived from `sdi_dataset_EL`. The filter area also now includes a custom status filter that uses color balls instead of text labels for the individual status options.

### Rule

- Traffic-light status is derived from the existing EL required-fields checklist:
  - `green`: `0` missing required fields
  - `yellow`: `1` to `2` missing required fields
  - `red`: `3+` missing required fields
- The traffic-light indicator is rendered beside each QR code value.
- The filter control is now labeled `Attribute Status`.
- The filter dropdown options behave as follows:
  - `All Statuses`: text label
  - `Green`: green status ball
  - `Yellow`: yellow status ball
  - `Red`: red status ball
- The yellow palette for this EL status UI uses `#d8f73b`.

### Implementation notes

- The backend checklist payload now includes:
  - `missing_count`
  - `traffic_light`
  - `traffic_light_label`
- The QR-code checklist popover and the inline traffic-light indicator both use the same backend checklist payload so the status logic is not duplicated in the browser.
- Each EL table row stores the normalized traffic-light value in a row attribute so DataTables can filter without parsing icon colors or visible labels.
- The filter uses a custom dropdown UI backed by a hidden select, so the existing client-side filtering, query-string persistence, reset behavior, and review-link propagation continue to work.
- The traffic filter UI is synchronized on:
  - initial page load
  - tab switches
  - filter reset
  - DataTables redraws

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`
- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `UBC_Asset_Capture_Application_Technical_Documentation.md`
- `asset_capture_app_dev/.agent/QR_codes_db_schema.md`

### Review UI

- The EL review form now exposes editable fields in the `Technical Details` section for:
  - `Power Rating`
  - `Power Rating (UoM)`
- The form uses the existing field names, so review saves continue to pass through the same normalization logic.
- Inputs like `75KVA`, `75 KVA`, or `75` + `KVA` normalize on save to:
  - `Power Rating = 75`
  - `Power Rating (UoM) = KVA`

### Additional file updated

- `review/Asset_dashboard_browser_EL/review_asset_templates/review.html`

## 2026-04-02: EL Location blank-preserve rule

### Summary

The EL review/dashboard flow no longer auto-fills `Location` from the `UBC Asset Tag` or `Branch Panel` when the field is blank.

### Rule

- If `Location` already has a value, it is kept unless the reviewer edits it.
- If `Location` is blank, it now stays blank.
- The tag dictionary still auto-derives `Volts`, but it no longer backfills `Location`.
- Legacy auto-derived `Location` values are cleared when they exactly match the old tag-derived location, so stored values like `Level 0`, `Level 1`, or `Level 2` do not continue to appear after the rule change.

### Scope

- Review dashboard load
- Review save path
- EL DB sync into `sdi_dataset_EL`

### File updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`

## 2026-04-02: EL explicit-only amperage extraction rule

### Summary

EL AI extraction now keeps `Ampere` only when amperage is explicitly printed in the source image.

Transformer current must no longer be inferred from values like `75KVA`, `600V`, or `208Y/120V`.

### Rule

- `Ampere` must be visibly printed with an amperage unit or amperage label.
- The EL prompt now requires an exact evidence snippet in `Ampere Source Text` during parsing.
- Post-validation keeps the amperage only when that evidence, or OCR context, contains the same whole-number amp value with an amp unit/label.
- If the model only sees transformer specs and calculates a likely current, the saved `Ampere` is cleared.
- Manual review edits are not blocked by this change; the strict rule applies to AI extraction output.

### Related fixes

- Corrected the EL multimodal image labels so the API now treats:
  - `EL-0` as `Asset Plate/Label`
  - `EL-1` as `UBC Asset Tag`
  - `EL-2` as `Panel Schedule`
- OCR context now focuses on `EL-0` and `EL-1`, instead of the old mismatched `EL-1` and `EL-2`.
- Added an EL extraction rule version so older JSON payloads are treated as stale when intentionally rerun.
- Targeted reruns with `--qr <QR_CODE>` now bypass the processed-QR skip, which allows refreshing an existing EL asset after rule changes.

### Files updated

- `API/API_interface_EL_ver00.py`
- `API/validators_shared.py`

## 2026-04-02: EL transformer volts normalization

### Summary

EL voltage normalization now preserves transformer primary-secondary notation instead of collapsing it to the first voltage token.

### Rule

- Transformer-style values are now normalized to the full primary-secondary format:
  - `600V-208Y/120V` -> `600V-208Y/120V`
  - `600-208Y/120V` -> `600V-208Y/120V`
  - `600 DELTA-208Y/120V` -> `600V-208Y/120V`
  - `600 DELTA 208Y/120V` -> `600V-208Y/120V`
- Secondary-only Wye notation is also preserved:
  - `208Y/120V` -> `208Y/120V`
- Existing panel notation remains unchanged:
  - `208/120V`
  - `480/277V`
  - `600/347V`

### Related notes

- The EL extraction prompt now explicitly allows transformer voltage notation like `600V-208Y/120V`.
- The EL extraction rule version was bumped so targeted reruns can refresh older transformer JSON payloads that were previously normalized to `600V`.

### Files updated

- `API/API_interface_EL_ver00.py`
- `API/validators_shared.py`

## 2026-04-02: EL `Fed From Amperage Rating` DB-derived mapping

### Summary

`Fed From Amperage Rating` is now derived from curated EL data already stored in `sdi_dataset_EL` and is no longer left unmapped.

### Rule

- For each EL row, use:
  - same `Building`
  - current row `Supply From`
  - match against another EL row `UBC Asset Tag`
- If a match is found:
  - copy the matched row `Amperage Rating` into `Fed From Amperage Rating`
- If no match is found:
  - keep `Fed From Amperage Rating` blank

### Scope

- EL DB sync in `sdi_dataset_EL`
- EL historical backfill in `sdi_dataset_EL`
- SDI package tables:
  - `sdi_print_out`
  - `sdi_print_out_arch`
- Planon export/template fill from the stored field

### Related notes

- The lookup is exact after trim/case normalization.
- Matching is restricted to the same building.
- The SDI export now carries the stored `Fed From Amperage Rating` field directly; it is no longer something that would need to be recomputed at the final spreadsheet step.
- The SQLite migration script now backfills this field for existing databases as well.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

## 2026-04-07: EL `Voltage Rating` source-of-truth shift

### Summary

`Voltage Rating` is now the canonical stored EL voltage field for SDI export. The legacy `Volts` field remains as a compatibility mirror during the transition.

### Rule

- EL sync resolves voltage using the current volts logic:
  - start from structured `Volts`
  - keep the current dictionary/tag volts override unchanged
- The resolved value is dual-written to:
  - `Voltage Rating`
  - `Volts`
- `Voltage Rating (UoM)` is stored as:
  - `VLT` when `Voltage Rating` is nonblank
  - blank otherwise

### Scope

- EL DB sync in `sdi_dataset_EL`
- EL historical backfill in `sdi_dataset_EL`
- SDI package tables:
  - `sdi_print_out`
  - `sdi_print_out_arch`
- Planon export/template fill from stored:
  - `Voltage Rating`
  - `Voltage Rating (UoM)`

### Related notes

- SDI now prefers stored `Voltage Rating` and only falls back to `Volts` for legacy blanks.
- The SQLite migration script now ensures and backfills both voltage columns in EL and package tables.
- `Volts` is still preserved as a compatibility mirror and is not removed in this phase.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

## 2026-04-09: EL `Fed From Equipment ID` canonical shift

### Summary

`Fed From Equipment ID` is now the canonical stored/export field for the EL upstream source identifier. `Supply From` remains the editable EL working field and compatibility source during the transition.

### Rule

- EL sync derives:
  - `Fed From Equipment ID = Supply From`
- EL DB sync stores both:
  - `Supply From`
  - `Fed From Equipment ID`
- SDI export now prefers stored `Fed From Equipment ID`
- Legacy fallback remains:
  - if `Fed From Equipment ID` is blank, use `Supply From`

### Scope

- EL DB sync in `sdi_dataset_EL`
- EL historical backfill in `sdi_dataset_EL`
- SDI package tables:
  - `sdi_print_out`
  - `sdi_print_out_arch`
- Planon export/template fill from stored `Fed From Equipment ID`

### Related notes

- `Supply From` is not removed in this phase.
- `Fed From Amperage Rating` logic remains based on `Supply From`.
- No EL review UI rename is included in this phase.
- The SQLite migration script now ensures and backfills `Fed From Equipment ID` in EL and package tables.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `SDI_process/app.py`
- `scripts/backfill_amperage_columns_sqlite.py`

## 2026-04-09: EL tag, location, and panel-voltage extraction hardening

### Summary

The EL API now applies stricter source rules for `UBC Asset Tag`, `Location`, and panel `Volts` so panel outputs are derived from the intended image/source instead of nearby QR labels or warning text.

### Rule

- `UBC Asset Tag` must come from the printed panel identifier, not the blue `Asset Identification` QR sticker or its long numeric code.
- `Location` is EL-2-only and is accepted only when EL-2 shows an explicit labeled header such as:
  - `Location:`
  - `Room:`
  - `Space:`
  - `Area:`
  - `Level:`
  - `Floor:`
- Panel `Volts` now prefer the dictionary voltage code carried in the finalized `UBC Asset Tag`:
  - voltage code `2` -> `208/120V`
  - voltage code `6` -> `600/347V`
- If the finalized tag does not expose a recognized panel voltage code, the EL API keeps the AI-captured `Volts` value.

### Implementation notes

- Added EL post-processing so QR-like numeric identifiers are rejected for `UBC Asset Tag` and the printed panel tag is preferred.
- Added a seq-2 reread path for `Location` with internal `Location Source Text` validation.
- Panel-name fragments such as `NRM1` are no longer accepted as `Location`.
- Dictionary-derived panel voltage now overrides AI voltage when a recognized panel prefix and leading voltage code are present in the finalized tag.
- Older EL JSON outputs are treated as stale when stored `Volts` do not match the current dictionary-derived panel voltage.

### Files updated

- `API/API_interface_EL_ver00.py`
- `API/validators_shared.py`
- `dictionary/electrical.dictionary.py`

## 2026-04-10: EL OCR resilience and amperage alignment

### Summary

EL OCR now handles upside-down EL plates more reliably, and EL amperage post-processing stays aligned with the saved value.

### Rule

- When OCR is enabled for EL assets, the API now tries both:
  - original image orientation
  - 180-degree rotated orientation
- The stronger OCR result is used as the EL OCR context.
- When `EL_HYBRID_OCR_AGENT` is not explicitly set, OCR now runs by default whenever `EL_OCR_MODE` is not `off`.
- Valid panel amperage values like `225A` are no longer dropped because they exceed an old numeric ceiling.
- If the saved `Ampere` field is blank, EL confidence for `Ampere` is forced to `0`.

### Implementation notes

- Added OCR scoring/orientation selection for EL-0 and EL-1.
- This specifically improves panelboard plate reads where the plate photo is upside down.
- The OCR/default-runtime change applies only to the EL API extraction path; no EL review JSON schema changes were introduced.

### Files updated

- `API/API_interface_EL_ver00.py`

## 2026-04-11: EL transformer-only power rating proof rule

### Summary

EL `Power Rating` capture is now transformer-only and requires explicit proof text. This supersedes the earlier broad EL AI power-rating capture rule for panel assets.

### Rule

- Only transformer-style EL tags such as `TX-*` may keep:
  - `Power Rating`
  - `Power Rating (UoM)`
- Panel tags such as `PNL-*`, `CDP-*`, `MDP-*`, `MCC-*`, `ATS-*`, and `SPL-*` now save both fields blank.
- Valid transformer power-rating evidence must be an explicit positive whole-number adjacency pattern such as:
  - `75 KVA`
  - `KVA 75`
  - `15 KW`
  - `500 VA`
- Decimal/malformed voltage-adjacent text must not populate power rating, including:
  - `0.208 kVac`
  - `VAC`
  - `208Y/120V`
  - `600/347V`
  - `600V-208Y/120V`

### Implementation notes

- Added internal `Power Rating Source Text` to the EL AI extraction schema for proof validation.
- Transformer power rating is now validated against explicit source text first, with OCR context used as secondary support.
- Non-transformer EL outputs are now treated as stale when stored power-rating fields are nonblank.
- EL extraction rule version is now `14`.

### Files updated

- `API/API_interface_EL_ver00.py`
- `API/validators_shared.py`

## 2026-04-12: EL dashboard QR required-fields hover checklist

### Summary

The EL dashboard now shows a read-only required-fields checklist when users hover or focus the QR code value. The checklist is sourced directly from `sdi_dataset_EL` at page render time and indicates whether each required field is filled or blank for the current EL asset.

### Rule

- Every EL asset group checks `UBC Asset Tag`.
- Additional checks are applied by stored `Asset Group` from `sdi_dataset_EL`:
  - `Interior Distribution Transformers`:
    - `Power Rating`
    - `Power Rating (UoM)`
    - `Voltage Rating`
    - `Voltage Rating (UoM)`
    - `Equipment ID`
    - `Equipment Type`
    - `Fed From Amperage Rating`
    - `Fed From Amperage Rating (UoM)`
    - `Fed From Equipment ID`
    - `Power Type`
  - `Panels`:
    - `Amperage Rating`
    - `Amperage Rating (UoM)`
    - `Equipment ID`
    - `Voltage Rating`
    - `Voltage Rating (UoM)`
    - `Supply From`
    - `Equipment Type`
    - `Fed From Amperage Rating`
    - `Fed From Amperage Rating (UoM)`
    - `Fed From Equipment ID`
    - `Power Type`
  - `Other Service and Distribution`:
    - `Amperage Rating`
    - `Amperage Rating (UoM)`
    - `Equipment ID`
    - `Equipment Type`
    - `Fed From Equipment ID`
    - `Power Type`
    - `Voltage Rating`
    - `Voltage Rating (UoM)`
  - `Motor Control Centers`:
    - `Amperage Rating`
    - `Amperage Rating (UoM)`
    - `Equipment ID`
    - `Equipment Type`
    - `Fed From Amperage Rating`
    - `Fed From Amperage Rating (UoM)`
    - `Fed From Equipment ID`
    - `Power Type`
    - `Voltage Rating`
    - `Voltage Rating (UoM)`
  - `Automatic Transfer Switches`:
    - `Amperage Rating`
    - `Amperage Rating (UoM)`
    - `Equipment ID`
    - `Equipment Type`
    - `Fed From Amperage Rating`
    - `Fed From Equipment ID`
    - `Power Type`
    - `Voltage Rating`
    - `Voltage Rating (UoM)`
- A field is marked filled only when the stored `sdi_dataset_EL` value is not `NULL`, not empty, and not whitespace after trimming.
- Asset groups without an explicit checklist rule currently show only the universal `UBC Asset Tag` check.

### Implementation notes

- The EL dashboard now bulk-reads the required checklist columns from `sdi_dataset_EL` once per page render for all visible QR codes.
- The checklist payload is attached to each rendered EL row before `dashboard.html` is built.
- The QR code cell is now the hover/focus trigger and opens a Bootstrap popover.
- Each checklist line shows:
  - green `check-circle` when the field is filled
  - red `x-circle` when the field is blank
- If no matching `sdi_dataset_EL` row exists for a QR, the popover still opens, shows a short missing-row note, and marks the universal `UBC Asset Tag` check as missing.
- Popovers are re-initialized after DataTables redraws so the checklist keeps working after sort, filter, or pagination changes.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`

## 2026-04-13: EL `Supply From` upstream-ID normalization

### Summary

EL `Supply From` extraction now keeps the upstream equipment identifier only, instead of saving the full descriptive phrase from the label.

### Rule

- `Supply From` is normalized from EL `Fed`, `Fed From`, or `Supply From` text.
- The normalized value should be the upstream equipment identifier only.
- Examples:
  - `FED FROM MDC IN MAIN ELEC. RM` -> `MDC`
  - `CDP-2N1 FED FROM MDC IN MAIN ELEC. RM` -> `MDC`
  - `FED FROM PANEL 2N0D1` -> `2N0D1`
  - `FED FROM CDP 2N1` -> `CDP-2N1`
  - `FROM TX-N0N1` -> `TX-N0N1`
- Generic descriptive words such as `PANEL`, `PANELBOARD`, `ROOM`, `RM`, `SPACE`, `AREA`, `LEVEL`, `FLOOR`, `MAIN`, and `ELEC` are not preserved as the final `Supply From` value.
- When a real equipment prefix is part of the upstream identifier, it is preserved.

### Implementation notes

- Tightened the shared `normalize_supply_from()` rule so it:
  - anchors to the `FED FROM` / `FROM` phrase when present anywhere in the captured text
  - strips trailing room/location wording
  - converts common equipment phrases into a canonical upstream identifier
- Updated the EL extraction prompt so the model is instructed to return only the upstream equipment identifier for `Fed` / `Fed From`.
- EL extraction rule version is now `15`, so older EL JSON outputs are treated as stale on rerun.

### Files updated

- `API/API_interface_EL_ver00.py`
- `API/validators_shared.py`

## 2026-04-13: EL `Fed From Amperage Rating` blank-clear rule and live refresh

### Summary

`Fed From Amperage Rating` and `Fed From Amperage Rating (UoM)` are now cleared whenever `Supply From` is blank, so stale upstream values do not remain attached to EL rows or SDI package rows. The EL review form also now refreshes the derived `Fed From` values immediately when reviewers change `Supply From`.

### Rule

- If `Supply From` is blank:
  - set `Fed From Amperage Rating` blank
  - set `Fed From Amperage Rating (UoM)` blank
- If `Supply From` has a value:
  - derive `Fed From Amperage Rating` from the same-building `UBC Asset Tag` match
  - derive `Fed From Amperage Rating (UoM)` from the resolved amperage value
- The EL review form now re-runs the upstream lookup on `Supply From` change so the displayed `Fed From` values stay aligned before save.

### Scope

- EL derived-field cleanup/backfill in `sdi_dataset_EL`
- SDI package tables:
  - `sdi_print_out`
  - `sdi_print_out_arch`
- EL review form live refresh for derived `Fed From` values

### Implementation notes

- The EL and SDI backfill paths now skip the upstream lookup when `Supply From` is blank instead of leaving older derived amperage values in place.
- Existing rows with blank `Supply From` now have stale `Fed From Amperage Rating` and `Fed From Amperage Rating (UoM)` cleared.
- Added an authenticated EL lookup endpoint for the review UI:
  - `/api/fed_from_lookup/<building>/<supply_from>`
- The review form uses that endpoint on `Supply From` change to refresh:
  - `Fed From Amperage Rating`
  - `Fed From Amperage Rating (UoM)`

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `review/Asset_dashboard_browser_EL/review_asset_templates/review.html`
- `SDI_process/app.py`
- `run_db_cleanup.py`

## 2026-05-14: Review-app listing-page column visibility trim

### Summary

Each Review-app listing page ("Asset Review Dashboard - Mechanical / Backflow / Electrical") now hides a fixed set of nameplate columns from the New / Update / Manual tab tables. This is a presentation-only change — no schema, JSON, SDI, Planon, AI, or audit-trail behavior is affected.

### Columns hidden on the listing tables

- **ME** — `Manufacturer`, `Model`, `Serial Number`, `Technical Safety BC` (DataTables column indices 6, 7, 8, 10).
- **BF** — `Type`, `Model`, `Serial Number` (indices 5, 7, 8).
- **EL** — `Amperage Rating`, `Volts`, `Location` (indices 7, 8, 9).

### Scope of change

- Applies to `review_asset_templates/dashboard.html` in each of the three review apps, on all three table tabs (New / Update / Manual) and in both standalone and Dashboard-portal-embedded contexts.
- The per-asset review page (`review.html`, opened via the per-row Review button) is intentionally untouched — reviewers still see every field when editing a single asset.
- All downstream consumers (`sdi_dataset`, `sdi_dataset_EL`, `sdi_print_out`, Planon export, AI extraction, audit trail, dashboard KPIs) continue to carry the full field set.

### Implementation notes

- Each `initAssetTable(...)` `DataTable({...})` config gains an `initComplete` callback:
  ```javascript
  initComplete: function () {
      this.api().columns([/* hidden indices */]).visible(false);
  }
  ```
- The `initComplete` path is used (rather than `columnDefs.visible: false`) so the hide reliably overrides any restored `stateSave` visibility from prior sessions where the columns were shown.
- The hide is unconditional. An earlier attempt gated it on `{% if g.embedded %}` (server-side) and on `window.self !== window.top` (client-side); both were dropped after the user confirmed they want the columns hidden everywhere the listing table is shown.
- Related auth-flow note: the review apps' `login.html` form action does not preserve the `next` query parameter on POST, so any URL-flag-gated server-side approach would fail after a fresh-login redirect anyway. See `rules/review_apps.rules.md` "Known auth-flow gotcha".

### Files updated

- `review/Asset_dasboard_browser_ME/review_asset_templates/dashboard.html`
- `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html`
- `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`
- `Markdowns_documentation/rules/review_apps.rules.md` (new "Listing-Page Column Visibility Rules" section + "Known auth-flow gotcha" subsection)

## 2026-05-14: Review-app Avg AI Conf gauge + new Comp Score column

### Summary

The "Avg AI Conf" column on the review-app listing tables (ME, BF, EL) now renders as a green gradient capsule gauge with a triangle marker at the row's percentage and the numeric value below, replacing the prior single-tone pill and earlier five-color segmented bar. A new **"Comp Score"** column is inserted immediately after Avg AI Conf, rendering the same gauge but sourced from each asset's completeness score. No schema, JSON, SDI, Planon, AI extraction, or audit-trail behavior is affected -- both fields already existed; only the rendering and the column slot are new.

### Avg AI Conf gauge

- Same data source as before (`Avg_ai_conf` on the item dict, populated from `_normalize_avg_ai_conf(_extract_avg_ai_conf(raw))`).
- Visualization changed from a single colored pill (low / medium / high based on `<70`, `70-80`, `>=80`) to a bordered green capsule gauge with a triangle marker at the row's percentage.
- The capsule uses `linear-gradient(to right, #dcfce7 0%, #16a34a 100%)`, so higher percentages sit farther into the darker green side.
- The capsule has `border: 1px solid rgba(21, 128, 61, 0.45)` and `box-sizing: border-box` to keep the border inside the 12px bar height.
- The legacy `.avg-ai-conf-pill` CSS rules remain in each `dashboard.html` (orphaned, no markup references them anymore). They are a candidate for a later cleanup pass.

### Comp Score column

- New column inserted immediately after "Avg AI Conf" on the listing tables of all three review apps.
- Header label: `Comp Score`.
- Value pulled from `raw["completeness_score"]` (the top-level field that `API/API_interface_*_ver00.py` writes during AI extraction).
- Normalized through the existing `_normalize_avg_ai_conf()` helper (reused as a generic 0-100 → "XX,YY%" formatter, despite its name).
- Renders with the same bordered green capsule gauge component as Avg AI Conf.
- For assets whose JSON predates the completeness writeback, the cell shows `N/A`; re-running `./run_ai_and_sync.sh <discipline> <qr_code>` populates the field.

### DataTables column-index impact

The Comp Score insertion shifted indices for every column AFTER Avg AI Conf. Updated index variables in each `dashboard.html`:

- **ME**: `colApproved` 17 → 18; `colAction` 19 → 20.
- **BF**: `colApproved` 16 → 17; `colAction` 18 → 19.
- **EL**: `colAssetGroup` 12 → 13; `colApproved` 14 → 15; `colAction` 16 → 17.

The hidden-column index lists from the listing-page column-visibility rule are all BEFORE the new column (`[6, 7, 8, 10]` for ME, `[5, 7, 8]` for BF, `[7, 8, 9]` for EL) and were left unchanged.

### Files updated

- `review/Asset_dasboard_browser_ME/asset_plate_reviewer.py` (item dict gains `Comp_score` / `Comp_score_display`)
- `review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py` (same)
- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py` (same)
- `review/Asset_dasboard_browser_ME/review_asset_templates/dashboard.html` (green gradient capsule CSS + border + new `<th>` and `<td>` per tab + index bumps)
- `review/Asset_dasboard_browser_BF/review_asset_templates/dashboard.html` (same)
- `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html` (same)
- `Markdowns_documentation/rules/review_apps.rules.md` (new "Confidence and Completeness Gauge Rules" section)

## 2026-06-12: EL `Fed From Amperage Rating` sourced from the SLD

### Summary

`Fed From Amperage Rating` is no longer derived from sibling captured assets in `sdi_dataset_EL`. It is now looked up from the SLD diagram table (`electrical_building_schema`). If the building has no active SLD data, the field stays blank.

### Rule

- If `Supply From` has a value:
  - match the normalized tag against `electrical_building_schema."Equipment ID"` in the same `Building` (case-insensitive trim on both sides), restricted to active rows (`new_draw = 'TRUE'`)
  - copy the matched SLD row's `Amperage Rating` into `Fed From Amperage Rating` (no Power Rating fallback)
  - derive `Fed From Amperage Rating (UoM)` = `A` when a value resolves, blank otherwise (unchanged)
- If `Supply From` is blank, or no active SLD row matches:
  - keep `Fed From Amperage Rating` and `Fed From Amperage Rating (UoM)` blank
- There is no fallback to `sdi_dataset_EL` parent rows.

### Required-fields checklist impact

- The dashboard QR hover checklist now treats `Fed From Amperage Rating` and `Fed From Amperage Rating (UoM)` as required **only when the asset's building has active SLD data** (`electrical_building_schema` rows with `new_draw = 'TRUE'`).
- Buildings without SLD data exclude both fields from the checklist and counts; the popover shows a note: "No SLD loaded for this building — Fed From Amperage checks skipped."
- Payload gains a `sld_available` flag (`_build_el_required_fields_payload`).

### Review UI

- The read-only `Fed From Amp Rating` empty-state placeholder changed from `Not linked yet` to `No SLD data` (page render, Supply From clear handler, and `/api/fed_from_lookup` refresh path).
- The live `/api/fed_from_lookup/<building>/<supply_from>` endpoint now resolves against the SLD automatically (shared resolver).

### Scope

- EL review load/save, approve toggle, JSON→DB sync, and the `sdi_dataset_EL` bulk backfill (`_ensure_el_fed_from_amperage_column`) all resolve via the SLD now.
- SDI package backfill (`SDI_process/app.py` `_ensure_package_amperage_columns`, applied to `sdi_print_out` / `sdi_print_out_arch`) also derives from the SLD; the legacy in-table `UBC Tag` self-join fallback was removed (when the SLD table is absent, stored values are left untouched).
- `scripts/normalize_el_supply_from_persistence.py` builds its parent amperage map from `electrical_building_schema` (active rows) instead of `sdi_dataset_EL`, so re-running it preserves SLD-derived values.
- `scripts/backfill_amperage_columns_sqlite.py` (frozen SQLite rollback) intentionally unchanged.

### Files updated

- `review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py`
- `review/Asset_dashboard_browser_EL/review_asset_templates/review.html`
- `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html`
- `SDI_process/app.py`
- `scripts/normalize_el_supply_from_persistence.py`
- `Markdowns_documentation/response_schema_notes.md`

## 2026-06-12: SLD xlsx export — amperage warning highlight, column layout, and embedded Expand-All diagram

### Summary

The SLD Excel report (`GET /sld/api/download-xlsx`) uses a 13-column layout that separates own-asset data (navy headers) from fed-from (parent) asset data (beige headers), highlights unsafe amperage comparisons, hides worksheet gridlines, embeds the UBC Facilities logo at the top-left corner, and embeds a picture of the single line diagram in "Expand All" mode below the last table row.

### Column layout

- Own asset (navy `002145`): `Hierarchy`, `Equip QR Code`, `Equipment ID`, `Room`, `Voltage`, `Amperage`, `Power`, `Power Type` (new)
- Fed-from asset (beige `FCD5B4`): `Fed QR Code`, `Fed From`, `Fed From Amp Rating` (new), `Power` (new — the feeding asset's power rating)
- Comparison: when the row's `Amperage` is greater than the feeding asset's amperage, the row's `Amperage` cell is red-highlighted. No separate flag column is exported.
- `Check` (last, unchanged ✓/✗ semantics)
- Update (same day): the `Wire` column was dropped from the export, and the UBC Facilities logo (`ubc-facilities_logo.jpg` — the same one the page and the review xlsx export use) is embedded at A1 via `excel_export._embed_corner_logo`.
- Update (same day): the embedded Expand-All diagram now carries the page's legend strip composited above the tree (`composeLegendOntoDiagramPng` in `sld.js` reads the `#sld-legend-bar` items dynamically, so legend changes flow into the export automatically; best-effort — a composition failure embeds the legendless tree).

- Update 2026-06-30: the diagram PDF export now reuses that same legend compositor and places the legend + diagram image inside a rounded A4 landscape report board with the UBC Facilities logo, building name, and export date. The Switch Over table PDF path remains separate.
- Update 2026-06-30 (follow-up): the composed legend + diagram image is top-aligned inside the rounded report board, and the PDF footer prints the report user plus the exact creation timestamp.

### Rules

- The fed-from (parent) row is the same-building SLD row whose `Equipment ID` matches the asset's `Supply From` (whitespace-collapsed, case-insensitive). All four beige cells are blank when no parent exists.
- `Power Type` comes from the matched `sdi_dataset_EL` row's stored `Power Type` (respects human review overrides); blank when the SDI row has none. Added to `_enrich_asset_display_fields`, so all `get_all_assets` consumers now carry the key.
- Diagram embed: the browser captures the D3 SVG tree fully expanded (without painting the expansion on screen, persisting collapse state, or disturbing zoom), POSTs the PNG to `POST /sld/api/diagram-image`, and passes the returned single-use token to the download URL (`&diagram_token=`). The token cache is filesystem-backed (`<tempdir>/sld_xlsx_diagram_cache`, 120 s TTL) because the EL service runs multiple gunicorn workers.
- Best-effort: capture/upload/embed failures (stale cached `sld.js`, expired token, missing Pillow) fall back to the table-only spreadsheet — the download never blocks.
- The `Assets` worksheet sets `showGridLines=False`; table borders remain visible.

### Known divergence

- `exportSwiftTablePdf` (PDF of the Swift table) still renders the previous 11-column layout; the legacy client-side ExcelJS exporter (`exportSwiftExcelClientLegacy`, dead rollback code) is also unchanged.

### Files updated

- `review/Asset_dashboard_browser_EL/sld_blueprint.py` (enrichment, columns, diagram-image endpoint + embed)
- `review/Asset_dashboard_browser_EL/review_asset_templates/static/sld/sld.js` (shared capture helpers, expand-all capture, export wiring)
- `review/Asset_dashboard_browser_EL/review_asset_templates/dashboard.html` (sld.js cache-bust `?v=20260612-xlsx-diagram-embed`)
- `review/Asset_dashboard_browser_EL/requirements.txt` (`pillow>=10`)
## 2026-07-10: Review and SDI Installation Date flow

ME, BF, and EL review forms now read the optional QR-level `QR_codes.installation_date`, display it as `DD/MM/YYYY`, and let an editor update or clear it. Review saves validate a real date no later than today, store canonical `YYYY-MM-DD`, and audit changes against `QR_codes`; the value is deliberately excluded from extraction JSON and discipline completeness/confidence rules.

SDI package creation joins the QR-level value into `Installation Date`, preserves it through active/archive package transfers, and exports a valid value as `YYYY-MM-DD`. Missing or invalid legacy values export blank and never block packaging.

Same-day UI refinement: the field is labeled plain "Installation Date" (no "(optional)" suffix or DD/MM/YYYY helper note), typing applies an auto-slash `DD/MM/YYYY` mask, and a calendar button opens the native date picker (capped at today) to fill the field. EL renders it inside the Identity card, matching ME/BF. Markup/JS blocks are identical across the three review.html copies; the hidden native picker input is never submitted.

## 2026-07-21: SpaceUID fallback location seed

The live PostgreSQL `SpaceUID` table received three synthetic fallback locations for each of its 765 current distinct nonblank `Property.Property code` values:

- `Z01Rooftop` / `RT`
- `Z02Notfound_Room` / `NF`
- `Z03External_building` / `EB`

All three use `Space Name = -` and `Floor Name = Floor: -`. `Z02Notfound_Room` deliberately uses an underscore so QR autofill preserves the complete Space number. A same-day correction updated the 2,295 seeded `Space Name` values from `No Room Identification` to `-`, with one field-level audit entry per row. The idempotent owner-run migration `scripts/migrations/2026-07-21_spaceuid_special_locations.sql` inserts only missing keys, aborts on conflicting values or a changed 765-property precondition, validates one exact row per property/type, and writes five atomic `audit_trail` entries per inserted row. This is a one-time snapshot seed; it does not add a trigger for future property codes.

## 2026-07-23: Life Cycle Installation-Date-only completeness

The Life Cycle Assessment now uses **Installation Date as its only Complete / Incomplete criterion**. A row with an Installation Date moves to the Complete tab even when Make, Space Number, or Serial Number is missing; a row without an Installation Date remains Incomplete. The on-screen hints, missing-value highlighting, XLSX highlighting, and operating documentation follow the same rule.

The redundant **Missing field** filter was removed from the Incomplete tab in the same workstream. Since every Incomplete row is missing Installation Date by definition, the filter offered no additional distinction.
