from __future__ import annotations

import re


label_schema = {
    "panel": {
        "format": "<ABBR>-<VOLTAGE>-<SYSTEM>-<LOCATION>-<TYPE>-<SEQUENCE>",
        "order": ["abbr", "voltage", "system", "location", "type", "sequence"],
        "codes": {
            "abbr": {
                "MDP": "Main Distribution Panel",
                "MDC": "Main Distribution Center",
                "CDP": "Central Distribution Panel",
                "SPL": "Splitter",
                "MCC": "Motor Control Centre",
                "PNL": "Panel",
                "ATS": "Automatic Transfer Switches",
                "SWBD": "Distribution Switchboard",
            },
            "voltage": {
                "6": "600/347V",
                "2": "208/120V",
            },
            "system": {
                "N": "Normal",
                "E": "Life Safety",
                "S": "Standby",
            },
            "location": {
                "0": "Level 0",
                "1": "Level 1",
                "2": "Level 2",
                "R": "Roof",
            },
            "type": {
                "L": "Lighting",
                "P": "Power",
                "M": "Mechanical",
                "D": "Distribution",
                "T": "Tenant",
                "R": "Receptacle",
                "Z": "Miscellaneous",
                "F": "Fire/Life-Safety",
            },
            "sequence": "1, 2, 3, ...",
        },
        "example": "CDP-6-N-1-L-1",
    },
    "legacy_panel": {
        "format": "<ABBR>-<LEGACY_PANEL_CODE>",
        "order": ["abbr", "legacy_panel_code"],
        "codes": {
            "abbr": {
                "MDP": "Main Distribution Panel",
                "CDP": "Central Distribution Panel",
                "EDC": "Electrical Distribution Center",
                "NDC": "Normal Distribution Center",
                "SPL": "Splitter",
                "MCC": "Motor Control Centre",
                "PNL": "Panel",
                "ATS": "Automatic Transfer Switches",
            },
            "legacy_panel_code": "Alphanumeric panel code used on legacy UBC drawings, e.g. 3BA, 3B2A, 5A2",
        },
        "example": "CDP-3B2A",
    },
    "panel_tag": {
        "format": "<PANEL_CODE>",
        "order": ["panel_code"],
        "codes": {
            "panel_code": "Panel tag used on legacy UBC drawings, e.g. B3A1, HB1A1, 1A41, 5A68",
        },
        "example": "5A61",
    },
    "switchboard": {
        "format": "SWBD-<SWITCHBOARD_CODE>",
        "order": ["abbr", "switchboard_code"],
        "codes": {
            "abbr": {
                "SWBD": "Distribution Switchboard",
            },
            "switchboard_code": "Switchboard suffix used on drawing families such as A, B, LS, 1A",
        },
        "example": "SWBD-A",
    },
    "transformer": {
        "format": "TX-<SYSTEM><LOCATION>[<SECONDARY_SYSTEM>]<SEQUENCE>",
        "order": ["prefix", "system", "location", "secondary_system", "sequence"],
        "codes": {
            "prefix": {"TX": "Transformer"},
            "system": {
                "N": "Normal",
                "E": "Life Safety",
                "S": "Standby",
            },
            "location": {
                "0": "Level 0",
                "1": "Level 1",
                "1.5": "Mezzanine 1.5",
                "2": "Level 2",
                "R": "Roof",
            },
            "secondary_system": {
                "N": "Normal",
                "E": "Life Safety",
                "S": "Standby",
            },
            "sequence": "1, 2, 3, ...",
        },
        "example": "TX-E2S1 (Life Safety TX on Level 2 feeding Standby bus #1)",
    },
    "generator": {
        "format": "GEN-<SEQUENCE>",
        "order": ["prefix", "sequence"],
        "codes": {
            "prefix": {"GEN": "Generator"},
            "sequence": "1, 2, 3, ...",
        },
        "example": "GEN-1",
    },
}


_PANEL_ABBRS = frozenset(label_schema["panel"]["codes"]["abbr"])
_PANEL_VOLTAGES = frozenset(label_schema["panel"]["codes"]["voltage"])
_PANEL_SYSTEMS = frozenset(label_schema["panel"]["codes"]["system"])
_PANEL_TYPES = frozenset(label_schema["panel"]["codes"]["type"])
_TRANSFORMER_SYSTEMS = frozenset(label_schema["transformer"]["codes"]["system"])
_ALIAS_IDS = {"MDP": "MDC"}
_SPECIAL_IDS = frozenset({"MDC"})
_LOCATION_PATTERN = r"(?:\d+(?:\.\d+)?|R)"

_PANEL_CANONICAL_PATTERN = re.compile(
    rf"^(?P<abbr>[A-Z]+)-(?P<voltage>[A-Z0-9])-(?P<system>[A-Z])-(?P<location>{_LOCATION_PATTERN})(?:-(?P<type>[A-Z])-(?P<sequence>\d+))?$"
)
_PANEL_DISTRIBUTION_PATTERN = re.compile(
    rf"^(?P<abbr>[A-Z]+)-(?P<voltage>[A-Z0-9])(?P<system>[A-Z])(?P<location>{_LOCATION_PATTERN})$"
)
_PANEL_TYPED_PATTERN = re.compile(
    rf"^(?P<abbr>[A-Z]+)-(?P<voltage>[A-Z0-9])(?P<system>[A-Z])(?P<location>{_LOCATION_PATTERN})(?P<type>[A-Z])(?P<sequence>\d+)$"
)
_COMPACT_PANEL_PATTERN = re.compile(
    rf"^(?P<voltage>[A-Z0-9])(?P<system>[A-Z])(?P<location>{_LOCATION_PATTERN})(?P<type>[A-Z])(?P<sequence>\d+)$"
)
_LEGACY_CODE_SHAPE = r"(?:(?:HB|B)?\d[A-Z]\d*(?:[A-Z]\d*)?|[A-Z]{1,3}\d?)"
_BROKEN_MODERN_TAG_SHAPES = (
    re.compile(r"^[26][NES][NESLPMDTRZF]\d+$"),
    re.compile(r"^[26][NES][NESLPMDTRZF]$"),
    re.compile(r"^[26][NES]\d+[LPMDTRZF]$"),
    re.compile(r"^\d[NES]\d+[LPMDTRZF]\d*$"),
    re.compile(r"^\d[A-Z][LPMDTRZF]\d+$"),
)


def _looks_like_broken_modern_tag(code: str) -> bool:
    return any(pat.fullmatch(code) for pat in _BROKEN_MODERN_TAG_SHAPES)
_LEGACY_PANEL_PATTERN = re.compile(
    rf"^(?P<abbr>MDP|CDP|EDC|NDC|SPL|MCC|PNL|ATS)-(?P<legacy_panel_code>{_LEGACY_CODE_SHAPE})$"
)
_LEGACY_PANEL_TAG_PATTERN = re.compile(
    r"^(?P<panel_code>(?:HB|B)?\dA\d{1,3})$"
)
_SWITCHBOARD_PATTERN = re.compile(
    rf"^(?P<abbr>SWBD)-(?P<switchboard_code>{_LEGACY_CODE_SHAPE})$"
)
_TRANSFORMER_CANONICAL_PATTERN = re.compile(
    rf"^(?P<prefix>TX)-(?P<system>[A-Z])-(?P<location>{_LOCATION_PATTERN})(?:-(?P<secondary_system>[A-Z]))?-(?P<sequence>\d+)$"
)
_TRANSFORMER_PATTERN = re.compile(
    rf"^(?P<prefix>TX)-(?P<system>[A-Z])(?P<location>{_LOCATION_PATTERN})(?P<secondary_system>[NES])?(?P<sequence>\d+)$"
)
_GENERATOR_PATTERN = re.compile(r"^GEN-(?P<sequence>\d+)$")
_PANEL_WRAPPER_PATTERN = re.compile(r"PANEL\s+'([^']+)'", re.IGNORECASE)
_PANEL_PLAIN_WRAPPER_PATTERN = re.compile(r"^PANEL\s+(?P<panel_code>[A-Z0-9-]+)$", re.IGNORECASE)


def _normalize_text(value: str) -> str:
    text = str(value).strip().upper().strip("'\"")
    if not text:
        return ""

    panel_match = _PANEL_WRAPPER_PATTERN.search(text)
    if panel_match:
        text = panel_match.group(1).strip().upper()
    else:
        plain_panel_match = _PANEL_PLAIN_WRAPPER_PATTERN.fullmatch(text)
        if plain_panel_match:
            text = plain_panel_match.group("panel_code").strip().upper()

    text = re.sub(r"\s+", "", text)
    return _ALIAS_IDS.get(text, text)


def _normalize_panel_id(value: str) -> str:
    match = _PANEL_CANONICAL_PATTERN.fullmatch(value)
    if not match:
        return value

    abbr = match.group("abbr")
    voltage = match.group("voltage")
    system = match.group("system")
    location = match.group("location")
    panel_type = match.group("type")
    sequence = match.group("sequence")

    if panel_type and sequence:
        return f"{abbr}-{voltage}{system}{location}{panel_type}{sequence}"
    return f"{abbr}-{voltage}{system}{location}"


def _normalize_transformer_id(value: str) -> str:
    match = _TRANSFORMER_CANONICAL_PATTERN.fullmatch(value)
    if not match:
        return value

    secondary = match.group("secondary_system") or ""
    return (
        f"{match.group('prefix')}-{match.group('system')}"
        f"{match.group('location')}{secondary}{match.group('sequence')}"
    )


def _normalize_panel_tag_id(value: str) -> str:
    if value.startswith(("CDP-", "PNL-", "MCC-", "SPL-", "ATS-", "SWBD-", "TX-")) or value in _SPECIAL_IDS:
        return value

    compact_match = _COMPACT_PANEL_PATTERN.fullmatch(value)
    if compact_match:
        panel_type = compact_match.group("type")
        if panel_type == "D":
            return f"CDP-{value}"
        return f"PNL-{value}"

    if _LEGACY_PANEL_TAG_PATTERN.fullmatch(value):
        return f"PNL-{value}"

    return value


def normalize_dictionary_id(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""

    normalized = _normalize_panel_id(normalized)
    normalized = _normalize_transformer_id(normalized)
    normalized = _normalize_panel_tag_id(normalized)
    return normalized


def _is_valid_location(value: str) -> bool:
    return bool(re.fullmatch(_LOCATION_PATTERN, value))


def _is_valid_panel_groups(groups: dict[str, str]) -> bool:
    abbr = groups.get("abbr", "")
    voltage = groups.get("voltage", "")
    system = groups.get("system", "")
    location = groups.get("location", "")
    panel_type = groups.get("type", "")

    if abbr not in _PANEL_ABBRS:
        return False
    if voltage not in _PANEL_VOLTAGES:
        return False
    if system not in _PANEL_SYSTEMS:
        return False
    if not _is_valid_location(location):
        return False
    if panel_type and panel_type not in _PANEL_TYPES:
        return False
    return True


def _is_valid_legacy_panel(groups: dict[str, str]) -> bool:
    abbr = groups.get("abbr", "")
    code = groups.get("legacy_panel_code", "")
    if abbr not in {"MDP", "CDP", "EDC", "NDC", "SPL", "MCC", "PNL", "ATS"}:
        return False
    if _looks_like_broken_modern_tag(code):
        return False
    return bool(re.fullmatch(_LEGACY_CODE_SHAPE, code))


def _is_valid_switchboard(groups: dict[str, str]) -> bool:
    abbr = groups.get("abbr", "")
    code = groups.get("switchboard_code", "")
    if abbr != "SWBD":
        return False
    if _looks_like_broken_modern_tag(code):
        return False
    return bool(re.fullmatch(_LEGACY_CODE_SHAPE, code))


def _is_valid_legacy_panel_tag(groups: dict[str, str]) -> bool:
    code = groups.get("panel_code", "")
    return bool(re.fullmatch(r"(?:HB|B)?\dA\d{1,3}", code))


def is_dictionary_id(value: str) -> bool:
    normalized = normalize_dictionary_id(value)
    if not normalized:
        return False

    if normalized in _SPECIAL_IDS:
        return True

    panel_distribution_match = _PANEL_DISTRIBUTION_PATTERN.fullmatch(normalized)
    if panel_distribution_match and _is_valid_panel_groups(panel_distribution_match.groupdict()):
        return True

    panel_typed_match = _PANEL_TYPED_PATTERN.fullmatch(normalized)
    if panel_typed_match and _is_valid_panel_groups(panel_typed_match.groupdict()):
        return True

    compact_panel_match = _COMPACT_PANEL_PATTERN.fullmatch(normalized)
    if compact_panel_match:
        groups = compact_panel_match.groupdict()
        if (
            groups["voltage"] in _PANEL_VOLTAGES
            and groups["system"] in _PANEL_SYSTEMS
            and _is_valid_location(groups["location"])
            and groups["type"] in _PANEL_TYPES
        ):
            return True

    legacy_panel_match = _LEGACY_PANEL_PATTERN.fullmatch(normalized)
    if legacy_panel_match and _is_valid_legacy_panel(legacy_panel_match.groupdict()):
        return True

    switchboard_match = _SWITCHBOARD_PATTERN.fullmatch(normalized)
    if switchboard_match and _is_valid_switchboard(switchboard_match.groupdict()):
        return True

    legacy_panel_tag_match = _LEGACY_PANEL_TAG_PATTERN.fullmatch(normalized)
    if legacy_panel_tag_match and _is_valid_legacy_panel_tag(legacy_panel_tag_match.groupdict()):
        return True

    transformer_match = _TRANSFORMER_PATTERN.fullmatch(normalized)
    if transformer_match:
        secondary = transformer_match.group("secondary_system")
        return (
            transformer_match.group("prefix") == "TX"
            and transformer_match.group("system") in _TRANSFORMER_SYSTEMS
            and _is_valid_location(transformer_match.group("location"))
            and (secondary is None or secondary in _TRANSFORMER_SYSTEMS)
        )

    if _GENERATOR_PATTERN.fullmatch(normalized):
        return True

    return False
