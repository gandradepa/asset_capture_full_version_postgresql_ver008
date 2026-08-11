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
