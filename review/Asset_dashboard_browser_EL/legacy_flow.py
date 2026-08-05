"""Legacy electrical flow rules (gated by Buildings.Process).

Loaded by the EL review app and the SLD extractor ONLY for buildings whose
"Buildings"."Process" is 'Legacy'. The standard flow never imports this module.
Dictionary source: dictionary/electrical.dictionary_old.py (working copy).
"""
import importlib.util
import os
import re


class BuildingProcessError(Exception):
    """Buildings.Process is blank/NULL/missing — processing must stop and warn."""

    def __init__(self, building_code):
        self.building_code = building_code
        super().__init__(
            f"Building {building_code!r} has no Process value (Standard/Legacy). "
            "Set \"Buildings\".\"Process\" before running the electrical flow."
        )


def get_building_process(conn, building_code):
    cur = conn.execute(
        'SELECT "Process" FROM "Buildings" WHERE "Code" = ?', (str(building_code or ""),)
    )
    row = cur.fetchone()
    value = (row[0] if row and row[0] is not None else "").strip()
    if value in ("Standard", "Legacy"):
        return value
    raise BuildingProcessError(building_code)


_LEGACY_DICT_CANDIDATES = (
    os.environ.get("ELEC_LEGACY_DICT_PATH", "").strip() or None,
    "/home/developer/dictionary/electrical.dictionary_old.py",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "..", "dictionary", "electrical.dictionary_old.py")),
)
_schema_cache = None


def load_legacy_schema():
    global _schema_cache
    if _schema_cache is None:
        for path in _LEGACY_DICT_CANDIDATES:
            if path and os.path.exists(path):
                spec = importlib.util.spec_from_file_location("electrical_dictionary_legacy", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _schema_cache = getattr(mod, "label_schema", {})
                break
        else:
            # Silent {} degrades the whole legacy flow to a no-op (parse_legacy_ident
            # returns None for every label, so Equipment ID / Branch Panel /
            # Equipment Type / Power Type stop filling) with no visible symptom.
            # Warn once -- the cache below makes this print at most one time per
            # process. Do not raise: a missing dictionary must not 500 the review
            # app, and the gate itself (Buildings.Process) is unaffected.
            print(
                "[WARN] Legacy electrical dictionary not found in any candidate path "
                f"({', '.join(p for p in _LEGACY_DICT_CANDIDATES if p)}) -- legacy "
                "ident/ratings rules will not fill any field. Set ELEC_LEGACY_DICT_PATH "
                "or install dictionary/electrical.dictionary_old.py."
            )
            _schema_cache = {}
    return _schema_cache


def _normalize_label(text):
    text = str(text or "").strip().upper()
    text = re.sub(r"\s+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def parse_legacy_ident(label_text):
    schema = load_legacy_schema()
    legacy = schema.get("panel_legacy") or {}
    panel = schema.get("panel") or {}
    tag = _normalize_label(label_text)
    if not tag or not legacy:
        return None
    parts = tag.split("-")

    abbr_map = dict(legacy.get("codes", {}).get("abbr", {}))
    panel_abbrs = set(panel.get("codes", {}).get("abbr", {}))
    hints = legacy.get("codes", {}).get("system_hints", {})
    ident_pattern = legacy.get("codes", {}).get("ident", {}).get("pattern", "")
    if not ident_pattern:
        return None
    ident_re = re.compile(ident_pattern)

    # Schema-driven ident normalization (2026-07-30): dotted abbreviations
    # lose their periods ('U.P.S.1' -> 'UPS1') and known plate typos map to
    # canonical letters ('USP1' -> 'UPS1'), BEFORE the pattern match. Both
    # rules live in the dictionary (panel_legacy.codes.ident_normalization);
    # absent key = no-op, so older dictionaries keep pre-existing behavior.
    ident_norm = legacy.get("codes", {}).get("ident_normalization", {}) or {}

    def _normalize_ident(candidate):
        for ch in str(ident_norm.get("strip_chars") or ""):
            candidate = candidate.replace(ch, "")
        m_letters = re.match(r"^([A-Z]+)", candidate)
        if m_letters:
            canonical = (ident_norm.get("equivalences") or {}).get(m_letters.group(1))
            if canonical:
                candidate = canonical + candidate[m_letters.end():]
        return candidate

    # PNL-U / PANEL-NPH style: prefix + letter-leading ident
    if len(parts) == 2 and parts[0] in abbr_map and not parts[1][0].isdigit() \
            and ident_re.fullmatch(_normalize_ident(parts[1])):
        prefix, ident = "PNL", _normalize_ident(parts[1])
        # Shortest-match wins: "E" shadows compound hints like "EM" so they normalize to
        # their single-letter system code. Downstream (corroborated_power_type, Task 5)
        # only accepts system_hint in {'N', 'E', 'UPS', ''}, making this invariant mandatory.
        hint = next((k for k in sorted(hints, key=len) if ident.startswith(k)), "")
        m = re.search(r"(\d+)$", ident)
        return dict(equipment_id=f"{prefix}-{ident}", prefix=prefix, ident=ident,
                    system_hint=hint, unit_number=m.group(1) if m else "")

    # MCC2 / ATS style: non-PNL panel-family prefix, optional glued unit digit, nothing else
    if len(parts) == 1:
        for abbr in sorted(panel_abbrs, key=len, reverse=True):
            if abbr in ("PNL",):
                continue
            if parts[0] == abbr or (parts[0].startswith(abbr) and parts[0][len(abbr):].isdigit()):
                unit = parts[0][len(abbr):]
                return dict(equipment_id=parts[0], prefix=abbr, ident=unit,
                            system_hint="", unit_number=unit)
    return None


# Context markers that identify a manufacturer-nameplate winding-current table
# rather than a lamacoid's service amperage. '%' catches the tap rows
# ('100% 69.4'), the H.T./B.T./H.V./L.V. alternatives catch the winding row
# labels, and COURANT/CURRENT catches the column header itself.
_NAMEPLATE_AMP_CONTEXT_RE = re.compile(
    r"(?:COURANT|CURRENT|%|\bH\.?T\.?\b|\bB\.?T\.?\b|\bH\.?V\.?\b|\bL\.?V\.?\b)"
)


def legacy_ratings_from_label(text):
    up = str(text or "").upper()
    out = dict(voltage="", voltage_uom="", ampere="", ampere_uom="",
               phase="", wires="", breaker=False)

    m = re.search(r"\b(\d{2,3})\s*/\s*(\d{2,3})\s*V(?:OLTS?)?\b", up)
    if m:
        hi, lo = sorted((int(m.group(1)), int(m.group(2))), reverse=True)
        out["voltage"], out["voltage_uom"] = f"{hi}/{lo}", "VLT"
    else:
        m = re.search(r"\b(\d{3})\s*V(?:OLTS?)?\b", up)
        if m:
            out["voltage"], out["voltage_uom"] = m.group(1), "VLT"

    # Hardened ampere extraction: collect all matches, filter, prefer word-form or BRK-followed
    candidates = []
    for m in re.finditer(r"\b(\d{2,4})\s*(A(?:MPS?)?)\b", up):
        digit_run, unit = m.group(1), m.group(2)
        # Reject leading zero (room numbers like 0060)
        if digit_run[0] == "0":
            continue
        # Reject if preceded by RM/ROOM/PANEL/PNL/# (allowing one space or punct)
        start = m.start()
        context = up[max(0, start - 10):start].rstrip()
        if re.search(r"(?:\b(?:RM\.?|ROOM|PANEL|PNL)|#)$", context):
            continue
        # Reject manufacturer-nameplate winding-current context. A transformer
        # plate prints its current table under a 'COURANT CURRENT (A)' header
        # with '%'-tap rows and H.T./B.T. row labels; those amps are per-tap
        # per-winding values, not the asset's Amperage Rating (see
        # legacy_nameplate_specs). Needs a wider window than the 10-char
        # lamacoid check because the row label precedes the tap percentage.
        if _NAMEPLATE_AMP_CONTEXT_RE.search(up[max(0, start - 28):start]):
            continue
        # Track: (digit_run, has_word_form, is_brk_followed)
        is_word_form = unit in ("AMP", "AMPS")
        is_brk_followed = bool(re.search(r"\bBRK\b", up[m.end():m.end() + 20]))
        candidates.append((digit_run, is_word_form, is_brk_followed))

    if candidates:
        # Prefer: first with word form, or first with BRK-followed, or first overall
        word_form = next((c for c in candidates if c[1]), None)
        if word_form:
            out["ampere"], out["ampere_uom"] = word_form[0], "AMP"
        else:
            brk_followed = next((c for c in candidates if c[2]), None)
            if brk_followed:
                out["ampere"], out["ampere_uom"] = brk_followed[0], "AMP"
            else:
                out["ampere"], out["ampere_uom"] = candidates[0][0], "AMP"

    m = re.search(r"\b(\d)\s*PH(?:ASE)?\b|\b(\d)\s*Ø", up)
    if m:
        out["phase"] = m.group(1) or m.group(2)

    m = re.search(r"\b(\d)\s*W(?:IRE)?\b", up)
    if m:
        out["wires"] = m.group(1)

    out["breaker"] = bool(re.search(r"\bBRK\b", up))
    return out


# --- Manufacturer nameplate (EL-0 "Asset Plate (Optional)") ------------------
# Source precedence (user rule, 2026-08-04): the EL-1 lamacoid is the PRIMARY
# source and the EL-0 manufacturer nameplate is SECONDARY. The two are parsed
# by separate functions reading separate fields ('label_text' vs
# 'nameplate_text') so a nameplate reading can never silently outrank a
# lamacoid one, and so a panel whose lamacoid quotes its upstream feeder
# ('THROUGH TRANS. "T1" 112.5 K.V.A.') never inherits that rating.

# Self-cooled / forced-air cooling-class codes. A dual-rated transformer prints
# the forced-air rating alongside the base one; only the base self-cooled
# rating at the lowest temperature rise is the asset's Power Rating.
_NAMEPLATE_FORCED_AIR_RE = re.compile(r"\b(?:AFN|FA|ONAF|OFAF|ODAF)\b")
# The impedance base line ('SUR/ON 1500 KVA') is the plate's own declaration of
# which rating everything else is referenced to -- the most reliable signal.
# The `(?<![\d.])` lookbehind and the optional decimal group are BOTH required.
# Without them `\b(\d{1,6})` word-boundaries onto the fraction of a decimal
# rating -- '112.5 KVA' matches at the boundary between '.' and '5' and yields
# 5, which is a *valid* pair that normalize_power_rating_pair passes through
# unchanged, so a 112.5 kVA transformer would reach Planon as 5 kVA. 37.5 /
# 75.0 / 112.5 / 167.5 are standard dry-type sizes, so this is the common case,
# not an edge case. Mirrors validators_shared._POWER_RATING_NUM_FIRST_RE.
_NAMEPLATE_BASE_KVA_RE = re.compile(
    r"\b(?:SUR\s*/?\s*ON|BASE|BASED\s+ON)\b[^\d\n]{0,20}(?<![\d.])(\d{1,6}(?:\.\d+)?)\s*K\.?\s*V\.?\s*A\.?\b"
)
_NAMEPLATE_ANY_KVA_RE = re.compile(r"(?<![\d.])(\d{1,6}(?:\.\d+)?)\s*K\.?\s*V\.?\s*A\.?\b")


def _nameplate_kva_int(text):
    """Truncate a printed kVA figure toward zero, or '' if unusable.

    ``normalize_power_rating_pair`` rejects decimals outright, so a fractional
    plate size must become an integer here or the value is silently dropped
    downstream. Truncation (not rounding) matches the stored precedent:
    ``sdi_dataset_EL`` holds ``112`` for a 112.5 kVA unit, while
    ``electrical_building_schema`` keeps the exact ``112.5``.
    """
    try:
        value = int(float(str(text).strip()))
    except (TypeError, ValueError):
        return ""
    return str(value) if value > 0 else ""
# '12470 H.T. H.V. 95 KV BIL' -- the winding designator follows the value
# (ABB bilingual layout).
_NAMEPLATE_HV_RE = re.compile(r"\b(\d{3,6})\s*(?:H\.?\s*T\.?[\s/]*)?H\.?\s*V\.?(?![A-Z])")
# '600Y/346.4 B.T. L.V. 10 KV BIL'
_NAMEPLATE_LV_RE = re.compile(
    r"\b(\d{2,5}\s*Y?\s*/\s*\d{1,5}(?:\.\d+)?|\d{2,5}\s*Y?)\s*(?:B\.?\s*T\.?[\s/]*)?L\.?\s*V\.?(?![A-Z])"
)
# 'PRI. 600 V' / 'SEC. 208Y/120 V' -- the designator PRECEDES the value
# (Delta/GE-style layout; TX-11 production regression). The window between the
# marker and the digits admits no letters or digits, so the French plate label
# 'TRANSFORMATEUR TYPE SEC' cannot reach across 'CAT. #' into the catalog
# number, and `(?<![A-Z0-9])` keeps digits embedded in codes like 'DA3075V'
# from ever matching.
_NAMEPLATE_PRI_RE = re.compile(
    r"\bPRI(?:\.|MARY)?\b[^0-9A-Z]{0,12}(?<![A-Z0-9])(\d{3,6})\s*V?\b"
)
_NAMEPLATE_SEC_RE = re.compile(
    r"\bSEC(?:\.|ONDARY)?\b[^0-9A-Z]{0,12}(?<![A-Z0-9])"
    r"(\d{2,5}\s*Y?\s*/\s*\d{1,5}(?:\.\d+)?|\d{2,5}\s*Y?)\s*V?\b"
)
# '75 ANN' under a kVA column header -- a number immediately followed by a
# self-cooled cooling-class code is the base rating even when the unit is a
# column header rather than a suffix. 'TYPE ANN' (ABB) has no digits before
# ANN and cannot match.
_NAMEPLATE_KVA_CLASS_RE = re.compile(
    r"(?<![\d.])(\d{1,6}(?:\.\d+)?)\s*(?:K\.?\s*V\.?\s*A\.?\s*)?\b(?:ANN|ONAN|OA)\b"
)
# UBC-canonical phase-to-neutral nominals. A wye secondary prints the exact
# line/sqrt(3) value (600/sqrt3 = 346.4) but every stored EL row uses the
# integer nominal, so plain rounding (which would give 346) is wrong.
_WYE_PHASE_NOMINAL = {
    "600": "347", "208": "120", "480": "277",
    "240": "139", "415": "240", "400": "231", "380": "220",
}


def _nameplate_secondary_nominal(text):
    """Normalize a printed secondary voltage to its UBC-canonical nominal."""
    raw = re.sub(r"\s+", "", str(text or "").upper())
    m = re.match(r"^(\d{2,5})(Y?)(?:/(\d{1,5}(?:\.\d+)?))?$", raw)
    if not m:
        return ""
    line, wye, phase = m.group(1), m.group(2), m.group(3)
    if phase is None:
        return line
    # A printed INTEGER phase voltage is authoritative -- never substitute a
    # wye nominal for it. Doing so rewrites correctly-printed non-wye
    # secondaries: split-phase '240/120' would become '240/139' (240/sqrt(3)),
    # a value printed on no plate and rejected wholesale downstream
    # (normalize_volts('600-240/139') -> ''), losing a usable reading.
    if "." not in phase:
        return f"{line}{wye}/{phase}"
    # Decimal phase: the plate is printing line/sqrt(3) exactly (600/sqrt(3) =
    # 346.4). Map it to the UBC-canonical integer nominal, since every stored
    # EL row uses the nominal and the shared normalizers reject decimals.
    nominal = _WYE_PHASE_NOMINAL.get(line)
    if nominal is None:
        try:
            nominal = str(int(round(float(phase))))
        except (TypeError, ValueError):
            return line
    return f"{line}{wye}/{nominal}"


def legacy_nameplate_specs(text):
    """Read technical specs from a manufacturer nameplate transcription.

    Returns ``kva``/``kva_uom`` (base self-cooled rating only) and a composed
    ``voltage``/``voltage_uom`` primary-secondary pair such as
    ``12470-600Y/347``, matching the pair shape already stored for
    transformers in ``sdi_dataset_EL`` (e.g. ``600-208Y/120``).

    Deliberately emits NO amperage: a transformer plate prints per-tap,
    per-winding currents (five HV taps plus one LV value here) that the
    single-column ``Amperage Rating`` cannot represent, and 24 of 26
    transformer rows in ``sdi_dataset_EL`` are blank. See the amperage
    rejection in ``legacy_ratings_from_label``.

    NOTE (unconfirmed with Planon): a medium-voltage primary such as ``12470``
    is a value class this system has never stored -- no row >= 1000 V exists in
    ``sdi_dataset_EL`` or ``electrical_building_schema``. The legacy path can
    hold it because it bypasses ``normalize_volts`` (whose whitelist tops out
    at 600), but the standard flow would strip it. To store the secondary only,
    return ``secondary`` instead of the composed pair below.
    """
    up = str(text or "").upper()
    out = dict(kva="", kva_uom="", primary_volts="", secondary_volts="",
               voltage="", voltage_uom="", ampere="", ampere_uom="")
    if not up.strip():
        return out

    # Strip thousands separators first: normalize_power_rating_pair silently
    # truncates '1,500' to '500'.
    flat = re.sub(r"(?<=\d),(?=\d{3}\b)", "", up)

    m = _NAMEPLATE_BASE_KVA_RE.search(flat)
    kva = _nameplate_kva_int(m.group(1)) if m else ""
    if not kva:
        # No declared base: take the smallest explicit kVA from lines that do
        # not name a forced-air class. Forced-air and higher temperature-rise
        # ratings are always larger than the base self-cooled rating, so the
        # minimum is the base. Forced-air-only lines are a last resort rather
        # than a hard exclusion, so a plate that names nothing else still
        # yields a rating.
        self_cooled, forced_air = [], []
        for line in flat.splitlines():
            values = [km.group(1) for km in _NAMEPLATE_ANY_KVA_RE.finditer(line)]
            if not values:
                continue
            if _NAMEPLATE_FORCED_AIR_RE.search(line):
                forced_air.extend(values)
            else:
                self_cooled.extend(values)
        pool = self_cooled or forced_air
        if pool:
            try:
                kva = _nameplate_kva_int(min(pool, key=lambda v: float(v)))
            except (TypeError, ValueError):
                kva = ""
    if not kva:
        # Unit-as-column-header layout ('kVA' header, then '75 ANN'): the
        # number-then-class association is the only safe signal -- a bare
        # 'KVA header then number' rule would read the ABB temp-rise rows
        # ('KVA\n115°C 1500 2000') as a 115 kVA rating.
        m = _NAMEPLATE_KVA_CLASS_RE.search(flat)
        if m:
            kva = _nameplate_kva_int(m.group(1))
    if kva:
        out["kva"], out["kva_uom"] = kva, "KVA"

    hv = _NAMEPLATE_HV_RE.search(flat)
    if not hv:
        hv = _NAMEPLATE_PRI_RE.search(flat)
    if hv:
        out["primary_volts"] = hv.group(1)
    lv = _NAMEPLATE_LV_RE.search(flat)
    if not lv:
        lv = _NAMEPLATE_SEC_RE.search(flat)
    if lv:
        out["secondary_volts"] = _nameplate_secondary_nominal(lv.group(1))

    primary, secondary = out["primary_volts"], out["secondary_volts"]
    if primary and secondary:
        out["voltage"], out["voltage_uom"] = f"{primary}-{secondary}", "VLT"
    elif secondary:
        out["voltage"], out["voltage_uom"] = secondary, "VLT"
    elif primary:
        out["voltage"], out["voltage_uom"] = primary, "VLT"
    return out


def is_legacy_transformer(tag, equipment_id=""):
    """True when the legacy identity denotes a transformer.

    Power Rating is a transformer-only field, so this gates every nameplate
    contribution. Accepts the drifted ``T-X-MAIN`` spelling alongside
    ``TX-MAIN`` because that variant exists in production JSON today.
    """
    for value in (equipment_id, tag):
        up = str(value or "").strip().upper()
        if not up:
            continue
        if re.match(r"^T[-.\s]?X\b", up) or re.match(r"^T[-.\s]?X[-.\s]", up):
            return True
        # 'T1' / 'T-1' unit naming (production QR 0000186131, building 641).
        # The dictionary's own 'T-|EL' entry classifies the T- prefix as
        # Interior Distribution Transformers / 'Transformer'. Anchored to a
        # pure digit suffix so TSBC-ish words, 'T1A' codes and panel idents
        # never match.
        if re.match(r"^T[-.\s]?\d+$", up):
            return True
        _, etype = derive_legacy_equipment_metadata(up)
        if etype == "Transformer":
            return True
    return False


# Shared by normalize_legacy_supply_from and parse_legacy_identity_text so the
# MAIN/DIST-CTRE phrase rules and the '#'/'NO.' sequence-number rule stay in
# lockstep between the fed-from parser and the identity parser below.
_MAIN_DIST_CTRE_RE = re.compile(r"\bMAIN\s+DIST(?:RIBUTION)?\.?\s+C(?:EN)?TRE\.?\b")
_DIST_CTRE_RE = re.compile(r"\bDIST(?:RIBUTION)?\.?\s+C(?:EN)?TRE\.?\b")
_IDENT_SEQ_RE = re.compile(r"(?:#|\bNO\.?)\s*(\d+)")
# The `|TX` alternative makes this module's own composed output --
# "MDC via TX T1" -- parse back losslessly (see the round-trip head below).
_VIA_TX_RE = re.compile(
    r"\b(?:THROUGH|THRU|VIA)\s+(?:TRANS(?:FORMER)?S?\.?|TX)\s*[\"']?\s*([A-Z]{0,2}\d+[A-Z0-9]*)\s*[\"']?"
)
# Trailing 'FED FROM ...'/'FED BY ...'/'SUPPLIED FROM ...' clause, or a
# ratings clause (voltage/amperage/phase/wire), on a one-line label --
# truncated in parse_legacy_identity_text before any phrase/ident matching
# so a printed feeder identity or rating text never leaks into (or hijacks)
# the asset's own identity (review round-1 Important-1).
_IDENT_TAIL_RE = re.compile(
    r"\b(?:FED\s+FROM|FED\s+BY|SUPPLIED\s+FROM)\b"
    r"|\b\d{2,3}\s*/\s*\d{2,3}\s*V(?:OLTS?)?\b"
    r"|\b\d{3}\s*V(?:OLTS?)?\b"
    r"|\b\d{2,4}\s*A(?:MPS?)?\b"
    r"|\b\d\s*PH(?:ASE)?\b|\b\d\s*Ø"
    r"|\b\d\s*W(?:IRE)?\b"
)


def normalize_legacy_supply_from(text):
    """Parse legacy supply-from text to normalized fed-from identifiers.

    Returns dict with keys:
    - fed_from_id: 'MDC', 'DCC', 'DCC-1' (hyphen form, user rule 2026-07-29;
      the pre-existing 'DCC #1' form still parses and normalizes forward),
      'SD-N4', 'GENERATOR', or ''
    - via: 'ATS', 'TX <id>' (transformer), or ''
    - source_room: room number exactly as printed, leading zeros preserved
      ('RM. 0060' -> '0060'), or ''
    - raw: input text trimmed

    Rule order matters: MAIN variants (MDC) must be checked before non-MAIN (DCC)
    because MAIN is the discriminator. Also handles the already-normalized
    round trip (this module's own composed 'MDC via TX T1' / 'DCC-1' output
    parses back losslessly) and bare MDC/DCC tokens printed without the
    MAIN/DIST/CTRE phrase.
    """
    raw = str(text or "").strip()
    up = raw.upper()
    out = dict(fed_from_id="", via="", source_room="", raw=raw)
    if not up:
        return out

    if re.search(r"\bVIA\s+ATS\b", up):
        out["via"] = "ATS"

    if not out["via"]:
        m = _VIA_TX_RE.search(up)
        if m:
            out["via"] = f"TX {m.group(1)}"

    # Track the VIA-UPS span (any whitespace run) so the primary-feeder UPS
    # check below can exclude it without a fixed-width lookbehind.
    _m_via_ups = re.search(r"\bVIA\s+U\.?P\.?S\b\.?(?!/)", up)
    if not out["via"] and _m_via_ups:
        out["via"] = "UPS"

    m = re.search(r"\bRM\.?\s*(\d+)\b", up)
    if m:
        out["source_room"] = m.group(1)

    unit = ""
    m = re.search(r"#\s*(\d+)", up)
    if m:
        unit = f"-{m.group(1)}"

    # Already-normalized round trip: values this module itself composed
    # ('MDC via TX T1', 'DCC-1') parse back losslessly, and the pre-2026-07-29
    # '#'-form ('DCC #1') still parses and normalizes forward to the hyphen
    # form. The VIA ATS / _VIA_TX_RE scans above already ran on `up`, so
    # 'MDC via TX T1' keeps its via here. The negative lookahead (rather than
    # a plain \b) stops a bare 'MDC'/'DCC' match from truncating a hyphenated
    # composed X-tag like 'DCC-2XXD1' down to its bare prefix (review round-1
    # Critical) -- the unit alternative only consumes '-<digits>' when the
    # digits end the token, so 'DCC-1' matches but 'DCC-2XXD1' falls through
    # to the generic hyphenated-token fallback.
    m = re.match(r"^(MDC|DCC)(?:\s*#\s*(\d+)|-(\d+))?(?![-\w])", up)
    if m:
        unit_digits = m.group(2) or m.group(3)
        out["fed_from_id"] = m.group(1) + (f"-{unit_digits}" if unit_digits else "")
        return out

    # Order matters: MAIN variants first (the word MAIN is the MDC/DCC discriminator).
    if _MAIN_DIST_CTRE_RE.search(up):
        out["fed_from_id"] = ("MDC" + unit).strip()
    elif _DIST_CTRE_RE.search(up):
        out["fed_from_id"] = ("DCC" + unit).strip()
    elif re.search(r"\bGENERATOR\b", up):
        out["fed_from_id"] = "GENERATOR"
    else:
        # Bare abbreviation printed directly (no MAIN/DIST/CTRE phrase), e.g.
        # 'FED FROM MDC IN MAIN ELEC. RM.' -- 'MAIN ELEC.' never contains the
        # literal token 'MDC', so there is no conflict with the phrase checks
        # above. The (?!-) guard stops this from matching the bare prefix of
        # a hyphenated composed tag like 'FED FROM DCC-2XXD1' (review
        # round-1 Critical) -- that case falls through to the generic
        # hyphenated-token fallback below instead.
        m = re.search(r"\b(MDC|DCC)\b(?!-)(?:\s*#\s*(\d+))?", up)
        if m:
            out["fed_from_id"] = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
            return out
        m = re.search(r"\b([A-Z]{2,4}-[A-Z0-9]+)\b", up)
        if m:
            out["fed_from_id"] = m.group(1)
            return out
        # UPS as the PRIMARY upstream feeder (2026-07-30) -- only after every
        # more-specific identity above failed, so 'FED FROM DCC-1 VIA UPS'
        # keeps DCC-1 (UPS lands in `via` instead; review finding). The
        # \b-before-optional-final-period keeps 'UPS1' (a panel NAMED UPS1)
        # from matching and (?!/) keeps compound idents like 'UPS/CM' out.
        # The VIA-UPS span (matched above, any whitespace run) is excised
        # first so a pure routing qualifier ('... VIA UPS' with no primary
        # feeder found) never doubles as the feeder. 'MAIN ELECTRICAL' never
        # collides with the MAIN-DIST-CTRE phrase check above (that requires
        # DIST + CTRE).
        up_minus_via = (
            up[:_m_via_ups.start()] + up[_m_via_ups.end():] if _m_via_ups else up
        )
        if re.search(r"\bU\.?P\.?S\b\.?(?!/)", up_minus_via):
            out["fed_from_id"] = "UPS"
    return out


def compose_legacy_supply_from(fed):
    """Human-readable Supply From per the photo-table convention: 'MDC via ATS',
    'MDC via TX T1', 'DCC-1', 'GENERATOR', or ''. Supply From and Fed From
    Equipment ID carry the same feeder identifier in the same hyphenated form
    (user rule, 2026-07-29); Supply From may additionally carry a ' via ...'
    qualifier when the plate prints one."""
    fid = str((fed or {}).get("fed_from_id", "")).strip()
    via = str((fed or {}).get("via", "")).strip()
    if not fid:
        return ""
    return f"{fid} via {via}" if via else fid


def parse_legacy_identity_text(text):
    """Parse a printed legacy identity line ('DIST. CTRE. #1', 'PANEL U', 'MCC2',
    'MCC NO. 2', 'MAIN DIST. CTRE.') into a normalized identity dict, or None.

    Returns {equipment_id, prefix, ident, system_hint, sequence}. equipment_id
    follows the fed-from identifier convention ('MDC', 'DCC-1', 'PNL-U',
    'MCC2') so Fed From Equipment ID cross-references connect in the SLD.
    Numbered MDC/DCC units use the hyphen form ('DCC-1', user rule
    2026-07-29); the pre-existing '#'-form ('DCC #1') still parses and
    normalizes forward.
    Standard-shaped tags (CDP-6-N-1-L-1, 2N1.5P1) intentionally return None --
    they belong to the standard parser even inside a Legacy building.

    A trailing 'FED FROM ...'/ratings clause on a one-line label ('PANEL U
    120/208V - 3PH - 4W FED FROM DIST. CTRE. #1') is truncated before any
    phrase/ident matching so the feeder's own identity never hijacks the
    identity of the asset being labeled (review round-1 Important-1).
    """
    raw = str(text or "").strip().upper()
    if not raw:
        return None
    m_tail = _IDENT_TAIL_RE.search(raw)
    if m_tail:
        raw = raw[:m_tail.start()].strip()
    if not raw:
        return None
    seq = ""
    m = _IDENT_SEQ_RE.search(raw)
    if m:
        seq = m.group(1)
    if _MAIN_DIST_CTRE_RE.search(raw):
        eq = "MDC" + (f"-{seq}" if seq else "")
        return dict(equipment_id=eq, prefix="MDC", ident="", system_hint="", sequence=seq)
    if _DIST_CTRE_RE.search(raw):
        eq = "DCC" + (f"-{seq}" if seq else "")
        return dict(equipment_id=eq, prefix="DCC", ident="", system_hint="", sequence=seq)
    # Bare 'MDC' / 'DCC' / 'DCC-<n>' / legacy 'DCC #<n>' token -- this
    # module's own composed fed-from output, or a plate that prints the
    # abbreviation instead of the phrase -- must round-trip to the same
    # canonical hyphen shape ('DCC-1'), not the glued 'DCC1' form the
    # ident-delegation path below would otherwise produce (review round-1
    # Important-2). The '$' anchor keeps composed X-tags ('DCC-2XXD1') out:
    # their trailing letters fail the digits-to-end requirement.
    m_bare = re.match(r"^(MDC|DCC)\s*(?:[#-]\s*(\d+))?$", raw)
    if m_bare:
        prefix = m_bare.group(1)
        bare_seq = m_bare.group(2) or ""
        eq = prefix + (f"-{bare_seq}" if bare_seq else "")
        return dict(equipment_id=eq, prefix=prefix, ident="", system_hint="", sequence=bare_seq)
    ident_source = raw
    if seq:
        # 'MCC NO. 2' -> try the glued form 'MCC2' through parse_legacy_ident
        ident_source = _IDENT_SEQ_RE.sub("", raw).strip() + seq
    parsed = parse_legacy_ident(ident_source)
    if parsed is None and ident_source != raw:
        parsed = parse_legacy_ident(raw)
    if parsed:
        return dict(
            equipment_id=parsed["equipment_id"], prefix=parsed["prefix"],
            ident=parsed["ident"], system_hint=parsed["system_hint"],
            sequence=parsed["unit_number"],
        )
    return None


# Legacy-only map: shared ELECTRICAL_EQUIPMENT_TYPE_MAP entries plus MDC/DCC.
# Deliberately duplicated (not imported) so the shared standard-flow file
# stays untouched per the no-standard-changes constraint.
LEGACY_EQUIPMENT_TYPE_MAP = {
    "MDP": "Main Distribution Panel",
    "CDP": "Central Distribution Panel",
    "SPL": "Splitter",
    "MCC": "Motor Control Center",
    "PNL": "Panel",
    "SWBD": "Switchboard",
    "ATS": "Automatic Transfer Switch",
    "TX": "Transformer",
    "MDC": "Main Distribution Centre",
    "DCC": "Distribution Centre",
}

_VOLTAGE_TO_CODE = {"208/120": "2", "600/347": "6", "600": "6", "208": "2"}

_EMERGENCY_FED = re.compile(r"^SD-E|^GENERATOR$")
_NORMAL_FED = re.compile(r"^SD-N")


def derive_legacy_equipment_metadata(equipment_id):
    up = str(equipment_id or "").strip().upper()
    if not up:
        return "", ""
    for code in sorted(LEGACY_EQUIPMENT_TYPE_MAP, key=len, reverse=True):
        if up.startswith(code):
            return "", LEGACY_EQUIPMENT_TYPE_MAP[code]
    return "", ""


def corroborated_power_type(system_hint, fed_from):
    hint = str(system_hint or "").strip().upper()
    fid = str((fed_from or {}).get("fed_from_id", "")).strip().upper()
    via = str((fed_from or {}).get("via", "")).strip().upper()
    if hint == "N" and _NORMAL_FED.search(fid):
        return "N"
    if hint == "E" and (_EMERGENCY_FED.search(fid) or via == "ATS"):
        return "E"
    return ""


def compose_x_tag(prefix, voltage, system, sequence, type_code="X"):
    v = _VOLTAGE_TO_CODE.get(str(voltage or "").strip(), "X")
    s = str(system or "").strip().upper() or "X"
    t = str(type_code or "").strip().upper() or "X"
    q = str(sequence or "").strip() or "X"
    return f"{prefix}-{v}{s}X{t}{q}"


# Prefixes whose printed name verifies the Distribution type segment (D) in
# the X-composed decode structure, and whose Description word is "Distribution".
_X_TAG_TYPE_D_PREFIXES = ("MDC", "DCC")
# Prefixes the X-composed standard structure applies to (compose_x_tag). Since
# 2026-07-30 the X-tag is a dictionary-decode/display structure only — the
# stored UBC Asset Tag is always the equipment identity ('DCC-1', 'PNL-U').
_X_TAG_PREFIXES = ("MDC", "DCC", "MCC", "SWBD", "SPL", "CDP", "MDP")


def legacy_structured_from_raw(raw):
    """Compose review-ready structured_data from RAW legacy model output.

    `raw` keys (all optional, unnormalized strings): 'Label Text',
    'Nameplate Text', 'UBC Asset Tag', 'Supply From', 'Volts', 'Ampere'.
    Never fabricates a PNL- prefix; unparseable identities stay verbatim.
    Location is always blank (field-gap rule 6). The Power Rating pair is
    transformer-only: blank unless is_legacy_transformer() matches AND the
    EL-0 nameplate transcription in 'Nameplate Text' carries a rating.
    A non-dict `raw` is treated as empty (review round-1 Minor-3).
    """
    if not isinstance(raw, dict):
        raw = {}
    label_text = str(raw.get("Label Text") or "").strip()
    # EL-0 manufacturer plate, kept in its own field so the lamacoid parse
    # above can never be contaminated by nameplate text (and vice versa).
    nameplate_text = str(raw.get("Nameplate Text") or "").strip()
    raw_tag = str(raw.get("UBC Asset Tag") or "").strip()
    raw_supply = str(raw.get("Supply From") or "").strip()

    identity = parse_legacy_identity_text(raw_tag)
    if identity is None and label_text:
        identity = parse_legacy_identity_text(label_text.splitlines()[0])

    # Only the voltage/ampere value itself is used below -- the UoM siblings
    # from this fallback lookup are intentionally not emitted (canonical
    # UoM fields are filled by apply_legacy_rules at approval; review
    # round-1 Minor-1).
    ratings = legacy_ratings_from_label(label_text)
    if not ratings["voltage"]:
        ratings["voltage"] = legacy_ratings_from_label(str(raw.get("Volts") or ""))["voltage"]
    if not ratings["ampere"]:
        ratings["ampere"] = legacy_ratings_from_label(str(raw.get("Ampere") or ""))["ampere"]

    fed = normalize_legacy_supply_from(raw_supply)
    supply_from = compose_legacy_supply_from(fed) or raw_supply

    equipment_id = identity["equipment_id"] if identity else ""
    if not equipment_id and is_legacy_transformer(raw_tag):
        # The legacy ident grammar models PANEL identities, so a transformer
        # tag such as 'TX-MAIN' does not parse -- but it plainly is the
        # equipment's identity. Leaving it blank let a JSON->DB sync overwrite
        # a correct stored Equipment ID with '', and Equipment ID / Equipment
        # Type are both in the required field set for the transformer asset
        # groups. Non-transformer plates that fail to parse still stay blank
        # rather than fabricating an identity.
        equipment_id = raw_tag.strip().upper()
    _, equipment_type = derive_legacy_equipment_metadata(equipment_id)
    if not equipment_type and is_legacy_transformer(raw_tag, equipment_id):
        # 'T1'-style unit names carry no map prefix ('TX') to derive from,
        # but the transformer gate has already proven the identity class.
        equipment_type = "Transformer"
    power_type = corroborated_power_type(identity["system_hint"], fed) if identity else ""

    # UBC Asset Tag = the equipment identity ('DCC-1', 'PNL-U', 'MCC2'), user
    # rule 2026-07-30. The X-composed standard structure ('DCC-2XXD1', via
    # compose_x_tag above) remains the dictionary-decode structure — the
    # voltage/system/type segment codes still map through the standard
    # label_schema — but it is never stored as the tag.
    tag = identity["equipment_id"] if identity else raw_tag

    # Nameplate contribution: transformer-only, and strictly SECONDARY to the
    # lamacoid. Voltage is filled only where the lamacoid produced nothing.
    nameplate = legacy_nameplate_specs(nameplate_text)
    power_rating = power_rating_uom = ""
    if is_legacy_transformer(tag, equipment_id):
        power_rating, power_rating_uom = nameplate["kva"], nameplate["kva_uom"]
        if not ratings["voltage"] and nameplate["voltage"]:
            ratings["voltage"] = nameplate["voltage"]
            ratings["voltage_uom"] = nameplate["voltage_uom"]

    branch_panel = identity["ident"] if identity and identity["prefix"] == "PNL" else ""
    # Description = "<type word> - <Equipment ID>" (user rule, 2026-07-29):
    # the identifier is the Equipment ID ('DCC-1'), not the composed X-tag,
    # and a DCC/MDC is described as a "Distribution" (the dictionary type-D
    # word its printed name verifies), e.g. "Distribution - DCC-1".
    # Never assert a type we didn't derive (review round-1 Important-3): a
    # blank equipment_type must never be silently fabricated as "Panel".
    desc_word = (
        "Distribution"
        if identity and identity["prefix"] in _X_TAG_TYPE_D_PREFIXES
        else equipment_type
    )
    desc_ident = equipment_id or tag
    description = f"{desc_word} - {desc_ident}" if (desc_word and desc_ident) else ""

    return {
        "UBC Asset Tag": tag,
        "Branch Panel": branch_panel,
        "Ampere": ratings["ampere"],
        "Power Rating": power_rating,
        "Power Rating (UoM)": power_rating_uom,
        "Supply From": supply_from,
        "Volts": ratings["voltage"],
        "Location": "",
        "Description": description,
        "Equipment ID": equipment_id,
        "Equipment Type": equipment_type,
        "Power Type": power_type,
        "Fed From Equipment ID": fed["fed_from_id"],
        "label_text": label_text,
        "nameplate_text": nameplate_text,
    }


def snapshot_reviewer_location(data):
    """Return the current ``Location`` value (stripped) for a later restore.

    Half of the Legacy-only guard for invariant 6 -- see
    ``restore_reviewer_location`` for why it exists. Returns '' for a blank/
    missing Location or a non-dict input, which makes the paired restore a no-op.
    """
    if not isinstance(data, dict):
        return ""
    return str(data.get("Location") or "").strip()


def restore_reviewer_location(data, snapshot):
    """Put back a reviewer-entered ``Location`` that the tag-dictionary pass blanked.

    Invariant 6 (never erase a human override), Legacy path only. The EL review
    app's ``_apply_tag_dictionary_first`` calls
    ``_clear_legacy_tag_derived_location``, which blanks ``Location`` whenever it
    equals the value the STANDARD tag parser derives from the tag. For a
    digit-ending legacy ident that match is a coincidence, not evidence of an
    auto-derivation: ``PNL-EM3`` parses as volt 'E' / system 'M' / location '3'
    and derives "Level 3", so a reviewer who genuinely typed "Level 3" would have
    it silently erased -- and the erasure persists to the review JSON and to
    ``sdi_dataset_EL`` (Planon). Legacy rule 6 says Location is capture/review
    owned and must never be tag-derived either way, so for Legacy buildings the
    clear is always wrong.

    Restores only when the snapshot was non-blank AND the value is now blank, so
    a deliberate reviewer clear (blank in, blank out) and any non-blank rewrite
    are both left alone. Returns True when a restore happened.
    """
    if not isinstance(data, dict) or not snapshot:
        return False
    if str(data.get("Location") or "").strip():
        return False
    data["Location"] = snapshot
    return True


def _set_if_blank(data, key, value):
    if value and not str(data.get(key) or "").strip():
        data[key] = value
        return True
    return False


def apply_legacy_rules(data):
    """Fill legacy-derivable fields in-place; blanks only, never overrides.

    Field-gap rules 1-8 (docs/superpowers/plans/2026-07-28-el-legacy-flow-field-gaps.md).
    """
    tag = str(data.get("UBC Asset Tag") or "").strip()
    parsed = parse_legacy_ident(tag)
    changed = False

    if parsed:
        changed |= _set_if_blank(data, "Equipment ID", parsed["equipment_id"])
        if parsed["prefix"] == "PNL":
            changed |= _set_if_blank(data, "Branch Panel", parsed["ident"])
        _, etype = derive_legacy_equipment_metadata(parsed["equipment_id"])
        changed |= _set_if_blank(data, "Equipment Type", etype)

    # "label_text" is the EL-1 lamacoid transcription written by the legacy
    # extraction step; every production legacy JSON carries it. It falls back
    # to `tag` (the UBC Asset Tag) for callers that supply neither, which keeps
    # the ratings fills below (Volts/Ampere/Voltage Rating/Amperage Rating)
    # harmless on pre-legacy payloads. See "EL Legacy Flow Rules" in
    # Markdowns_documentation/rules/review_apps.rules.md.
    ratings = legacy_ratings_from_label(str(data.get("label_text") or tag))
    volts_locked = str(data.get("volts_manual_override") or "").strip() == "1"
    if not volts_locked:
        changed |= _set_if_blank(data, "Volts", ratings["voltage"])
        changed |= _set_if_blank(data, "Voltage Rating", ratings["voltage"])
        changed |= _set_if_blank(data, "Voltage Rating (UoM)", ratings["voltage_uom"])
    changed |= _set_if_blank(data, "Ampere", ratings["ampere"])
    changed |= _set_if_blank(data, "Amperage Rating", ratings["ampere"])
    changed |= _set_if_blank(data, "Amperage Rating (UoM)", ratings["ampere_uom"])

    # EL-0 manufacturer nameplate: transformer-only, and strictly secondary to
    # the lamacoid above -- _set_if_blank means anything the lamacoid or a
    # reviewer already supplied wins (invariant 6). Read from its own
    # "nameplate_text" key rather than "label_text", so a panel whose lamacoid
    # quotes its upstream feeder ('THROUGH TRANS. "T1" 112.5 K.V.A.') cannot
    # inherit that transformer's rating. No amperage is contributed.
    if is_legacy_transformer(tag, (parsed or {}).get("equipment_id", "")):
        nameplate = legacy_nameplate_specs(str(data.get("nameplate_text") or ""))
        if not volts_locked:
            changed |= _set_if_blank(data, "Volts", nameplate["voltage"])
            changed |= _set_if_blank(data, "Voltage Rating", nameplate["voltage"])
            changed |= _set_if_blank(data, "Voltage Rating (UoM)", nameplate["voltage_uom"])
        changed |= _set_if_blank(data, "Power Rating", nameplate["kva"])
        changed |= _set_if_blank(data, "Power Rating (UoM)", nameplate["kva_uom"])

    fed = normalize_legacy_supply_from(str(data.get("Supply From") or ""))
    changed |= _set_if_blank(data, "Fed From Equipment ID", fed["fed_from_id"])

    if parsed:
        power = corroborated_power_type(parsed["system_hint"], fed)
        changed |= _set_if_blank(data, "Power Type", power)
    # Rule 6: Location intentionally untouched. Rule 9: Fed From Amperage untouched.
    return changed


class _LegacyDictShim:
    """Wraps the SLD dictionary module for Legacy buildings: legacy panel
    idents (PANEL U, MCC2, ATS, ...) are accepted/normalized via
    parse_legacy_ident; everything else delegates to the wrapped module
    unchanged. Never installed for Standard buildings (see gate in
    extract_electrical_schema.py main())."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self.label_schema = wrapped.label_schema

    def is_dictionary_id(self, value):
        if parse_legacy_ident(value) is not None:
            return True
        return self._wrapped.is_dictionary_id(value)

    def normalize_dictionary_id(self, value):
        parsed = parse_legacy_ident(value)
        if parsed is not None:
            return parsed["equipment_id"]
        return self._wrapped.normalize_dictionary_id(value)


def legacy_dictionary_shim(dictionary_module):
    return _LegacyDictShim(dictionary_module)
