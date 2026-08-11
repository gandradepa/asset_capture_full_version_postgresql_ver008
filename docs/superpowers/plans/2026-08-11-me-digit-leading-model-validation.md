# ME Digit-Leading Model Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve a clearly labeled Honeywell Analytics Model `301-EM`, deploy the tested validator to production, and reprocess QR `0000188207` through the chained AI-to-database workflow.

**Architecture:** Extend the existing short-model shape gate by one narrow, hyphen-preserving digit-leading form instead of adding a manufacturer exception or changing source ownership. The existing OCR label parser, targeted reread, UI-parity validation, confidence calculation, JSON writer, and PostgreSQL sync continue to own their current responsibilities.

**Tech Stack:** Python 3.12, `unittest`, OpenCV/Tesseract regression fixtures where needed, PostgreSQL through `db.py`, Bash deployment scripts, OpenAI structured vision extraction.

## Global Constraints

- Mechanical Model remains owned by sequence `-0`; no cross-sequence borrowing.
- Preserve parameterized SQL, discipline isolation, `Col_process`/human override protections, and audit logging.
- Do not run ME extraction or database sync standalone; production reprocessing must use `API/run_ai_and_sync.sh`.
- Deploy only after a red-green TDD cycle and focused ME regression verification.
- Back up the live API, JSON, and curated row before replacement or reprocessing.
- Reprocess only unapproved QR `0000188207`; abort if any other ME QR is pending.
- Update canonical `Markdowns_documentation/` first, then synchronize `.agent_app/` mirrors.

---

### Task 1: Add regression coverage and the minimal validator rule

**Files:**
- Create: `test/test_me_digit_leading_model.py`
- Modify: `API/API_interface_ME_ver00.py:4295-4300`
- Modify: `Markdowns_documentation/rules/asset_extraction_api.rules.md`
- Modify: `.agent_app/rules/asset_extraction_api.rules.md`
- Modify: `Markdowns_documentation/attributes_changes.md`
- Modify: `docs/superpowers/specs/2026-08-11-me-digit-leading-model-validation-design.md`

**Interfaces:**
- Consumes: `AssetProcessor._is_model_code_candidate(value: str, manufacturer_hint: str = "") -> bool` and `AssetProcessor._parse_nameplate_model_serial(text: str) -> dict[str, str]`.
- Produces: acceptance of the exact short form `301-EM` without changing existing tag, numeric-only, rating, prose, long-model, or serial-collision behavior.

- [ ] **Step 1: Write the failing regression test**

```python
"""Regression coverage for labeled digit-leading ME model codes."""

from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "API"
sys.path.insert(0, str(API_DIR))

from API_interface_ME_ver00 import AssetProcessor  # noqa: E402


class MeDigitLeadingModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = object.__new__(AssetProcessor)

    def test_hyphenated_digit_leading_model_is_valid(self) -> None:
        self.assertTrue(
            self.processor._is_model_code_candidate(
                "301-EM", "Honeywell Analytics"
            )
        )

    def test_explicit_model_label_parses_honeywell_code(self) -> None:
        parsed = self.processor._parse_nameplate_model_serial(
            "Honeywell Analytics Model: 301-EM Serial#: 5340RFS13150043"
        )

        self.assertEqual(parsed["Model"], "301-EM")
        self.assertEqual(parsed["Serial Number"], "5340RFS13150043")

    def test_relaxed_shape_does_not_accept_ratings_tags_or_numeric_ids(self) -> None:
        rejected = ("208V", "1200 VAC", "HUM 5", "118668")

        for value in rejected:
            with self.subTest(value=value):
                self.assertFalse(
                    self.processor._is_model_code_candidate(
                        value, "Honeywell Analytics"
                    )
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest test.test_me_digit_leading_model -v`

Expected: two failures because `301-EM` is rejected directly and the label parser returns a blank Model; the negative guard test passes.

- [ ] **Step 3: Implement the minimal short-code shape**

Add one alternative inside the existing `len(compact) < 8` return expression:

```python
or re.fullmatch(r"\d{2,5}-[A-Z]{2,4}", v)
```

The regex operates on `v`, not `compact`, so the printed hyphen remains mandatory.

- [ ] **Step 4: Verify GREEN and existing ME model behavior**

Run:

```powershell
python -m unittest test.test_me_digit_leading_model test.test_me_long_model_serial_collision -v
python -m unittest discover -s test -p 'test_me_*.py' -v
python -m py_compile API/API_interface_ME_ver00.py test/test_me_digit_leading_model.py
```

Expected: all discovered ME tests pass and both files compile without warnings or errors.

- [ ] **Step 5: Update canonical and mirrored documentation**

Document that short digit-leading models are accepted only in the bounded `2-5 digits + hyphen + 2-4 letters` form, with QR `0000188207` / `301-EM` as the incident. Copy the canonical rules file to `.agent_app/rules/asset_extraction_api.rules.md` and append a dated entry to `Markdowns_documentation/attributes_changes.md`.

- [ ] **Step 6: Commit the tested implementation**

```powershell
git add -- API/API_interface_ME_ver00.py test/test_me_digit_leading_model.py Markdowns_documentation/rules/asset_extraction_api.rules.md .agent_app/rules/asset_extraction_api.rules.md Markdowns_documentation/attributes_changes.md docs/superpowers/specs/2026-08-11-me-digit-leading-model-validation-design.md docs/superpowers/plans/2026-08-11-me-digit-leading-model-validation.md
git diff --cached --check
git commit -m "Accept labeled digit-leading ME model codes"
```

---

### Task 2: Deploy, reprocess one QR, and verify production state

**Files:**
- Deploy: `API/API_interface_ME_ver00.py` to `/home/developer/API/API_interface_ME_ver00.py`
- Preserve: `/home/developer/Output_jason_api/0000188207_ME_353.json` as a timestamped backup
- Use: `/home/developer/API/reset_me_asset.py`
- Use: `/home/developer/API/run_ai_and_sync.sh`

**Interfaces:**
- Consumes: the tested API file, PostgreSQL backend variables from `/home/developer/db_backend.env`, and the existing ME reset/chained-run scripts.
- Produces: live JSON and `sdi_dataset` agreement on Model `301-EM`, with `ai_status = 1` and a fresh audit entry.

- [ ] **Step 1: Verify pre-deploy production guards**

Confirm the live API SHA-256 is still `096d97edec823d93e62fae32e45793fbad11a6181c5916c19352ad7c6cdd5531`, QR `0000188207` remains unapproved with all `Col_process = 0`, and the pending ME set is empty. Abort on any mismatch.

- [ ] **Step 2: Stage and validate the tested API file**

Upload it as `/home/developer/API/API_interface_ME_ver00.py.codex_stage_20260811`, verify the staged SHA-256 matches the local tested file, and compile the staged path with the production virtualenv before replacing anything.

- [ ] **Step 3: Acquire the AI lock, create backups, and deploy atomically**

Acquire `/tmp/ai_check.lock` with `flock` so cron cannot overlap. Create `/home/developer/deploy_backups/me_digit_model_<UTCstamp>/`, copy the live API and JSON there, write the current parameterized `sdi_dataset` row as JSON, then atomically move the staged API into place. No Flask/Gunicorn restart is required because the extractor is a per-run script.

- [ ] **Step 4: Reset and run the single-QR chained pipeline**

Under the same lock, run:

```bash
cd /home/developer/API
python reset_me_asset.py 0000188207 --apply
./run_ai_and_sync.sh /home/developer/API/API_interface_ME_ver00.py
```

Immediately after reset and before launching the chain, use parameterized DB reads to assert that the pending ME set is exactly `['0000188207']`; abort and restore the backup if another ME QR is pending.

- [ ] **Step 5: Verify live output and audit evidence**

Require all of the following:

- JSON `structured_data.Model == "301-EM"` and `Serial Number == "5340RFS13150043"`.
- PostgreSQL `sdi_dataset.Model == "301-EM"` and `Serial == "5340RFS13150043"`.
- `QR_codes.ai_status == 1` and all three asset rows remain `Col_process == 0`.
- The new JSON is newer than the backup and the audit trail contains the chained JSON-sync update for this QR.
- The deployed API hash equals the locally tested hash; no unrelated ME QR was processed.

- [ ] **Step 6: Preserve deployment evidence**

Record the backup directory, deployment hash, chained-run log path, JSON values, database values, and audit row identifiers in the final handoff.
