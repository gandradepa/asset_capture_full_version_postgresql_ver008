# Review App (BF) Agent

Current documentation refresh: 2026-07-07.

## Purpose
Backflow asset review for human validation of BF extraction results and approval state.

## Scope
- In scope: BF review dashboard, review form, BF image checks, approval behavior, `sdi_dataset` sync.
- Out of scope: EL panel logic and ME-only fields such as dashboard `Main Asset`.

## Inputs
- `<QR>_BF_<Building>.json` payloads
- BF image sequence files
- Dictionary values for `Asset Group` and `Attribute`

## Outputs
- Updated BF JSON payloads
- `QR_codes` approval updates
- `sdi_dataset` synchronization for BF assets

## Critical Conventions
- Local port: `5004`; production port: `8004`.
- Keep BF approval, save, and navigation behavior aligned with the shared review-app rules where the schema matches.
- BF dictionary behavior must continue to preserve `Asset Group` and `Attribute` through approval changes.
- Reviewer overrides of `Asset Group` / `Attribute` persist via `asset_group_manual` / `attribute_manual` (`"1"`) in the structured JSON; `apply_dictionary_rules` must never overwrite a flagged, non-blank field. `Description` keeps its blank-only dictionary fill. See `rules/review_apps.rules.md`.
- BF does not render a `Main Asset` dashboard column even though BF image naming still includes a main-asset concept.
- BF dashboard bulk Manual / Approved header checkboxes render only when `can_edit` is true and must use the existing per-row toggle endpoints, not a backend bulk endpoint.

## Validation Checklist
- [ ] BF approval toggles do not regress `Asset Group` or `Attribute`.
- [ ] A manual `Asset Group` / `Attribute` edit survives save, JSON sync, render, and UBC Tag changes; re-selecting the dictionary value clears the flag.
- [ ] Sync hooks remain in place for image and JSON registration.
- [ ] `sdi_dataset` remains aligned with the BF JSON payload after save or approval changes.
- [ ] Image hooks still validate the BF sequence set (`-0..-3`, with `-3` being the optional **Extra Photo** included in `SEQ_SHOW` but absent from `SEQ_CHECK`).
- [ ] The Photo column renders a `+1` chip when seq `-3` is present; the chip never triggers "Missed Photo".
- [ ] Bulk Manual skips Approved rows, and bulk Approved uncheck skips exported / Planon-locked rows after `/check_sdi/<qr_code>`.

## Embedded Mode
Runs both standalone and inside the central Dashboard iframe (`?embedded=true`). A `before_request` hook sets `g.embedded`; templates wrap user-nav, brand header, and user dropdown in `{% if not g.embedded %}` while keeping all functional controls visible. Cookie config: `SameSite=None; Secure`. Internal `<a>` clicks have `?embedded=true` re-appended by a small JS script. See `rules/review_apps.rules.md`.
