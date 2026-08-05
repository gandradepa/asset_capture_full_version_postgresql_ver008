# Extraction API Rules

Current documentation refresh: 2026-08-03.

## Purpose

The extraction layer reads captured images, performs OCR and LLM extraction, writes JSON, and updates workflow state in PostgreSQL `qr_code_db` through the shared `db.py` layer. The SQLite `QR_codes.db` is rollback/reference only and is no longer part of live QR-code processes.

## Script Shape

- ME, BF, and EL extraction scripts use `Config` plus `AssetProcessor`.
- Use `ThreadPoolExecutor` with the script's configured `MAX_WORKERS`.
- Use structured schema validation before saving output.

## EL Legacy Extraction Gate (Buildings.Process)

Current documentation refresh: 2026-07-30.

- EL extraction is gated by `"Buildings"."Process"`, resolved per building before any model call (`_load_building_process_map` in `API_interface_EL_ver00.py`):
  - `Standard` → the untouched standard EL flow (byte-identical behavior to pre-gate).
  - `Legacy` → `_process_legacy_asset()`: verbatim-transcription prompt (`EL_LEGACY_PROMPT`) with the raw-preserving `ELLegacyStructuredExtraction` model (no normalizing validators — the standard model destroys legacy strings like `MAIN DIST. CTRE.` at parse time), then `legacy_flow.legacy_structured_from_raw()` for all rule-based structuring.
  - Blank/NULL `Process`, or DB unreachable → **skip the asset with a warning; never default to Standard** (fail-closed).
- Legacy JSON envelope keys: `"process": "Legacy"`, `"el_legacy_rule_version"`, `structured_data.label_text` (verbatim **lamacoid** transcription, EL-1), `structured_data.nameplate_text` (verbatim **manufacturer nameplate** transcription, EL-0; added 2026-08-04, rule v5 — kept in its own field so plate text can never contaminate the lamacoid parse), and `structured_data.rating_plausibility_flags` (parsed-but-rejected readings from the plausibility tripwire, 2026-08-05). `label_text`/`nameplate_text` enable rule-only regeneration without a new vision call.
- `review/Asset_dashboard_browser_EL/legacy_flow.py` is the single legacy rules module, shared by the extraction API (via importlib), the EL review app, and the SLD extractor. Never fork its logic into the API script.
- **Prompt and schema move together (extra="forbid").** `ELLegacyStructuredExtraction` / `ELLegacyConfidenceScores` forbid unknown keys: if `EL_LEGACY_PROMPT` asks for a field the models do not declare (e.g. `Nameplate Text`), every legacy QR fails to parse and returns `ERROR (Legacy extraction failed)`. Ship both in one commit.
- Scoring is transformer-aware: `_el_legacy_scoring_fields()` selects `EL_LEGACY_TRANSFORMER_SCORING_FIELDS` (`UBC Asset Tag`, `Volts`, `Power Rating`, `Power Rating (UoM)`) via `legacy_flow.is_legacy_transformer()`, else the flat 4-field set — without the split a transformer caps at 50% and is permanently flagged. Confidence is provenance-aware (`_el_legacy_conf_scores()`): a nameplate-derived field inherits the `Nameplate Text` transcription score, because the model scores `Volts`/`Ampere` against the lamacoid it correctly left blank.
- Plausibility tripwire reason codes: `unrecognized_voltage` / `implausible_amperage` are appended to `manual_review.reason_codes` from `structured_data.rating_plausibility_flags` — a parsed reading that matches no known voltage system or standard amperage size is stored blank and flagged, never stored as data. Rails live in `legacy_flow` (`is_plausible_voltage` / `is_plausible_amperage` / `_apply_rating_rails`).
- Staleness / regeneration: `EL_LEGACY_RULE_VERSION` (currently **6** — v5 2026-08-04: manufacturer-nameplate support; v6 2026-08-05: Supply From stores one feeder identifier, no composite sentences or `via` qualifiers) is independent of `Config.EXTRACTION_RULE_VERSION`. Bumping it makes `_existing_el_legacy_output_needs_rescore()` flag legacy JSONs stale so they reset `ai_status` and regenerate on the next run. Never bump `EXTRACTION_RULE_VERSION` for legacy-only rule changes — that forces fleet-wide standard re-extraction.
- `_load_ai_processed_qrs()` and the existing-JSON skip must route payloads with `"process": "Legacy"` through the legacy rescore check, not the standard one (the standard check re-scores legacy JSONs with standard rules and creates a permanent billable re-extraction loop).
- Human-reviewed legacy JSON is authoritative: `modified=true`, `structured_data.Approved == "True"` (added 2026-08-04 — the extractor previously lacked the Approved check the review app already enforced, so a CLI rerun with `EL_OVERWRITE_EXISTING_JSON=true` could overwrite approved work), `supply_from_manual_override=1`, or `volts_manual_override=1` block both stale-flagging and overwrite — same invariant as the standard flow.
- Dictionaries: Legacy uses `dictionary/electrical.dictionary_old.py` (the working copy, incl. `panel_legacy.codes.ident_normalization` for plate-typo/punctuation normalization); Standard uses `dictionary/electrical.dictionary.py`. Never cross them.
- Legacy identifier and field conventions (Equipment ID = UBC Asset Tag, hyphen form `DCC-1` / `PNL-UPS1`; Supply From ≡ Fed From Equipment ID; Description `<type word> - <Equipment ID>`; X-tag structure is dictionary-decode-only) are specified in `review_apps.rules.md` → "EL Legacy Flow Rules". The extraction script must not re-implement them — they live in `legacy_flow.py`.

## OpenAI Model Rules

- Default models are ME `gpt-5.4-mini`, BF `gpt-5.4-mini`, and EL `gpt-5.4`.
- Default model fallback is disabled for all three disciplines.
- Keep both the per-asset model limit and per-model attempt limit at `1` unless an explicit operational override is approved.
- The legacy ME single-image helper must use `PRIMARY_LLM_MODEL`; it must not silently select `FALLBACK_LLM_MODEL`.
- SLD uses `gpt-5.4` as its only default primary model and has no default fallback models.
- Preserve the existing environment-variable override interfaces; changing defaults must not remove the operational escape hatch.

## JSON Save Guard

- Extraction may overwrite an existing JSON only when the new result is at least as complete as the existing result.
- Even when save is skipped, `ai_status` still needs to be updated so the same asset does not loop forever.
- EL's existing-JSON skip must not apply when `_existing_el_output_needs_rescore()` says the payload is stale under the current extraction rules. In that case the processor must continue extraction and rewrite the stale JSON so `ai_status` can settle back to `1`.
- EL approved / SDI-locked rows must not be reset from `ai_status=1` back to `0` by the stale-JSON detector, because the worker deliberately excludes approved rows from AI reprocessing.
- EL human-reviewed JSON is authoritative. `_existing_el_output_needs_rescore()` must not flag payloads with `modified=true`, `supply_from_manual_override=1`, or `volts_manual_override=1` for AI rerun, because re-extraction can erase reviewer corrections.

## Discipline-Specific Completeness

- ME completeness:
  `Manufacturer`, `Model`, `Serial Number`, `Year`, `UBC Tag`, plus `Technical Safety BC` only when seq `-3` exists.
- BF completeness:
  `Manufacturer`, `Model`, `Serial Number`, `Diameter`.
- EL completeness:
  `UBC Asset Tag`, `Ampere`, `Supply From`.

## Discipline-Specific Confidence

- Confidence must be reconciled against the final saved field values.
- Blank final fields must not retain stale non-zero confidence.
- EL averages exclude `Volts`, `Location`, and `Branch Panel`.
- ME includes `Technical Safety BC` only when seq `-3` exists.
- When source confidence is missing, synthesized confidence should come from actual evidence, not from placeholder zeros pretending to be certainty.

## ME Ownership Rules

- Seq `-0` owns `Manufacturer`, `Model`, `Serial Number`, and `Year`.
- Seq `-1` owns `UBC Tag`.
- Seq `-3` owns `Technical Safety BC`; this field stores the Safety Authority sticker value on the `UNIT NO.` / `BC Safety Authority Unit No.` row, not W.P., S.O. No., date, or form/revision text.
- `Technical Safety BC` pressure-vessel values use the `PV` prefix and should preserve all six visible digits; short `PV` reads with fewer than six digits are treated as unreadable instead of guessed.
- TSBC rereads may send zoomed UNIT NO. row/value crops ahead of the full seq `-3` image; ambiguous digits must stay blank rather than guessed.
- Seq `-4` is the optional **Extra Photo** slot, owns no fields, and is never sent to the LLM (excluded via `VALID_SUFFIXES`).
- If the owning source is absent or not evidenced, leave the field blank.

## ME UBC Tag Hybrid Consensus

Current documentation refresh: 2026-08-03.

- The normal ME simple extraction remains the primary source: production keeps `gpt-5.4-mini` with `ME_IMAGE_DETAIL=low`. Consensus adds no API call for an unchallenged tag.
- For every readable seq `-1`, the local validator detects the dominant dark elongated placard (3-75% frame area, elongation at least 1.6, rectangular fill at least 0.5), falling back to the full image. It performs exactly four bounded Tesseract reads: two opposite orientations, each as grayscale and Otsu, over the central 70%, PSM 11, character whitelist, four-second timeout.
- Local OCR is one independent source, not four sources. A local prefix or core vote exists only when at least two variants agree. Partial evidence such as prefix `HX` with no reliable core is valid.
- ME prefixes are loaded from `dictionary/mechanical_dictionary.py` through `ast.parse()` and `ast.literal_eval()`. Dictionary membership may challenge a prefix but is never counted as visual evidence. Dictionary read/parse failure is non-fatal.
- Challenge triggers are: missing/malformed primary tag, a dictionary-unknown prefix, an explicit primary confidence below 70, or local disagreement with the primary. A missing model confidence alone is not a challenge.
- A challenged asset receives at most one independent judge call, with no retry: `ME_UBC_JUDGE_MODEL=gpt-5.6-terra`, `ME_UBC_JUDGE_DETAIL=original`, and `ME_UBC_JUDGE_REASONING_EFFORT=low`. The call sends two opposite orientations without disclosing primary/local candidates. Each prepared image has a 1280-pixel maximum edge, and output is capped at 220 completion tokens.
- Prefix and core are resolved separately. A challenged component changes only when two non-empty visual sources agree; unchallenged primary components are preserved. Existing forms including `HUM 5`, dotted cores, and multi-segment tags remain valid.
- Unresolved disagreement or judge failure preserves the primary component, caps UBC confidence at 65, and adds `ubc_consensus_unresolved`; an unresolved unknown prefix also adds `ubc_prefix_unrecognized`. Confirmed/corrected quorum receives at least 92 confidence. An accepted unchallenged tag with no primary model score receives 84, not a synthesized 95.
- `manual_review.ubc_consensus` records status (`accepted_primary`, `confirmed_by_quorum`, `corrected_by_quorum`, or `unresolved`), triggers, component votes, final tag, judge model, call count, token usage, and only a normalized failure category (`timeout`, `quota`, `auth`, `rate_limit`, `api`, or `parse`). Raw API exception details are not stored.
- Cost guard and rollback: `ME_UBC_CONSENSUS_ENABLED=1` enables the cascade; setting it to `0` restores the legacy targeted UBC reread behavior. Monitor returned judge token usage, but never relax the one-call/no-retry/image/token caps without explicit approval.
- This behavior is ME-only. It introduces no database schema or endpoint changes and does not alter BF or EL. Human-reviewed values and existing manual-override protections remain authoritative.

## ME Manufacturer Canonicalization

Current documentation refresh: 2026-07-08.

- ME manufacturer canonicalization resolves in a fixed order: `ME_MANUFACTURER_REGEX_RULES` → compact alias lookup (`ME_MANUFACTURER_ALIAS_LOOKUP`) → shared `normalize_manufacturer()` whitelist → guarded generic fallback.
- The generic fallback intentionally rejects multi-token names without a legal suffix (`CO`, `INC`, `LTD`, ...), an `&`, or a hyphen, to block OCR noise. Legitimate makers with that shape (e.g. `Spirax Sarco`, `Gardner Denver`) must be added to `ME_MANUFACTURER_REGEX_RULES`, or the extracted value is silently wiped to blank — there is no log line when this happens.
- Wipe signature: `Manufacturer` blank with confidence 0 while `Model` / `Serial Number` / `Year` from the same seq `-0` plate are confident. Treat this as a missing dictionary entry, not a vision failure; the fix is a regex rule (zero per-run cost), never a model-tier escalation.
- Additions to date: `Spirax Sarco`, `Siemens` (2026-06-22); `Gardner Denver` (2026-07-08, from the QR `0000186422` miss — see `INCIDENT_2026-07-08_me_manufacturer_whitelist_vm_drift.md`).
- All-numeric model codes are accepted only for manufacturers in `ME_NUMERIC_MODEL_MANUFACTURERS`, 5-12 digits, excluding year/date shapes — see "Siemens Nameplate Extraction Rules" below for the implementation contract.

## Extra Photo Exclusion (All Disciplines)

- ME, BF, and EL all support one optional **Extra Photo** sequence: ME `-4`, BF `-3`, EL `-3`.
- The pipeline's `FILENAME_PATTERN` regex was widened so discovery can see the file and log it as `invalid_seq` rather than `name_mismatch`.
- The actual gate is `VALID_SUFFIXES`, which was left unchanged. Extra Photo sequences are deliberately absent from each `VALID_SUFFIXES` set, so they are discovered, logged, and skipped before being added to `info["images"]`.
- Do not promote an Extra Photo sequence into `VALID_SUFFIXES` for "more data" — it carries no field-owner contract and would introduce ambiguous evidence.

## OCR and Prompting Rules

- OCR is a recovery and evidence layer, not permission to hallucinate.
- Prompt guidance and merge logic may be discipline-specific when required by plate layout.
- For ME, seq-0 plate evidence is critical for model and serial acceptance on difficult families such as Rheem / Ruud water heaters.
- For ME, if seq `-3` exists but the primary extraction leaves `Technical Safety BC` blank, a targeted seq-3 reread may be used to extract only the `UNIT NO.` / `BC Safety Authority Unit No.` row.
- If an existing ME JSON would normally be skipped but has blank `Technical Safety BC` and a readable seq `-3` image, the processor may perform a TSBC-only repair and preserve all other existing structured fields.

## ME Long-Model and Model/Serial Collision Defense

Current documentation refresh: 2026-07-23.

ME model codes are not universally short. Trane fan-coil nameplates can carry dense 40-character configuration strings; incident QR `0000186301` has Model `BCHC090H1A0A2AF7P000000B0000000000000000` and Serial Number `T03M77537`.

- `_is_model_code_candidate()` accepts bounded, code-like alphanumeric model values up to 64 compact characters. Values longer than the legacy 32-character envelope must contain at least two letters and eight digits, use only model-code separators, and contain no more than four whitespace-delimited groups.
- Label-aware model regexes cover the same 64-character bound. Fast OCR may consider a fallback model only from text immediately following `MODEL`, `MODEL NO.`, `UNIT MODEL`, `TYPE`, `CATALOG`, or `ITEM`; the presence of a model label elsewhere in a full OCR block does not authorize unrelated tokens.
- `_has_model_label_evidence()` requires a label-local parsed match. A serial appearing elsewhere on the same plate is not model evidence.
- Production simple mode reads upright grayscale before thresholded OCR variants so tightly printed trailing zeroes survive. If label-local OCR differs only by one or two trailing zeroes, it corroborates the longer zero-padded Model.
- Fast OCR preserves the first upright long-model read instead of treating a longer rotated/noisy read as automatically better. A complete upright alphanumeric serial is likewise not replaced solely by a longer noisy token.
- A candidate with identical compact `Model` and `Serial Number` values is invalid and forces model reread/OCR rescue. Any collision that survives merge is resolved using label evidence, receives `manual_review.reason_codes = model_serial_collision`, and a final save guard prevents duplicate Model/Serial values from being written.

## DB Sync Rules

- JSON-to-DB sync helpers must upsert curated rows rather than append duplicates.
- `sdi_dataset` is for ME and BF.
- `sdi_dataset_EL` is for EL.
- `Avg_ai_conf` must be preserved through sync when present.

## Siemens Nameplate Extraction Rules

Current documentation refresh: 2026-06-25.

Siemens control valves and actuators have a nameplate layout that differs from standard manufacturer plates:

- The **serial identifier field** is labeled **`Product No.`**, not `S/N`, `Serial`, or `Serial Number`.
- The **model number** is often purely numeric (e.g., `03134`), not an alphanumeric code.
- The **`Cv` flow coefficient** label appears on the nameplate but is **not** a serial or model field — it must be ignored during extraction.

### Implementation

- `ME_NUMERIC_MODEL_MANUFACTURERS = {"Siemens"}` (case-insensitive set) identifies manufacturers whose model numbers may be purely numeric.
- `_manufacturer_allows_numeric_model(manufacturer: str) -> bool` checks this set; called by both `_is_model_code_candidate()` and `_has_serial_label_evidence()`.
- `_has_serial_label_evidence()` accepts a `manufacturer_hint` parameter. The `PRODUCT|PROD\.?\s*NO` cue regex is activated **only** when `_manufacturer_allows_numeric_model(manufacturer_hint)` is `True`, preventing the broadened pattern from mis-firing on other manufacturers.
- `_clean_labeled_serial_value()` recognizes the `NNN-NNNNNN` shape (3 digits – 3-to-6 digits, e.g. `599-0335`) before the generic token extraction logic, preserving the full hyphenated value without truncation.
- `_evaluate_llm_candidate()` passes `candidate.get("Manufacturer", "")` to `_is_model_code_candidate()` so Siemens numeric model strings are not flagged as weak candidates and discarded.
- LLM prompts (`_llm_multi_image_simple()`, `_llm_multi_image()`, `_reread_model_serial_from_nameplate_llm()`) include a Siemens-specific instruction: when the Manufacturer is Siemens and no explicit `S/N` / `Serial` field is present, use `Product No.` as the Serial Number, and accept purely numeric strings as valid Model numbers.

## Serial Date-Misread Defense (ME + BF)

Current documentation refresh: 2026-07-06.

Rotated or upside-down nameplate photos can make the LLM read the MFG date as the Serial Number (incident: QR `0000261040`, Rheem/Ruud ST120 storage tank — serial extracted as `8102/90`, which is `09/2018` read rotated 180°, at 96% model confidence). Defense is layered as validation + prompt + rescue; images sent to the LLM are NOT altered.

- `validators_shared.looks_like_date_misread_serial(serial, year_hint="")` detects date-shaped serials: `MM/YY`, `MM/YYYY`, `YYYY/MM` shapes with `/ - .` separators (including blocks whose character reversal reads as a year, e.g. `8102` → `2018`), and compact all-digit values (`810290`) only when the extracted Year corroborates them (±1). Values containing letters are never flagged; Taco order serials (`20588223/1`, shape `\d{8}/\d`) are explicitly allowed.
- ME: `_is_serial_candidate()` rejects date-misreads, which sets `serial_weak` and automatically triggers the existing targeted nameplate reread + multi-rotation OCR rescue even when LLM confidence is high. A final belt in `_build_ui_parity_struct()` (and the legacy guardrail path) blanks any date-like serial that survives. When suspected, `manual_review.reason_codes` gains `serial_date_misread_suspected` and the Serial Number confidence score is capped at 65 (below the 70 manual-review threshold).
- BF: a date-like serial from any LLM attempt is blanked immediately in `process_single_asset()`, which lowers completeness and triggers the existing heavier-model fallback with OCR context; the same reason code and confidence cap apply.
- Prompts (ME generic nameplate guidance, Rheem/Ruud override, targeted reread; BF main prompt) instruct the model to mentally rotate the plate upright, never output a date-shaped value as Serial Number, and return `""` when the labeled serial field is unreadable rather than substituting a nearby number.
- OCR rescue rotation variants include 180° in ME and BF `_ocr_text_variants()` (previously only 90° CW/CCW; EL already scored 180° in `_extract_best_ocr_text()`).
- EL is unchanged by this defense.
- **Rheem/Ruud reread trust (2026-07-06 follow-up):** a targeted-reread serial matching the family shape (letter prefix + 8-10 digits, `_matches_rheem_ruud_serial_shape()`) is trusted even without OCR corroboration, and OCR-rescue candidates never overwrite a reread-accepted serial. Prior behavior let garbled OCR from a rotated plate (`HEMN61080`) outrank the correct reread value (`A221812671`) because `require_direct_model_serial_evidence` demanded OCR support that a rotated photo cannot provide.
- **ORDER NO. is a low-confidence fallback (2026-07-06 follow-up):** the field labeled `SERIAL` / `SERIAL NO` / `S/N` always takes priority for Serial Number. `ORDER NO.` / `PRODUCT NO.` may be used ONLY when the SERIAL-labeled field is absent or its value is genuinely unreadable, and the prompts instruct the model to report such fallbacks with LOW confidence (<70) so the record is routed to manual review. In the OCR rescue layer, SERIAL-cued candidates outrank ORDER/PRODUCT-cued ones in `_parse_nameplate_model_serial()` (pattern order) and `_best_serial_candidate_from_texts()` (vote weight 4 vs 3). Incident: QR `0000083767` (ENERMAX heat exchanger, rotated photo) extracted the Order No. `3604JG0492` at 90% confidence instead of the labeled serial `9771-A` because the old prompt listed ORDER NO as a coequal serial label.
- **Taco serial-label isolation and CRN vessel exception (2026-07-13):** Taco Serial Number normally requires an explicit `SERIAL` / `SERIAL NO` / `S/N` label. `PART NO.`, `ORDER NO.`, the CRN value, dates, pressure values, and certification identifiers remain forbidden. A narrow tank/pressure-vessel exception activates when OCR finds CRN plus an independent vessel cue (`MAWP`, `MDMT`, `ASME`/`NB`, `CERTIFIED BY`, or `PSI AT`) or the targeted vision schema explicitly confirms that same pressure-vessel context. The reread uses high-detail opposite full-plate rotations and the configured stronger fallback model first, then may return the digits-only identifier stamped in the plate's top/header border. The accepted value receives reduced confidence (capped at 65) and reason code `pressure_vessel_unlabeled_serial`. Generic OCR parsing never promotes arbitrary unlabeled identifiers. Incident QR `0000086593`: Part No. `RES-2AE` is rejected; the stronger targeted reread consistently identifies top stamp `434637`.
- **AquaPLEX storage-tank identity (2026-07-13):** standalone `AquaPLEX` branding canonicalizes to `AquaPLEX`; the combined `Durawatt/AquaPLEX` rule remains separate. Short suffix tank models such as `L 600A-TR` are valid model codes (the compact `L600ATR` form allows up to three trailing letters). When the main extraction and OCR leave Manufacturer blank, a focused seq-0 vision reread inspects the logo/header; on a PVI storage tank branded AquaPLEX it returns `AquaPLEX`, not `PVI`. If that reread is unavailable, the exact confirmed model identity `L600ATR` deterministically recovers `AquaPLEX`; near matches are not accepted. Incident QR `0000260587`.
- **Omega Compressors identity (2026-07-15):** `OMEGA COMPRESSORS` / `Omega Compressor(s)` canonicalizes to `Omega Compressors`. The exact confirmed model identity `TK5080V02M` recovers the manufacturer when an existing ME JSON has a blank Manufacturer; the repair preserves every other field and review metadata is recalculated. Near model matches are not accepted. Incident QR `0000186687`.
- **Unknown manufacturer acceptance (2026-07-15):** the manufacturer dictionary canonicalizes known suppliers but is no longer a strict whitelist. A compact brand-like value returned by vision/OCR may survive even when unseen, including all-uppercase multiword logos. Field labels and maintenance text (`MODEL NO`, `COMPRESSOR OIL`, `MADE IN CANADA`, `USE ONLY`, etc.) remain blocked by hard-token, forbidden-phrase, and descriptor-only gates. Unrecognized manufacturers are capped at 65 confidence and receive `manufacturer_unrecognized` plus `low_confidence_manufacturer`, forcing human review before approval.
- **Serial reread corroboration (2026-07-06 follow-up):** a non-strict-family serial with no OCR label evidence is now cross-checked against the targeted nameplate reread. On mismatch (or empty reread) the value is kept but confidence is capped at 65 and reason code `serial_unverified` is added, forcing manual review. Incident: QR `0000083767` returned `UISD-SEL4` at 76% confidence — a confabulation of the plate's TUBESIDE/SHELLSIDE table labels — which previously cleared the 70 threshold unflagged.
- **Unknown-manufacturer names with corporate suffixes (2026-07-06 follow-up):** `ME_GENERIC_MANUFACTURER_STOPWORDS` previously contained `LTD` / `INC` / `CO` / `CORPORATION` / `COMPANY`, so every unknown multi-word manufacturer was discarded as noise — while the canonicalizer simultaneously REQUIRED one of those suffixes for multi-token names (catch-22; `Enermax Fabricators Ltd.` always blanked). Corporate/origin tokens moved to `ME_MANUFACTURER_CORPORATE_TOKENS`: they are noise only when the whole candidate consists of them (`"Ltd."`, `"CO CANADA"`), and legitimate names like `Enermax Fabricators Ltd` now survive.
- **UBC Tag prefix length (2026-07-07 follow-up):** the tag parse/canonicalize regexes capped the letter prefix at 4 (`[A-Z]{1,4}`), so a 5-letter placard prefix could not match at the string start and the pattern latched onto a later fragment — QR `0000081480`'s clear placard `CHWBT-W-4` was stored as `W-4`. Prefix widened to `[A-Z]{1,6}` in `_parse_ubc_tag_from_text()` (all three patterns) and `Config.UBC_TAG_PATTERNS`; `stop_prefixes` still rejects label words (SERIAL, MODEL, ORDER). All pre-existing formats (FC NO., FC-6.32, HUM 5, DST-4, T-CHB-01) verified unchanged.
- **UBC Tag placard support (2026-07-06 follow-up):** the main mapping guidance and the seq-1 UBC reread prompt now accept placard-style equipment identifiers (`DST-4`, `EF-1`, `HUM 5`) when the tag has no `<PREFIX> NO.` field; previously the prompts only described `FC NO.`-style plates so placard tags always came back blank. `normalize_ubc_tag()` and `UBC_TAG_PATTERNS` already preserved these shapes downstream.

## Error-Handling Rules

- A single-asset failure must not crash the batch.
- Catch file, OCR, API, and validation errors and continue.
- Log enough context to find the failing QR, discipline, and building.

## Chained Execution Rules

- `run_ai_and_sync.sh` chains AI extraction to DB sync automatically.
- This is the recommended launch method from the Dashboard.
- The separate manual `update_db` task has been removed from the Dashboard launcher.

## Shared Validator Rules

- `validators_shared.py` provides validation utilities shared across ME, BF, and EL scripts.
- Extraction scripts should use shared validators rather than duplicating validation logic.
- `looks_like_date_misread_serial()` is the shared date-misread detector used by ME and BF serial acceptance gates (see "Serial Date-Misread Defense").
