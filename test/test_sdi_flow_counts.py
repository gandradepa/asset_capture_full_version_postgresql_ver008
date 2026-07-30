from __future__ import annotations

import importlib.util
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing


ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "Dashboard"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
MODULE_PATH = ROOT / "Dashboard" / "charts" / "flow_quantity_chart.py"
SPEC = importlib.util.spec_from_file_location("flow_quantity_chart", MODULE_PATH)
flow_quantity_chart = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(flow_quantity_chart)


class SdiFlowCountTests(unittest.TestCase):
    def _with_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return pathlib.Path(tmp.name)

    def test_review_cards_count_distinct_base_qr_by_highest_process(self):
        db_path = self._with_db()
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute('CREATE TABLE "QR_code_assets" ("code_assets" TEXT, "Col_process" TEXT)')
            conn.execute('CREATE TABLE "sdi_dataset" ("QR Code" TEXT, "Building" TEXT)')
            conn.execute('CREATE TABLE "sdi_dataset_EL" ("QR Code" TEXT, "Building" TEXT)')
            conn.execute('CREATE TABLE "sdi_print_out" ("QR Code" TEXT, "Building" TEXT)')
            conn.execute('CREATE TABLE "sdi_print_out_arch" ("QR Code" TEXT, "Building" TEXT)')
            conn.execute('CREATE TABLE "QR_codes" ("QR_code_ID" TEXT, "sdi" INTEGER)')

            conn.executemany(
                'INSERT INTO "QR_code_assets" ("code_assets", "Col_process") VALUES (?, ?)',
                [
                    ("QR001 100 ME - 0", "0"),
                    ("QR001 100 ME - 1", "0"),
                    ("QR002 100 EL - 0", "0"),
                    ("QR002 100 EL - 1", "1"),
                    ("QR003 100 BF - 0", "2"),
                    ("QR004 100 ME - 0", "9"),
                    ("QR005 100 ME - 0", "bad"),
                ],
            )
            conn.executemany(
                'INSERT INTO "sdi_dataset" ("QR Code", "Building") VALUES (?, ?)',
                [("QRA", "100"), ("QRB", "100")],
            )
            conn.executemany(
                'INSERT INTO "sdi_dataset_EL" ("QR Code", "Building") VALUES (?, ?)',
                [("QRC", "100"), ("QRB", "100")],
            )
            conn.executemany(
                'INSERT INTO "sdi_print_out" ("QR Code", "Building") VALUES (?, ?)',
                [("QRB", "100"), ("QRD", "100"), ("QRF", "100")],
            )
            conn.executemany(
                'INSERT INTO "sdi_print_out_arch" ("QR Code", "Building") VALUES (?, ?)',
                [("QRC", "100"), ("QRE", "100"), ("QRG", "100")],
            )
            conn.executemany(
                'INSERT INTO "QR_codes" ("QR_code_ID", "sdi") VALUES (?, ?)',
                [
                    ("QRA", 0),
                    ("QRB", 0),
                    ("QRC", 0),
                    ("QRD", 0),
                    ("QRE", 0),
                    ("QRF", 1),
                    ("QRG", 1),
                ],
            )
            conn.commit()

        workflow = flow_quantity_chart.build_asset_workflow(str(db_path))

        card_counts = {card["key"]: card["count"] for card in workflow["cards"]}
        self.assertEqual(card_counts, {"new": 1, "update": 1, "manual": 1})
        self.assertEqual(workflow["total_distinct_qr"], 3)
        self.assertEqual(workflow["unclassified_count"], 2)

        flow_counts = {step["key"]: step["count"] for step in workflow["flow"]}
        self.assertEqual(flow_counts, {"queue": 1, "requested": 2, "planon": 2})

    def test_missing_optional_sdi_tables_do_not_crash(self):
        db_path = self._with_db()
        self.addCleanup(lambda: db_path.unlink(missing_ok=True))

        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute('CREATE TABLE "QR_code_assets" ("code_assets" TEXT, "Col_process" INTEGER)')
            conn.execute(
                'INSERT INTO "QR_code_assets" ("code_assets", "Col_process") VALUES (?, ?)',
                ("QR100 200 ME - 0", 0),
            )
            conn.commit()

        workflow = flow_quantity_chart.build_asset_workflow(str(db_path))

        card_counts = {card["key"]: card["count"] for card in workflow["cards"]}
        flow_counts = {step["key"]: step["count"] for step in workflow["flow"]}
        self.assertEqual(card_counts, {"new": 1, "update": 0, "manual": 0})
        self.assertEqual(flow_counts, {"queue": 0, "requested": 0, "planon": 0})


if __name__ == "__main__":
    unittest.main()
