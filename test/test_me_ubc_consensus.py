"""Regression coverage for ME UBC-tag hybrid consensus."""

from __future__ import annotations

import base64
import pathlib
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = ROOT / "API"
FIXTURE = ROOT / "test" / "fixtures" / "0000261035_ME_seq1.jpg"
sys.path.insert(0, str(API_DIR))

from API_interface_ME_ver00 import (  # noqa: E402
    AssetProcessor,
    Config,
    MEUBCTagOnlyExtraction,
    MEStructuredExtraction,
    cv2,
)


class MeUbcConsensusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = object.__new__(AssetProcessor)
        self.processor._load_me_ubc_prefixes = lambda: {"CH", "HX"}

    @staticmethod
    def _local_vote(prefix: str = "", core: str = "") -> dict:
        return {
            "prefix": prefix,
            "core": core,
            "prefix_votes": 2 if prefix else 0,
            "core_votes": 2 if core else 0,
        }

    @staticmethod
    def _judge(tag: str, *, error: str = "") -> dict:
        return {
            "tag": tag,
            "call_count": 1,
            "usage": {
                "prompt_tokens": 450,
                "completion_tokens": 40,
                "total_tokens": 490,
            },
            "error": error,
        }

    def test_component_quorum_corrects_unknown_cx_prefix_to_hx(self) -> None:
        """A locally challenged unknown prefix changes only after Terra agrees."""
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="HX")
        )
        judge_calls = []

        def judge(*_args, **_kwargs):
            judge_calls.append(1)
            return self._judge("HX-1A")

        self.processor._reread_ubc_consensus_judge = judge

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="CX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "HX-1A")
        self.assertEqual(result["status"], "corrected_by_quorum")
        self.assertIn("prefix_unrecognized", result["triggers"])
        self.assertIn("local_prefix_disagreement", result["triggers"])
        self.assertEqual(result["judge"]["call_count"], 1)
        self.assertEqual(len(judge_calls), 1)

    def test_component_quorum_corrects_valid_ch_prefix_when_hx_wins(self) -> None:
        """Dictionary-valid CH must still be challenged by independent HX evidence."""
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="HX")
        )
        self.processor._reread_ubc_consensus_judge = (
            lambda *_args, **_kwargs: self._judge("HX-1A")
        )

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="CH-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "HX-1A")
        self.assertEqual(result["status"], "corrected_by_quorum")

    def test_dictionary_cannot_veto_two_visual_sources_on_new_prefix(self) -> None:
        """Dictionary validity is a trigger only, never a fourth vote or veto."""
        self.processor._load_me_ubc_prefixes = lambda: {"CH", "HX"}
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="ZZ")
        )
        self.processor._reread_ubc_consensus_judge = (
            lambda *_args, **_kwargs: self._judge("ZZ-1A")
        )

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="CX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "ZZ-1A")
        self.assertEqual(result["status"], "corrected_by_quorum")
        self.assertNotIn("ubc_consensus_unresolved", result["reason_codes"])

    def test_primary_and_judge_quorum_defeats_conflicting_local_ocr(self) -> None:
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="CH")
        )
        self.processor._reread_ubc_consensus_judge = (
            lambda *_args, **_kwargs: self._judge("HX-1A")
        )

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="HX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "HX-1A")
        self.assertEqual(result["status"], "confirmed_by_quorum")

    def test_three_way_prefix_conflict_preserves_primary_and_flags_unresolved(self) -> None:
        self.processor._load_me_ubc_prefixes = lambda: {"CH", "CX", "HX"}
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="HX")
        )
        self.processor._reread_ubc_consensus_judge = (
            lambda *_args, **_kwargs: self._judge("CH-1A")
        )

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="CX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "CX-1A")
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["confidence_cap"], 65)
        self.assertIn("ubc_consensus_unresolved", result["reason_codes"])

    def test_valid_local_agreement_avoids_terra_call(self) -> None:
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="HX", core="1A")
        )

        def forbidden_judge(*_args, **_kwargs):
            raise AssertionError("An unchallenged UBC tag must not call Terra")

        self.processor._reread_ubc_consensus_judge = forbidden_judge

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="HX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "HX-1A")
        self.assertEqual(result["status"], "accepted_primary")
        self.assertEqual(result["judge"]["call_count"], 0)

    def test_missing_sequence_one_stays_blank_without_terra(self) -> None:
        self.processor._extract_local_ubc_vote = lambda *_args, **_kwargs: self._local_vote()

        def forbidden_judge(*_args, **_kwargs):
            raise AssertionError("Missing seq-1 must not call Terra")

        self.processor._reread_ubc_consensus_judge = forbidden_judge

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="",
            primary_confidence=0,
            images={},
        )

        self.assertEqual(result["final_tag"], "")
        self.assertEqual(result["status"], "accepted_primary")
        self.assertEqual(result["judge"]["call_count"], 0)

    def test_judge_failure_is_not_retried_and_preserves_primary(self) -> None:
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="HX")
        )
        judge_calls = []

        def failed_judge(*_args, **_kwargs):
            judge_calls.append(1)
            return self._judge("", error="timeout")

        self.processor._reread_ubc_consensus_judge = failed_judge

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="CX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "CX-1A")
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["judge"]["error"], "timeout")
        self.assertEqual(len(judge_calls), 1)

    def test_dictionary_failure_is_non_fatal(self) -> None:
        self.processor._load_me_ubc_prefixes = lambda: (_ for _ in ()).throw(
            ValueError("invalid dictionary")
        )
        self.processor._extract_local_ubc_vote = (
            lambda _images, _prefixes=None: self._local_vote(prefix="HX")
        )
        self.processor._reread_ubc_consensus_judge = (
            lambda *_args, **_kwargs: self._judge("HX-1A")
        )

        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="CX-1A",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["final_tag"], "HX-1A")
        self.assertEqual(result["status"], "corrected_by_quorum")

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_judge_request_is_single_bounded_independent_terra_call(self) -> None:
        calls = []

        def parse(**kwargs):
            calls.append(kwargs)
            parsed = MEUBCTagOnlyExtraction(**{"UBC Tag": "HX-1A"})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))],
                usage=SimpleNamespace(
                    prompt_tokens=410,
                    completion_tokens=18,
                    total_tokens=428,
                ),
            )

        self.processor.client = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
            )
        )

        with patch.object(Config, "ME_UBC_JUDGE_MODEL", "gpt-5.6-terra"), patch.object(
            Config, "ME_UBC_JUDGE_DETAIL", "original"
        ), patch.object(Config, "ME_UBC_JUDGE_REASONING_EFFORT", "low"):
            result = self.processor._reread_ubc_consensus_judge(
                "0000261035", {"1": str(FIXTURE)}
            )

        self.assertEqual(result["tag"], "HX-1A")
        self.assertEqual(result["call_count"], 1)
        self.assertEqual(result["usage"]["total_tokens"], 428)
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertEqual(request["max_completion_tokens"], 220)
        self.assertEqual(request["reasoning_effort"], "low")
        self.assertIs(request["response_format"], MEUBCTagOnlyExtraction)

        content = request["messages"][0]["content"]
        self.assertNotIn("CX-1A", str(content))
        self.assertNotIn("OCR", content[0]["text"].upper())
        image_parts = [part for part in content if part["type"] == "image_url"]
        self.assertEqual(len(image_parts), 2)
        for part in image_parts:
            self.assertEqual(part["image_url"]["detail"], "original")
            encoded = part["image_url"]["url"].split(",", 1)[1]
            decoded = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            prepared = cv2.imdecode(decoded, cv2.IMREAD_COLOR)
            self.assertIsNotNone(prepared)
            self.assertLessEqual(max(prepared.shape[:2]), 1280)

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_judge_timeout_returns_normalized_failure_without_retry(self) -> None:
        calls = []

        def parse(**_kwargs):
            calls.append(1)
            raise TimeoutError("sensitive transport detail")

        self.processor.client = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
            )
        )

        result = self.processor._reread_ubc_consensus_judge(
            "0000261035", {"1": str(FIXTURE)}
        )

        self.assertEqual(result["tag"], "")
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(result["call_count"], 1)
        self.assertEqual(len(calls), 1)

    def test_judge_preprocessing_failure_reports_zero_api_calls(self) -> None:
        self.processor.client = object()

        result = self.processor._reread_ubc_consensus_judge(
            "0000261035", {"1": "missing-seq-1.jpg"}
        )

        self.assertEqual(result["call_count"], 0)

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_judge_quota_and_parse_failures_are_normalized_without_retry(self) -> None:
        for failure, expected in (
            (RuntimeError("insufficient_quota"), "quota"),
            (ValueError("invalid structured response"), "parse"),
        ):
            with self.subTest(expected=expected):
                calls = []

                def parse(**_kwargs):
                    calls.append(1)
                    raise failure

                self.processor.client = SimpleNamespace(
                    beta=SimpleNamespace(
                        chat=SimpleNamespace(
                            completions=SimpleNamespace(parse=parse)
                        )
                    )
                )
                result = self.processor._reread_ubc_consensus_judge(
                    "0000261035", {"1": str(FIXTURE)}
                )

                self.assertEqual(result["error"], expected)
                self.assertEqual(len(calls), 1)

    def test_consensus_confidence_policy(self) -> None:
        corrected = self.processor._apply_ubc_consensus_confidence(
            {"UBC Tag": 40},
            {"status": "corrected_by_quorum", "final_tag": "HX-1A"},
        )
        unresolved = self.processor._apply_ubc_consensus_confidence(
            {"UBC Tag": 96},
            {"status": "unresolved", "final_tag": "CX-1A"},
        )
        unchallenged = self.processor._apply_ubc_consensus_confidence(
            {"UBC Tag": 95},
            {
                "status": "accepted_primary",
                "final_tag": "HX-1A",
                "primary": {"confidence": 0},
            },
        )

        self.assertEqual(corrected["UBC Tag"], 92)
        self.assertEqual(unresolved["UBC Tag"], 65)
        self.assertEqual(unchallenged["UBC Tag"], 84)

    def test_local_ubc_ocr_uses_four_second_timeout(self) -> None:
        image = np.zeros((20, 80), dtype=np.uint8)
        with patch(
            "API_interface_ME_ver00.pytesseract.image_to_string",
            return_value="HX-1A",
        ) as image_to_string:
            value = self.processor._safe_tesseract_ubc_text(
                image,
                "--oem 3 --psm 11",
            )

        self.assertEqual(value, "HX-1A")
        self.assertEqual(image_to_string.call_args.kwargs["timeout"], 4)

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_local_validator_runs_exactly_four_variants(self) -> None:
        calls = []

        def local_read(_image, _config):
            calls.append(1)
            return "HX-1A"

        self.processor._safe_tesseract_ubc_text = local_read
        vote = self.processor._extract_local_ubc_vote(
            {"1": str(FIXTURE)},
            {"HX"},
        )

        self.assertEqual(len(calls), 4)
        self.assertEqual(vote["prefix"], "HX")

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_local_vote_preserves_multisegment_core_separators(self) -> None:
        for tag, prefix, core in (
            ("T-CHB-01", "T", "CHB-01"),
            ("CHWBT-W-4", "CHWBT", "W-4"),
        ):
            with self.subTest(tag=tag):
                self.processor._safe_tesseract_ubc_text = (
                    lambda _image, _config, value=tag: value
                )
                vote = self.processor._extract_local_ubc_vote(
                    {"1": str(FIXTURE)},
                    {prefix},
                )

                self.assertEqual(vote["prefix"], prefix)
                self.assertEqual(vote["core"], core)

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_correct_multisegment_primary_avoids_judge(self) -> None:
        self.processor._load_me_ubc_prefixes = lambda: {"T"}
        self.processor._safe_tesseract_ubc_text = (
            lambda _image, _config: "T-CHB-01"
        )

        def forbidden_judge(*_args, **_kwargs):
            raise AssertionError("matching multisegment evidence must not escalate")

        self.processor._reread_ubc_consensus_judge = forbidden_judge
        result = self.processor._resolve_ubc_consensus(
            qr="0000261035",
            primary_tag="T-CHB-01",
            primary_confidence=95,
            images={"1": str(FIXTURE)},
        )

        self.assertEqual(result["status"], "accepted_primary")
        self.assertEqual(result["final_tag"], "T-CHB-01")
        self.assertEqual(result["judge"]["call_count"], 0)

    def test_real_dictionary_contains_hx_prefix(self) -> None:
        self.assertIn("HX", AssetProcessor._load_me_ubc_prefixes())

    def test_final_normalizer_preserves_hum_and_multisegment_separators(self) -> None:
        hum = self.processor._validate_and_normalize({"UBC Tag": "HUM 5"})
        multi = self.processor._validate_and_normalize({"UBC Tag": "T-CHB-01"})

        self.assertEqual(hum["UBC Tag"], "HUM 5")
        self.assertEqual(multi["UBC Tag"], "T-CHB-01")

    def test_raw_model_echo_is_not_independent_image_evidence(self) -> None:
        self.processor._extract_ubc_from_fc_no_rois = lambda _images: ""
        self.processor._ocr_text_candidates_for_ubc = lambda _images: []

        self.assertFalse(
            self.processor._has_ubc_label_evidence("HX-1A", {}, "HX-1A")
        )

    def test_manual_review_metadata_contains_consensus_audit(self) -> None:
        structured = {
            "Manufacturer": "Taco",
            "Model": "A1",
            "Serial Number": "12345",
            "Year": "2020",
            "UBC Tag": "CX-1A",
            "Technical Safety BC": "",
        }
        consensus = {
            "status": "unresolved",
            "triggers": ["local_prefix_disagreement"],
            "final_tag": "CX-1A",
            "reason_codes": ["ubc_consensus_unresolved"],
        }

        metadata = self.processor._build_manual_review_metadata(
            structured,
            {field: 95 for field in structured},
            100,
            has_tsbc_source=False,
            score_context={
                "ubc_consensus": consensus,
                "extra_reason_codes": ["ubc_consensus_unresolved"],
            },
        )

        self.assertEqual(metadata["flag_for_review"], 1)
        self.assertIn("ubc_consensus_unresolved", metadata["reason_codes"])
        self.assertEqual(metadata["ubc_consensus"], consensus)

    def test_disabling_consensus_preserves_primary_for_legacy_path(self) -> None:
        self.processor._resolve_ubc_consensus = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled consensus must not run")
        )

        with patch.object(Config, "ME_UBC_CONSENSUS_ENABLED", False):
            tag, consensus = self.processor._maybe_resolve_ubc_consensus(
                qr="0000261035",
                primary_tag="CX-1A",
                primary_confidence=45,
                images={"1": str(FIXTURE)},
            )

        self.assertEqual(tag, "CX-1A")
        self.assertEqual(consensus, {})

    def test_disabling_consensus_keeps_targeted_legacy_reread_available(self) -> None:
        calls = []

        def reread(_qr, _images):
            calls.append(1)
            return "HX-1A"

        self.processor._reread_ubc_from_tag_llm = reread
        with patch.object(Config, "ME_UBC_CONSENSUS_ENABLED", False):
            result = self.processor._build_ui_parity_struct(
                qr="0000261035",
                info={"images": {"1": str(FIXTURE)}},
                llm_cleaned={
                    "Manufacturer": "Taco",
                    "Model": "A1",
                    "Serial Number": "12345",
                    "Year": "2020",
                    "UBC Tag": "1A",
                    "Technical Safety BC": "",
                },
                raw_manufacturer="Taco",
                llm_model="A1",
                raw_year="2020",
                has_nameplate_source=False,
                has_tsbc_source=False,
            )

        self.assertEqual(result["UBC Tag"], "HX-1A")
        self.assertEqual(len(calls), 1)

    def test_simple_partial_result_preserves_primary_confidence(self) -> None:
        parsed = MEStructuredExtraction(
            **{
                "UBC Tag": "CX-1A",
                "Confidence Scores": {"UBC Tag": 61},
            }
        )
        self.processor.client = SimpleNamespace(
            beta=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        parse=lambda **_kwargs: SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(parsed=parsed, refusal=None)
                                )
                            ]
                        )
                    )
                )
            )
        )

        with patch.object(Config, "ENABLE_LLM_FALLBACK", False), patch.object(
            Config, "ENABLE_PREMIUM_FALLBACK", False
        ), patch.object(Config, "MAX_LLM_ATTEMPTS_PER_MODEL", 1):
            result = self.processor._llm_multi_image_simple(
                "0000261035",
                {"images": {"1": str(FIXTURE)}},
                has_nameplate_source=False,
                has_tsbc_source=False,
            )

        self.assertEqual(result["confidence_scores"]["UBC Tag"], 61)

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_real_fixture_detector_selects_placard_instead_of_qr_card(self) -> None:
        image = cv2.imread(str(FIXTURE))

        placard, bbox = self.processor._detect_ubc_placard(image)

        x, y, width, height = bbox
        self.assertGreater(width * height, 180_000)
        self.assertTrue(200 <= x <= 240)
        self.assertTrue(220 <= y <= 255)
        self.assertGreater(height, 750)
        self.assertGreater(placard.size, 0)

    @unittest.skipUnless(
        cv2 is not None and shutil.which("tesseract"),
        "OpenCV and Tesseract are required for the real OCR regression",
    )
    def test_real_fixture_local_vote_recovers_hx_prefix(self) -> None:
        vote = self.processor._extract_local_ubc_vote(
            {"1": str(FIXTURE)},
            {"CH", "HX"},
        )

        self.assertEqual(vote["prefix"], "HX")
        self.assertGreaterEqual(vote["prefix_votes"], 2)


if __name__ == "__main__":
    unittest.main()
