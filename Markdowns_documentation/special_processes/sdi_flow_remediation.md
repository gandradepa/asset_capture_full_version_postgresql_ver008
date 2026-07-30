# SDI Flow Integrity — Remediation Runbook

How to read and act on the findings from `scripts/audit_sdi_flow_integrity.py`, the
read-only reconciliation for the **approve → package → archive** flow (Electrical).

> **Golden rule:** never hand-edit PostgreSQL operational tables to change exclusion or approval. Use the SDI/Review workflows and audit-backed routes.
> Use the app endpoints below — they write both legs of each pair atomically and keep
> the review JSON in sync. Direct SQL drifts the stores apart (that is exactly the
> class of problem this audit detects).

---

## 1. The flow and its states

```
(1) approve   sdi_dataset_EL.Approved = '1'  (+ QR_codes.Approved = '1', JSON Approved = "True")
(2) package   approved & NOT-excluded rows  ->  sdi_print_out      ("Packaged Assets" tab)
(3) archive   Package Actions -> Archive     ->  sdi_print_out_arch
```

**SDI-exclusion (Manual Entry)** is a parallel *terminal* state. It is kept consistent
across two stores by `_db_toggle_qr_sdi()` (Asset_dashboard_EL.py:1936) via
`POST /toggle_sdi/<doc_id>`:

- `QR_codes.sdi = 1`  ⇔  `QR_code_assets.Col_process = 2`
- The review JSON `ExcludeSDI` is **derived** from `Col_process` at load time
  (Asset_dashboard_EL.py:2189) — it is *not* a stored third source of truth.

> **Matching note:** `QR_code_assets.code_assets` is `"<qr> <building> EL - <n>"`, not the
> bare QR. Always match with `code_assets LIKE '<qr>%'` (the audit and the app both do).
> An exact-match query on the bare QR will *wrongly* report "no row".

An asset that is **excluded (`sdi=1`) never enters packaging** (`build_unpackaged_dataset`
drops `sdi=1`, SDI_process/app.py:861), so it never reaches the archive. That is **by
design** — excluded ≠ broken.

---

## 2. Running the audit

```bash
# Full human report (prod):
python3 /home/developer/scripts/audit_sdi_flow_integrity.py

# Cron-friendly (prints only on findings):
python3 /home/developer/scripts/audit_sdi_flow_integrity.py --quiet

# Drill down / filter:
python3 /home/developer/scripts/audit_sdi_flow_integrity.py --qr 0000184485
python3 /home/developer/scripts/audit_sdi_flow_integrity.py --building 314-1
```

Exit code: `0` = no DRIFT, `1` = ≥1 DRIFT (or any finding with `--strict`), `2` = setup error.

---

## 3. Finding types and what to do

| Severity | Finding | Meaning | Action |
|---|---|---|---|
| **DRIFT** | `exclusion_pair_mismatch` | `sdi=1` without `Col_process=2` (or inverse) — exclusion set outside the canonical path | §4.1 — re-apply via `/toggle_sdi` |
| **DRIFT** | `approval_mismatch` | On a non-archived QR, `Approved` disagrees across `sdi_dataset_EL` / `QR_codes` / JSON | §4.2 — re-sync via `/toggle_approved` |
| **WORKLIST** | `approved_but_unarchivable` | Approved **and** properly SDI-excluded → terminal; never packages/archives. **Valid state**, needs a human decision | §4.3 — re-include or keep excluded |
| **INFO** | `approved_blank_identity` | Approved with blank UBC Asset Tag | §4.4 — supply a tag if it should be in SDI |
| **INFO** | `ready_not_packaged` | Approved, includible, not archived, not yet packaged | Normal backlog — package it in the SDI Process app |

`DRIFT` should be **zero** in a healthy DB and is the only class that fails the run.
`WORKLIST` / `INFO` are operational backlogs, expected to be non-zero.

---

## 4. Remediation recipes

`doc_id` = `<qr>_EL_<building>` (e.g. `0000184485_EL_314-1`). All endpoints require an
authenticated `reviewer_electrical:editor` session on the EL app
(`reviewel.assetcap.facilities.ubc.ca`, local `127.0.0.1:8005`). Prefer doing these
through the **UI** (the dashboard buttons call exactly these routes); the `curl` forms
are for scripted/batch fixes.

> **Before any change:** take a snapshot (`scripts/backup_daily.py` or copy the DB) and
> re-run the audit `--qr <code>` to capture the before-state.

### 4.1 `exclusion_pair_mismatch` (re-align the exclusion pair)
Re-apply the exclusion state through the canonical toggle so both legs match. From the
current (mismatched) state, toggle once and confirm; if it lands on the wrong value,
toggle again:
```bash
curl -X POST "https://reviewel.assetcap.facilities.ubc.ca/toggle_sdi/<doc_id>" --cookie "session=<token>"
```
Re-run `--qr <code>`; the mismatch must clear.

### 4.2 `approval_mismatch` (re-sync approval across stores)
`toggle_approved` rewrites `sdi_dataset_EL.Approved` + `QR_codes.Approved` + JSON together,
so toggling **twice** (off → on) re-aligns all three:
```bash
curl -X POST ".../toggle_approved/<doc_id>" --cookie "session=<token>"   # -> un-approve (all blank)
curl -X POST ".../toggle_approved/<doc_id>" --cookie "session=<token>"   # -> approve (all '1'/'True')
```
If the asset should **not** be approved, toggle **once** (to un-approve) and stop.
Re-run `--qr <code>` to confirm the three stores agree.

### 4.3 `approved_but_unarchivable` (the case-by-case worklist)
These are **approved + properly SDI-excluded**. Decide per asset:

**Decision A — it belongs in SDI (re-include):**
1. If the row has a **blank UBC Asset Tag** (the `[BLANK UBC TAG]` flag): the review form
   is locked once approved (early return at Asset_dashboard_EL.py:2924), so first
   un-approve (`POST /toggle_approved/<doc_id>`), set the tag via the review form
   (`POST /review/<doc_id>` → `save_review`, syncs JSON + `sdi_dataset_EL`), then re-approve.
2. Clear the exclusion (one toggle, `sdi 1→0`, also sets `Col_process=0`):
   ```bash
   curl -X POST ".../toggle_sdi/<doc_id>" --cookie "session=<token>"
   ```
3. The asset now appears in the SDI Process **Unpackaged** list → create the package →
   **Package Actions → Archive**.

**Decision B — it is correctly excluded (keep out of SDI):**
- **No change needed** — the pair is already consistent (`sdi=1` + `Col_process=2`); it
  stays out of SDI permanently. (Optional, only if "approved *and* excluded" is considered
  misleading in your process: un-approve it with a single `POST /toggle_approved/<doc_id>`.)
- Note: with the SLD dropdown rule set to **"archived only"**, a building keeps showing in
  the SLD dropdown as long as it has any excluded-non-archived QR. That is the accepted
  trade-off of Decision B.

### 4.4 `approved_blank_identity` (data quality)
A blank UBC Asset Tag may be acceptable for a Manual-Entry/excluded asset. If the asset
should be in SDI, supply the tag first (see §4.3 Decision A step 1). Otherwise no action.

---

## 5. After remediation
1. Re-run the audit for the affected QR(s): `--qr <code>` → confirm the finding cleared.
2. Run the full audit: expect `DRIFT=0`; `WORKLIST`/`INFO` reduced by what you fixed.
3. The nightly `run_update_db.sh` rebuild can re-introduce `approval_mismatch` if approval
   lives only in JSON/`QR_codes` — if a fix reappears next day, that points at the rebuild
   path (a separate follow-up, see the audit's purpose note).

## 6. Snapshot of the first run (2026-06-01, prod)
- `DRIFT=1` — `approval_mismatch` qr `0000184490` (217): `QR_codes`=approved, `sdi_dataset_EL`=not.
- `WORKLIST=12` — approved + SDI-excluded (217: 7, 750: 3, 314-1: 2). All have a consistent
  `sdi=1`+`Col_process=2` pair (properly excluded).
- `INFO=5` — approved rows with blank UBC tags (subset of the worklist).
