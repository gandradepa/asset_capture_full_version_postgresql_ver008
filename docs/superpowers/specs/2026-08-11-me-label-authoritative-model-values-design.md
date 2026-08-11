# ME Label-Authoritative Model Values (Design)

**Date:** 2026-08-11
**Status:** Approved by the user for specification and implementation planning

## Problem

Mechanical nameplates use model values with many structures: mixed letters and
digits, digit-leading codes, alphabetic-only codes, spaces, and punctuation.
The current ME extraction pipeline treats the value's shape as the primary
validity signal even when the value is visibly printed immediately after an
explicit `Model:` label.

Production QR `0000188234` demonstrates the failure. Its sequence `-0`
nameplate clearly prints `Model: QCC-M`. The primary vision pass read `OCC-M`,
while targeted rereads and OCR could not promote a Model because every path
eventually called `_is_model_code_candidate()`. That generic function requires
at least five compact characters and normally requires a digit, so both the
misread `OCC-M` and the correct `QCC-M` were rejected. The final JSON and
`sdi_dataset` row therefore stored a blank Model with confidence `0`.

This is the same architectural class as the earlier `301-EM` incident, but it
cannot be solved sustainably by adding another model-number pattern. A future
manufacturer can legitimately print a different format and encounter the same
shape gate again.

## Goal

When sequence `-0` contains an explicit model-field label, accept the bounded
value adjacent to that label regardless of its character structure. Continue
to apply strict shape validation to unlabeled OCR tokens and raw guesses so
ratings, serials, UBC tags, and surrounding prose are not promoted to Model.

## Non-goals

- Do not relax unlabeled model-candidate validation.
- Do not borrow Model from ME sequences `-1`, `-2`, `-3`, or `-4`.
- Do not change BF or EL extraction behavior.
- Do not add manufacturer-specific or model-specific allowlists.
- Do not overwrite reviewed values or bypass `Col_process`/approval guards.
- Do not infer or spell-correct a value when no reader can see an explicit
  model label and adjacent value.

## Considered approaches

### 1. Label-authoritative acceptance (selected)

Create a separate acceptance path for values proven to be adjacent to an
explicit model label on sequence `-0`. The generic model-code validator remains
strict for every unlabeled path.

This makes semantic context authoritative without weakening noise protection.
It generalizes to `QCC-M`, `301-EM`, numeric-only models, spaced models, and
punctuation-heavy models.

### 2. Relax the global model validator

Allow arbitrary short, alphabetic, numeric, or punctuated values in
`_is_model_code_candidate()`. This is smaller mechanically, but it would also
admit unlabeled ratings, dates, serial fragments, tags, and OCR noise across
many existing call sites.

### 3. Continue adding format patterns

Add patterns for alphabetic-hyphen models such as `QCC-M`, then repeat for each
new incident. This retains strictness but encodes an open-ended set of OEM
formats and does not meet the requirement that explicit labels outrank shape.

## Approved architecture

### Two validation tiers

The implementation will distinguish two kinds of evidence:

1. **Unlabeled candidate:** Continue using `_is_model_code_candidate()` and all
   existing tag, rating, prose, length, digit/letter, date, and collision
   defenses.
2. **Explicitly labeled value:** Use a dedicated label-aware acceptance helper.
   A labeled value is not required to contain a digit, satisfy a minimum compact
   length, or match a known model-number pattern.

The generic helper must remain unchanged except where necessary to call or
coexist with the new labeled-value helper. A bare `QCC-M`, `118668`, or `208V`
without label provenance must remain invalid through the generic path.

### Explicit label provenance

Label authority exists only when all of the following are true:

- The source is the ME sequence `-0` asset-plate image.
- The cue is one of the existing supported labels: `Model`, `Model No.`,
  `Model Number`, `Unit Model`, `Type`, `Catalog`, or `Item`.
- The value begins immediately after the cue, optional number marker, and
  optional punctuation.
- Extraction stops at the original line boundary or the next recognized field
  label, including Serial/SN, Order, Voltage, Amps, Hz, Date, Rating, Capacity,
  or another model cue.

The label-aware extractor must inspect raw line structure before globally
collapsing whitespace. Known field boundaries remain a fallback for OCR engines
that return a flattened line.

### Labeled-value safety contract

An explicitly labeled Model value is accepted when it:

- contains at least one visible alphanumeric character;
- is at most 64 characters after trimming;
- contains no control characters or line breaks;
- does not include a second field label captured as part of the value;
- is not identical to the accepted Serial Number after compact comparison; and
- is not empty after existing harmless normalization.

Significant separators and punctuation remain intact. Normalization may trim
leading/trailing whitespace, collapse repeated internal spaces, and retain the
pipeline's existing uppercase storage convention, but it must not remove or
rearrange meaningful punctuation.

Numeric-only and alphabetic-only values are valid under this labeled contract.
Their safety comes from explicit seq-0 field provenance, not from their shape.

### Source precedence and disagreements

The existing primary vision result remains the first candidate. If it fails the
generic shape gate or lacks trustworthy model evidence, the current targeted
high-resolution seq-0 Model/Serial reread runs.

Source precedence for a format-agnostic labeled value is:

1. targeted high-resolution seq-0 model-field reread;
2. direct seq-0 OCR value adjacent to an explicit model label;
3. primary multi-image vision value only when the same value has explicit
   seq-0 label evidence.

A targeted reread is label-authoritative because its request is restricted to
the explicit Model row on sequence `-0`. Its result must use the labeled-value
contract instead of the generic shape gate.

If explicit readers disagree, retain the highest-precedence bounded value and
flag the asset for manual review with a model-label disagreement reason. Do not
blank a clearly labeled value solely because a lower-precedence reader differs.

### Pipeline integration

The label-aware contract must be used consistently by:

- `_parse_nameplate_model_serial()`;
- `_model_candidates_near_label()`;
- `_has_model_label_evidence()`;
- targeted Model/Serial reread post-processing;
- fast/full OCR Model extraction; and
- the final UI-parity Model acceptance decision.

The final save collision guard remains authoritative: a Model matching the
Serial Number after compact comparison is rejected and sent to manual review.
Sequence ownership, approval protection, human overrides, confidence
reconciliation, JSON structure, and PostgreSQL table selection remain
unchanged.

## Confidence and manual review

- Exact explicit-label evidence may use the existing high-confidence labeled
  Model policy.
- A targeted result that conflicts with another explicit reader remains stored
  but receives a confidence cap and manual-review reason.
- Missing or boundary-ambiguous labeled text remains blank and flagged rather
  than capturing neighboring prose.
- Unknown Manufacturer handling remains independent; it may still produce a
  manufacturer review reason but must not erase a valid labeled Model.

## Testing strategy

Add focused regression coverage for the new label-aware contract:

- `Model: QCC-M` parses and survives UI parity for QR `0000188234`.
- `Model: 301-EM` remains accepted.
- explicitly labeled numeric-only, alphabetic-only, spaced, and
  punctuation-heavy values are accepted within the 64-character bound;
- the parser stops before `Serial`, `S/N`, `Rating`, `Voltage`, `Date`, and
  other neighboring labels;
- a bare/unlabeled `QCC-M`, rating, numeric identifier, and prose remain
  rejected by `_is_model_code_candidate()`;
- Model/Serial collisions remain rejected;
- no seq-0 image means no Model, even if another sequence contains similar
  text; and
- the existing digit-leading, long-model, collision, and UBC-consensus ME
  suites remain green.

Tests must exercise production methods rather than duplicating regexes in test
code. The bug reproduction must fail before the implementation is added.

## Documentation

Update the canonical extraction rules first, then synchronize the
`.agent_app/rules/asset_extraction_api.rules.md` mirror. Add a dated change note
explaining that explicit seq-0 Model labels are format-authoritative while
unlabeled candidates remain shape-restricted.

## Production rollout

After local RED/GREEN verification and the full ME regression suite:

1. Confirm QR `0000188234` is unapproved and all associated
   `QR_code_assets.Col_process` values remain `0`.
2. Confirm no other ME QR is queued, acquire the shared AI lock, and take
   timestamped backups of the live API and current JSON.
3. Deploy only the tested ME API file after verifying old/new hashes and a
   production parser smoke test.
4. Reset and reprocess only QR `0000188234` through
   `API/run_ai_and_sync.sh`; do not run extraction or the bookkeeping updater
   standalone.
5. Trigger the normal ME reviewer JSON-sync path only if the chained updater
   has not yet refreshed `sdi_dataset`, after first confirming the target is the
   only pending ME reviewer JSON.
6. Verify the deployed hash, service health, fresh JSON, curated PostgreSQL row,
   audit entries, `ai_status=1`, unchanged approval and `Col_process` values,
   and an empty ME processing queue.

Production success requires Model `QCC-M` in both JSON and `sdi_dataset`, with
Serial `QCCM1608B00041` preserved and no unrelated QR or discipline modified.
