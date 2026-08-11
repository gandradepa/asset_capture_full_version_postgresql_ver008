# ME Digit-Leading Model Validation Fix (Design)

**Date:** 2026-08-11
**Status:** Approved by the user for implementation and production reprocessing

## Problem

Mechanical QR `0000188207` has a readable Honeywell Analytics nameplate with
`Model: 301-EM`. The production image, extracted JSON, PostgreSQL row, and a
deterministic replay show that the image and database-sync layers are healthy.
The ME model-code validator rejects the normalized short code `301EM` because
its short mixed-code patterns accept only letter-leading forms. The same
validator is used by the OCR parser, targeted vision reread, and final
UI-parity guard, so every rescue path converges on a blank Model field.

## Considered approaches

1. **Label-backed digit-leading short codes (selected).** Extend the existing
   model validator to recognize a bounded digit-leading mixed shape such as
   `301-EM`, while retaining the existing model-label evidence requirement in
   OCR paths and every tag/date/noise guard. This fixes the general false
   negative without coupling the rule to one manufacturer.
2. **Broadly accept all short digit-leading alphanumerics.** Smaller code
   change, but it would admit ratings, voltages, and unrelated identifiers as
   models when OCR context is weak.
3. **Honeywell-only allowlist.** Lowest immediate blast radius, but it encodes
   a valid model-number shape as a manufacturer exception and will fail again
   for another maker using the same convention.

## Approved design

- Add the minimal digit-leading short-code shape to
  `AssetProcessor._is_model_code_candidate()`. The accepted compact value must
  begin with 2-5 digits and end with 1-4 letters; the full value remains subject
  to the existing length, tag-like, prose, and digit/letter gates.
- Keep source ownership unchanged: Model remains seq `-0` only. OCR-derived
  values still require an explicit model-like label through the existing
  label-aware parser/evidence paths.
- Add regression tests proving `301-EM` is accepted and parsed from an explicit
  `Model:` label, while a bare numeric rating, a tag-like value, and prose remain
  rejected. Existing accepted model families must remain green.
- Update the canonical ME extraction rules first, then sync the `.agent_app`
  mirror and add a concise entry to `attributes_changes.md`.

## Production rollout and verification

- Run the focused regression test red before changing production code, then
  green after the minimal validator change. Run the existing ME model/serial
  regression suites and compile the changed Python files.
- Before deployment, back up the live API file and the QR's JSON using the
  timestamped backup convention. Deploy only the tested API file after
  confirming its pre-deploy hash still matches the investigated version.
- Reprocess only QR `0000188207` through `API/run_ai_and_sync.sh`; do not run the
  extraction script or database sync separately. The QR is unapproved and its
  three `QR_code_assets` rows have `Col_process = 0`, so no reviewed human
  override is eligible to be overwritten.
- Success requires exact agreement across the nameplate, output JSON, and
  `sdi_dataset`: Model `301-EM`, Serial `5340RFS13150043`, with an audit trail
  entry from the chained pipeline. No unrelated QR or discipline is modified.
