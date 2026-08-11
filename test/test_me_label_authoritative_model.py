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
        for value in ("QCC-M", "118668", "208V", "1200 VAC"):
            with self.subTest(value=value):
                self.assertFalse(self.processor._is_model_code_candidate(value))

    def test_labeled_contract_rejects_empty_control_overlong_and_collision(self) -> None:
        self.assertTrue(
            hasattr(self.processor, "_is_labeled_model_value_candidate"),
            "the explicit-label contract is not implemented",
        )
        helper = self.processor._is_labeled_model_value_candidate
        self.assertFalse(helper(""))
        self.assertFalse(helper("QCC\nM"))
        self.assertFalse(helper("A" * 65))
        self.assertFalse(helper("QCCM1608B00041", serial_value="QCCM1608B00041"))

    def test_labeled_parser_never_returns_model_serial_collision(self) -> None:
        parsed = self.processor._parse_nameplate_model_serial(
            "Model: QCCM1608B00041\nSerial: QCCM1608B00041"
        )
        self.assertEqual(parsed["Model"], "")
        self.assertEqual(parsed["Serial Number"], "QCCM1608B00041")

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
