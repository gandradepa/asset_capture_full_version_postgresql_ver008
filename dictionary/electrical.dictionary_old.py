# WORKING COPY (per user direction, 2026-07-27): this file receives all electrical
# dictionary updates going forward. dictionary/electrical.dictionary.py is the intact
# production file consumed by the EL review app and extraction API — do NOT modify it
# unless explicitly requested; promote changes from this file only on explicit request.
#
# Placeholder convention (adopted 2026-07-27): when composing a standard tag from
# field-captured data, write "X" in any segment with no printed/verified value,
# e.g. MCC-6XXX2, PNL-2XXXX. "X" is NOT a decodable code — parsers must treat an
# X segment as unknown/no data, never look it up in the code maps below.
label_schema = {
    "panel": {
        "format": "<ABBR>-<VOLTAGE>-<SYSTEM>-<LOCATION>-<TYPE>-<SEQUENCE>",
        "order": ["abbr", "voltage", "system", "location", "type", "sequence"],
        "codes": {
            "abbr": {
                "MDP": "Main Distribution Panel",
                "CDP": "Central Distribution Panel",
                "SPL": "Splitter",
                "MCC": "Motor Control Centre",
		"PNL": "Panel",
		"ATS": "Automatic Transfer Switches",
		"SWBD": "Distribuition",
		"MDC": "Main Distribution Centre",  # synonym of Main Distribution Panel (adopted 2026-07-27); matches API Config.ABBREVIATIONS "MDC" prefix
		"DCC": "Distribution Centre"  # adopted 2026-07-28; printed variations map here: "DISTRIBUTION CENTRE", "DIST. CTRE.", "DIST CTRE", "DIST. CENTRE" (without "MAIN" — "MAIN DIST. CENTRE" stays MDC)
            },
            "voltage": {
                "6": "600/347V",
                "2": "208/120V"
            },
            "system": {
                "N": "Normal",
                "E": "Life Safety",
                "S": "Standby"
            },
            "location": {
                "0": "Level 0",
                "1": "Level 1",
                "2": "Level 2",
                # For higher levels, continue the pattern: "3": "Level 3", ...
                "R": "Roof"
            },
            "type": {
                "L": "Lighting",
                "P": "Power",
                "M": "Mechanical",
                "D": "Distribution",
		"T": "Tenant"
            },
            "sequence": "1, 2, 3, ..."
        },
        "example": "CDP-6-N-1-L-1"
    },
    "panel_legacy": {
        # Legacy panel identity labels (adopted 2026-07-27): field lamacoids that name
        # a panel by a short identity code instead of the standard segment tag, e.g.
        # "PNL D", "PNL U", "PNL EM", "PNL NPH", "PNL QQ", "PNL EM3", "PNL UPS/CM"
        # (some plates print the full word: "PANEL U"). Typical form is 1 or 2 letters
        # with an optional trailing number; up to 3 letters and slash-joined compound
        # identifiers also occur in the field.
        "format": "PNL <IDENT>",
        "order": ["abbr", "ident"],
        "codes": {
            "abbr": {
                "PNL": "Panel",
                "PANEL": "Panel"  # descriptor word as printed on some lamacoids; normalize to PNL
            },
            "ident": {
                "pattern": "^[A-Z]{1,3}[0-9]{0,2}(?:/[A-Z]{1,3}[0-9]{0,2})?$",
                "description": "Panel identity code: 1-2 letters (up to 3 seen in the field) with an optional trailing number; slash-joined compounds allowed. The identity code is a name, not decodable voltage/system/location/type segments.",
                "examples": ["D", "U", "EM", "NPH", "QQ", "EM3", "UPS/CM"]
            },
            "ident_normalization": {
                # Applied to the ident candidate BEFORE the pattern match
                # (adopted 2026-07-30, building 641 "PANEL U.P.S.1"/"PANEL USP1"):
                # dotted abbreviations lose their periods ('U.P.S.1' -> 'UPS1'),
                # and known plate typos map to the canonical letters.
                "strip_chars": ".",
                "equivalences": {
                    "USP": "UPS"  # letter transposition seen on the beige lamacoid; blue plate prints U.P.S.1
                }
            },
            "system_hints": {
                # Leading letters that SUGGEST the supply system; hints only — corroborate
                # (e.g. via the fed-from source) before writing a system value.
                "N": "Normal",
                "E": "Life Safety",
                "EM": "Life Safety",
                "UPS": "UPS supply"
            }
        },
        "example": "PNL U"
    },
    "transformer": {
        "format": "TX-<SYSTEM>-<LOCATION>-<SEQUENCE>",
        "order": ["prefix", "system", "location", "sequence"],
        "codes": {
            "prefix": { "TX": "Transformer" },
            "system": {
                "N": "Normal",
                "E": "Life Safety",
                "S": "Standby"
            },
            "location": {
                "0": "Level 0",
                "1": "Level 1",
                "2": "Level 2",
                # Extend as needed for higher levels
                "R": "Roof"
            },
            "sequence": "1, 2, 3, ..."
        },
        "example": "TX-N-0-1"
    }
}
