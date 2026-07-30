import re

ELECTRICAL_EQUIPMENT_TYPE_MAP = {
    "MDP": "Main Distribution Panel",
    "CDP": "Central Distribution Panel",
    "SPL": "Splitter",
    "MCC": "Motor Control Center",
    "PNL": "Panel",
    "SWBD": "Switchboard",
    "ATS": "Automatic Transfer Switch",
    "TX": "Transformer",
}

ELECTRICAL_POWER_TYPE_MAP = {
    "N": "Normal",
    "E": "Emergency",
    "S": "Standby",
    "ES": "Emergency & Standby",
    "NE": "Normal & Emergency",
    "NES": "Normal, Emergency, & Standby",
    "NS": "Normal & Standby",
}

_SYSTEM_CODE_RE = re.compile(r"NES|NE|NS|ES|N|E|S")

def _normalize_tag_text(tag_value):
    return str(tag_value or "").strip().upper()

def derive_electrical_equipment_type(tag_value):
    tag_upper = _normalize_tag_text(tag_value)
    if not tag_upper:
        return ""
    for code, name in sorted(ELECTRICAL_EQUIPMENT_TYPE_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if tag_upper.startswith(code):
            return name
    return ""

def derive_electrical_power_type(tag_value):
    tag_upper = _normalize_tag_text(tag_value)
    if not tag_upper:
        return ""

    parts = tag_upper.split("-")
    if len(parts) <= 1:
        return ""

    match = _SYSTEM_CODE_RE.search(parts[1])
    if not match:
        return ""

    system_code = match.group(0)
    return system_code if system_code in ELECTRICAL_POWER_TYPE_MAP else ""

def parse_electrical_equipment_metadata(tag_value):
    tag_upper = _normalize_tag_text(tag_value)
    if not tag_upper:
        return "", ""
    return derive_electrical_power_type(tag_upper), derive_electrical_equipment_type(tag_upper)
