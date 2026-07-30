"""Regression tests for the placeholder-sync wipe guard across EL, ME, BF.

Background
----------
A periodic placeholder-sync in each dashboard calls a blank-payload upsert
when a new image lands in Capture_photos_upload. That upsert overwrites every
editable column in the SDI row with "". A guard named ``_sdi_row_has_data*``
prevents the wipe when the row already holds real AI-captured content.

A prior EL incident was caused by the guard being too narrow — it only looked
at ``UBC Asset Tag`` / ``Equipment ID``. Editing the tag to a value that
blanked both identity columns then let the next placeholder sync zero the
whole row.

These tests assert the WIDENED predicate across all three dashboards. Each
case seeds a representative row, runs the exact predicate the production
guard uses against a real SQLite DB, and checks the result.

IMPORTANT
---------
The predicates below are kept in sync with the production guard functions:
  * EL -> _sdi_row_has_data in
          review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py
  * ME -> _sdi_row_has_data_me in
          review/Asset_dasboard_browser_ME/asset_plate_reviewer.py
  * BF -> _sdi_row_has_data_bf in
          review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py
If you widen or narrow any guard, update the matching predicate here so this
regression stays honest. The full-module import is avoided on purpose: the
EL/ME/BF dashboard modules do ``Flask(__name__)`` and load auth / GPS blueprints
at import time, which requires the production environment.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import tempfile
import unittest


# ---------------------------------------------------------------------------
# Predicates — MIRROR the production guard functions. Keep in sync.
# ---------------------------------------------------------------------------

EL_GUARD_SQL = (
    'SELECT 1 FROM "sdi_dataset_EL" '
    'WHERE "QR Code"=? AND "Building"=? AND ('
    'TRIM(COALESCE("UBC Asset Tag",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Equipment ID",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Branch Panel",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Supply From",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Volts",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Ampere",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Location",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Description",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Asset Group",\'\')) <> \'\' '
    'OR TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), \'\')) NOT IN (\'\', \'0\', \'0.0\')'
    ') LIMIT 1'
)

ME_GUARD_SQL = (
    'SELECT 1 FROM "sdi_dataset" '
    'WHERE "QR Code"=? AND "Building"=? AND ('
    'TRIM(COALESCE("UBC Tag",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Manufacturer",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Model",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Serial",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Asset Group",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Description",\'\')) <> \'\' '
    'OR TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), \'\')) NOT IN (\'\', \'0\', \'0.0\')'
    ') LIMIT 1'
)

BF_GUARD_SQL = (
    'SELECT 1 FROM "sdi_dataset" '
    'WHERE "QR Code"=? AND "Building"=? AND ('
    'TRIM(COALESCE("UBC Tag",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Manufacturer",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Model",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Serial",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Asset Group",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Description",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Diameter",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Year",\'\')) <> \'\' '
    'OR TRIM(COALESCE("Technical Safety BC",\'\')) <> \'\' '
    'OR TRIM(COALESCE(CAST("Avg_ai_conf" AS TEXT), \'\')) NOT IN (\'\', \'0\', \'0.0\')'
    ') LIMIT 1'
)


# Minimal schemas — only the columns each guard touches plus the composite key.
EL_SCHEMA = '''
CREATE TABLE "sdi_dataset_EL" (
    "QR Code" TEXT,
    "Building" TEXT,
    "UBC Asset Tag" TEXT,
    "Equipment ID" TEXT,
    "Branch Panel" TEXT,
    "Supply From" TEXT,
    "Volts" TEXT,
    "Ampere" TEXT,
    "Location" TEXT,
    "Description" TEXT,
    "Asset Group" TEXT,
    "Avg_ai_conf" REAL
);
'''

SDI_SCHEMA = '''
CREATE TABLE "sdi_dataset" (
    "QR Code" TEXT,
    "Building" TEXT,
    "UBC Tag" TEXT,
    "Manufacturer" TEXT,
    "Model" TEXT,
    "Serial" TEXT,
    "Asset Group" TEXT,
    "Description" TEXT,
    "Diameter" TEXT,
    "Year" TEXT,
    "Technical Safety BC" TEXT,
    "Avg_ai_conf" REAL
);
'''


def _guard_hit(conn: sqlite3.Connection, sql: str, qr: str, building: str) -> bool:
    return conn.execute(sql, (qr, building)).fetchone() is not None


def _insert_el_row(conn: sqlite3.Connection, **cols) -> None:
    defaults = dict.fromkeys(
        (
            "UBC Asset Tag", "Equipment ID", "Branch Panel", "Supply From",
            "Volts", "Ampere", "Location", "Description", "Asset Group",
        ),
        "",
    )
    defaults["Avg_ai_conf"] = None
    defaults.update(cols)
    defaults.setdefault("QR Code", "Q1")
    defaults.setdefault("Building", "B1")
    col_sql = ", ".join(f'"{k}"' for k in defaults)
    placeholders = ", ".join(["?"] * len(defaults))
    conn.execute(
        f'INSERT INTO "sdi_dataset_EL" ({col_sql}) VALUES ({placeholders})',
        list(defaults.values()),
    )
    conn.commit()


def _insert_sdi_row(conn: sqlite3.Connection, **cols) -> None:
    defaults = dict.fromkeys(
        (
            "UBC Tag", "Manufacturer", "Model", "Serial", "Asset Group",
            "Description", "Diameter", "Year", "Technical Safety BC",
        ),
        "",
    )
    defaults["Avg_ai_conf"] = None
    defaults.update(cols)
    defaults.setdefault("QR Code", "Q1")
    defaults.setdefault("Building", "B1")
    col_sql = ", ".join(f'"{k}"' for k in defaults)
    placeholders = ", ".join(["?"] * len(defaults))
    conn.execute(
        f'INSERT INTO "sdi_dataset" ({col_sql}) VALUES ({placeholders})',
        list(defaults.values()),
    )
    conn.commit()


class ElGuardTests(unittest.TestCase):
    """Widened EL guard — the incident-class case that motivated the fix."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "t.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(EL_SCHEMA)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_protects_row_with_branch_panel_and_supply_from_only(self) -> None:
        # Reproduces the 0000184474 incident shape: identity columns cleared
        # by an edit, but real AI-captured content still present elsewhere.
        _insert_el_row(
            self.conn,
            **{
                "UBC Asset Tag": "",
                "Equipment ID": "",
                "Branch Panel": "S1.5D1",
                "Supply From": "TX-S1.5N1",
                "Avg_ai_conf": 100.0,
            },
        )
        self.assertTrue(_guard_hit(self.conn, EL_GUARD_SQL, "Q1", "B1"))

    def test_protects_row_with_only_avg_ai_conf(self) -> None:
        _insert_el_row(self.conn, **{"Avg_ai_conf": 55.0})
        self.assertTrue(_guard_hit(self.conn, EL_GUARD_SQL, "Q1", "B1"))

    def test_protects_row_with_only_description(self) -> None:
        _insert_el_row(self.conn, **{"Description": "Panel - PNL-S1.5D1"})
        self.assertTrue(_guard_hit(self.conn, EL_GUARD_SQL, "Q1", "B1"))

    def test_releases_row_when_all_signals_blank(self) -> None:
        # Row exists but every content column is blank — this IS the
        # placeholder-eligible case the sync is designed to populate.
        _insert_el_row(self.conn)
        self.assertFalse(_guard_hit(self.conn, EL_GUARD_SQL, "Q1", "B1"))

    def test_releases_when_row_missing(self) -> None:
        self.assertFalse(_guard_hit(self.conn, EL_GUARD_SQL, "Q1", "B1"))


class MeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "t.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SDI_SCHEMA)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_protects_row_with_manufacturer_and_model(self) -> None:
        _insert_sdi_row(
            self.conn,
            **{"UBC Tag": "", "Manufacturer": "Viking", "Model": "VK-100"},
        )
        self.assertTrue(_guard_hit(self.conn, ME_GUARD_SQL, "Q1", "B1"))

    def test_releases_when_all_signals_blank(self) -> None:
        _insert_sdi_row(self.conn)
        self.assertFalse(_guard_hit(self.conn, ME_GUARD_SQL, "Q1", "B1"))


class BfGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "t.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SDI_SCHEMA)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_protects_row_with_bf_specific_signals(self) -> None:
        _insert_sdi_row(
            self.conn,
            **{"UBC Tag": "", "Diameter": "50", "Year": "2019"},
        )
        self.assertTrue(_guard_hit(self.conn, BF_GUARD_SQL, "Q1", "B1"))

    def test_protects_row_with_technical_safety_bc(self) -> None:
        _insert_sdi_row(
            self.conn,
            **{"UBC Tag": "", "Technical Safety BC": "TSBC-12345"},
        )
        self.assertTrue(_guard_hit(self.conn, BF_GUARD_SQL, "Q1", "B1"))

    def test_releases_when_all_signals_blank(self) -> None:
        _insert_sdi_row(self.conn)
        self.assertFalse(_guard_hit(self.conn, BF_GUARD_SQL, "Q1", "B1"))


class GuardProductionSourceTests(unittest.TestCase):
    """Catches drift between the test predicate and the production guard.

    Each production guard has a stable marker — the full list of the column
    names it checks. If someone narrows a guard in code, these assertions
    (grep the source file) will fail, prompting a sync of this test's
    predicate constants. This is a compromise: we cannot cleanly import the
    dashboard modules in a unit test because they initialise Flask+auth at
    import time.
    """

    REPO = pathlib.Path(__file__).resolve().parents[1]

    def _assert_guard_mentions(self, relpath: str, marker: str, columns: list[str]) -> None:
        src = (self.REPO / relpath).read_text(encoding="utf-8")
        self.assertIn(marker, src, f"Expected marker {marker!r} in {relpath}")
        idx = src.index(marker)
        # Inspect the next ~3 KB — enough to cover the guard body including
        # its docstring and the full SQL predicate.
        body = src[idx : idx + 3000]
        for col in columns:
            self.assertIn(
                col, body,
                f'Guard in {relpath} appears to no longer protect column "{col}". '
                f'Update either the guard or this test predicate.'
            )

    def test_el_guard_columns(self) -> None:
        self._assert_guard_mentions(
            "review/Asset_dashboard_browser_EL/Asset_dashboard_EL.py",
            "def _sdi_row_has_data(",
            [
                "UBC Asset Tag", "Equipment ID", "Branch Panel", "Supply From",
                "Volts", "Ampere", "Location", "Description", "Asset Group",
                "Avg_ai_conf",
            ],
        )

    def test_me_guard_columns(self) -> None:
        self._assert_guard_mentions(
            "review/Asset_dasboard_browser_ME/asset_plate_reviewer.py",
            "def _sdi_row_has_data_me(",
            [
                "UBC Tag", "Manufacturer", "Model", "Serial", "Asset Group",
                "Description", "Avg_ai_conf",
            ],
        )

    def test_bf_guard_columns(self) -> None:
        self._assert_guard_mentions(
            "review/Asset_dasboard_browser_BF/asset_plate_reviewer_bf.py",
            "def _sdi_row_has_data_bf(",
            [
                "UBC Tag", "Manufacturer", "Model", "Serial", "Asset Group",
                "Description", "Diameter", "Year", "Technical Safety BC",
                "Avg_ai_conf",
            ],
        )


if __name__ == "__main__":
    unittest.main()
