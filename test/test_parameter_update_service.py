from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "asset_capture_app_dev"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from utils import parameter_update_service as pus


def _write_json(path: pathlib.Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ParameterUpdateJsonTests(unittest.TestCase):
    def test_asset_type_change_retires_active_review_jsons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            json_dir = pathlib.Path(tmp)
            _write_json(
                json_dir / "0000184490_ME_217.json",
                {"qr_code": "0000184490", "building_number": "217", "asset_type": "- ME"},
            )
            _write_json(
                json_dir / "0000184490_EL_217.json",
                {"qr_code": "0000184490", "building_number": "217", "asset_type": "- EL"},
            )

            updates, backup_dir = pus.update_json_files(
                "0000184490",
                "217",
                "217",
                "ME",
                "EL",
                json_dir=str(json_dir),
                asset_type_changed=True,
            )

            self.assertEqual(len(updates), 2)
            self.assertFalse((json_dir / "0000184490_ME_217.json").exists())
            self.assertFalse((json_dir / "0000184490_EL_217.json").exists())
            self.assertEqual(
                sorted(p.name for p in json_dir.glob("0000184490_*.json.bak_*_param_update")),
                sorted(pathlib.Path(new).name for _old, new in updates),
            )
            self.assertTrue(backup_dir and pathlib.Path(backup_dir).exists())

    def test_building_change_only_updates_matching_discipline_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            json_dir = pathlib.Path(tmp)
            _write_json(
                json_dir / "0000184490_ME_217.json",
                {
                    "qr_code": "0000184490",
                    "building_number": "217",
                    "asset_type": "- ME",
                    "structured_data": {"Location": "old"},
                },
            )
            _write_json(
                json_dir / "0000184490_EL_217.json",
                {"qr_code": "0000184490", "building_number": "217", "asset_type": "- EL"},
            )

            updates, _backup_dir = pus.update_json_files(
                "0000184490",
                "217",
                "218",
                "ME",
                "ME",
                json_dir=str(json_dir),
                new_location="new location",
                asset_type_changed=False,
            )

            self.assertEqual(len(updates), 1)
            self.assertFalse((json_dir / "0000184490_ME_217.json").exists())
            self.assertTrue((json_dir / "0000184490_ME_218.json").exists())
            self.assertTrue((json_dir / "0000184490_EL_217.json").exists())
            payload = json.loads((json_dir / "0000184490_ME_218.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["building_number"], "218")
            self.assertEqual(payload["asset_type"], "- ME")
            self.assertEqual(payload["structured_data"]["Location"], "new location")

    def test_processed_log_cleanup_keeps_other_disciplines_for_metadata_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = pathlib.Path(tmp)
            (log_dir / "processed_json.log").write_text(
                json.dumps({"0000184490_ME_217.json": 1.0}),
                encoding="utf-8",
            )
            (log_dir / "processed_json_el.log").write_text(
                json.dumps({"0000184490_EL_217.json": 1.0}),
                encoding="utf-8",
            )
            (log_dir / "processed_images.log").write_text(
                "0000184490 217 ME - 0.jpg\n",
                encoding="utf-8",
            )
            (log_dir / "processed_images_el.log").write_text(
                "0000184490 217 EL - 0.jpg\n",
                encoding="utf-8",
            )

            pus.update_processed_logs(
                str(log_dir),
                "0000184490",
                [
                    (
                        r"C:\tmp\0000184490 217 ME - 0.jpg",
                        r"C:\tmp\0000184490 218 ME - 0.jpg",
                        "0000184490 217 ME - 0",
                        "0000184490 218 ME - 0",
                    )
                ],
                [
                    (
                        r"C:\tmp\0000184490_ME_217.json",
                        r"C:\tmp\0000184490_ME_218.json",
                    )
                ],
                clear_all_for_qr=False,
            )

            me_log = json.loads((log_dir / "processed_json.log").read_text(encoding="utf-8"))
            el_log = json.loads((log_dir / "processed_json_el.log").read_text(encoding="utf-8"))
            self.assertEqual(me_log, {})
            self.assertEqual(el_log, {"0000184490_EL_217.json": 1.0})
            self.assertEqual((log_dir / "processed_images.log").read_text(encoding="utf-8"), "")
            self.assertEqual(
                (log_dir / "processed_images_el.log").read_text(encoding="utf-8"),
                "0000184490 217 EL - 0.jpg\n",
            )


class ExecuteParameterUpdateTests(unittest.TestCase):
    def test_current_params_use_photo_type_when_qr_asset_type_is_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            db_path = base / "QR_codes.db"
            upload_dir = base / "Capture_photos_upload"
            upload_dir.mkdir()
            (upload_dir / "0000184490 217 ME - 0.jpg").write_bytes(b"fake image")

            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE "QR_codes" (
                        "QR_code_ID" TEXT PRIMARY KEY,
                        "ai_status" TEXT,
                        "Location" TEXT,
                        "Building Code" TEXT,
                        "asset_type" TEXT
                    );
                    CREATE TABLE "sdi_dataset" (
                        "QR Code" TEXT,
                        "Building" TEXT,
                        "Location" TEXT,
                        "Attribute" TEXT
                    );
                    """
                )
                conn.execute(
                    'INSERT INTO "QR_codes" VALUES (?, ?, ?, ?, ?)',
                    ("0000184490", "1", "1022 Mechanical Room Floor: 1", "", ""),
                )
                conn.execute(
                    'INSERT INTO "sdi_dataset" VALUES (?, ?, ?, ?)',
                    ("0000184490", "217", "1022 Mechanical Room Floor: 1", "Electrical"),
                )
                conn.commit()

                params = pus.get_current_params(conn, "0000184490", str(upload_dir))
            finally:
                conn.close()

            self.assertIsNotNone(params)
            self.assertEqual(params["building_code"], "217")
            self.assertEqual(params["asset_type"], "ME")

    def test_asset_type_change_aligns_files_db_json_and_processed_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            db_path = base / "QR_codes.db"
            upload_dir = base / "Capture_photos_upload"
            json_dir = base / "Output_jason_api"
            data_dir = db_path.parent
            upload_dir.mkdir()
            json_dir.mkdir()

            for seq in ("0", "1"):
                (upload_dir / f"0000184490 217 ME - {seq}.jpg").write_bytes(b"fake image")

            _write_json(
                json_dir / "0000184490_ME_217.json",
                {"qr_code": "0000184490", "building_number": "217", "asset_type": "- ME"},
            )
            _write_json(
                json_dir / "0000184490_EL_217.json",
                {"qr_code": "0000184490", "building_number": "217", "asset_type": "- EL"},
            )

            (data_dir / "processed_json.log").write_text(
                json.dumps({"0000184490_ME_217.json": 1.0}),
                encoding="utf-8",
            )
            (data_dir / "processed_json_el.log").write_text(
                json.dumps({"0000184490_EL_217.json": 1.0}),
                encoding="utf-8",
            )
            (data_dir / "processed_images.log").write_text(
                "0000184490 217 ME - 0.jpg\n0000184490 217 ME - 1.jpg\n",
                encoding="utf-8",
            )
            (data_dir / "processed_images_el.log").write_text("", encoding="utf-8")

            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE "QR_codes" (
                        "QR_code_ID" TEXT PRIMARY KEY,
                        "ai_status" TEXT,
                        "Location" TEXT,
                        "Building Code" TEXT,
                        "asset_type" TEXT
                    );
                    CREATE TABLE "QR_code_assets" (
                        "ID" INTEGER PRIMARY KEY AUTOINCREMENT,
                        "code_assets" TEXT
                    );
                    CREATE TABLE "sdi_dataset" (
                        "QR Code" TEXT,
                        "Building" TEXT,
                        "Location" TEXT,
                        "Attribute" TEXT
                    );
                    CREATE TABLE "sdi_dataset_EL" (
                        "QR Code" TEXT,
                        "Building" TEXT,
                        "Location" TEXT,
                        "Attribute" TEXT
                    );
                    """
                )
                conn.execute(
                    'INSERT INTO "QR_codes" VALUES (?, ?, ?, ?, ?)',
                    ("0000184490", "1", "old location", "217", "ME"),
                )
                conn.executemany(
                    'INSERT INTO "QR_code_assets" ("code_assets") VALUES (?)',
                    [
                        ("0000184490 217 ME - 0",),
                        ("0000184490 217 ME - 1",),
                    ],
                )
                conn.execute(
                    'INSERT INTO "sdi_dataset" VALUES (?, ?, ?, ?)',
                    ("0000184490", "217", "old location", "Mechanical"),
                )
                conn.execute(
                    'INSERT INTO "sdi_dataset_EL" VALUES (?, ?, ?, ?)',
                    ("0000184490", "217", "old location", "Electrical"),
                )
                conn.commit()
            finally:
                conn.close()

            old_env = os.environ.get("JSON_OUTPUT_DIR")
            os.environ["JSON_OUTPUT_DIR"] = str(json_dir)
            try:
                success, message = pus.execute_parameter_update(
                    db_path=str(db_path),
                    upload_dir=str(upload_dir),
                    qr_code="0000184490",
                    old_params={
                        "building_code": "217",
                        "location": "old location",
                        "asset_type": "Mechanical",
                    },
                    new_params={
                        "building_code": "217",
                        "location": "new location",
                        "asset_type": "Electrical",
                    },
                )
            finally:
                if old_env is None:
                    os.environ.pop("JSON_OUTPUT_DIR", None)
                else:
                    os.environ["JSON_OUTPUT_DIR"] = old_env

            self.assertTrue(success, message)
            self.assertTrue((upload_dir / "0000184490 217 EL - 0.jpg").exists())
            self.assertTrue((upload_dir / "0000184490 217 EL - 1.jpg").exists())
            self.assertFalse((upload_dir / "0000184490 217 ME - 0.jpg").exists())
            self.assertFalse(list(json_dir.glob("0000184490_*.json")))
            self.assertGreaterEqual(len(list(json_dir.glob("0000184490_*.json.bak_*_param_update"))), 2)

            conn = sqlite3.connect(db_path)
            try:
                qr_row = conn.execute(
                    'SELECT "ai_status", "Location", "asset_type" FROM "QR_codes" WHERE "QR_code_ID"=?',
                    ("0000184490",),
                ).fetchone()
                self.assertEqual(qr_row, ("0", "new location", "EL"))
                assets = sorted(
                    row[0]
                    for row in conn.execute(
                        'SELECT "code_assets" FROM "QR_code_assets" ORDER BY "code_assets"'
                    )
                )
                self.assertEqual(
                    assets,
                    ["0000184490 217 EL - 0", "0000184490 217 EL - 1"],
                )
                self.assertEqual(
                    conn.execute('SELECT COUNT(*) FROM "sdi_dataset"').fetchone()[0],
                    0,
                )
                self.assertEqual(
                    conn.execute('SELECT COUNT(*) FROM "sdi_dataset_EL"').fetchone()[0],
                    0,
                )
            finally:
                conn.close()

            self.assertNotIn("0000184490", (data_dir / "processed_json.log").read_text(encoding="utf-8"))
            self.assertNotIn("0000184490", (data_dir / "processed_json_el.log").read_text(encoding="utf-8"))
            self.assertNotIn("0000184490", (data_dir / "processed_images.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
