# ME Label-Authoritative Model Values Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept any bounded Model value that is explicitly attached to a supported model-field label on the ME sequence `-0` nameplate, then deploy the tested behavior and repair production QR `0000188234`.

**Architecture:** Keep `_is_model_code_candidate()` strict for unlabeled tokens and add a separate label-authoritative contract carried through the label parser, OCR, targeted seq-0 reread, evidence checks, and UI-parity merge. Context controls acceptance; model-number shape no longer controls explicitly labeled values. PostgreSQL remains downstream of the fresh JSON and the normal ME reviewer JSON-sync hook.

**Tech Stack:** Python 3.12+, `unittest`, OpenCV/Tesseract OCR helpers, OpenAI structured vision rereads, PostgreSQL through `db.py`, Bash deployment scripts, Gunicorn ME reviewer.

## Global Constraints

- Mechanical Model remains owned only by sequence `-0`; never borrow it from `-1`, `-2`, `-3`, or `-4`.
- Explicit labels are limited to `Model`, `Model No.`, `Model Number`, `Unit Model`, `Type`, `Catalog`, and `Item`.
- Labeled values may be alphabetic-only, numeric-only, mixed, spaced, or punctuated, but must contain an alphanumeric character, contain no control/newline characters, and be at most 64 characters.
- Unlabeled candidates keep every current strict shape, tag, rating, date, prose, and collision guard.
- Model and Serial Number may never be identical after compact comparison.
- Do not erase reviewed values; production preflight must require unapproved QR state and `Col_process = 0` for every target asset row.
- Use parameterized SQL only and the production PostgreSQL `db.py` layer.
- Reprocess through `API/run_ai_and_sync.sh`; never run extraction or bookkeeping sync standalone.
- Update canonical documentation before mechanically synchronizing the `.agent_app` mirror.
- Preserve unrelated user files and changes, including the existing untracked `%sn` and `HTTP` paths.

---

### Task 1: Implement the label-authoritative Model contract with TDD

**Files:**
- Create: `test/test_me_label_authoritative_model.py`
- Modify: `API/API_interface_ME_ver00.py:1928-2290`
- Modify: `API/API_interface_ME_ver00.py:3237-3395`
- Modify: `API/API_interface_ME_ver00.py:4248-4350`
- Modify: `API/API_interface_ME_ver00.py:4510-4545`
- Modify: `API/API_interface_ME_ver00.py:6200-6400`
- Modify: `API/API_interface_ME_ver00.py:5334-5370`
- Modify: `Markdowns_documentation/rules/asset_extraction_api.rules.md`
- Modify: `.agent_app/rules/asset_extraction_api.rules.md`
- Modify: `Markdowns_documentation/attributes_changes.md`

**Interfaces:**
- Consumes: `AssetProcessor._normalize_model_candidate(value, manufacturer_hint="")`, `_is_model_code_candidate(value, manufacturer_hint="")`, `_parse_nameplate_model_serial(text)`, `_model_candidates_near_label(text, manufacturer_hint="")`, `_has_model_label_evidence(model_value, images, evidence_texts=None)`, `_build_ui_parity_struct(...)`, `_reread_model_serial_from_nameplate_llm(...)`.
- Produces: `AssetProcessor._is_labeled_model_value_candidate(value, serial_value="") -> bool`; extends `_is_model_code_candidate(value, manufacturer_hint="", explicitly_labeled=False) -> bool`; label-aware parser/OCR/reread/UI-parity behavior preserving `QCC-M`.

- [ ] **Step 1: Create the focused failing regression file**

Create `test/test_me_label_authoritative_model.py` with real production-method assertions:

```python
"""Regression coverage for format-agnostic, explicitly labeled ME Models."""

from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "API"
sys.path.insert(0, str(API_DIR))

from API_interface_ME_ver00 import AssetProcessor  # noqa: E402


class MeLabelAuthoritativeModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = object.__new__(AssetProcessor)

    def test_explicit_model_labels_accept_varied_formats(self) -> None:
        cases = {
            "Model: QCC-M\nS/N: QCCM1608B00041": "QCC-M",
            "Model No.: 301-EM\nSerial: 5340RFS13150043": "301-EM",
            "Unit Model: 03134\nProduct No.: 599-0335": "03134",
            "Type: SERIES A / REV.2\nSerial No.: 88219": "SERIES A/REV.2",
            "Catalog: A&B_7+#2\nVoltage: 208 V": "A&B_7+#2",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = self.processor._parse_nameplate_model_serial(text)
                self.assertEqual(parsed["Model"], expected)

    def test_labeled_parser_stops_at_neighboring_fields(self) -> None:
        parsed = self.processor._parse_nameplate_model_serial(
            "Model: QCC-M Serial: QCCM1608B00041 Rating: 90-240 VAC Date: AUGUST 2016"
        )
        self.assertEqual(parsed["Model"], "QCC-M")
        self.assertEqual(parsed["Serial Number"], "QCCM1608B00041")

    def test_unlabeled_generic_candidates_remain_strict(self) -> None:
        for value in ("QCC-M", "118668", "208V", "1200 VAC", "SERIES A / REV.2"):
            with self.subTest(value=value):
                self.assertFalse(self.processor._is_model_code_candidate(value))

    def test_labeled_contract_rejects_empty_control_overlong_and_collision(self) -> None:
        self.assertFalse(self.processor._is_labeled_model_value_candidate(""))
        self.assertFalse(self.processor._is_labeled_model_value_candidate("QCC\nM"))
        self.assertFalse(self.processor._is_labeled_model_value_candidate("A" * 65))
        self.assertFalse(
            self.processor._is_labeled_model_value_candidate(
                "QCCM1608B00041", serial_value="QCCM1608B00041"
            )
        )

    def test_ui_parity_uses_targeted_labeled_reread_for_qcc_m(self) -> None:
        self.processor._collect_nameplate_evidence_texts = lambda _images: [
            "Critical Environment Technologies Canada Inc.\n"
            "Model: QCC-M\nS/N: QCCM1608B00041\nDate of Mfr: AUGUST 2016"
        ]
        self.processor._normalize_manufacturer_with_context = (
            lambda raw, _images, allow_ocr=True: raw
        )
        self.processor._reread_model_serial_from_nameplate_llm = (
            lambda *_args, **_kwargs: {
                "Model": "QCC-M",
                "Serial Number": "QCCM1608B00041",
            }
        )
        self.processor._reread_year_from_nameplate_llm = lambda *_args, **_kwargs: ""
        self.processor._extract_year_from_rois = lambda _images: ""
        self.processor._fallback_year_from_ocr = lambda _images: ""
        self.processor._reread_ubc_from_tag_llm = lambda *_args, **_kwargs: ""

        merged = self.processor._build_ui_parity_struct(
            qr="0000188234",
            info={"images": {"0": "0000188234 557 ME - 0.jpg"}},
            llm_cleaned={
                "Manufacturer": "Critical Environment Technologies Canada Inc.",
                "Model": "OCC-M",
                "Serial Number": "QCCM1608B00041",
                "Year": "2016",
                "UBC Tag": "RMD-0029",
                "Technical Safety BC": "",
            },
            raw_manufacturer="Critical Environment Technologies Canada Inc.",
            llm_model="OCC-M",
            raw_year="AUGUST 2016",
            has_nameplate_source=True,
            has_tsbc_source=False,
        )

        self.assertEqual(merged["Model"], "QCC-M")
        self.assertEqual(merged["Serial Number"], "QCCM1608B00041")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m unittest test.test_me_label_authoritative_model -v
```

Expected: failures because `_is_labeled_model_value_candidate` does not exist, `QCC-M` and other shape-independent labeled values parse as blank, and UI parity cannot retain the targeted `QCC-M` reread.

- [ ] **Step 3: Add the dedicated labeled-value helper and explicit provenance flag**

In `AssetProcessor`, add a bounded helper next to `_is_model_code_candidate()`:

```python
def _is_labeled_model_value_candidate(
    self,
    value: str,
    serial_value: str = "",
) -> bool:
    raw = str(value or "").strip()
    if not raw or len(raw) > 64:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return False
    if not any(ch.isalnum() for ch in raw):
        return False
    if serial_value and self._model_serial_values_collide(raw, serial_value):
        return False
    return True
```

Extend the existing generic function without changing its default behavior:

```python
def _is_model_code_candidate(
    self,
    value: str,
    manufacturer_hint: str = "",
    explicitly_labeled: bool = False,
) -> bool:
    v = (value or "").strip().upper()
    if explicitly_labeled:
        return self._is_labeled_model_value_candidate(v)
    # existing generic validation remains unchanged below
```

Broaden `_clean_model_preserving_separators()` only as a normalization step so printable punctuation survives labeled storage. Remove control characters, uppercase, collapse whitespace, normalize spacing around `/.-`, and retain printable punctuation; do not use this normalization change to make unlabeled candidates valid.

- [ ] **Step 4: Make the label parsers preserve raw line boundaries and use labeled acceptance**

In `_model_candidates_near_label()`:

```python
tail = upper[cue.end(): cue.end() + ME_MAX_MODEL_CODE_LENGTH + 80]
tail = re.split(r"[\r\n]", tail, maxsplit=1)[0]
tail = boundary_re.split(tail, maxsplit=1)[0]
```

Replace generic acceptance of the extracted adjacent value with:

```python
if candidate and self._is_model_code_candidate(
    candidate,
    manufacturer_hint,
    explicitly_labeled=True,
):
    candidates.append(candidate)
```

In `_parse_nameplate_model_serial()`, preserve the original text for labeled
candidate extraction before global whitespace collapse, prefer the first
`_model_candidates_near_label()` result, and make the existing label-regex
fallback call `_is_model_code_candidate(candidate, explicitly_labeled=True)`.
Serial parsing remains unchanged.

- [ ] **Step 5: Route labeled provenance through OCR, evidence, and targeted reread**

Apply `explicitly_labeled=True` only where provenance is guaranteed:

- parsed output from `_parse_nameplate_model_serial()`;
- output from `_model_candidates_near_label()`;
- Model returned by `_reread_model_serial_from_nameplate_llm()`, whose prompt is restricted to explicit seq-0 Model labels; and
- Model returned by label-bounded ROI OCR.

Remove the `len(model_compact) < 5` early rejection from
`_has_model_label_evidence()` and compare compact values only after the parser
has established label provenance.

In `_reread_model_serial_from_nameplate_llm()` replace:

```python
if not self._is_model_code_candidate(model_value, manufacturer_hint):
    model_value = ""
```

with:

```python
if not self._is_model_code_candidate(
    model_value,
    manufacturer_hint,
    explicitly_labeled=True,
):
    model_value = ""
```

- [ ] **Step 6: Preserve label-authoritative provenance through UI parity**

Change the local `_model_acceptable()` helper inside
`_build_ui_parity_struct()` to accept `explicitly_labeled: bool = False` and
pass it into `_is_model_code_candidate()`.

Track a local `model_label_authoritative` boolean. Set it when:

- exact seq-0 `_has_model_label_evidence()` supports the current value;
- the targeted Model reread returns an accepted bounded value; or
- label-bounded OCR supplies the final Model.

Every later recomputation of `model_weak` must call:

```python
_model_acceptable(
    merged.get("Model", ""),
    explicitly_labeled=model_label_authoritative,
)
```

Do not change `_evaluate_llm_candidate()`; raw primary multi-image output has no
label provenance and must remain subject to the generic gate so weak values
still trigger the targeted reread.

Update the final Model corroboration log/confidence path to treat exact label
evidence as explicit provenance. Keep the existing final Model/Serial collision
guard authoritative.

- [ ] **Step 7: Run focused GREEN and all ME regressions**

Run:

```powershell
python -m unittest test.test_me_label_authoritative_model -v
python -m unittest discover -s test -p 'test_me_*.py' -v
python -m py_compile API/API_interface_ME_ver00.py test/test_me_label_authoritative_model.py
```

Expected: all focused tests pass; all existing digit-leading, long-model,
collision, and UBC-consensus tests remain green; compilation exits `0`.

- [ ] **Step 8: Update canonical and mirrored operating documentation**

In `Markdowns_documentation/rules/asset_extraction_api.rules.md`, document:

- explicit seq-0 Model labels are format-authoritative;
- values are bounded to 64 characters and stop at line/field boundaries;
- unlabeled candidates remain shape-restricted;
- targeted reread and label OCR carry explicit provenance; and
- QR `0000188234` / `QCC-M` is the incident.

Append `## 2026-08-11: ME label-authoritative Model values` to
`Markdowns_documentation/attributes_changes.md`. Then mechanically copy the
canonical rules file to `.agent_app/rules/asset_extraction_api.rules.md` and
verify both SHA-256 hashes match.

- [ ] **Step 9: Review and commit the tested implementation**

Run:

```powershell
git diff --check
git diff -- API/API_interface_ME_ver00.py test/test_me_label_authoritative_model.py Markdowns_documentation/rules/asset_extraction_api.rules.md .agent_app/rules/asset_extraction_api.rules.md Markdowns_documentation/attributes_changes.md
git status --short
```

Stage only the five implementation/test/documentation paths and commit:

```powershell
git add -- API/API_interface_ME_ver00.py test/test_me_label_authoritative_model.py Markdowns_documentation/rules/asset_extraction_api.rules.md .agent_app/rules/asset_extraction_api.rules.md Markdowns_documentation/attributes_changes.md
git diff --cached --check
git commit -m "Accept explicitly labeled ME model values"
```

---

### Task 2: Deploy and repair QR 0000188234 in production

**Files:**
- Deploy: `API/API_interface_ME_ver00.py` to `/home/developer/API/API_interface_ME_ver00.py`
- Read: `/home/developer/Capture_photos_upload/0000188234 557 ME - 0.jpg`
- Replace through chained reprocessing: `/home/developer/Output_jason_api/0000188234_ME_557.json`
- Update through normal ME reviewer JSON sync: PostgreSQL `sdi_dataset`
- Back up under: `/home/developer/deploy_backups/me_label_model_0000188234_<UTC>`

**Interfaces:**
- Consumes: committed and fully tested ME API file; `API/reset_me_asset.py`; `API/run_ai_and_sync.sh`; `/tmp/ai_check.lock`; PostgreSQL `db.py`; ME reviewer on `127.0.0.1:8001`.
- Produces: production JSON and `sdi_dataset` Model `QCC-M`, preserved Serial `QCCM1608B00041`, audit entries, `ai_status=1`, and unchanged human-processing state.

- [ ] **Step 1: Run fresh local verification and calculate the deploy hash**

Run:

```powershell
python -m unittest discover -s test -p 'test_me_*.py' -v
python -m py_compile API/API_interface_ME_ver00.py test/test_me_label_authoritative_model.py
$newHash=(Get-FileHash -Algorithm SHA256 -LiteralPath 'API/API_interface_ME_ver00.py').Hash.ToLower()
Write-Output $newHash
git status --short
```

Expected: all ME tests pass, compilation exits `0`, and only the known untracked
`%sn`/`HTTP` paths remain outside committed work.

- [ ] **Step 2: Perform read-only production preflight**

Verify the live API still has the previously deployed hash
`f620e1326d054289a067d7349ed670d7c1ba2b6b082e04d8c4bdfa6503a88537`, the
photo hash still matches the investigated image, and the shared lock state:

```powershell
ssh -o BatchMode=yes developer@142.103.68.1 "sha256sum /home/developer/API/API_interface_ME_ver00.py '/home/developer/Capture_photos_upload/0000188234 557 ME - 0.jpg'; if flock -n /tmp/ai_check.lock -c true; then echo AI_LOCK=available; else echo AI_LOCK=busy; fi"
```

Use production Python plus `db.py` with parameterized SQL to assert:

```python
target = conn.execute(
    'SELECT ai_status, "Approved", sdi, asset_type FROM "QR_codes" WHERE "QR_code_ID" = ?',
    ("0000188234",),
).fetchone()
assets = conn.execute(
    'SELECT code_assets, "Col_process" FROM "QR_code_assets" WHERE qr_code_id = ? ORDER BY code_assets',
    ("0000188234",),
).fetchall()
pending_me = conn.execute(
    'SELECT "QR_code_ID" FROM "QR_codes" WHERE CAST(ai_status AS TEXT) = ? '
    'AND UPPER(COALESCE(asset_type, ?)) = ? ORDER BY "QR_code_ID"',
    ("0", "", "ME"),
).fetchall()
```

Require: target `ai_status=1`, `Approved` null/false, `asset_type=ME`, all three
`Col_process=0`, and no pending ME QR. Stop without mutation if any condition
differs.

- [ ] **Step 3: Stage and compile the tested API on the VM**

Upload to a commit-specific `.py` staging path without replacing live code:

```powershell
scp -o BatchMode=yes API/API_interface_ME_ver00.py developer@142.103.68.1:/home/developer/API/API_interface_ME_ver00.py.codex_stage_label_model.py
ssh -o BatchMode=yes developer@142.103.68.1 "/home/developer/asset_capture_app_dev/venv/bin/python3 -m py_compile /home/developer/API/API_interface_ME_ver00.py.codex_stage_label_model.py; sha256sum /home/developer/API/API_interface_ME_ver00.py.codex_stage_label_model.py"
```

Require the staged hash to equal `$newHash`.

- [ ] **Step 4: Acquire the AI lock, back up, atomically deploy, and smoke-test**

Run one guarded remote session that holds file descriptor `200` on
`/tmp/ai_check.lock` across backup, deploy, reset, extraction, and verification:

```bash
set -Eeuo pipefail
exec 200>/tmp/ai_check.lock
flock -n 200 || exit 75
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/home/developer/deploy_backups/me_label_model_0000188234_${stamp}"
mkdir -p "$backup"
cp -a /home/developer/API/API_interface_ME_ver00.py "$backup/API_interface_ME_ver00.py.before"
cp -a /home/developer/Output_jason_api/0000188234_ME_557.json "$backup/0000188234_ME_557.json.before"
chmod --reference=/home/developer/API/API_interface_ME_ver00.py /home/developer/API/API_interface_ME_ver00.py.codex_stage_label_model.py
mv -f -- /home/developer/API/API_interface_ME_ver00.py.codex_stage_label_model.py /home/developer/API/API_interface_ME_ver00.py
/home/developer/asset_capture_app_dev/venv/bin/python3 -m py_compile /home/developer/API/API_interface_ME_ver00.py
```

Import the deployed module and assert:

```python
processor = object.__new__(AssetProcessor)
assert processor._parse_nameplate_model_serial(
    "Critical Environment Technologies Canada Inc.\n"
    "Model: QCC-M\nS/N: QCCM1608B00041"
) == {"Model": "QCC-M", "Serial Number": "QCCM1608B00041"}
assert not processor._is_model_code_candidate("QCC-M")
```

- [ ] **Step 5: Reset and reprocess only QR 0000188234 through the chain**

While retaining the same AI lock and after re-running the DB safety assertions:

```bash
set -a
. /home/developer/db_backend.env
set +a
/home/developer/asset_capture_app_dev/venv/bin/python3 /home/developer/API/reset_me_asset.py 0000188234 --apply
export ME_MAX_WORKERS=1 ME_OCR_MODE=light ME_HYBRID_OCR_AGENT=0
export ME_SIMPLE_MODE=1 ME_SIMPLE_MAX_IMAGES=3 ME_SIMPLE_MAX_TOKENS=550 ME_IMAGE_DETAIL=low
export ME_UBC_CONSENSUS_ENABLED=1 ME_UBC_JUDGE_MODEL=gpt-5.6-terra
export ME_UBC_JUDGE_DETAIL=original ME_UBC_JUDGE_REASONING_EFFORT=low
export ME_API_MAX_RETRIES=1 ME_API_TIMEOUT=45 ME_API_RETRY_DELAY=0.5
cd /home/developer/API
./run_ai_and_sync.sh /home/developer/API/API_interface_ME_ver00.py
```

Immediately assert the fresh JSON contains `structured_data.Model == "QCC-M"`
and `structured_data["Serial Number"] == "QCCM1608B00041"`. If extraction
fails, retain the lock, preserve any failed fresh JSON in the backup directory,
restore the original JSON, and restore the original `ai_status` with
parameterized SQL before releasing the lock.

- [ ] **Step 6: Run the normal ME reviewer sync for exactly the target JSON**

Compare `/home/developer/asset_capture_app_dev/data/processed_json.log` against
ME JSON mtimes. Require `0000188234_ME_557.json` to be the only pending ME
reviewer JSON. Trigger the normal guarded hook:

```bash
curl --silent --show-error --max-time 120 --output /dev/null \
  http://127.0.0.1:8001/
```

The expected unauthenticated HTTP result is a redirect to `/login`; the
`before_request` hook performs the JSON-to-`sdi_dataset` sync before that
redirect.

- [ ] **Step 7: Verify every production boundary**

Use a parameterized production Python check to assert:

```python
assert payload["structured_data"]["Model"] == "QCC-M"
assert payload["structured_data"]["Serial Number"] == "QCCM1608B00041"
assert payload["confidence_scores"]["Model"] > 0

target = conn.execute(
    'SELECT ai_status, "Approved", sdi, asset_type FROM "QR_codes" WHERE "QR_code_ID" = ?',
    ("0000188234",),
).fetchone()
sdi = conn.execute(
    'SELECT "Model", "Serial", "Avg_ai_conf" FROM "sdi_dataset" WHERE "QR Code" = ?',
    ("0000188234",),
).fetchone()
assets = conn.execute(
    'SELECT "Col_process" FROM "QR_code_assets" WHERE qr_code_id = ? ORDER BY code_assets',
    ("0000188234",),
).fetchall()
audits = conn.execute(
    'SELECT id, field_name, old_value, new_value, source, description '
    'FROM audit_trail WHERE qr_code = ? AND id > ? ORDER BY id',
    ("0000188234", audit_baseline),
).fetchall()
```

Require:

- live API hash equals the committed local file;
- JSON and `sdi_dataset` both hold Model `QCC-M`;
- Serial remains `QCCM1608B00041`;
- `ai_status=1`, `Approved` unchanged, `sdi=0`, and all `Col_process=0`;
- an AI JSON-sync audit changes Model from blank to `QCC-M`;
- the ME pending queue is empty;
- the reviewer processed-log mtime matches the fresh JSON;
- `curl --fail http://127.0.0.1:8001/health` succeeds; and
- the timestamped backup contains the previous API, JSON, deployment log, and
  executed deployment script.

- [ ] **Step 8: Run final local verification and report deployment evidence**

Run again:

```powershell
python -m unittest discover -s test -p 'test_me_*.py' -v
python -m py_compile API/API_interface_ME_ver00.py test/test_me_label_authoritative_model.py
git diff --check
git status --short
```

Report the implementation commit, deployed SHA-256, test count, backup path,
JSON/DB Model and Serial, audit IDs, queue state, and service health. Explicitly
state whether a service restart was required.
