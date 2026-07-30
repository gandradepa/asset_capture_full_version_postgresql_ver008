#!/usr/bin/env python3
"""Inspect why each Category-A EL JSON is flagged 'stale' by the EL extractor's
``_existing_el_output_needs_rescore`` safety rule. Pure read-only.

Usage: python3 inspect_el_stale_jsons.py
"""
from __future__ import annotations
import glob
import json
import os
import re

QRS = [
    "0000184439", "0000184441", "0000184445",
    "0000184478", "0000184479", "0000184480",
    "0000184485", "0000184489", "0000184557",
    "0000184558", "0000184865",
]
JSON_DIR = "/home/developer/Output_jason_api"
CURRENT_RULE_VERSION = 15  # from API_interface_EL_ver00.py Config.EXTRACTION_RULE_VERSION

_NUM_FIRST = re.compile(r"(?<!\d)(\d{1,5})\s*(KVA|KW|VA)\b", re.IGNORECASE)
_UNIT_FIRST = re.compile(r"\b(KVA|KW|VA)\s*(\d{1,5})(?!\d)", re.IGNORECASE)


def normalize_power_rating_pair(value: str, uom: str = ""):
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
        m = _NUM_FIRST.search(text)
        if m:
            return m.group(1), m.group(2).upper()
        m = _UNIT_FIRST.search(text)
        if m:
            return m.group(2), m.group(1).upper()
    if (
        re.fullmatch(r"\d{1,5}", str(value or "").strip())
        and str(uom or "").strip().upper() in {"KVA", "KW", "VA"}
    ):
        return str(value or "").strip(), str(uom or "").strip().upper()
    return "", ""


def reasons_stale(payload):
    reasons = []
    try:
        rv = int(payload.get("el_extraction_rule_version", 0) or 0)
    except (TypeError, ValueError):
        rv = 0
    if rv < CURRENT_RULE_VERSION:
        reasons.append(f"rule_version={rv}<{CURRENT_RULE_VERSION}")

    structured = payload.get("structured_data") or {}
    conf = payload.get("confidence_scores") or {}
    if not isinstance(structured, dict):
        structured = {}
    if not isinstance(conf, dict):
        conf = {}

    legacy_struct = {"Fed", "Fed From"} & set(structured.keys())
    legacy_conf = {"Fed", "Fed From"} & set(conf.keys())
    if legacy_struct:
        reasons.append(f"legacy_struct_keys={sorted(legacy_struct)}")
    if legacy_conf:
        reasons.append(f"legacy_conf_keys={sorted(legacy_conf)}")

    if "Power Rating" not in structured:
        reasons.append("missing:Power Rating")
    if "Power Rating (UoM)" not in structured:
        reasons.append("missing:Power Rating (UoM)")

    pr = str(structured.get("Power Rating", "") or "").strip()
    pruom = str(structured.get("Power Rating (UoM)", "") or "").strip()
    npr, npruom = normalize_power_rating_pair(pr, pruom)
    if pr != npr:
        reasons.append(f"PR_value mismatch (stored={pr!r}, normalized={npr!r})")
    if pruom != npruom:
        reasons.append(f"PR_uom mismatch (stored={pruom!r}, normalized={npruom!r})")
    return reasons, rv


def main():
    print(f"Current EL rule version: {CURRENT_RULE_VERSION}\n")
    for qr in QRS:
        matches = sorted(glob.glob(os.path.join(JSON_DIR, f"{qr}_EL_*.json")))
        if not matches:
            print(f"{qr}: NO EL JSON FOUND")
            continue
        for path in matches:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                print(f"{qr}: error reading {path}: {e}")
                continue
            reasons, rv = reasons_stale(payload)
            if reasons:
                print(f"{qr}  {os.path.basename(path)}  STALE => {', '.join(reasons)}")
            else:
                print(f"{qr}  {os.path.basename(path)}  OK (rule_version={rv})")


if __name__ == "__main__":
    main()
