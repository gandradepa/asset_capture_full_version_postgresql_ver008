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
		"MDC": "Main Distribution Centre"  # synonym of Main Distribution Panel; promoted from electrical.dictionary_old.py on explicit request 2026-07-27
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
