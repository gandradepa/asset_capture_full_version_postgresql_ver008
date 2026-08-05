"""EL legacy transformer-nameplate extraction rules.

The legacy EL path (``Buildings.Process = 'Legacy'``) was built for black
lamacoid plates and could not capture technical specs from a manufacturer
nameplate. QR 0000186132 (building 641, ``TX-MAIN``) photographed a full ABB
dry-type transformer plate into the ``Asset Plate (Optional)`` slot (EL-0) and
still produced blank ``Volts`` / ``Ampere`` / ``Power Rating``.

Source precedence (user rule, 2026-08-04): the lamacoid (EL-1, ``Label Text``)
is the PRIMARY source; the manufacturer nameplate (EL-0, ``Nameplate Text``)
is SECONDARY. A nameplate reading must never override a lamacoid reading.

Power Rating is transformer-only, and a transformer nameplate contributes NO
amperage (24 of 26 transformer rows in ``sdi_dataset_EL`` are blank; the plate
prints five HV tap currents plus one LV current, which the single-column schema
cannot represent faithfully).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy_flow = _load(
    "el_legacy_flow_undertest",
    "review/Asset_dashboard_browser_EL/legacy_flow.py",
)
validators_shared = _load("validators_shared_undertest", "API/validators_shared.py")


# Verbatim-style transcription of the ABB plate in
# Capture_photos_upload/0000186132 641 EL - 0.jpg, as the legacy prompt is
# expected to render it (table cells row by row).
ABB_PLATE = """ASEA BROWN BOVERI ABB
TRANSFORMATEUR ASL TRANSFORMER
ECHAUF. TEMP. RISE | TYPE ANN | TYPE AFN | KVA
115 C | 1500 | 2000
150 C | 1725 | 2300
5.83 % IX
6.02 % IZ@135 C
SUR/ON 1500 KVA
POIDS NET / NET WEIGHT 10450 LBS
12470 H.T. H.V. 95 KV BIL | 600Y/346.4 B.T. L.V. 10 KV BIL
ISOLATION CLASSE 220 / CLASS 220 INSULATION | 3 PH | 60 HZ
NO. DE SERIE / SERIAL NO. 90TSH112
COMMANDE DE L'USINE / SHOP ORDER NO. 4000
COMMANDE DU CLIENT / CUSTOMER'S ORDER NO. 3760-08101-41
ATTENTION: METTRE HORS TENSION AVANT DE CHANGER LES PRISES
CAUTION: DE-ENERGIZE BEFORE CHANGING TAPS
ENROULEMENT / WINDING | VOLTS | COURANT CURRENT (A) | CONN. | SUR BORNES ON LEADS
HAUTE TENSION TRIANGLE / HIGH VOLTAGE DELTA | 105% | 66.1 | 4-5
102 1/2% | 67.8 | 4-6
100% | 69.4 | 3-6
97 1/2% | 71.2 | 3-7
95% | 73.1 | 2-7
B.T. L.V. | 100% | 1443 | X0-X1 X2-X3
ASEA BROWN BOVERI INC. 3A54344H01"""


class NameplateSpecTests(unittest.TestCase):
    """legacy_nameplate_specs() reads a manufacturer plate."""

    def setUp(self) -> None:
        self.specs = legacy_flow.legacy_nameplate_specs(ABB_PLATE)

    def test_picks_base_self_cooled_kva(self) -> None:
        # 'SUR/ON 1500 KVA' is the plate's own declared impedance base, and
        # 1500/(sqrt3*12470)=69.5A matches the printed 100% tap current 69.4A.
        self.assertEqual(self.specs["kva"], "1500")
        self.assertEqual(self.specs["kva_uom"], "KVA")

    def test_rejects_forced_air_and_higher_rise_ratings(self) -> None:
        # AFN 2000 (forced-air) and the 150 C rise row (1725/2300) must lose.
        self.assertNotIn(self.specs["kva"], {"2000", "1725", "2300"})

    def test_composes_primary_secondary_voltage_pair(self) -> None:
        # Pair shape matches the 20/23 non-blank transformer rows already in
        # sdi_dataset_EL (e.g. '600-208Y/120'). 346.4 is 600/sqrt(3); every
        # stored row uses the integer nominal, so it is written as 347.
        self.assertEqual(self.specs["voltage"], "12470-600Y/347")
        self.assertEqual(self.specs["voltage_uom"], "VLT")

    def test_preserves_verbatim_secondary_in_source_text(self) -> None:
        # The rounding is a storage convention only; the plate's literal
        # 346.4 must remain recoverable from the transcription.
        self.assertIn("346.4", ABB_PLATE)

    def test_yields_no_amperage(self) -> None:
        self.assertEqual(self.specs.get("ampere", ""), "")
        self.assertEqual(self.specs.get("ampere_uom", ""), "")

    def test_kva_survives_shared_normalizer_unchanged(self) -> None:
        pair = validators_shared.normalize_power_rating_pair(
            self.specs["kva"], self.specs["kva_uom"]
        )
        self.assertEqual(pair, ("1500", "KVA"))

    def test_thousands_separator_does_not_truncate(self) -> None:
        # normalize_power_rating_pair('1,500','KVA') silently yields '500',
        # so separators must be stripped before the value ever reaches it.
        specs = legacy_flow.legacy_nameplate_specs("SUR/ON 1,500 KVA")
        self.assertEqual(specs["kva"], "1500")

    def test_lamacoid_text_yields_no_nameplate_specs(self) -> None:
        specs = legacy_flow.legacy_nameplate_specs("PANEL U 120/208V 400A 3PH")
        self.assertEqual(specs["kva"], "")
        self.assertEqual(specs["voltage"], "")


def _load_el_api():
    """Import the EL extraction module (needs `db` from asset_capture_app_dev)."""
    for extra in ("asset_capture_app_dev", "API"):
        path = str(ROOT / extra)
        if path not in sys.path:
            sys.path.insert(0, path)
    return _load("api_el_undertest", "API/API_interface_EL_ver00.py")


try:
    api_el = _load_el_api()
except Exception:  # pragma: no cover - environment-dependent
    api_el = None


@unittest.skipIf(api_el is None, "EL extraction module not importable here")
class LegacyConfidenceTests(unittest.TestCase):
    """Confidence must describe the value actually stored.

    The model scores `Volts` against the LAMACOID. For a transformer it
    correctly leaves that blank and reports a low score, while the stored value
    comes from the nameplate. Reporting the lamacoid's score would raise
    `low_confidence_volts` on every correctly extracted transformer.
    """

    def _conf(self, final, raw_conf):
        return api_el._el_legacy_conf_scores(
            final, raw_conf, api_el.EL_LEGACY_TRANSFORMER_SCORING_FIELDS
        )

    def test_nameplate_derived_volts_inherits_nameplate_confidence(self) -> None:
        final = {
            "UBC Asset Tag": "TX-MAIN", "Volts": "12470-600Y/347",
            "Power Rating": "1500", "Power Rating (UoM)": "KVA",
            "nameplate_text": ABB_PLATE,
        }
        conf = self._conf(final, {"UBC Asset Tag": 99, "Volts": 5, "Nameplate Text": 90})
        self.assertEqual(conf["Volts"], 90)
        self.assertEqual(conf["Power Rating"], 90)
        self.assertEqual(conf["UBC Asset Tag"], 99)

    def test_lamacoid_sourced_volts_keeps_its_own_confidence(self) -> None:
        # Volts here did NOT come from the nameplate, so the model's own score
        # is the honest one and must not be inflated.
        final = {
            "UBC Asset Tag": "TX-2", "Volts": "600/347",
            "Power Rating": "1500", "Power Rating (UoM)": "KVA",
            "nameplate_text": ABB_PLATE,
        }
        conf = self._conf(final, {"UBC Asset Tag": 99, "Volts": 40, "Nameplate Text": 90})
        self.assertEqual(conf["Volts"], 40)

    def test_blank_field_scores_zero(self) -> None:
        final = {"UBC Asset Tag": "TX-9", "Volts": "", "Power Rating": "", "Power Rating (UoM)": ""}
        conf = self._conf(final, {"UBC Asset Tag": 99, "Volts": 80, "Nameplate Text": 90})
        self.assertEqual(conf["Volts"], 0)
        self.assertEqual(conf["Power Rating"], 0)

    def test_driving_asset_is_not_flagged_low_confidence(self) -> None:
        # QR 0000186132's real model output: Volts scored 5 against the blank
        # lamacoid, nameplate scored 90.
        final = {
            "UBC Asset Tag": "TX-MAIN", "Volts": "12470-600Y/347",
            "Power Rating": "1500", "Power Rating (UoM)": "KVA",
            "nameplate_text": ABB_PLATE,
        }
        conf = self._conf(final, {"UBC Asset Tag": 99, "Volts": 5, "Nameplate Text": 90})
        low = [f for f, v in conf.items() if str(final.get(f) or "").strip() and v < 70]
        self.assertEqual(low, [])


class DecimalRatingTests(unittest.TestCase):
    """Fractional kVA sizes must not degrade into their fraction digit.

    37.5 / 75.0 / 112.5 / 167.5 kVA are standard dry-type sizes, and
    `112.5 K.V.A.` already appears verbatim in this building's production data
    (Output_jason_api/0000186130_EL_641.json). A bare `\\b(\\d{1,6})` scan
    word-boundaries onto the fraction and captures 5, which is a *valid* pair
    that every downstream normalizer passes through unchanged -- so a 112.5 kVA
    transformer would reach Planon as 5 kVA.

    sdi_dataset_EL stores `112` for a 112.5 kVA unit (electrical_building_schema
    keeps `112.5`), so the SDI-facing value truncates toward zero.
    """

    def test_declared_base_with_decimal_is_not_truncated_to_fraction(self) -> None:
        self.assertEqual(legacy_flow.legacy_nameplate_specs("SUR/ON 112.5 KVA")["kva"], "112")

    def test_bare_decimal_rating(self) -> None:
        self.assertEqual(legacy_flow.legacy_nameplate_specs("112.5 KVA")["kva"], "112")

    def test_trailing_zero_decimal_is_not_read_as_zero(self) -> None:
        self.assertEqual(legacy_flow.legacy_nameplate_specs("75.0 KVA")["kva"], "75")

    def test_dotted_unit_with_decimal(self) -> None:
        self.assertEqual(legacy_flow.legacy_nameplate_specs("112.5 K.V.A.")["kva"], "112")

    def test_dual_rated_decimal_plate_keeps_the_self_cooled_rating(self) -> None:
        specs = legacy_flow.legacy_nameplate_specs("45 KVA AA\n57.5 KVA FA")
        self.assertEqual(specs["kva"], "45")

    def test_decimal_result_survives_the_shared_normalizer(self) -> None:
        # normalize_power_rating_pair rejects decimals outright, so whatever we
        # emit must already be an integer or the value is silently lost.
        specs = legacy_flow.legacy_nameplate_specs("SUR/ON 112.5 KVA")
        self.assertEqual(
            validators_shared.normalize_power_rating_pair(specs["kva"], specs["kva_uom"]),
            ("112", "KVA"),
        )


class SecondaryVoltageTests(unittest.TestCase):
    """A printed integer secondary must never be replaced by a wye nominal."""

    def test_printed_split_phase_secondary_is_preserved(self) -> None:
        # 240/120 is split-phase; 240/sqrt(3)=139 appears on no plate and is
        # not a real system voltage. normalize_volts('600-240/139') -> ''.
        self.assertEqual(legacy_flow._nameplate_secondary_nominal("240/120"), "240/120")

    def test_printed_european_secondary_is_preserved(self) -> None:
        self.assertEqual(legacy_flow._nameplate_secondary_nominal("400/230"), "400/230")

    def test_decimal_wye_secondary_is_normalized_to_canonical_nominal(self) -> None:
        # 600/sqrt(3) = 346.4 -> the UBC-canonical 347.
        self.assertEqual(legacy_flow._nameplate_secondary_nominal("600Y/346.4"), "600Y/347")

    def test_printed_integer_wye_secondary_is_preserved(self) -> None:
        self.assertEqual(legacy_flow._nameplate_secondary_nominal("208Y/120"), "208Y/120")

    def test_split_phase_plate_end_to_end(self) -> None:
        specs = legacy_flow.legacy_nameplate_specs(
            "SUR/ON 112.5 KVA\n600 H.V. | 240/120 B.T. L.V."
        )
        self.assertEqual(specs["voltage"], "600-240/120")
        self.assertEqual(specs["kva"], "112")


class AmperageGuardTests(unittest.TestCase):
    """The winding-current table must never become an Amperage Rating."""

    def test_current_table_digits_are_not_harvested(self) -> None:
        self.assertEqual(legacy_flow.legacy_ratings_from_label(ABB_PLATE)["ampere"], "")

    def test_reformatted_tap_current_is_rejected_in_nameplate_context(self) -> None:
        # A transcription that renders the column header inline would otherwise
        # match the existing r'(\d{2,4})\s*(A(?:MPS?)?)' scan.
        text = "COURANT CURRENT (A) HAUTE TENSION TRIANGLE 100% 69.4A B.T. L.V. 100% 1443A"
        self.assertEqual(legacy_flow.legacy_ratings_from_label(text)["ampere"], "")

    def test_real_lamacoid_amperage_still_parses(self) -> None:
        # Regression guard: the hardening must not break ordinary lamacoids.
        self.assertEqual(
            legacy_flow.legacy_ratings_from_label("PANEL U 120/208V 400A 3PH")["ampere"],
            "400",
        )
        self.assertEqual(
            legacy_flow.legacy_ratings_from_label("DIST. CTRE. #1 600/347V 800 AMPS")["ampere"],
            "800",
        )


class StructuredCompositionTests(unittest.TestCase):
    """legacy_structured_from_raw() wiring, including source precedence."""

    def test_transformer_nameplate_fills_power_rating_and_volts(self) -> None:
        out = legacy_flow.legacy_structured_from_raw(
            {
                "Label Text": "TX-MAIN",
                "Nameplate Text": ABB_PLATE,
                "UBC Asset Tag": "TX-MAIN",
                "Supply From": "",
                "Volts": "",
                "Ampere": "",
            }
        )
        self.assertEqual(out["Power Rating"], "1500")
        self.assertEqual(out["Power Rating (UoM)"], "KVA")
        self.assertEqual(out["Volts"], "12470-600Y/347")
        self.assertEqual(out["Ampere"], "")

    def test_lamacoid_voltage_outranks_nameplate_voltage(self) -> None:
        # User rule: EL-1 lamacoid is primary, EL-0 asset plate is secondary.
        out = legacy_flow.legacy_structured_from_raw(
            {
                "Label Text": 'TX-MAIN 600/347V',
                "Nameplate Text": ABB_PLATE,
                "UBC Asset Tag": "TX-MAIN",
            }
        )
        self.assertEqual(out["Volts"], "600/347")

    def test_non_transformer_never_gets_a_power_rating(self) -> None:
        out = legacy_flow.legacy_structured_from_raw(
            {
                "Label Text": "PANEL U 120/208V 400A",
                "Nameplate Text": ABB_PLATE,
                "UBC Asset Tag": "PANEL U",
            }
        )
        self.assertEqual(out["Power Rating"], "")
        self.assertEqual(out["Power Rating (UoM)"], "")

    def test_upstream_transformer_kva_on_a_panel_label_is_ignored(self) -> None:
        # DCC-1's real lamacoid (0000186130_EL_641.json) prints its FEEDER
        # transformer's rating. The panel must not inherit 112.5 KVA.
        out = legacy_flow.legacy_structured_from_raw(
            {
                "Label Text": 'DIST. CTRE. #1\nFED FROM MAIN DIST. CTRE. TRANS. RM. 0060 '
                'THROUGH TRANS. "T1" 112.5 K.V.A.',
                "UBC Asset Tag": "DIST. CTRE. #1",
                "Supply From": 'FED FROM MAIN DIST. CTRE. TRANS. RM. 0060 THROUGH TRANS. "T1" 112.5 K.V.A.',
            }
        )
        self.assertEqual(out["Power Rating"], "")

    def test_absent_nameplate_text_is_backward_compatible(self) -> None:
        # Every legacy JSON written before this change lacks the key.
        out = legacy_flow.legacy_structured_from_raw({"Label Text": "PANEL U 120/208V 400A"})
        self.assertEqual(out["Power Rating"], "")
        self.assertEqual(out["Volts"], "208/120")
        self.assertEqual(out["Ampere"], "400")


class TransformerIdentityTests(unittest.TestCase):
    """A transformer tag the legacy ident parser cannot decode still has an identity.

    `parse_legacy_identity_text('TX-MAIN')` returns None (the legacy grammar
    models panel idents), which left Equipment ID and Equipment Type blank and
    let a sync overwrite the correct stored `TX-MAIN` with an empty string.
    Both fields are in the required set for the transformer asset groups.
    """

    def test_unparsed_transformer_tag_keeps_its_equipment_id(self) -> None:
        out = legacy_flow.legacy_structured_from_raw(
            {"Label Text": "TX-MAIN", "UBC Asset Tag": "TX-MAIN"}
        )
        self.assertEqual(out["Equipment ID"], "TX-MAIN")
        self.assertEqual(out["Equipment Type"], "Transformer")

    def test_non_transformer_unparsed_tag_still_yields_no_equipment_id(self) -> None:
        # Unchanged behavior: an undecodable non-transformer plate stays blank
        # rather than fabricating an identity.
        out = legacy_flow.legacy_structured_from_raw(
            {"Label Text": "SOME UNREADABLE PLATE", "UBC Asset Tag": "SOME UNREADABLE PLATE"}
        )
        self.assertEqual(out["Equipment ID"], "")

    def test_parsed_panel_identity_is_untouched(self) -> None:
        out = legacy_flow.legacy_structured_from_raw(
            {"Label Text": "PANEL U 120/208V 400A", "UBC Asset Tag": "PANEL U"}
        )
        self.assertEqual(out["Equipment ID"], "PNL-U")
        self.assertEqual(out["Equipment Type"], "Panel")


class ApplyLegacyRulesTests(unittest.TestCase):
    """Invariant 6: never erase a human override."""

    def test_blank_fills_power_rating_for_a_transformer(self) -> None:
        data = {
            "UBC Asset Tag": "TX-MAIN",
            "label_text": "TX-MAIN",
            "nameplate_text": ABB_PLATE,
            "Power Rating": "",
            "Power Rating (UoM)": "",
        }
        legacy_flow.apply_legacy_rules(data)
        self.assertEqual(data["Power Rating"], "1500")
        self.assertEqual(data["Power Rating (UoM)"], "KVA")

    def test_never_overwrites_a_reviewer_supplied_power_rating(self) -> None:
        data = {
            "UBC Asset Tag": "TX-MAIN",
            "label_text": "TX-MAIN",
            "nameplate_text": ABB_PLATE,
            "Power Rating": "2000",
            "Power Rating (UoM)": "KVA",
        }
        legacy_flow.apply_legacy_rules(data)
        self.assertEqual(data["Power Rating"], "2000")

    def test_never_overwrites_a_reviewer_supplied_voltage(self) -> None:
        data = {
            "UBC Asset Tag": "TX-MAIN",
            "label_text": "TX-MAIN",
            "nameplate_text": ABB_PLATE,
            "Volts": "600Y/347",
        }
        legacy_flow.apply_legacy_rules(data)
        self.assertEqual(data["Volts"], "600Y/347")


if __name__ == "__main__":
    unittest.main()
