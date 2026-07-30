"""Regression coverage for long ME models and Model/Serial collisions."""

from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "API"
sys.path.insert(0, str(API_DIR))

from API_interface_ME_ver00 import AssetProcessor  # noqa: E402


TRANE_MODEL = "BCHC090H1A0A2AF7P000000B0000000000000000"
TRANE_MODEL_ZERO_AS_O = "BCHC09OH1A0A2AF7P000000B0000000000000000"
TRANE_SERIAL = "T03M77537"
TRANE_TEXT = (
    "TRANE "
    f"MODEL NO: {TRANE_MODEL} "
    f"SERIAL NO: {TRANE_SERIAL} "
    "SALES ORDER: CTV549A RATED VOLTAGE: 575 HZ: 60 PH: 3"
)


class MeLongModelSerialCollisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = object.__new__(AssetProcessor)

    def _stub_non_identity_rereads(self) -> None:
        self.processor._collect_nameplate_evidence_texts = lambda _images: [TRANE_TEXT]
        self.processor._normalize_manufacturer_with_context = (
            lambda raw, _images, allow_ocr=True: raw
        )
        self.processor._reread_year_from_nameplate_llm = lambda *_args, **_kwargs: ""
        self.processor._extract_year_from_rois = lambda _images: ""
        self.processor._fallback_year_from_ocr = lambda _images: ""
        self.processor._reread_ubc_from_tag_llm = lambda *_args, **_kwargs: ""

    @staticmethod
    def _llm_fields(model: str) -> dict[str, str]:
        return {
            "Manufacturer": "Trane",
            "Model": model,
            "Serial Number": TRANE_SERIAL,
            "Year": "",
            "UBC Tag": "",
            "Technical Safety BC": "",
        }

    def test_trane_40_character_model_is_valid(self) -> None:
        self.assertEqual(len(TRANE_MODEL), 40)
        self.assertTrue(
            self.processor._is_model_code_candidate(TRANE_MODEL, "Trane")
        )

    def test_label_parser_keeps_long_model_and_serial_separate(self) -> None:
        parsed = self.processor._parse_nameplate_model_serial(TRANE_TEXT)

        self.assertEqual(parsed["Model"], TRANE_MODEL)
        self.assertEqual(parsed["Serial Number"], TRANE_SERIAL)

    def test_model_evidence_must_be_adjacent_to_model_label(self) -> None:
        self.assertTrue(
            self.processor._has_model_label_evidence(
                TRANE_MODEL,
                {},
                evidence_texts=[TRANE_TEXT],
            )
        )
        self.assertFalse(
            self.processor._has_model_label_evidence(
                TRANE_SERIAL,
                {},
                evidence_texts=[TRANE_TEXT],
            )
        )

    def test_fast_ocr_does_not_promote_serial_to_model(self) -> None:
        self.processor._ocr_text_variants = lambda _path: [TRANE_TEXT]
        self.processor._ocr_text_from_dark_nameplate = lambda _path: []
        self.processor._ocr_text_from_red_nameplate = lambda _path: []

        extracted = self.processor._ocr_extract_model_serial_fast(
            {"0": "0000186301 166 ME - 0.jpg"}
        )

        self.assertEqual(extracted["Model"], TRANE_MODEL)
        self.assertEqual(extracted["Serial Number"], TRANE_SERIAL)

    def test_ui_parity_keeps_valid_long_primary_model(self) -> None:
        self._stub_non_identity_rereads()

        def forbidden_model_reread(*_args, **_kwargs):
            raise AssertionError("A valid long model must not trigger a model reread")

        self.processor._reread_model_serial_from_nameplate_llm = forbidden_model_reread

        merged = self.processor._build_ui_parity_struct(
            qr="0000186301",
            info={"images": {"0": "0000186301 166 ME - 0.jpg"}},
            llm_cleaned=self._llm_fields(TRANE_MODEL),
            raw_manufacturer="Trane",
            llm_model=TRANE_MODEL,
            raw_year="",
            has_nameplate_source=True,
            has_tsbc_source=False,
        )

        self.assertEqual(merged["Model"], TRANE_MODEL)
        self.assertEqual(merged["Serial Number"], TRANE_SERIAL)
        self.assertFalse(self.processor._last_model_serial_collision)

    def test_label_local_ocr_repairs_zero_as_letter_o_in_long_model(self) -> None:
        self._stub_non_identity_rereads()

        def forbidden_model_reread(*_args, **_kwargs):
            raise AssertionError("Label-local OCR should repair the model before a reread")

        self.processor._reread_model_serial_from_nameplate_llm = forbidden_model_reread

        merged = self.processor._build_ui_parity_struct(
            qr="0000186301",
            info={"images": {"0": "0000186301 166 ME - 0.jpg"}},
            llm_cleaned=self._llm_fields(TRANE_MODEL_ZERO_AS_O),
            raw_manufacturer="Trane",
            llm_model=TRANE_MODEL_ZERO_AS_O,
            raw_year="",
            has_nameplate_source=True,
            has_tsbc_source=False,
        )

        self.assertEqual(merged["Model"], TRANE_MODEL)
        self.assertEqual(merged["Serial Number"], TRANE_SERIAL)

    def test_label_local_ocr_trailing_zero_trim_keeps_full_long_model(self) -> None:
        self._stub_non_identity_rereads()
        truncated_text = TRANE_TEXT.replace(TRANE_MODEL, TRANE_MODEL[:-1])
        self.processor._collect_nameplate_evidence_texts = (
            lambda _images: [truncated_text]
        )

        def forbidden_model_reread(*_args, **_kwargs):
            raise AssertionError(
                "A one-zero OCR trim should corroborate the full long model"
            )

        self.processor._reread_model_serial_from_nameplate_llm = forbidden_model_reread

        merged = self.processor._build_ui_parity_struct(
            qr="0000186301",
            info={"images": {"0": "0000186301 166 ME - 0.jpg"}},
            llm_cleaned=self._llm_fields(TRANE_MODEL),
            raw_manufacturer="Trane",
            llm_model=TRANE_MODEL,
            raw_year="",
            has_nameplate_source=True,
            has_tsbc_source=False,
        )

        self.assertEqual(merged["Model"], TRANE_MODEL)
        self.assertEqual(merged["Serial Number"], TRANE_SERIAL)

    def test_duplicate_primary_fields_force_label_aware_ocr_rescue(self) -> None:
        self._stub_non_identity_rereads()
        self.processor._reread_model_serial_from_nameplate_llm = (
            lambda *_args, **_kwargs: {
                "Model": "",
                "Serial Number": TRANE_SERIAL,
            }
        )
        self.processor._ocr_extract_model_serial = lambda _images: {
            "Model": TRANE_MODEL,
            "Serial Number": TRANE_SERIAL,
        }

        merged = self.processor._build_ui_parity_struct(
            qr="0000186301",
            info={"images": {"0": "0000186301 166 ME - 0.jpg"}},
            llm_cleaned=self._llm_fields(TRANE_SERIAL),
            raw_manufacturer="Trane",
            llm_model=TRANE_SERIAL,
            raw_year="",
            has_nameplate_source=True,
            has_tsbc_source=False,
        )

        self.assertEqual(merged["Model"], TRANE_MODEL)
        self.assertEqual(merged["Serial Number"], TRANE_SERIAL)
        self.assertTrue(self.processor._last_model_serial_collision)

    def test_duplicate_llm_candidate_is_not_critical_valid(self) -> None:
        feedback, critical_valid = self.processor._evaluate_llm_candidate(
            {
                "Manufacturer": "Trane",
                "Model": TRANE_SERIAL,
                "Serial Number": TRANE_SERIAL,
                "Year": "2020",
                "UBC Tag": "FC-TX",
            }
        )

        self.assertFalse(critical_valid)
        self.assertTrue(any("both contain" in line for line in feedback))


if __name__ == "__main__":
    unittest.main()
