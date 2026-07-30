import re
from typing import Dict, Optional, Set

# Manufacturers we accept across ME/BF (can be extended)
VALID_MANUFACTURERS: Set[str] = {"Watts", "Wilkins", "Conbraco", "Apollo", "Bell & Gossett", "Armstrong"}

# Electrical panel/tag schema hints (from dictionary/electrical.dictionary.py)
PANEL_ABBR = {"MDP", "CDP", "SPL", "MCC", "PNL", "SWBD", "ATS", "MDC"}
SYSTEM_CODES = {"N", "E", "S"}
LOCATION_CODES = {str(i) for i in range(0, 10)} | {"R"}
TYPE_CODES = {"L", "P", "M", "D"}


def normalize_year(year_str: str, min_year: int = 1950, max_year: int = 2026) -> str:
    s = (year_str or "").strip()
    if not s:
        return ""
    m = re.search(r"\b(19\d{2}|20\d{2})\b", s)
    if not m:
        return ""
    year = int(m.group(1))
    if year < min_year or year > max_year:
        return ""
    return str(year)


def normalize_manufacturer(name: str) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"[^A-Za-z& ]+", "", name).strip().title()
    for val in VALID_MANUFACTURERS:
        if cleaned.lower() == val.lower():
            return val
    return ""


def normalize_serial(serial: str) -> str:
    s = (serial or "").strip()
    if not s:
        return ""
    s = re.sub(r"[^\w\-]", "", s)
    return s[:64]


def looks_like_date_misread_serial(serial: str, year_hint: str = "") -> bool:
    """
    Detect serial values that are actually manufacturing dates, including dates
    read from a rotated/upside-down nameplate (e.g. "8102/90" is "09/2018"
    photographed rotated 180 degrees).

    Values containing letters are never flagged. Bare all-digit values without a
    separator are only flagged when year_hint corroborates them (protects
    legitimate numeric serials). Taco-style order serials ("20588223/1") are
    explicitly allowed.
    """
    s = (serial or "").strip()
    if not s:
        return False
    if re.search(r"[A-Za-z]", s):
        return False
    if re.fullmatch(r"\d{8}/\d", s):
        return False  # Taco order-number serials, e.g. 20588223/1

    def _reads_as_year(block: str) -> bool:
        return bool(re.fullmatch(r"(19|20)\d{2}", block))

    def _reads_as_month(block: str) -> bool:
        return bool(re.fullmatch(r"0?[1-9]|1[0-2]", block))

    parts = re.split(r"[/\-.]", s)
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        a, b = parts
        # MM/YYYY or YYYY/MM, accepting blocks whose character-reversal reads
        # as month/year (upside-down capture: "8102" -> "2018", "90" -> "09").
        for mm, yyyy in ((a, b), (b, a)):
            if len(yyyy) == 4 and (_reads_as_year(yyyy) or _reads_as_year(yyyy[::-1])):
                if _reads_as_month(mm) or _reads_as_month(mm[::-1]):
                    return True
        # MM/YY or YY/MM date shapes.
        if len(a) <= 2 and len(b) == 2 and _reads_as_month(a):
            return True
        if len(a) == 2 and len(b) <= 2 and _reads_as_month(b):
            return True
        return False

    # Compact all-digit values: only flag when the extracted Year corroborates
    # a MMYY / MMYYYY / YYYYMM reading (forwards or reversed).
    hint = (year_hint or "").strip()
    if not re.fullmatch(r"(19|20)\d{2}", hint):
        return False
    hint_year = int(hint)

    digits = re.sub(r"\D", "", s)
    if len(digits) not in (4, 6):
        return False

    def _year_matches(year_block: str) -> bool:
        if len(year_block) == 2:
            candidates = (1900 + int(year_block), 2000 + int(year_block))
        else:
            candidates = (int(year_block),)
        return any(abs(y - hint_year) <= 1 for y in candidates)

    for d in (digits, digits[::-1]):
        if len(d) == 4:
            pairs = ((d[:2], d[2:]), (d[2:], d[:2]))  # MMYY, YYMM
        else:
            pairs = ((d[:2], d[2:]), (d[4:], d[:4]))  # MMYYYY, YYYYMM
        for mm, yy in pairs:
            if _reads_as_month(mm) and _year_matches(yy):
                return True
    return False


def normalize_model(model: str) -> str:
    """
    General cleanup for model strings. Special-case: FCU MI vs FCU M2 confusion from handwritten labels.
    If we see 'FCU M2' and the rest is alphabetic, prefer 'FCU MI'.
    Normalize FCU separators (FCU-104 -> FCU 104).
    Handle common handwriting slip: ECMI -> FCMI.
    
    ENHANCED: Broader M1 -> MI correction for all FCU/FC patterns.
    Catches: "12 FC M1" -> "12 FC MI", "FCU-104 M1" -> "FCU-104 MI"
    """
    s = (model or "").strip()
    if not s:
        return ""
    s = re.sub(r"[^\w\s\-\.]", "", s).upper()
    
    # ENHANCED: Broader M1 -> MI correction (handwriting confusion: digit 1 vs letter I)
    # This catches patterns like "12 FC M1", "FCU M1", "FCUM1", etc.
    s = re.sub(r"\bM1\b", "MI", s)  # Standalone M1 -> MI
    s = re.sub(r"\bML\b", "MI", s)  # Standalone Ml (lowercase L misread) -> MI
    
    # Heuristic: FCU M2 -> FCU MI when token is likely letter I (digit 2 misread)
    if re.search(r"\bFCU\s+M2\b", s):
        s = re.sub(r"\bFCU\s+M2\b", "FCU MI", s)
    s = re.sub(r"\bFCU[\-\s]+", "FCU ", s)
    s = re.sub(r"\bFC\s*MI\b", "FC MI", s)
    s = re.sub(r"\bECMI\b", "FCMI", s)
    s = re.sub(r"\b(\d+)\s*ECMI\b", r"\1 FCMI", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80]


def normalize_ubc_tag(tag: str) -> str:
    s = (tag or "").strip()
    if not s:
        return ""
    s = re.sub(r"[^A-Za-z0-9\-.]", "", s)
    return s[:32]


def normalize_diameter(val: str) -> str:
    if not val:
        return ""
    t = val.strip().replace("”", '"').replace("“", '"').replace("''", '"')
    t = t.replace("INCHES", "in").replace("INCH", "in").replace("IN.", "in").replace("IN ", "in ")
    patterns = [
        r"\b\d{1,2}\s*-\s*\d/\d\s*\"?",   # 1-1/2"
        r"\b\d{1,2}\s+\d/\d\s*\"?",       # 1 1/2"
        r"\b\d{1,2}/\d\s*\"?",            # 1/2"
        r"\b\d{1,2}(?:\.\d{1,2})?\s*\"?", # 1.25"
    ]
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            s = m.group(0)
            s = re.sub(r"\s+", "", s)
            if not s.endswith('"'):
                s = s + '"'
            s = s.replace("--", "-").replace("-\"", '-"')
            return s
    m2 = re.search(r"\b\d{1,2}(?:\.\d{1,2})\b", t)
    if m2:
        return m2.group(0) + '"'
    return ""


def normalize_ampere(val: str) -> str:
    s = (val or "").strip().upper()
    if not s:
        return ""
    m = re.search(r"(\d{1,4})(?:\s*A|AMP|AMPS)?", s)
    if not m:
        return ""
    return m.group(1)


_AMPERE_UNIT_PATTERN = r"(?:A|AMP|AMPS|AMPERE|AMPERES)\b"
_EXPLICIT_AMPERE_NUM_FIRST_RE = re.compile(rf"(?<!\d)(\d{{1,4}})\s*{_AMPERE_UNIT_PATTERN}", re.IGNORECASE)
_EXPLICIT_AMPERE_UNIT_FIRST_RE = re.compile(rf"\b{_AMPERE_UNIT_PATTERN}\s*(\d{{1,4}})(?!\d)", re.IGNORECASE)


def extract_explicit_ampere_candidates(text: str) -> list[str]:
    source = str(text or "").strip().upper()
    if not source:
        return []

    candidates: list[str] = []
    for pattern in (_EXPLICIT_AMPERE_NUM_FIRST_RE, _EXPLICIT_AMPERE_UNIT_FIRST_RE):
        for match in pattern.finditer(source):
            value = match.group(1)
            if value not in candidates:
                candidates.append(value)
    return candidates


def normalize_explicit_ampere(value: str, *sources: str) -> str:
    normalized_value = normalize_ampere(value)
    if not normalized_value:
        return ""

    # Also treat the raw value itself as a source so that an LLM response
    # like "225A" (number + unit) counts as self-corroborating evidence.
    all_sources = [str(value or "").strip()] + list(sources)

    explicit_candidates: list[str] = []
    for source in all_sources:
        for candidate in extract_explicit_ampere_candidates(source):
            if candidate not in explicit_candidates:
                explicit_candidates.append(candidate)

    if normalized_value in explicit_candidates:
        return normalized_value
    return ""


def normalize_volts(val: str) -> str:
    # Returns the bare voltage value without unit letters ("208/120", not
    # "208/120V"); the unit is carried separately in "Voltage Rating (UoM)".
    s = (val or "").strip().upper()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)

    # Transformer-style primary-secondary notation:
    # 600V-208Y/120V, 600-208Y/120V, 600 DELTA-208Y/120V, 600 DELTA 208Y/120V
    transformer_patterns = [
        re.compile(
            r"\b(208|480|600)\s*V?\s*DELTA\s*(?:-| )\s*(208|480|600)\s*(Y)?\s*/\s*(120|208|240|277|347|480|600)\s*V?\b"
        ),
        re.compile(
            r"\b(208|480|600)\s*V?\s*-\s*(208|480|600)\s*(Y)?\s*/\s*(120|208|240|277|347|480|600)\s*V?\b"
        ),
    ]
    for pattern in transformer_patterns:
        match = pattern.search(s)
        if match:
            primary, secondary, wye_flag, tertiary = match.groups()
            secondary_text = f"{secondary}{'Y' if wye_flag else ''}"
            return f"{primary}-{secondary_text}/{tertiary}"

    # Preserve explicit Wye notation when only the secondary side is visible.
    m_wye = re.search(r"\b(208|480|600)\s*Y\s*/\s*(120|208|240|277|347|480|600)\s*V?\b", s)
    if m_wye:
        return f"{m_wye.group(1)}Y/{m_wye.group(2)}"

    # Common patterns: 208/120V, 600/347V, 480/277V
    m = re.search(r"(208|480|600)[/ ](120|277|347)\s*V?", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m2 = re.search(r"\b(208|480|600)V\b", s)
    if m2:
        return m2.group(1)
    return ""


POWER_RATING_UNITS = ("KVA", "KW", "VA")
_POWER_RATING_UNIT_PATTERN = "|".join(sorted(POWER_RATING_UNITS, key=len, reverse=True))
_POWER_RATING_NUM_FIRST_RE = re.compile(
    rf"(?<![\d.])([1-9]\d{{0,4}})\s*({_POWER_RATING_UNIT_PATTERN})\b",
    re.IGNORECASE,
)
_POWER_RATING_UNIT_FIRST_RE = re.compile(
    rf"\b({_POWER_RATING_UNIT_PATTERN})\s*([1-9]\d{{0,4}})(?![\d.])",
    re.IGNORECASE,
)


def normalize_power_rating_uom(val: str) -> str:
    s = (val or "").strip().upper()
    if not s:
        return ""
    for unit in POWER_RATING_UNITS:
        if re.fullmatch(unit, s, flags=re.IGNORECASE):
            return unit
    return ""


def normalize_power_rating_pair(value: str, uom: str = "") -> tuple[str, str]:
    candidates = [
        str(value or "").strip(),
        str(uom or "").strip(),
        f"{str(value or '').strip()} {str(uom or '').strip()}".strip(),
        f"{str(uom or '').strip()} {str(value or '').strip()}".strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        text = candidate.upper()
        match = _POWER_RATING_NUM_FIRST_RE.search(text)
        if match:
            return match.group(1), match.group(2).upper()
        match = _POWER_RATING_UNIT_FIRST_RE.search(text)
        if match:
            return match.group(2), match.group(1).upper()

    normalized_uom = normalize_power_rating_uom(uom)
    value_text = str(value or "").strip()
    if normalized_uom and re.fullmatch(r"[1-9]\d{0,4}", value_text):
        return value_text, normalized_uom

    normalized_value_uom = normalize_power_rating_uom(value)
    uom_text = str(uom or "").strip()
    if normalized_value_uom and re.fullmatch(r"[1-9]\d{0,4}", uom_text):
        return uom_text, normalized_value_uom

    return "", ""


def extract_explicit_power_rating_candidates(text: str) -> list[tuple[str, str]]:
    source = str(text or "").strip().upper()
    if not source:
        return []

    candidates: list[tuple[str, str]] = []
    for pattern in (_POWER_RATING_NUM_FIRST_RE, _POWER_RATING_UNIT_FIRST_RE):
        for match in pattern.finditer(source):
            if pattern is _POWER_RATING_NUM_FIRST_RE:
                value, unit = match.group(1), match.group(2).upper()
            else:
                unit, value = match.group(1).upper(), match.group(2)
            candidate = (value, unit)
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def normalize_explicit_power_rating_pair(value: str, uom: str = "", *sources: str) -> tuple[str, str]:
    normalized_value, normalized_uom = normalize_power_rating_pair(value, uom)
    if not normalized_value or not normalized_uom:
        return "", ""

    explicit_candidates: list[tuple[str, str]] = []
    for source in sources:
        for candidate in extract_explicit_power_rating_candidates(source):
            if candidate not in explicit_candidates:
                explicit_candidates.append(candidate)

    if (normalized_value, normalized_uom) in explicit_candidates:
        return normalized_value, normalized_uom
    return "", ""


_SUPPLY_FROM_LEAD_RE = re.compile(
    r"^(?:(?:FED|FEED)\s*FROM|SUPPLY\s*FROM|SOURCE\s*FROM|FROM)\b[\s:;,\-]*",
    re.IGNORECASE,
)
_SUPPLY_FROM_CUE_RE = re.compile(
    r"\b(?:(?:FED|FEED)\s*FROM|SUPPLY\s*FROM|SOURCE\s*FROM|FROM)\b[\s:;,\-]*",
    re.IGNORECASE,
)
_SUPPLY_FROM_STOP_RE = re.compile(
    r"\b(?:IN|AT|LOC(?:ATION)?|ROOM|RM|SPACE|SPC|AREA|LEVEL|FLOOR)\b",
    re.IGNORECASE,
)
_SUPPLY_FROM_TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,31}")
_SUPPLY_FROM_SKIP_TOKENS = {
    "FED",
    "FEED",
    "FROM",
    "SUPPLY",
    "SOURCE",
    "PANEL",
    "PANELBOARD",
    "BOARD",
    "EQUIPMENT",
    "ID",
    "IN",
    "AT",
    "ROOM",
    "RM",
    "SPACE",
    "SPC",
    "AREA",
    "LEVEL",
    "FLOOR",
    "MAIN",
    "ELECTRICAL",
    "ELEC",
}
_SUPPLY_FROM_JOINABLE_PREFIXES = PANEL_ABBR | {"TX"}


def _strip_supply_from_lead(text: str) -> str:
    cleaned = str(text or "").strip()
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = _SUPPLY_FROM_LEAD_RE.sub("", cleaned).strip(" -:;,/")
    return cleaned


def normalize_supply_from(val: str) -> str:
    s = str(val or "").upper()
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"[|]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    embedded_match = _SUPPLY_FROM_CUE_RE.search(s)
    if embedded_match and embedded_match.start() > 0:
        s = s[embedded_match.start():]
    s = _strip_supply_from_lead(s)
    if not s:
        return ""

    stop_match = _SUPPLY_FROM_STOP_RE.search(s)
    if stop_match and stop_match.start() > 0:
        s = s[:stop_match.start()].strip(" -:;,/")
        s = _strip_supply_from_lead(s)
        if not s:
            return ""

    if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,31}", s):
        return s[:80]

    tokens = _SUPPLY_FROM_TOKEN_RE.findall(s)
    if not tokens:
        return s[:80]

    for idx, token in enumerate(tokens):
        if token in _SUPPLY_FROM_SKIP_TOKENS:
            continue

        if token == "SWITCHBOARD" and idx + 1 < len(tokens):
            next_token = tokens[idx + 1]
            if next_token not in _SUPPLY_FROM_SKIP_TOKENS:
                return f"SWBD-{next_token}"[:80]

        if token == "TRANSFORMER" and idx + 1 < len(tokens):
            next_token = tokens[idx + 1]
            if next_token not in _SUPPLY_FROM_SKIP_TOKENS:
                return f"TX-{next_token}"[:80]

        if token in _SUPPLY_FROM_JOINABLE_PREFIXES and idx + 1 < len(tokens):
            next_token = tokens[idx + 1]
            if next_token not in _SUPPLY_FROM_SKIP_TOKENS:
                return f"{token}-{next_token}"[:80]

        return token[:80]

    return s[:80]


_EL_PANEL_DECIMAL_FLOOR_RE = re.compile(
    r"^(?P<abbr>(?:MDP|CDP|SPL|MCC|PNL|SWBD|ATS)-?)?"
    r"(?P<head>[26][NES])15(?P<tail>[LPMD]\d{1,3})$",
    re.IGNORECASE,
)
_EL_TRANSFORMER_DECIMAL_FLOOR_RE = re.compile(
    r"^(?P<prefix>TX-?[NES])15(?P<tail>[A-Z]\d{1,3})$",
    re.IGNORECASE,
)


def restore_el_decimal_floor_marker(raw_tag: str) -> str:
    """Restore the missing dot in compact EL tags for 1.5 floor assets."""
    tag = str(raw_tag or "").strip().upper()
    if not tag or "." in tag:
        return tag

    panel_match = _EL_PANEL_DECIMAL_FLOOR_RE.fullmatch(tag)
    if panel_match:
        abbr = panel_match.group("abbr") or ""
        if abbr and not abbr.endswith("-"):
            abbr = f"{abbr}-"
        return f"{abbr}{panel_match.group('head')}1.5{panel_match.group('tail')}"

    transformer_match = _EL_TRANSFORMER_DECIMAL_FLOOR_RE.fullmatch(tag)
    if transformer_match:
        prefix = transformer_match.group("prefix")
        if prefix.startswith("TX") and not prefix.startswith("TX-"):
            prefix = f"TX-{prefix[2:]}"
        return f"{prefix}1.5{transformer_match.group('tail')}"

    return tag


def normalize_el_supply_from_tag(value: object) -> str:
    """Return the canonical stored EL Supply From equipment tag.

    This keeps the phrase-cleaning behavior from normalize_supply_from and then
    applies the EL tag-prefix rule used by the review dashboard.
    """
    clean = normalize_supply_from(str(value or ""))
    clean = clean.upper().replace("EQUIPMENT NAME:", "").replace("MAIN", "").strip()
    clean = restore_el_decimal_floor_marker(clean)
    if not clean:
        return ""

    if clean.startswith("TX") or (clean.startswith("T") and len(clean) > 1 and clean[1].isdigit()):
        return clean

    if clean[0].isdigit():
        return f"PNL-{clean}"

    for abbr in sorted(PANEL_ABBR, key=len, reverse=True):
        if clean.startswith(abbr):
            remainder = clean[len(abbr):].lstrip(" -_")
            return f"{abbr}-{remainder}" if remainder else abbr

    return clean


def normalize_panel_tag(tag: str) -> str:
    """
    Enforce Panel tag like CDP-6-N-1-L-1 or TX-N-0-1.
    Falls back to alphanum+dash capped length.
    """
    s = (tag or "").strip().upper()
    if not s:
        return ""
    # transformer
    tx = re.match(r"^TX-([NES])-([0-9R])-([0-9]{1,3})$", s)
    if tx:
        return f"TX-{tx.group(1)}-{tx.group(2)}-{tx.group(3)}"
    # panel
    parts = s.split("-")
    if len(parts) >= 5:
        abbr, volt, sys, loc, typ, *rest = parts
        if abbr in PANEL_ABBR and volt in {"6", "2"} and sys in SYSTEM_CODES and loc in LOCATION_CODES and typ in TYPE_CODES:
            seq = rest[0] if rest else ""
            if re.match(r"^\d{1,3}$", seq):
                return f"{abbr}-{volt}-{sys}-{loc}-{typ}-{seq}"
    # fallback safe tag
    fallback = re.sub(r"[^A-Z0-9\-.]", "", s)[:32]
    return fallback


def normalize_description(desc: str) -> str:
    s = (desc or "").strip()
    if not s:
        return ""
    return s[:120]


# -----------------------------------------------------------------------------
# Geographic coordinates (per-capture device GPS — discipline-agnostic)
# -----------------------------------------------------------------------------
def _normalize_coordinate(val: object, low: float, high: float) -> str:
    """Validate a single decimal-degree coordinate.

    Returns a cleaned decimal string (rounded to 6 dp ≈ 0.11 m, trailing zeros
    trimmed) when `val` parses to a finite number inside [low, high]; otherwise
    "". Coordinates are best-effort capture metadata, so anything unparseable,
    non-finite, or out of range is simply dropped rather than stored.
    """
    s = str(val if val is not None else "").strip()
    if not s:
        return ""
    try:
        num = float(s)
    except (TypeError, ValueError):
        return ""
    if num != num or num in (float("inf"), float("-inf")):  # NaN / ±inf
        return ""
    if num < low or num > high:
        return ""
    text = f"{num:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def normalize_latitude(val: object) -> str:
    """Latitude in decimal degrees, valid range [-90, 90]. "" if invalid."""
    return _normalize_coordinate(val, -90.0, 90.0)


def normalize_longitude(val: object) -> str:
    """Longitude in decimal degrees, valid range [-180, 180]. "" if invalid."""
    return _normalize_coordinate(val, -180.0, 180.0)


def normalize_lat_lng(lat: object, lng: object) -> tuple[str, str]:
    """Validate a coordinate PAIR.

    Returns ("", "") unless BOTH the latitude and longitude are individually
    valid — a half-coordinate cannot locate an asset, so partial pairs are
    discarded rather than stored misleadingly.
    """
    nlat = normalize_latitude(lat)
    nlng = normalize_longitude(lng)
    if not nlat or not nlng:
        return "", ""
    return nlat, nlng


def format_gps_coordinates(lat: object, lng: object) -> str:
    """Return the stored review-display coordinate pair as ``lat,lng``."""
    nlat, nlng = normalize_lat_lng(lat, lng)
    return f"{nlat},{nlng}" if nlat and nlng else ""


def resolve_capture_coordinates(
    device_lat: object,
    device_lng: object,
    building_lat: object = "",
    building_lng: object = "",
    existing_source: str = "",
    existing_lat: str = "",
) -> tuple[str, str, str]:
    """Decide which coordinates to persist for a capture and their provenance.

    Precedence:
      * A precise *device* GPS fix always wins.
      * A *building*-centroid fallback fills only when there is no precise device
        fix in this submission AND there is no existing device fix to protect —
        it must never DOWNGRADE a stored precise fix to a coarse centroid.

    Returns ``(lat, lng, source)`` where source is ``"device"``, ``"building"``,
    or ``""`` (meaning: write nothing / leave any existing value untouched).
    """
    dlat, dlng = normalize_lat_lng(device_lat, device_lng)
    if dlat and dlng:
        return dlat, dlng, "device"

    blat, blng = normalize_lat_lng(building_lat, building_lng)
    if blat and blng:
        already_device = (
            str(existing_source or "").strip() == "device"
            and str(existing_lat or "").strip() != ""
        )
        if already_device:
            return "", "", ""  # keep the precise fix; do not overwrite with a centroid
        return blat, blng, "building"

    return "", "", ""


def completeness_score(data: Dict[str, str], required: Set[str]) -> float:
    if not required:
        return 100.0
    present = sum(1 for f in required if data.get(f, "").strip())
    return (present / len(required)) * 100.0
