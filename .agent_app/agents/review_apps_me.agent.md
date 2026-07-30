# Review App (ME) Agent

Current documentation refresh: 2026-07-07.

## Purpose
Mechanical asset review for engineers comparing extracted JSON against the captured ME image set.

## Scope
- In scope: ME review dashboard, review form, image and JSON sync hooks, approval behavior, ME `sdi_dataset` sync.
- Out of scope: EL schema handling, BF-only UI behavior, main analytics dashboard code.

## Inputs
- `<QR>_ME_<Building>.json` payloads from `Output_jason_api/`
- ME image sequence files from `Capture_photos_upload/`
- Mechanical dictionary lookups for `Asset Group`, `Attribute`, and `Main Asset`

## Outputs
- Updated JSON payload with human corrections and `modified=True` when applicable
- `QR_codes` approval state updates
- `sdi_dataset` row updates aligned with the persisted JSON payload

## Critical Conventions
- Port: `5002`
- Keep context-aware approval defaults in the dashboard tabs.
- Filter tag searches against both `UBC Asset Tag` and `UBC Tag` where supported.
- Approval toggles must preserve or backfill `Asset Group`, `Attribute`, and `Main Asset` before writing JSON and before SDI sync.
- Dictionary priority stays: exact composite key, composite prefix, legacy simple key.
- Reviewer overrides of `Asset Group` / `Attribute` persist via `asset_group_manual` / `attribute_manual` (`"1"`) in the structured JSON; `apply_dictionary_rules` must never overwrite a flagged, non-blank field. `Main Asset` stays dictionary-owned. See `rules/review_apps.rules.md`.

## Validation Checklist
- [ ] Image hooks still validate the ME sequence set (`-0..-4`, with `-4` being the optional **Extra Photo** included in `SEQ_SHOW` but absent from `SEQ_CHECK`).
- [ ] Approving from the dashboard does not blank `Asset Group`, `Attribute`, or `Main Asset`.
- [ ] A manual `Asset Group` / `Attribute` edit survives save, JSON sync, render, and UBC Tag changes; re-selecting the dictionary value clears the flag.
- [ ] The JSON payload and `sdi_dataset` stay aligned after save or approval.
- [ ] The Photo column renders a `+1` chip when seq `-4` is present; the chip never triggers "Missed Photo".

## Embedded Mode
Runs both standalone and inside the central Dashboard iframe (`?embedded=true`). A `before_request` hook sets `g.embedded`; templates wrap user-nav, brand header, and user dropdown in `{% if not g.embedded %}` while keeping all functional controls visible. Cookie config: `SameSite=None; Secure`. Internal `<a>` clicks have `?embedded=true` re-appended by a small JS script. See `rules/review_apps.rules.md`.
