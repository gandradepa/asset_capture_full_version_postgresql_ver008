# Asset Group Electrical Distribution Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a constrained `elec_dist_setup` Y/N flag, defaulting to `N`, to `public."Asset_Group"` in Local and VM PostgreSQL.

**Architecture:** Use one owner-run, transactional, idempotent SQL migration for both environments. Take a verified `pg_dump` immediately before each application, then verify the catalog definition, constraint, defaulted row counts, and invalid-value count independently on each database.

**Tech Stack:** PostgreSQL 18 (Local), PostgreSQL owner-role CLI tools, PowerShell/OpenSSH, SQL migration under `scripts/migrations/`.

## Global Constraints

- Target table is `public."Asset_Group"` in database `qr_code_db`.
- The stored business values are exactly `Y` and `N`; `N` is the default.
- The column name is lowercase `elec_dist_setup`.
- Local is `127.0.0.1:5432` as owner `postgres`.
- VM is `/tmp:5433` as owner `developer`, reached through `developer@142.103.68.1`.
- Take and verify a PostgreSQL dump before applying DDL in each environment.
- Do not modify unrelated dirty-worktree files.

---

### Task 1: Create the guarded schema migration

**Files:**
- Create: `scripts/migrations/2026-08-04_asset_group_elec_dist_setup.sql`
- Modify: `Markdowns_documentation/special_processes/04_database_topography.md`

**Interfaces:**
- Consumes: existing `public."Asset_Group"` table with 338 rows and owner-role PostgreSQL connection.
- Produces: `elec_dist_setup CHAR(1) NOT NULL DEFAULT 'N'` plus `ck_asset_group_elec_dist_setup` limiting values to `Y`/`N`.

- [ ] **Step 1: Confirm both pre-migration schemas**

Run catalog queries against Local and VM and confirm that `Asset_Group` exists, contains 338 rows, and does not already have `elec_dist_setup`.

- [ ] **Step 2: Write the transactional migration**

Add the column idempotently, populate nulls with `N`, reject incompatible pre-existing definitions or values, set the default and `NOT NULL`, add and validate the Y/N check constraint, and commit atomically.

- [ ] **Step 3: Document the schema contract**

Record the flag type, allowed values, default, and migration path in the canonical database-topography document.

- [ ] **Step 4: Run migration syntax rehearsal**

Run the migration inside a transaction that is rolled back, then query within that transaction to confirm the expected catalog and row-state results before touching either persistent environment.

### Task 2: Back up, apply, and verify Local

**Files:**
- Create: `db_backups/qr_code_db_local_pre_elec_dist_setup_<timestamp>.dump` (operational artifact; not source-controlled)

**Interfaces:**
- Consumes: Task 1 migration and Local PostgreSQL owner connection.
- Produces: verified Local schema and a non-empty custom-format restore point.

- [ ] **Step 1: Create and verify the Local backup**

Run local `pg_dump --format=custom --no-owner --no-privileges`, require exit code 0, and confirm the output file has non-zero length.

- [ ] **Step 2: Apply the migration**

Run local `psql -X -v ON_ERROR_STOP=1` against `qr_code_db` with the migration file.

- [ ] **Step 3: Verify Local**

Confirm the column is `character(1)`, is non-nullable, defaults to `'N'::bpchar`, has a validated Y/N constraint, all 338 rows equal `N`, and zero rows contain null or invalid values.

### Task 3: Back up, apply, and verify VM

**Files:**
- Create: `/home/developer/db_backups/qr_code_db_vm_pre_elec_dist_setup_<timestamp>.dump` (VM operational artifact; not source-controlled)

**Interfaces:**
- Consumes: Task 1 migration and VM PostgreSQL owner connection.
- Produces: verified VM schema and a non-empty custom-format restore point.

- [ ] **Step 1: Copy the migration to a temporary VM path**

Transfer the exact local migration to `/tmp/2026-08-04_asset_group_elec_dist_setup.sql`, then compare SHA-256 hashes.

- [ ] **Step 2: Create and verify the VM backup**

Run VM `pg_dump --format=custom --no-owner --no-privileges`, require exit code 0, and confirm the output file has non-zero length.

- [ ] **Step 3: Apply the migration**

Run VM `psql -X -v ON_ERROR_STOP=1` against `qr_code_db` with the uploaded migration.

- [ ] **Step 4: Verify VM and cross-environment parity**

Run the same catalog/data verification used locally and compare both outputs for identical column, constraint, and row-state results.
