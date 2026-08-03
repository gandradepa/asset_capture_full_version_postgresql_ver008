#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to extract structured data from industrial asset photographs using
an advanced ensemble of local OCR (Tesseract) and multimodal OpenAI models.

Updates in this version (2026-03-02-highres-30):
- Added FORCE_REPROCESS flag to Config for forcing re-extraction of all images.
- Database ai_status bypass when FORCE_REPROCESS is True.
- Smart UI Override: clean overwrite instead of merging with existing JSON.
- Deterministic AI settings where supported: TEMPERATURE=0.0 and SEED=42.
- Version 30 (The Direct Mapper): Merged explicit JSON key-mapping (from V28) with table-geometry rules (from V29) to fix blank field JSON mapping errors.
"""

import os
import json
import base64
import re
import ast
import tempfile
import time
import logging
import platform
import shutil
import sqlite3
from datetime import datetime, UTC
from collections import defaultdict
from contextlib import closing
import db as qrdb  # backend-agnostic QR_codes DB layer (Phase C / C4)
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Any, Optional, Set

# Third-party libraries
try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np
import pytesseract
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from llm_strategy import (
    APIConnectionError,
    APIStatusError,
    AuthFailed,
    BadRequestError,
    QuotaExceeded,
    RateLimitError,
    STATUS_API_ERROR,
    STATUS_AUTH,
    STATUS_LOW_QUALITY,
    STATUS_NO_JSON,
    STATUS_PARTIAL,
    STATUS_QUOTA,
    STATUS_SKIPPED_EXISTS,
    STATUS_SUCCESS,
    append_manual_review,
    get_llm_model_plan,
    is_auth_error,
    is_quota_error,
    role_for_position,
    warn_legacy_env_vars,
)
try:
    from validators_shared import (
        normalize_year,
        normalize_manufacturer,
        normalize_serial,
        normalize_ubc_tag,
        normalize_diameter,
        normalize_model,
        completeness_score,
        looks_like_date_misread_serial,
    )
except ImportError:
    logging.warning("validators_shared module not found. Using local fallbacks for ME script.")

    def _to_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def normalize_year(v):
        text = _to_text(v).upper()
        if not text:
            return ""

        for token in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", text):
            try:
                y = int(token)
            except ValueError:
                continue
            if 1950 <= y <= 2026:
                return f"{y:04d}"

        mm_yy = re.search(r"(?:0?[1-9]|1[0-2])\s*[/\\-]\s*(\d{2})(?:\D|$)", text)
        if mm_yy:
            yy = int(mm_yy.group(1))
            if 0 <= yy <= 26:
                return f"20{yy:02d}"
            if 50 <= yy <= 99:
                return f"19{yy:02d}"
        return ""

    def normalize_manufacturer(v):
        text = re.sub(r"\s+", " ", _to_text(v)).strip().upper()
        if not text:
            return ""

        patterns = [
            (r"\bATLAS\s+C[O0]P[C0]O\b", "Atlas Copco"),
            (r"\bWILLIAMS(?:\s+FURNACE(?:\s+COMPANY)?)?\b", "Williams"),
            (r"\bROCKWELL(?:\s+MANUFACTURING(?:\s+CO\.?)?)?\b", "Rockwell"),
            (r"\bBELL\s*&?\s*GOSSETT\b", "Bell & Gossett"),
            (r"\bARMSTRONG\b", "Armstrong"),
            (r"\bTACO(?:\s+CANADA(?:\s+LTD\.?)?)?\b", "Taco"),
            (r"\bCASADEI\b", "Casadei"),
            (r"\bMAKITA\b", "Makita"),
            (r"\bWATTS\b", "Watts"),
            (r"\bWILKINS\b", "Wilkins"),
            (r"\bCONBRACO\b", "Conbraco"),
            (r"\bAPOLLO\b", "Apollo"),
        ]
        for pattern, canonical in patterns:
            if re.search(pattern, text):
                return canonical

        tokens = re.findall(r"[A-Z&]+", text)
        if 1 <= len(tokens) <= 4 and len(text) <= 40 and not re.search(r"\d", text):
            titled = " ".join(t.capitalize() for t in tokens)
            return titled.replace(" And ", " & ")
        return ""

    def normalize_serial(v):
        text = _to_text(v).upper()
        if not text:
            return ""
        text = re.sub(r"[^A-Z0-9\-_\/\.]", "", text)
        return text[:64]

    def looks_like_date_misread_serial(serial, year_hint=""):
        return False

    def normalize_ubc_tag(v):
        text = _to_text(v).upper()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text).strip()
        text = text.replace("N0.", "NO.").replace(" N0 ", " NO ")
        m = re.search(
            r"\bFC\s*(?:NO\.?|NUMBER|#)\s*[:\-]?\s*([A-Z0-9]{1,4})[\s\.\-]*([A-Z0-9]{1,4})\b",
            text,
        )
        if m:
            left = re.sub(r"[^A-Z0-9]", "", m.group(1))
            right = re.sub(r"[^A-Z0-9]", "", m.group(2))
            if left and right and re.search(r"\d", left + right):
                return f"FC-{left}.{right}"[:80]

        compact = re.sub(r"[^A-Z0-9\.\-]", "", text.replace(" ", ""))
        if re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d)\d{2,4}", compact):
            compact = f"{compact[:2]}.{compact[2:]}"
        if re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d)[\.\-]\d{2,4}", compact):
            compact = compact.replace("-", ".")
            return f"FC-{compact}"[:80]
        return compact[:80]

    def normalize_diameter(v):
        return _to_text(v)

    def normalize_model(v):
        text = re.sub(r"\s+", " ", _to_text(v)).strip().upper()
        if not text:
            return ""
        # Fix common OCR split for short alphanumeric models (e.g., ZT 15 -> ZT15).
        if re.fullmatch(r"[A-Z]{1,6}\s+\d{1,6}[A-Z]?", text):
            text = text.replace(" ", "")
        text = re.sub(r"\b(FCU?)\s*(M1|ML)\b", r"\1 MI", text)
        text = re.sub(r"[^A-Z0-9\/\.\-\s]", "", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:80]

    def completeness_score(d, fields):
        if not d or not fields:
            return 0.0
        filled = sum(1 for f in fields if d.get(f))
        return (filled / len(fields)) * 100.0

ME_SCRIPT_VERSION = "2026-08-03-ubc-consensus-36"


def _compact_lookup_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


ME_CANONICAL_MANUFACTURER_ALIASES: Dict[str, List[str]] = {
    "Atlas Copco": ["Atlas Copco", "Atlas-Copco", "Atlas Copco Compressors"],
    "Williams": ["Williams", "Williams Furnace Company", "Williams Furnace Co"],
    "Rockwell": ["Rockwell", "Rockwell Manufacturing", "Rockwell Manufacturing Co", "Delta Power Tool Division"],
    "Bell & Gossett": ["Bell & Gossett", "Bell and Gossett"],
    "Armstrong": ["Armstrong"],
    "Taco": [
        "Taco",
        "Taco Inc",
        "Taco Inc.",
        "Taco, Inc.",
        "Taco Canada Ltd",
        "Taco Canada Ltd.",
        "Taco Comfort Solutions",
    ],
    "Casadei": ["Casadei", "Macchine Casadei"],
    "Makita": ["Makita"],
    "Watts": ["Watts"],
    "Wilkins": ["Wilkins"],
    "Conbraco": ["Conbraco"],
    "Apollo": ["Apollo"],
    "Polar Air": ["Polar Air"],
    "Greenheck": ["Greenheck", "Green Heck"],
    "Daikin": ["Daikin"],
    "Enviro-tec": ["Enviro-tec", "Enviro Tec"],
    "Filterco": ["Filterco"],
    "Trane": ["Trane"],
    "Condair": ["Condair"],
    "Aermec": ["Aermec", "AERMEC"],
    "Mitsubishi Electric": ["Mitsubishi Electric"],
    "Quick Tanks Inc.": ["Quick Tanks Inc", "Quick Tanks Inc.", "Quick-Tanks Inc", "Quick-Taks Inc."],
    "Wessels Co.": ["Wessels Co.", "Wessels Co"],
    "Energy Labs Inc.": ["Energy Labs Inc", "Energy Labs Inc."],
    "General International": ["General International"],
    "A.O. Smith Corporation": [
        "A.O. Smith Corporation",
        "A. O. Smith Corporation",
        "AO Smith Corporation",
        "AO Smith",
    ],
    "Rheem": [
        "Rheem",
        "Rheem Manufacturing",
        "Rheem Manufacturing Co.",
        "Rheem Manufacturing Co",
    ],
    "Ruud": [
        "Ruud",
        "Ruud/Rheem",
        "Rheem/Ruud",
    ],
    "Wilo": ["Wilo"],
    "Valent Air Management Systems": ["Valent Air Management Systems"],
    "Dunham-Bush": ["Dunham-Bush", "Dunham Bush"],
    "Airtex Manufacturing": ["Airtex Manufacturing"],
    "Carrier": ["Carrier", "Carried"],
    "Parker Hannifin Ltd.": ["Parker Hannifin Ltd", "Parker Hannifin Ltd."],
    "MultiStack": ["MultiStack", "MULTISTACK"],
    "Republic Manufacturing": ["Republic Manufacturing"],
    "Northwest Tech-Con Systems Ltd.": [
        "Northwest Tech-Con Systems Ltd.",
        "Northwest Tech-Con Systems Ltd",
        "Northwest Tech Con Systems Ltd.",
        "Northwest Tech-Con Systems",
        "Northwest Tech Con Systems",
    ],
    "Nelson Sobe Company, Inc.": ["Nelson Sobe Company, Inc.", "Nelson Sobe Company Inc", "Nelson Sobe Company"],
    "Systemair": ["Systemair", "System Air"],
    "Triangle Tube": ["Triangle Tube"],
    "Cantek": ["Cantek"],
    "Hero": ["Hero"],
    "Graco": ["Graco"],
    "Air 2000": ["Air 2000"],
    "Canadian Blower": ["Canadian Blower"],
    "Haakon": ["Haakon", "HAAKON", "HAKKON"],
    "Engineered Air": ["Engineered Air", "ENGINEERED AIR"],
    "Annex Air": ["Annex Air"],
    "Axiom Industries Ltd.": ["Axiom Industries Ltd.", "Axiom Industries Ltd"],
    "TechTop": ["TechTop", "Techtop", "TECHTOP"],
    "Hitop": ["Hitop", "HITOP"],
    "Durawatt Electric": ["Durawatt Electric", "DURAWATT ELECTRIC"],
    "Durawatt/AquaPLEX": ["Durawatt/AquaPLEX", "Durawatt Aquaplex", "DURAWATT AQUAPLEX"],
    "AquaPLEX": ["AquaPLEX", "Aquaplex", "AQUAPLEX"],
    "Omega Compressors": ["Omega Compressors", "OMEGA COMPRESSORS", "Omega Compressor"],
    "Valent": ["Valent"],
    "Hawkins": ["Hawkins"],
    "Danfoss": ["Danfoss"],
    "MTU": ["MTU"],
    "ClimateMaster": ["ClimateMaster", "Climate Master"],
    "American Standard": ["American Standard"],
    "Akhurst Machinery Limited": ["Akhurst Machinery Limited", "Akhust Machinery Limited"],
    "Graphtec": ["Graphtec"],
    "Epilog": ["Epilog"],
    "DuMill Industries Ltd.": ["DuMill Industries Ltd.", "DuMill Industries Ltd"],
    "Brod & McClung-Pace Co.": ["Brod & McClung-Pace Co.", "Brod and McClung-Pace Co"],
}

ME_MANUFACTURER_REGEX_RULES: List[Tuple[str, str]] = [
    (r"\bATLAS\s+C[O0]P[C0]O\b", "Atlas Copco"),
    (r"\bWILLIAMS(?:\s+FURNACE(?:\s+COMPANY|CO)?)?\b", "Williams"),
    (r"\bROCKWELL(?:\s+MANUFACTURING(?:\s+CO\.?)?)?\b", "Rockwell"),
    (r"\bRO[CGK]K(?:W|VV)?E?L{1,2}\b", "Rockwell"),
    (r"\bDELTA\s+POWER\s+TOOL\s+DIVISION\b", "Rockwell"),
    (r"\bPITTSBURGH\b.*\bDELTA\b.*\bTOOL\b", "Rockwell"),
    (r"\bBELL\s*&?\s*GOSSETT\b", "Bell & Gossett"),
    (r"\bA\.?\s*O\.?\s*SMITH(?:\s+CORPORATION)?\b", "A.O. Smith Corporation"),
    (r"\bBRADFORD\s+WHITE(?:\s+CORPORATION)?\b", "Bradford White Corporation"),
    (r"\bRUUD\b", "Ruud"),
    (r"\bRHEEM(?:\s+MANUFACTURING(?:\s+CO\.?)?)?\b", "Rheem"),
    (r"\bARMSTRONG\b", "Armstrong"),
    (r"\bTACO(?:\s+(?:CANADA(?:\s+LTD\.?)?|COMFORT\s+SOLUTIONS|,?\s*INC\.?))?\b", "Taco"),
    (r"\b(?:MACCHINE\s+)?CASADEI\b", "Casadei"),
    (r"\bMAKITA\b", "Makita"),
    (r"\bWATTS\b", "Watts"),
    (r"\bWILKINS\b", "Wilkins"),
    (r"\bCONBRACO\b", "Conbraco"),
    (r"\bAPOLLO\b", "Apollo"),
    (r"\bPOLAR\s+AIR\b", "Polar Air"),
    (r"\bGREENH[EA]CK\b", "Greenheck"),
    (r"\bDAIKIN\b", "Daikin"),
    (r"\bENVIRO[\s\-]?TEC\b", "Enviro-tec"),
    (r"\bFILTERCO\b", "Filterco"),
    (r"\bTRANE\b", "Trane"),
    (r"\bCONDAIR\b", "Condair"),
    (r"\bAERMEC\b", "Aermec"),
    (r"\bMITSUBISHI\s+ELECTRIC\b", "Mitsubishi Electric"),
    (r"\bQUICK[\s\-]?TA[KN]KS?\s+INC\.?\b", "Quick Tanks Inc."),
    (r"\bWESSELS\s+CO\.?\b", "Wessels Co."),
    (r"\bENERGY\s+LABS\s+INC\.?\b", "Energy Labs Inc."),
    (r"\bGENERAL\s+INTERNATIONAL\b", "General International"),
    (r"\bWILO\b", "Wilo"),
    (r"\bVALENT\s+AIR\s+MANAGEMENT\s+SYSTEMS\b", "Valent Air Management Systems"),
    (r"\bDUNHAM[\s\-]?BUSH\b", "Dunham-Bush"),
    (r"\bAIRTEX\s+MANUFACTURING\b", "Airtex Manufacturing"),
    (r"\bCARRI(?:ER|ED)\b", "Carrier"),
    (r"\bPARKER\s+HANNIFIN\s+LTD\.?\b", "Parker Hannifin Ltd."),
    (r"\bMULTISTACK\b", "MultiStack"),
    (r"\bREPUBLIC\s+MANUFACTURING\b", "Republic Manufacturing"),
    (r"\bR\s*E\s*P\s*U\s*B\s*L\s*I\s*C\b", "Republic Manufacturing"),
    (r"\b(?:REPUBLIC|EPUBLIC)\b", "Republic Manufacturing"),
    (r"\bUBLIC\b.*\bMANUFACTURING\b", "Republic Manufacturing"),
    (r"\bNORTHWEST\s+TECH[\s\-]?CON\s+SYSTEMS\s+LTD\.?\b", "Northwest Tech-Con Systems Ltd."),
    (r"\bNORTHWEST\s+TECH[\s\-]?CON\s+SYSTEMS\b", "Northwest Tech-Con Systems Ltd."),
    (r"\bNELSON\s+SOBE\s+COMPANY(?:\s*,?\s*INC\.?)?\b", "Nelson Sobe Company, Inc."),
    (r"\bSYSTEM\s+AIR\b", "Systemair"),
    (r"\bTRIANGLE\s+TUBE\b", "Triangle Tube"),
    (r"\bCANTEK\b", "Cantek"),
    (r"\bHERO\b", "Hero"),
    (r"\bGRACO\b", "Graco"),
    (r"\bAIR\s*2000\b", "Air 2000"),
    (r"\bCANADIAN\s+BLOWER\b", "Canadian Blower"),
    (r"\bHA[AK]{2}ON\b", "Haakon"),
    (r"\bENGINEERED\s+AIR\b", "Engineered Air"),
    (r"\bANNEX\s+AIR\b", "Annex Air"),
    (r"\bAXIOM\s+INDUSTRIES\s+LTD\.?\b", "Axiom Industries Ltd."),
    (r"\bTECHTOP\b", "TechTop"),
    (r"\bHITOP\b", "Hitop"),
    (r"\bDURAWATT\s+ELECTRIC\b", "Durawatt Electric"),
    (r"\bDURAWATT(?:\s*[/\-]\s*|\s+)AQUAPLEX\b", "Durawatt/AquaPLEX"),
    (r"\bAQUA\s*PLEX\b", "AquaPLEX"),
    (r"\bOMEGA\s+COMPRESSORS?\b", "Omega Compressors"),
    (r"\bVALENT\b", "Valent"),
    (r"\bHAWKINS\b", "Hawkins"),
    (r"\bDANFOSS\b", "Danfoss"),
    (r"\bMTU\b", "MTU"),
    (r"\bCLIMATE\s*MASTER\b", "ClimateMaster"),
    (r"\bAMERICAN\s+STANDARD\b", "American Standard"),
    (r"\bAKH(?:U|UR)ST\s+MACHINERY\s+LIMITED\b", "Akhurst Machinery Limited"),
    (r"\bGRAPHTEC\b", "Graphtec"),
    (r"\bEPILOG\b", "Epilog"),
    (r"\bDUMILL\s+INDUSTRIES\s+LTD\.?\b", "DuMill Industries Ltd."),
    (r"\bBROD\s*&?\s*MCCLUNG[\s\-]?PACE\s+CO\.?\b", "Brod & McClung-Pace Co."),
    (r"\bSPIRAX\s*/?\s*SARCO\b", "Spirax Sarco"),
    (r"\bSIEMENS(?:\s+BUILDING\s+TECHNOLOGIES)?\b", "Siemens"),
    (r"\bGARDNER\s+DENVER\b", "Gardner Denver"),
]

ME_MANUFACTURER_ALIAS_LOOKUP: Dict[str, str] = {}
for _canonical, _aliases in ME_CANONICAL_MANUFACTURER_ALIASES.items():
    for _alias in {_canonical, *(_aliases or [])}:
        _key = _compact_lookup_key(_alias)
        if _key:
            ME_MANUFACTURER_ALIAS_LOOKUP[_key] = _canonical

ME_MANUFACTURER_ALIAS_MATCHES: List[Tuple[str, str]] = sorted(
    ME_MANUFACTURER_ALIAS_LOOKUP.items(),
    key=lambda item: len(item[0]),
    reverse=True,
)

ME_GENERIC_MANUFACTURER_STOPWORDS: Set[str] = {
    "MODEL", "SERIAL", "NUMBER", "ORDER", "DATE", "YEAR", "MFG", "RPM", "GPM",
    "CAPACITY", "HEAD", "MOTOR", "PUMP", "AIR", "SYSTEM", "MADE", "NO",
}
# Corporate suffix / origin tokens are noise only when the WHOLE candidate is
# made of them ("LTD", "CO CANADA"); combined with a brand token they are part
# of a legitimate name ("Enermax Fabricators Ltd").
ME_MANUFACTURER_CORPORATE_TOKENS: Set[str] = {
    "LTD", "INC", "CORPORATION", "COMPANY", "CO", "SYSTEMS", "CANADA",
}

# A previously unseen brand may be accepted when it is a compact, brand-like
# candidate from the vision/OCR path. These hard blocks keep field labels and
# maintenance instructions from becoming manufacturers while avoiding a strict
# supplier whitelist.
ME_UNKNOWN_MANUFACTURER_HARD_TOKENS: Set[str] = {
    "MODEL", "SERIAL", "NUMBER", "ORDER", "DATE", "YEAR", "MFG", "RPM",
    "GPM", "CAPACITY", "HEAD", "VOLTAGE", "AMPERAGE", "PHASE", "HP", "NO",
}
ME_UNKNOWN_MANUFACTURER_DESCRIPTOR_TOKENS: Set[str] = {
    "AIR", "SYSTEM", "SYSTEMS", "COMPRESSOR", "COMPRESSORS", "PUMP", "PUMPS",
    "MOTOR", "MOTORS", "OIL", "TANK", "TANKS", "MADE", "IN", "CANADA",
    "USE", "ONLY", "READ", "MANUAL", "ASSOCIATED", "COMPONENT", "COMPONENTS",
}
ME_UNKNOWN_MANUFACTURER_FORBIDDEN_PHRASES: Tuple[str, ...] = (
    "COMPRESSOR OIL",
    "MADE IN CANADA",
    "USE ONLY",
    "READ MANUAL",
    "ALL ASSOCIATED COMPONENTS",
)

# Manufacturers known to stamp all-numeric model/product numbers on their
# nameplates (e.g. Siemens Building Technologies valve actuators: Model 011749 /
# 012135). For ONLY these makers does _is_model_code_candidate accept a
# letter-free model instead of discarding it as serial/date noise. Extend this
# set as new numeric-model manufacturers are confirmed from field plates.
ME_NUMERIC_MODEL_MANUFACTURERS: Set[str] = {"Siemens"}
ME_NUMERIC_MODEL_MANUFACTURER_KEYS: Set[str] = {
    _compact_lookup_key(_name) for _name in ME_NUMERIC_MODEL_MANUFACTURERS
}

# Long OEM configuration strings are valid model numbers (Trane fan-coil
# nameplates are a confirmed example). Keep a bounded limit, but do not force
# every model into the old 32-character short-code envelope.
ME_MAX_MODEL_CODE_LENGTH = 64

# Confirmed product-model identities used only as a last deterministic fallback
# after the model value has passed the normal label-evidence checks.  This avoids
# discarding a visible product brand when logo OCR is weak or a targeted vision
# reread times out.  Keep entries exact; this is not a fuzzy manufacturer guess.
ME_MODEL_MANUFACTURER_RULES: List[Tuple[str, str]] = [
    (r"^L600ATR$", "AquaPLEX"),
    (r"^TK5080V02M$", "Omega Compressors"),
]


# --- [1] Configuration ---
class Config:
    """Centralized configuration for the asset processing script."""
    # --- TESTING OVERRIDE ---
    # Set to False for production! This saves API costs by skipping ai_status=1
    FORCE_REPROCESS = False
    
    # ADD THESE TWO LINES:
    TEMPERATURE = 0.0
    SEED = 42

    # --- Paths ---
    ROOT_DEV_PATH = os.getenv("DEV_PATH", "/home/developer")
    IMAGE_FOLDER = os.path.join(ROOT_DEV_PATH, "Capture_photos_upload")
    OUTPUT_FOLDER = os.path.join(ROOT_DEV_PATH, "Output_jason_api")
    DEBUG_FOLDER = os.path.join(OUTPUT_FOLDER, "debug_ubc_tag")
    DB_PATH = os.path.join(ROOT_DEV_PATH, "asset_capture_app_dev/data/QR_codes.db")
    ENV_PATH = os.path.join(ROOT_DEV_PATH, "API/OpenAI_key_giba.env")

    # --- Database ---
    DB_TABLE = "sdi_dataset"
    AI_STATUS_TABLE = "QR_codes"
    AI_STATUS_QR_COLUMN = "QR_code_ID"
    AI_STATUS_COLUMN = "ai_status"

    # --- File Matching ---
    VALID_SUFFIXES = {"0", "1", "3"}
    VALID_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    
    # Updated Regex to accept 'T' prefix (e.g., T00001)
    FILENAME_PATTERN = re.compile(
        r"^([T]?\d+)\s+" r"(\d+(?:-\d+)?)\s+" r"([A-Z]{2})\s*-\s*([0-4])$", re.IGNORECASE
    )

    # --- OCR & Image Processing ---
    UBC_TAG_PATTERNS: List[str] = [
        # Accepts hyphen OR space separator (e.g., 'HUM 5', 'FC-6.32', 'CHWBT-W-4')
        r"([A-Z]{1,6}[-\s][\w\.-]+)",
        # Fallback pattern
        r"([A-Z]{2,3}-[A-Z0-9]+-[0-9]+)"
    ]
    TESSERACT_MIN_CONFIDENCE = 75.0
    # OCR strategy:
    # - off: no OCR calls
    # - light: OCR only for weak/missing fields (recommended)
    # - full: always run OCR enrichment
    OCR_MODE = os.getenv("ME_OCR_MODE", "light").strip().lower()
    # Hybrid OCR agent: provide OCR candidate text to the VLM for semantic correction.
    # Defaulting to 0 (off) to prevent raw OCR garble from poisoning the LLM context.
    HYBRID_OCR_AGENT_ENABLED = os.getenv("ME_HYBRID_OCR_AGENT", "0").strip().lower() not in {"0", "false", "no"}
    OCR_CONTEXT_MAX_CHARS = int(os.getenv("ME_OCR_CONTEXT_MAX_CHARS", "4500"))
    OCR_CONTEXT_MAX_LINES = int(os.getenv("ME_OCR_CONTEXT_MAX_LINES", "140"))

    # --- OpenAI API: cost-controlled model plan ---
    PRIMARY_LLM_MODEL = os.getenv("ME_PRIMARY_LLM_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini"
    FALLBACK_LLM_MODEL = os.getenv("ME_FALLBACK_LLM_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    PREMIUM_LLM_MODEL = os.getenv("ME_PREMIUM_LLM_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    ENABLE_LLM_FALLBACK = os.getenv("ME_ENABLE_LLM_FALLBACK", "false").strip().lower() == "true"
    ENABLE_PREMIUM_FALLBACK = os.getenv("ME_ENABLE_PREMIUM_FALLBACK", "false").strip().lower() == "true"
    try:
        _max_attempts_asset_env = int(os.getenv("ME_MAX_LLM_ATTEMPTS_PER_ASSET", "1"))
    except ValueError:
        _max_attempts_asset_env = 1
    MAX_LLM_ATTEMPTS_PER_ASSET = max(1, min(_max_attempts_asset_env, 3))
    try:
        _max_attempts_model_env = int(os.getenv("ME_MAX_LLM_ATTEMPTS_PER_MODEL", "1"))
    except ValueError:
        _max_attempts_model_env = 1
    MAX_LLM_ATTEMPTS_PER_MODEL = max(1, min(_max_attempts_model_env, 3))
    OVERWRITE_EXISTING_JSON = os.getenv("ME_OVERWRITE_EXISTING_JSON", "false").strip().lower() == "true"
    try:
        _retries_env = int(os.getenv("ME_API_MAX_RETRIES", "2"))
    except ValueError:
        _retries_env = 1
    API_MAX_RETRIES = max(1, min(_retries_env, 3))
    try:
        _retry_delay_env = float(os.getenv("ME_API_RETRY_DELAY", "0.8"))
    except ValueError:
        _retry_delay_env = 0.8
    API_RETRY_DELAY = max(0.0, _retry_delay_env)
    try:
        _timeout_env = float(os.getenv("ME_API_TIMEOUT", "45"))
    except ValueError:
        _timeout_env = 45.0
    API_TIMEOUT = max(10.0, _timeout_env)
    SIMPLE_MODE = os.getenv("ME_SIMPLE_MODE", "0").strip().lower() not in {"0", "false", "no"}
    # UI parity mode: keeps model output as primary truth and minimizes hard OCR overrides.
    UI_PARITY_MODE = os.getenv("ME_UI_PARITY_MODE", "1").strip().lower() not in {"0", "false", "no"}
    try:
        _simple_max_images_env = int(os.getenv("ME_SIMPLE_MAX_IMAGES", "3"))
    except ValueError:
        _simple_max_images_env = 3
    SIMPLE_MAX_IMAGES = max(1, min(_simple_max_images_env, 4))
    try:
        # VERSION 18 FIX: Increased token budget dramatically to avoid cutoff
        _simple_max_tokens_env = int(os.getenv("ME_SIMPLE_MAX_TOKENS", "1200"))
    except ValueError:
        _simple_max_tokens_env = 1200
    SIMPLE_MAX_TOKENS = max(200, min(_simple_max_tokens_env, 4000))
    _REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
    NORMAL_REASONING_EFFORT = os.getenv("ME_NORMAL_REASONING_EFFORT", "low").strip().lower()
    if NORMAL_REASONING_EFFORT not in _REASONING_EFFORTS:
        NORMAL_REASONING_EFFORT = "low"
    HARD_REASONING_EFFORT = os.getenv("ME_HARD_REASONING_EFFORT", "high").strip().lower()
    if HARD_REASONING_EFFORT not in _REASONING_EFFORTS:
        HARD_REASONING_EFFORT = "high"
    SIMPLE_REASONING_EFFORT = os.getenv("ME_SIMPLE_REASONING_EFFORT", NORMAL_REASONING_EFFORT).strip().lower()
    if SIMPLE_REASONING_EFFORT not in _REASONING_EFFORTS:
        SIMPLE_REASONING_EFFORT = NORMAL_REASONING_EFFORT
    SIMPLE_IMAGE_DETAIL = os.getenv("ME_IMAGE_DETAIL", "high").strip().lower()
    if SIMPLE_IMAGE_DETAIL not in {"low", "high", "auto"}:
        SIMPLE_IMAGE_DETAIL = "high"
    # ME seq-1 UBC-tag hybrid consensus. These settings do not change the
    # primary low-detail extraction; the independent judge is challenge-only.
    ME_UBC_CONSENSUS_ENABLED = os.getenv(
        "ME_UBC_CONSENSUS_ENABLED", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}
    ME_UBC_JUDGE_MODEL = (
        os.getenv("ME_UBC_JUDGE_MODEL", "gpt-5.6-terra").strip()
        or "gpt-5.6-terra"
    )
    ME_UBC_JUDGE_DETAIL = os.getenv(
        "ME_UBC_JUDGE_DETAIL", "original"
    ).strip().lower()
    if ME_UBC_JUDGE_DETAIL not in {"low", "high", "auto", "original"}:
        ME_UBC_JUDGE_DETAIL = "original"
    ME_UBC_JUDGE_REASONING_EFFORT = os.getenv(
        "ME_UBC_JUDGE_REASONING_EFFORT", "low"
    ).strip().lower()
    if ME_UBC_JUDGE_REASONING_EFFORT not in _REASONING_EFFORTS:
        ME_UBC_JUDGE_REASONING_EFFORT = "low"
    # Deliberately not configurable: these guards bound surprise cost.
    ME_UBC_JUDGE_MAX_CALLS = 1
    ME_UBC_JUDGE_MAX_INPUT_EDGE = 1280
    ME_UBC_JUDGE_MAX_COMPLETION_TOKENS = 220
    try:
        _early_score_env = float(os.getenv("ME_EARLY_ACCEPT_SCORE", "95"))
    except ValueError:
        _early_score_env = 80.0
    EARLY_ACCEPT_SCORE = max(50.0, min(_early_score_env, 100.0))
    try:
        _early_missing_env = int(os.getenv("ME_EARLY_ACCEPT_MAX_MISSING", "1"))
    except ValueError:
        _early_missing_env = 1
    EARLY_ACCEPT_MAX_MISSING = max(0, min(_early_missing_env, 6))
    # Safety: do not reintroduce stale nameplate fields (Model/Serial/Year) from existing JSON by default.
    ALLOW_NAMEPLATE_BACKFILL = os.getenv("ME_ALLOW_NAMEPLATE_BACKFILL", "0").strip().lower() in {"1", "true", "yes"}

    MANUAL_REVIEW_QUEUE_FILE = os.getenv(
        "ME_MANUAL_REVIEW_QUEUE_FILE",
        os.path.join(OUTPUT_FOLDER, "manual_review_queue_ME.jsonl"),
    )
    try:
        _fallback_min_score_env = float(os.getenv("ME_FALLBACK_MIN_SCORE", "95"))
    except ValueError:
        _fallback_min_score_env = 95.0
    FALLBACK_MIN_SCORE = max(0.0, min(_fallback_min_score_env, 100.0))
    try:
        _fallback_max_missing_env = int(os.getenv("ME_FALLBACK_MAX_MISSING", "1"))
    except ValueError:
        _fallback_max_missing_env = 1
    # Keep this independent from EXPECTED_FIELDS declaration order in class body.
    FALLBACK_MAX_MISSING = max(0, min(_fallback_max_missing_env, 6))

    # OCR policy:
    # - default fallback-only: OCR runs only to fill AI-missing fields
    OCR_VALIDATE_EXISTING_FIELDS = os.getenv("ME_OCR_VALIDATE_EXISTING_FIELDS", "0").strip().lower() in {"1", "true", "yes"}

    try:
        _manual_review_min_score_env = float(os.getenv("ME_MANUAL_REVIEW_MIN_SCORE", "95"))
    except ValueError:
        _manual_review_min_score_env = 95.0
    MANUAL_REVIEW_MIN_SCORE = max(0.0, min(_manual_review_min_score_env, 100.0))
    try:
        _manual_review_min_conf_env = int(os.getenv("ME_MANUAL_REVIEW_MIN_CONFIDENCE", "70"))
    except ValueError:
        _manual_review_min_conf_env = 70
    MANUAL_REVIEW_MIN_CONFIDENCE = max(0, min(_manual_review_min_conf_env, 100))

    # --- Concurrency ---
    # Keep ME conservative by default; this script can be memory-heavy (OCR + multi-image VLM).
    try:
        _workers_env = int(os.getenv("ME_MAX_WORKERS", "1"))
    except ValueError:
        _workers_env = 1
    MAX_WORKERS = max(1, min(_workers_env, 2))

    # --- Field Mapping & Validation ---
    FIELD_SOURCES: Dict[str, List[str]] = {
        "Manufacturer": ["0"], "Model": ["0"], "Serial Number": ["0"],
        "Year": ["0"], "UBC Tag": ["1"], "Technical Safety BC": ["3"],
    }
    EXPECTED_FIELDS: List[str] = list(FIELD_SOURCES.keys())
    YEAR_VALIDATION_RANGE = (1950, 2026)
    
    # --- Completeness Score ---
    COMPLETENESS_SCORE_FIELDS: List[str] = [
        "Manufacturer", "Model", "Serial Number", "Year", "UBC Tag"
    ]



class MEConfidenceScores(BaseModel):
    """Explicit sub-model required by OpenAI Structured Outputs (no generic dicts)."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    manufacturer: int = Field(default=0, alias="Manufacturer")
    model: int = Field(default=0, alias="Model")
    serial_number: int = Field(default=0, alias="Serial Number")
    year: int = Field(default=0, alias="Year")
    ubc_tag: int = Field(default=0, alias="UBC Tag")
    technical_safety_bc: int = Field(default=0, alias="Technical Safety BC")


class MEStructuredExtraction(BaseModel):
    """Strict schema for ME asset extraction returned by the vision model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    manufacturer: str = Field(default="", alias="Manufacturer")
    model: str = Field(default="", alias="Model")
    serial_number: str = Field(default="", alias="Serial Number")
    year: str = Field(default="", alias="Year")
    ubc_tag: str = Field(default="", alias="UBC Tag")
    technical_safety_bc: str = Field(default="", alias="Technical Safety BC")
    confidence_scores: MEConfidenceScores = Field(
        default_factory=MEConfidenceScores,
        alias="Confidence Scores",
        description="Per-field confidence 0-100. Keys must match the main extraction fields."
    )

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_to_string(cls, value: Any) -> Any:
        # Ignore dicts/objects so confidence_scores can be parsed properly
        if isinstance(value, (dict, BaseModel)):
            return value
        if value is None:
            return ""
        return str(value).strip()


class MEYearOnlyExtraction(BaseModel):
    """Minimal structured schema for targeted year reread on seq-0 nameplate."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)
    year: str = Field(default="", alias="Year")

    @field_validator("year", mode="before")
    @classmethod
    def _coerce_year(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class MEModelSerialOnlyExtraction(BaseModel):
    """Minimal structured schema for targeted model/serial reread on seq-0 nameplate."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)
    model: str = Field(default="", alias="Model")
    serial_number: str = Field(default="", alias="Serial Number")
    pressure_vessel_context: bool = Field(default=False, alias="Pressure Vessel Context")

    @field_validator("model", "serial_number", mode="before")
    @classmethod
    def _coerce_value(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class MEManufacturerOnlyExtraction(BaseModel):
    """Minimal structured schema for a targeted seq-0 manufacturer reread."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)
    manufacturer: str = Field(default="", alias="Manufacturer")

    @field_validator("manufacturer", mode="before")
    @classmethod
    def _coerce_manufacturer(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class MEUBCTagOnlyExtraction(BaseModel):
    """Minimal structured schema for targeted UBC tag reread on seq-1 tag image."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)
    ubc_tag: str = Field(default="", alias="UBC Tag")

    @field_validator("ubc_tag", mode="before")
    @classmethod
    def _coerce_ubc_tag(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class METechnicalSafetyBCOnlyExtraction(BaseModel):
    """Minimal structured schema for targeted TSBC unit-number reread on seq-3 sticker."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)
    technical_safety_bc: str = Field(default="", alias="Technical Safety BC")

    @field_validator("technical_safety_bc", mode="before")
    @classmethod
    def _coerce_technical_safety_bc(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class MEReasonedExtraction(BaseModel):
    """Schema for verbose extraction responses used by the legacy single-image helper."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str = ""
    confidence_scores: Dict[str, int] = Field(default_factory=dict)
    extracted_data: Dict[str, str] = Field(default_factory=dict)

    @field_validator("reasoning", mode="before")
    @classmethod
    def _coerce_reasoning(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("confidence_scores", mode="before")
    @classmethod
    def _coerce_confidence_scores(cls, value: Any) -> Dict[str, int]:
        if not isinstance(value, dict):
            return {}
        normalized: Dict[str, int] = {}
        for key, raw_score in value.items():
            try:
                score = int(float(raw_score))
            except (TypeError, ValueError):
                score = 0
            normalized[str(key)] = max(0, min(100, score))
        return normalized

    @field_validator("extracted_data", mode="before")
    @classmethod
    def _coerce_extracted_data(cls, value: Any) -> Dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): "" if raw_value is None else str(raw_value).strip()
            for key, raw_value in value.items()
        }


# --- [2] Setup Logging and Environment ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

def setup_environment():
    """Loads environment variables and configures Tesseract."""
    load_dotenv(dotenv_path=Config.ENV_PATH)
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")
    if cv2 is not None:
        try:
            cv2.setNumThreads(1)
        except Exception:
            pass
    
    if platform.system() == "Windows":
        user_local = os.getenv("LOCALAPPDATA", "")
        candidates = []
        if user_local:
            candidates.append(os.path.join(user_local, "Programs", "Tesseract-OCR", "tesseract.exe"))
        candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")

        resolved = next((p for p in candidates if p and os.path.exists(p)), None)
        if resolved:
            pytesseract.pytesseract.tesseract_cmd = resolved
        else:
            # Final fallback: rely on PATH if available.
            pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"
    else:
        pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"
    
    os.makedirs(Config.OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(Config.DEBUG_FOLDER, exist_ok=True)


# --- [3] Asset Processing Class ---
class AssetProcessor:
    """Orchestrates asset data extraction using an advanced ensemble methodology."""

    def __init__(self):
        self.run_id = datetime.now(UTC).strftime("ME-%Y%m%dT%H%M%SZ")
        warn_legacy_env_vars(
            "ME",
            (
                "ME_OPENAI_MODEL",
                "ME_OPENAI_PRIMARY_MODEL",
                "ME_PRIMARY_MODELS",
                "ME_FALLBACK_MODELS",
                "OPENAI_PRIMARY_MODEL",
            ),
        )
        # We control retries ourselves; disable the SDK's retry layer to avoid double-billing on transient errors.
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=Config.API_TIMEOUT, max_retries=0)
        logging.info(f"ME script version: {ME_SCRIPT_VERSION}")
        logging.info(f"ME script path: {os.path.abspath(__file__)}")
        logging.info(f"ME run id: {self.run_id} | pid={os.getpid()}")
        if Config.OCR_MODE not in {"off", "light", "full"}:
            logging.warning(f"Invalid ME_OCR_MODE '{Config.OCR_MODE}', defaulting to 'light'.")
            Config.OCR_MODE = "light"
        logging.info(f"ME OCR mode: {Config.OCR_MODE}")
        logging.info(f"ME max workers: {Config.MAX_WORKERS}")
        logging.info(f"ME API timeout: {Config.API_TIMEOUT}s | retries: {Config.API_MAX_RETRIES}")
        logging.info(
            "ME simple mode: %s | max_images=%s | max_tokens=%s | simple_reasoning=%s | image_detail=%s",
            Config.SIMPLE_MODE,
            Config.SIMPLE_MAX_IMAGES,
            Config.SIMPLE_MAX_TOKENS,
            Config.SIMPLE_REASONING_EFFORT,
            Config.SIMPLE_IMAGE_DETAIL,
        )
        logging.info(
            "ME UBC consensus: enabled=%s | judge_model=%s | detail=%s | reasoning=%s "
            "| call_cap=%s | max_edge=%s | completion_cap=%s",
            Config.ME_UBC_CONSENSUS_ENABLED,
            Config.ME_UBC_JUDGE_MODEL,
            Config.ME_UBC_JUDGE_DETAIL,
            Config.ME_UBC_JUDGE_REASONING_EFFORT,
            Config.ME_UBC_JUDGE_MAX_CALLS,
            Config.ME_UBC_JUDGE_MAX_INPUT_EDGE,
            Config.ME_UBC_JUDGE_MAX_COMPLETION_TOKENS,
        )
        logging.info(
            "ME reasoning tiers: normal=%s | hard=%s",
            Config.NORMAL_REASONING_EFFORT,
            Config.HARD_REASONING_EFFORT,
        )
        logging.info(f"ME UI parity mode: {Config.UI_PARITY_MODE}")
        logging.info(
            f"ME early accept: score>={Config.EARLY_ACCEPT_SCORE:.0f}, missing<={Config.EARLY_ACCEPT_MAX_MISSING}"
        )
        logging.info(
            "ME model plan: %s | per_asset_cap=%s | per_model_cap=%s | premium_enabled=%s | fallback_trigger=(score<%s or missing>%s)",
            get_llm_model_plan(Config),
            Config.MAX_LLM_ATTEMPTS_PER_ASSET,
            Config.MAX_LLM_ATTEMPTS_PER_MODEL,
            Config.ENABLE_PREMIUM_FALLBACK,
            f"{Config.FALLBACK_MIN_SCORE:.0f}",
            Config.FALLBACK_MAX_MISSING,
        )
        logging.info(
            "ME manual review thresholds: score>=%s | critical_confidence>=%s",
            f"{Config.MANUAL_REVIEW_MIN_SCORE:.0f}",
            Config.MANUAL_REVIEW_MIN_CONFIDENCE,
        )
        logging.info(f"ME nameplate backfill from existing JSON: {Config.ALLOW_NAMEPLATE_BACKFILL}")
        logging.info(
            f"ME OCR corroboration for already-populated AI fields: {Config.OCR_VALIDATE_EXISTING_FIELDS}"
        )
        logging.info(f"ME hybrid OCR agent enabled: {Config.HYBRID_OCR_AGENT_ENABLED}")
        logging.info(f"ME output folder: {Config.OUTPUT_FOLDER}")
        logging.info(f"ME DB path: {Config.DB_PATH}")
        self.qrs_to_ignore = self._load_qrs_to_ignore()
        self.ai_processed_qrs = self._load_ai_processed_qrs()
        logging.info(f"Loaded {len(self.qrs_to_ignore)} QR codes to ignore (approved).")
        logging.info(f"Loaded {len(self.ai_processed_qrs)} QR codes already processed by AI (ai_status=1).")

    def _load_qrs_to_ignore(self) -> Set[str]:
        """Loads QR codes that are already approved or have been flagged."""
        to_ignore = set()
        if not os.path.exists(Config.DB_PATH):
            logging.warning(f"Database not found: {Config.DB_PATH}. Proceeding without filtering processed assets.")
            return to_ignore
        try:
            with closing(qrdb.get_connection(sqlite_path=Config.DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                with closing(conn.cursor()) as cur:
                    # Robust query to handle diverse schema versions
                    try:
                        query = f'SELECT "QR Code" FROM "{Config.DB_TABLE}" WHERE "Approved" = \'1\''
                        cur.execute(query)
                        for row in cur.fetchall():
                            if qrid := str(row["QR Code"]).strip():
                                to_ignore.add(qrid)
                    except qrdb.DatabaseError:
                        logging.warning("Could not filter by Approved status (columns might be missing).")
        except qrdb.DatabaseError as e:
            logging.error(f"Error reading DB to filter assets: {e}.")
        return to_ignore

    def _load_ai_processed_qrs(self) -> Set[str]:
        """Loads QR codes from QR_codes where ai_status = 1."""
        processed = set()

        if Config.FORCE_REPROCESS:
            logging.warning("FORCE_REPROCESS is True. Ignoring database ai_status to force re-extraction.")
            return processed

        stale_processed: List[str] = []
        me_qrs_with_images = self._qrs_with_me_images()
        if not os.path.exists(Config.DB_PATH):
            return processed
        try:
            with closing(qrdb.get_connection(sqlite_path=Config.DB_PATH)) as conn:
                with closing(conn.cursor()) as cur:
                    columns = qrdb.table_columns(conn, Config.AI_STATUS_TABLE)  # backend-agnostic (PRAGMA on SQLite, information_schema on PG)
                    qr_col = self._resolve_column(columns, [Config.AI_STATUS_QR_COLUMN, "QR Code", "QR", "QRCode", "QR_code"])
                    ai_col = self._resolve_column(columns, [Config.AI_STATUS_COLUMN, "AI Status", "aiStatus"])
                    if not qr_col or not ai_col:
                        logging.warning(f"Could not load ai_status filter; missing columns in {Config.AI_STATUS_TABLE}")
                        return processed
                    query = f'SELECT "{qr_col}" AS qr, "{ai_col}" AS status FROM "{Config.AI_STATUS_TABLE}"'
                    cur.execute(query)
                    for qr, status in cur.fetchall():
                        if str(status).strip() == "1":
                            qr_val = str(qr).strip()
                            if qr_val:
                                # Safety rule: only treat ai_status=1 as processed when ME JSON exists on disk.
                                if self._find_existing_json_for_qr(qr_val, "ME"):
                                    processed.add(qr_val)
                                elif qr_val in me_qrs_with_images:
                                    # Reset only rows that correspond to ME assets in current image set.
                                    stale_processed.append(qr_val)
                                else:
                                    # Shared ai_status table: keep rows for non-ME assets untouched.
                                    processed.add(qr_val)
        except qrdb.DatabaseError as e:
            logging.error(f"Error reading ai_status from DB: {e}.")
        if stale_processed:
            updated = self._bulk_set_ai_status(stale_processed, 0)
            logging.warning(
                "Detected %d stale ME ai_status=1 rows without JSON; reset %d row(s) back to 0. Examples: %s",
                len(stale_processed),
                updated,
                stale_processed[:10],
            )
        return processed

    def _qrs_with_me_images(self) -> Set[str]:
        """Scans image folder and returns QRs that currently have ME image files."""
        qrs: Set[str] = set()
        if not os.path.isdir(Config.IMAGE_FOLDER):
            return qrs
        try:
            for filename in os.listdir(Config.IMAGE_FOLDER):
                base, ext = os.path.splitext(filename)
                if ext.lower() not in Config.VALID_EXTS:
                    continue
                match = Config.FILENAME_PATTERN.match(base)
                if not match:
                    continue
                qr, _, asset_type, seq = match.groups()
                if asset_type.upper() != "ME":
                    continue
                if seq not in Config.VALID_SUFFIXES:
                    continue
                qr_val = str(qr).strip()
                if qr_val:
                    qrs.add(qr_val)
        except OSError:
            return qrs
        return qrs

    def _bulk_set_ai_status(self, qrs: List[str], value: int) -> int:
        """Bulk updates ai_status for a list of QR codes. Returns rows affected."""
        if not qrs or not os.path.exists(Config.DB_PATH):
            return 0
        try:
            with closing(qrdb.get_connection(sqlite_path=Config.DB_PATH, timeout=10.0)) as conn:
                with closing(conn.cursor()) as cur:
                    columns = qrdb.table_columns(conn, Config.AI_STATUS_TABLE)  # backend-agnostic (PRAGMA on SQLite, information_schema on PG)
                    qr_col = self._resolve_column(columns, [Config.AI_STATUS_QR_COLUMN, "QR Code", "QR", "QRCode", "QR_code"])
                    ai_col = self._resolve_column(columns, [Config.AI_STATUS_COLUMN, "AI Status", "aiStatus"])
                    if not qr_col or not ai_col:
                        return 0
                    if not qrdb.is_postgres():
                        cur.execute("BEGIN IMMEDIATE")  # SQLite-only; PG uses MVCC + implicit txn
                    updated = 0
                    for qr in qrs:
                        cur.execute(
                            f'UPDATE "{Config.AI_STATUS_TABLE}" SET "{ai_col}" = ? WHERE "{qr_col}" = ?',
                            (str(value), qr),
                        )
                        updated += max(0, cur.rowcount)
                    conn.commit()
                    return updated
        except qrdb.DatabaseError as e:
            logging.error(f"Failed bulk ai_status update to {value}: {e}")
            return 0

    def _get_pending_qrs(self) -> Set[str]:
        """Loads QR codes from QR_codes where ai_status = 0."""
        pending = set()
        if not os.path.exists(Config.DB_PATH):
            return pending
        try:
            with closing(qrdb.get_connection(sqlite_path=Config.DB_PATH)) as conn:
                with closing(conn.cursor()) as cur:
                    columns = qrdb.table_columns(conn, Config.AI_STATUS_TABLE)  # backend-agnostic (PRAGMA on SQLite, information_schema on PG)
                    qr_col = self._resolve_column(columns, [Config.AI_STATUS_QR_COLUMN, "QR Code", "QR", "QRCode", "QR_code"])
                    ai_col = self._resolve_column(columns, [Config.AI_STATUS_COLUMN, "AI Status", "aiStatus"])
                    if not qr_col or not ai_col:
                        return pending
                    query = f'SELECT "{qr_col}" AS qr, "{ai_col}" AS status FROM "{Config.AI_STATUS_TABLE}"'
                    cur.execute(query)
                    for qr, status in cur.fetchall():
                        if str(status).strip() == "0":
                            qr_val = str(qr).strip()
                            if qr_val:
                                pending.add(qr_val)
        except qrdb.DatabaseError as e:
            logging.error(f"Error reading pending ai_status from DB: {e}.")
        return pending
        
    def discover_assets(self) -> Dict[str, Dict[str, Any]]:
        grouped = defaultdict(lambda: {"images": {}, "building": "", "asset_type": ""})
        stats = {
            "files_scanned": 0,
            "invalid_extension": 0,
            "name_mismatch": 0,
            "non_me": 0,
            "invalid_seq": 0,
            "ignored": 0,
            "already_ai_processed": 0,
        }
        
        pending_in_db = self._get_pending_qrs()
        if pending_in_db:
            logging.info(f"DB CHECK: Found {len(pending_in_db)} assets with ai_status='0' waiting for processing.")
            
        logging.info(f"Scanning for images in: {Config.IMAGE_FOLDER}")
        if not os.path.exists(Config.IMAGE_FOLDER):
             logging.error(f"Image folder not found: {Config.IMAGE_FOLDER}")
             return {}

        for filename in sorted(os.listdir(Config.IMAGE_FOLDER)):
            stats["files_scanned"] += 1
            base, ext = os.path.splitext(filename)
            if ext.lower() not in Config.VALID_EXTS:
                stats["invalid_extension"] += 1
                continue
            match = Config.FILENAME_PATTERN.match(base)
            if not match:
                stats["name_mismatch"] += 1
                continue
            qr, building, asset_type, seq = match.groups()
            
            # Filter for ME assets specifically
            if asset_type.upper() != "ME":
                stats["non_me"] += 1
                continue
            if seq not in Config.VALID_SUFFIXES:
                stats["invalid_seq"] += 1
                continue
            if qr in self.qrs_to_ignore:
                stats["ignored"] += 1
                continue
            if qr in self.ai_processed_qrs:
                stats["already_ai_processed"] += 1
                continue
            
            grouped[qr]["building"] = building
            grouped[qr]["asset_type"] = asset_type.upper()
            grouped[qr]["images"][seq] = os.path.join(Config.IMAGE_FOLDER, filename)
            
        discovered_qrs = set(grouped.keys())
        missing_images = pending_in_db - discovered_qrs - self.qrs_to_ignore - self.ai_processed_qrs
        if missing_images:
            logging.warning(f"MISSING IMAGES: {len(missing_images)} QRs have ai_status='0' in DB but NO matching 'ME' photos were found in {Config.IMAGE_FOLDER}.")
            logging.warning(f"Example missing QRs: {list(missing_images)[:10]}")

        logging.info(
            "ME discovery stats: scanned=%d, accepted_qrs=%d, non_me=%d, already_ai_processed=%d, ignored=%d, "
            "invalid_name=%d, invalid_ext=%d, invalid_seq=%d",
            stats["files_scanned"],
            len(grouped),
            stats["non_me"],
            stats["already_ai_processed"],
            stats["ignored"],
            stats["name_mismatch"],
            stats["invalid_extension"],
            stats["invalid_seq"],
        )
        logging.info(f"Found {len(grouped)} new assets to process.")
        return grouped

    def run(self):
        """Main execution flow: discover assets and process them concurrently."""
        assets = self.discover_assets()
        if not assets:
            msg = "No ME assets found for AI processing. Skipping ME run."
            logging.info(msg)
            print(msg)
            return

        saved_count = 0
        no_output_count = 0
        error_count = 0
        saved_items = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            future_to_qr = {executor.submit(self.process_single_asset, qr, info): qr for qr, info in assets.items()}
            for future in as_completed(future_to_qr):
                qr = future_to_qr[future]
                asset_status = "unknown"
                asset_reason = "unknown"
                json_path = ""
                try:
                    output_data, asset_reason = future.result()
                    if output_data:
                        save_result = self._save_result(output_data)
                        save_reason = save_result.get("reason", "unknown")
                        asset_reason = f"{asset_reason};save={save_reason}"
                        json_path = save_result.get("path", "")

                        if save_result.get("saved"):
                            saved_count += 1
                            asset_status = "saved"
                            logging.info(
                                f"Successfully processed and saved asset QR: {qr} "
                                f"(Completeness: {output_data['completeness_score']:.0f}%)"
                            )
                            if save_reason != "saved_and_ai_status_updated":
                                logging.warning(f"JSON saved for asset QR {qr} with warning: {save_reason}")

                            # Store details for summary
                            timestamp_log = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            saved_items.append(
                                f"- QR: {qr} | ME | {timestamp_log} | Building: {assets[qr].get('building', '')}"
                            )
                        else:
                            no_output_count += 1
                            asset_status = "no_json"
                            logging.warning(f"No JSON retained for asset QR {qr}: {save_reason}")
                    else:
                        no_output_count += 1
                        asset_status = "no_json"
                        logging.warning(
                            f"No JSON generated for asset QR {qr}: {asset_reason}"
                        )

                except QuotaExceeded:
                    error_count += 1
                    asset_status = STATUS_QUOTA
                    asset_reason = "llm_quota_exceeded"
                    images_dict = assets[qr].get("images", {})
                    append_manual_review(
                        Config.MANUAL_REVIEW_QUEUE_FILE,
                        qr=qr, building=str(assets[qr].get("building", "")), asset_type="ME",
                        image_paths=[p for p in images_dict.values() if p],
                        failure_reason="llm_quota_exceeded",
                        missing_fields=list(Config.EXPECTED_FIELDS),
                        attempted_models=list(get_llm_model_plan(Config)),
                        status=STATUS_QUOTA,
                    )
                    logging.info(f"[{qr}] Final status: {STATUS_QUOTA}")
                except AuthFailed:
                    error_count += 1
                    asset_status = STATUS_AUTH
                    asset_reason = "llm_auth_failed"
                    images_dict = assets[qr].get("images", {})
                    append_manual_review(
                        Config.MANUAL_REVIEW_QUEUE_FILE,
                        qr=qr, building=str(assets[qr].get("building", "")), asset_type="ME",
                        image_paths=[p for p in images_dict.values() if p],
                        failure_reason="llm_auth_failed",
                        missing_fields=list(Config.EXPECTED_FIELDS),
                        attempted_models=list(get_llm_model_plan(Config)),
                        status=STATUS_AUTH,
                    )
                    logging.info(f"[{qr}] Final status: {STATUS_AUTH}")
                except Exception as e:
                    error_count += 1
                    asset_status = "error"
                    asset_reason = f"exception:{e}"
                    logging.error(f"Failed to process asset QR {qr}: {e}", exc_info=True)
                finally:
                    logging.info(
                        f"[{self.run_id}] ME_ASSET_RESULT qr={qr} status={asset_status} "
                        f"reason={asset_reason} json_path={json_path}"
                    )
        
        print(
            f"\n--- SUMMARY ---\nTotal assets processed: {len(assets)}\nSuccessfully saved: {saved_count}\n"
            f"No JSON generated: {no_output_count}\nErrors: {error_count}"
        )
        logging.info(
            "[%s] ME run summary: total=%d, saved=%d, no_json=%d, errors=%d",
            self.run_id,
            len(assets),
            saved_count,
            no_output_count,
            error_count,
        )
        
        # --- NEW: Print detailed list of saved items for consistency ---
        if saved_items:
            print("")
            for line in saved_items:
                print(line)

    def process_single_asset(self, qr: str, info: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        """Processes all images for a single asset using a single multi-image LLM call + validators."""
        logging.info(f"[{self.run_id}] ME_ASSET_START qr={qr} images={sorted(info.get('images', {}).keys())}")
        self._last_model_serial_collision = False

        # Skip-if-exists guard: avoid duplicate billing for already-processed assets.
        if not Config.OVERWRITE_EXISTING_JSON:
            existing_path = self._find_existing_json_for_qr(qr, "ME")
            if existing_path and os.path.exists(existing_path):
                repair_payload = self._build_existing_tsbc_repair_payload(qr, info, existing_path)
                if repair_payload:
                    return repair_payload, "repaired_existing_tsbc"
                repair_payload = self._build_existing_manufacturer_repair_payload(qr, info, existing_path)
                if repair_payload:
                    return repair_payload, "repaired_existing_manufacturer"
                logging.info(f"[{qr}] Final status: {STATUS_SKIPPED_EXISTS} (existing JSON found, overwrite disabled).")
                return None, STATUS_SKIPPED_EXISTS

        has_tsbc_source = self._has_tsbc_source_image(info.get("images", {}))
        has_nameplate_source = self._has_nameplate_source_image(info.get("images", {}))
        has_ubc_source = self._is_readable_source_path(info.get("images", {}).get("1", ""))

        # --- 1. LLM Primary Extraction ---
        try:
            result = self._llm_multi_image(qr, info, has_nameplate_source, has_tsbc_source)
        except QuotaExceeded:
            images_dict = info.get("images", {}) if isinstance(info.get("images"), dict) else {}
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=str(info.get("building", "")), asset_type="ME",
                image_paths=[p for p in images_dict.values() if p],
                failure_reason="llm_quota_exceeded",
                missing_fields=list(Config.EXPECTED_FIELDS),
                attempted_models=list(get_llm_model_plan(Config)),
                status=STATUS_QUOTA,
            )
            logging.info(f"[{qr}] Final status: {STATUS_QUOTA}")
            return None, STATUS_QUOTA
        except AuthFailed:
            images_dict = info.get("images", {}) if isinstance(info.get("images"), dict) else {}
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=str(info.get("building", "")), asset_type="ME",
                image_paths=[p for p in images_dict.values() if p],
                failure_reason="llm_auth_failed",
                missing_fields=list(Config.EXPECTED_FIELDS),
                attempted_models=list(get_llm_model_plan(Config)),
                status=STATUS_AUTH,
            )
            logging.info(f"[{qr}] Final status: {STATUS_AUTH}")
            return None, STATUS_AUTH
        if not result:
            return None, "llm_multi_image_failed"

        data = result.get("extracted_data", {})
        raw_model = str(data.get("Model", "")).strip()
        raw_serial = str(data.get("Serial Number", "")).strip()
        raw_manufacturer = data.get("Manufacturer", "")
        llm_model = self._normalize_model_candidate(raw_model, raw_manufacturer)
        llm_serial = self._normalize_serial_candidate(raw_serial, raw_manufacturer)
        raw_year = str(data.get("Year", "")).strip()
        raw_ubc_tag = data.get("UBC Tag", "")
        raw_tsbc = self._normalize_tsbc_unit_no(data.get("Technical Safety BC", ""))
        if has_tsbc_source and not raw_tsbc:
            reread_tsbc = self._reread_tsbc_from_unit_no_llm(qr, info.get("images", {}))
            if reread_tsbc:
                raw_tsbc = reread_tsbc
        
        if not has_nameplate_source:
            if raw_model or raw_serial or str(raw_year).strip():
                logging.info(f"[{qr}] Dropping Model/Serial/Year because seq 0 (Asset Plate) image is missing/invalid.")
            raw_model = ""
            raw_serial = ""
            llm_model = ""
            llm_serial = ""
            raw_year = ""
        if raw_ubc_tag and not has_ubc_source:
            logging.info(f"[{qr}] Dropping UBC Tag value '{raw_ubc_tag}' because seq 1 image is missing/invalid.")
            raw_ubc_tag = ""
        if raw_tsbc and not has_tsbc_source:
            logging.info(f"[{qr}] Dropping Technical Safety BC value '{raw_tsbc}' because seq 3 image is missing/invalid.")
            raw_tsbc = ""

        llm_manufacturer_no_ocr = self._normalize_manufacturer_with_context(
            raw_manufacturer,
            info.get("images", {}),
            allow_ocr=False,
        )
        is_williams_candidate = bool(
            has_nameplate_source
            and (
                "WILLIAMS" in (raw_manufacturer or "").upper()
                or (llm_manufacturer_no_ocr or "").strip().lower() == "williams"
                or re.match(r"^\s*HH[\sA-Z0-9\-\/\.]{4,}", llm_model or "", flags=re.IGNORECASE)
            )
        )
        llm_ubc_no_ocr = self._normalize_ubc_tag_with_context(
            raw_ubc_tag,
            info.get("images", {}),
            allow_ocr=False,
        )

        has_uncertain_chars = "?" in raw_model or "?" in raw_serial
        if Config.OCR_MODE == "off":
            run_ocr_model_serial = False
            run_ocr_manufacturer = False
            run_ocr_ubc = False
        elif Config.OCR_MODE == "full":
            run_ocr_model_serial = has_nameplate_source
            run_ocr_manufacturer = True
            run_ocr_ubc = True
        else:
            # Fallback-only OCR policy:
            # OCR runs ONLY when AI models fail or return uncertain values.
            run_ocr_model_serial = has_nameplate_source and (
                not llm_model or not llm_serial or has_uncertain_chars or is_williams_candidate
            )
            run_ocr_manufacturer = not bool(llm_manufacturer_no_ocr)
            run_ocr_ubc = not bool(llm_ubc_no_ocr)
        if Config.ME_UBC_CONSENSUS_ENABLED:
            # The dedicated local validator below is an independent vote; do
            # not fold ordinary OCR into the primary model candidate first.
            run_ocr_ubc = False
        ocr_assisted_rescue = bool(
            Config.OCR_MODE != "off" and (run_ocr_model_serial or run_ocr_manufacturer or run_ocr_ubc)
        )

        llm_cleaned = {
            "Manufacturer": self._normalize_manufacturer_with_context(
                raw_manufacturer,
                info.get("images", {}),
                allow_ocr=run_ocr_manufacturer,
            ),
            "Model": raw_model,
            "Serial Number": raw_serial,
            "Year": raw_year,
            "UBC Tag": self._normalize_ubc_tag_with_context(
                raw_ubc_tag,
                info.get("images", {}),
                allow_ocr=run_ocr_ubc,
            ),
            "Technical Safety BC": raw_tsbc,
        }
        raw_fields = {
            "Manufacturer": raw_manufacturer,
            "Model": raw_model,
            "Serial Number": raw_serial,
            "Year": raw_year,
            "UBC Tag": raw_ubc_tag,
            "Technical Safety BC": raw_tsbc,
        }
        primary_confidence_scores = result.get("confidence_scores", {})
        if not isinstance(primary_confidence_scores, dict):
            primary_confidence_scores = {}
        final_ubc_tag, ubc_consensus = self._maybe_resolve_ubc_consensus(
            qr=qr,
            primary_tag=llm_cleaned.get("UBC Tag", ""),
            primary_confidence=primary_confidence_scores.get("UBC Tag", 0),
            images=info.get("images", {}),
        )
        if Config.ME_UBC_CONSENSUS_ENABLED:
            llm_cleaned["UBC Tag"] = final_ubc_tag

        if Config.UI_PARITY_MODE:
            merged_struct = self._build_ui_parity_struct(
                qr=qr,
                info=info,
                llm_cleaned=llm_cleaned,
                raw_manufacturer=raw_manufacturer,
                llm_model=raw_model,
                raw_year=raw_year,
                has_nameplate_source=has_nameplate_source,
                has_tsbc_source=has_tsbc_source,
            )
            pressure_vessel_evidence = self._collect_nameplate_evidence_texts(
                info.get("images", {})
            ) if has_nameplate_source else []
            pressure_vessel_unlabeled_serial = False
            if (
                _compact_lookup_key(merged_struct.get("Manufacturer", "")) == "TACO"
                and merged_struct.get("Serial Number")
                and not self._has_serial_label_evidence(
                    str(merged_struct.get("Serial Number", "")),
                    info.get("images", {}),
                    manufacturer_hint=merged_struct.get("Manufacturer", ""),
                )
            ):
                if self._is_pressure_vessel_unlabeled_serial_candidate(
                    str(merged_struct.get("Serial Number", "")),
                    pressure_vessel_evidence,
                    vision_confirmed_context=bool(
                        getattr(self, "_last_pressure_vessel_context_from_reread", False)
                    ),
                ):
                    pressure_vessel_unlabeled_serial = True
                    self._last_pressure_vessel_unlabeled_serial = True
                    logging.warning(
                        "[%s] Accepting Taco pressure-vessel top identifier '%s' as "
                        "Serial Number with reduced confidence.",
                        qr,
                        merged_struct.get("Serial Number", ""),
                    )
                else:
                    logging.warning(
                        "[%s] Dropping Taco Serial Number '%s' because seq 0 has neither "
                        "explicit Serial/Serial No./S/N evidence nor a qualified "
                        "pressure-vessel top identifier.",
                        qr,
                        merged_struct.get("Serial Number", ""),
                    )
                    merged_struct["Serial Number"] = ""
                    self._last_serial_unverified = True
            merged_struct = self._validate_and_normalize(merged_struct)
            manufacturer_corroborated = bool(
                self._canonicalize_manufacturer_candidate(merged_struct.get("Manufacturer", ""))
            )
            model_corroborated = bool(
                self._is_model_code_candidate(merged_struct.get("Model", ""), merged_struct.get("Manufacturer", ""))
                and self._has_model_label_evidence(
                    str(merged_struct.get("Model", "")),
                    info.get("images", {}),
                )
            )
            year_corroborated = bool(
                not merged_struct.get("Year")
                or not has_nameplate_source
                or self._has_year_evidence(
                    str(merged_struct.get("Year", "")),
                    info.get("images", {}),
                )
            )
            logging.info(
                f"[{qr}] ME_DECISION field=Manufacturer model_candidate='{raw_manufacturer}' "
                f"corroborated={manufacturer_corroborated} final='{merged_struct.get('Manufacturer', '')}' "
                "reason=ui_parity_model_first"
            )
            logging.info(
                f"[{qr}] ME_DECISION field=Model model_candidate='{llm_model}' "
                f"corroborated={model_corroborated} final='{merged_struct.get('Model', '')}' "
                "reason=ui_parity_model_first"
            )
            logging.info(
                f"[{qr}] ME_DECISION field=Year model_candidate='{raw_year}' "
                f"corroborated={year_corroborated} final='{merged_struct.get('Year', '')}' "
                "reason=ui_parity_model_first"
            )
            merged_score = completeness_score(
                merged_struct,
                self._me_completeness_fields(has_tsbc_source),
            )
            
            conf_scores = self._synthesize_final_confidence_scores(
                merged_struct,
                result.get("confidence_scores", {}),
                images=info.get("images", {}),
                raw_fields=raw_fields,
                has_nameplate_source=has_nameplate_source,
                has_tsbc_source=has_tsbc_source,
            )
            if ubc_consensus:
                conf_scores = self._apply_ubc_consensus_confidence(
                    conf_scores,
                    ubc_consensus,
                )
            serial_date_misread = bool(getattr(self, "_last_serial_date_misread", False))
            serial_unverified = bool(getattr(self, "_last_serial_unverified", False))
            pressure_vessel_unlabeled_serial = bool(
                getattr(self, "_last_pressure_vessel_unlabeled_serial", False)
            )
            if (
                serial_date_misread
                or serial_unverified
                or pressure_vessel_unlabeled_serial
            ) and merged_struct.get("Serial Number"):
                capped = min(
                    self._normalize_confidence_score(conf_scores.get("Serial Number", 0)), 65
                )
                conf_scores["Serial Number"] = capped
            avg_conf = self._compute_avg_ai_conf(conf_scores, has_tsbc_source=has_tsbc_source)

            return (
                {
                    "qr_code": qr,
                    "building_number": info.get("building", ""),
                    "asset_type": f"- {info.get('asset_type', 'ME').upper()}",
                    "structured_data": merged_struct,
                    "completeness_score": merged_score,
                    "confidence_scores": conf_scores,
                    "Avg_ai_conf": avg_conf,
                    "_score_context": {
                        "has_tsbc_source": has_tsbc_source,
                        "ocr_assisted_rescue": ocr_assisted_rescue,
                        "ocr_mode": Config.OCR_MODE,
                        "ubc_consensus": ubc_consensus,
                        "extra_reason_codes": (
                            (["serial_date_misread_suspected"] if serial_date_misread else [])
                            + (["serial_unverified"] if serial_unverified else [])
                            + (["pressure_vessel_unlabeled_serial"] if pressure_vessel_unlabeled_serial else [])
                            + (["model_serial_collision"] if self._last_model_serial_collision else [])
                            + list(ubc_consensus.get("reason_codes", []))
                        ),
                    },
                },
                "ready_to_save_ui_parity"
            )

        if not Config.OCR_VALIDATE_EXISTING_FIELDS:
            logging.info(
                f"[{qr}] OCR corroboration for existing AI values is disabled; "
                "OCR will be used only to fill missing fields."
            )

        # --- 2. Hybrid Ensemble (Merging LLM & Fast OCR inputs) ---
        cleaned = llm_cleaned.copy()
        ocr_serial_supported = False
        nameplate_evidence_cache: Optional[List[str]] = None

        def _ensure_nameplate_evidence_cache() -> List[str]:
            nonlocal nameplate_evidence_cache
            if nameplate_evidence_cache is None:
                nameplate_evidence_cache = self._collect_nameplate_evidence_texts(info.get("images", {}))
            return nameplate_evidence_cache
            
        if run_ocr_model_serial:
            try:
                ocr_results = self._ocr_extract_model_serial(info["images"])
                ocr_serial_supported = self._is_serial_candidate(ocr_results.get("Serial Number", ""))
                ocr_model_supported = self._is_model_code_candidate(ocr_results.get("Model", ""))
                ocr_has_strong_bundle = bool(ocr_model_supported and ocr_serial_supported)

                # Fallback-only OCR fill: do not overwrite existing AI-populated values.
                if not cleaned.get("Model") and self._is_model_code_candidate(ocr_results.get("Model", "")):
                    cleaned["Model"] = self._normalize_model_candidate(
                        ocr_results.get("Model", ""),
                        cleaned.get("Manufacturer", ""),
                    )
                    logging.info(f"[{qr}] Rescued Model '{cleaned['Model']}' using OCR nameplate fallback.")
                if not cleaned.get("Serial Number") and ocr_serial_supported:
                    cleaned["Serial Number"] = self._normalize_serial_candidate(
                        ocr_results.get("Serial Number", ""),
                        cleaned.get("Manufacturer", ""),
                    )
                    logging.info(f"[{qr}] Rescued Serial '{cleaned['Serial Number']}' using OCR nameplate fallback.")

                # Deal with previously marked uncertain characters using OCR if possible
                if "?" in cleaned.get("Model", ""):
                    cleaned["Model"] = self._resolve_uncertain_chars("Model", cleaned["Model"], ocr_results.get("Model", ""))
                if "?" in cleaned.get("Serial Number", ""):
                    cleaned["Serial Number"] = self._resolve_uncertain_chars("Serial Number", cleaned["Serial Number"], ocr_results.get("Serial Number", ""))

            except Exception as e:
                logging.warning(f"Hybrid ensemble failed, using LLM values only: {e}")
        else:
            logging.info(
                f"[{qr}] OCR model/serial enrichment skipped "
                f"(mode={Config.OCR_MODE}; ai_model_present={bool(llm_model)}; ai_serial_present={bool(llm_serial)})."
            )

        # --- 3. GUARDRAILS (Wipe invalid/hallucinated data) ---
        # Guardrail: Model
        if cleaned["Model"] and self._is_tag_like_model_candidate(cleaned["Model"]):
            logging.warning(f"[Model] Discarding tag-like value '{cleaned['Model']}'")
            cleaned["Model"] = ""

        if cleaned["Model"] and cleaned["UBC Tag"] and cleaned["UBC Tag"] in cleaned["Model"]:
            cleaned["Model"] = cleaned["Model"].replace(cleaned["UBC Tag"], "").strip()

        # Guardrail: Serial Number
        if self._is_qr_like_serial(cleaned.get("Serial Number", ""), qr):
            logging.warning(f"[{qr}] Removing Serial Number '{cleaned.get('Serial Number', '')}' because it matches QR code pattern.")
            cleaned["Serial Number"] = ""

        if looks_like_date_misread_serial(
            str(cleaned.get("Serial Number", "")), year_hint=str(cleaned.get("Year", ""))
        ):
            logging.warning(
                f"[{qr}] Removing Serial Number '{cleaned.get('Serial Number', '')}' "
                "because it reads as a manufacturing date (possibly rotated/upside-down)."
            )
            cleaned["Serial Number"] = ""

        # Guardrail: avoid inferred Year=2000 from model suffix "...000" unless OCR evidence supports year 2000.
        if (
            has_nameplate_source
            and str(cleaned.get("Year", "")).strip() == "2000"
            and re.sub(r"[^A-Z0-9]", "", str(cleaned.get("Model", "")).upper()).endswith("000")
            and not self._has_year_evidence(
                "2000",
                info.get("images", {}),
                evidence_texts=_ensure_nameplate_evidence_cache(),
            )
        ):
            logging.warning(
                f"[{qr}] Dropping likely inferred Year '2000' (no OCR year evidence; model suffix ends with '000')."
            )
            cleaned["Year"] = ""
            
        # Rescuer: If Year is missing (or was just dropped) and we have nameplate OCR, 
        # try to parse the MM/YY Williams-style production date code format directly from text.
        if not cleaned.get("Year") and has_nameplate_source:
            for text in _ensure_nameplate_evidence_cache():
                # Forgiving regex for 'MM / YY', 'MM-YY', or 'MM\YY' with optional spaces
                match = re.search(r"(?:^|[\s:;.,!|\[\]\(\)-])(0[1-9]|1[0-2])\s*[/\\-]\s*(\d{2})(?:[\s:;.,!|\[\]\(\)-]|$)", text)
                if match:
                    yy = int(match.group(2))
                    rescued_year = f"20{yy:02d}" if yy < 50 else f"19{yy:02d}"
                    cleaned["Year"] = rescued_year
                    logging.info(f"[{qr}] Rescued Year '{rescued_year}' from native OCR date code '{match.group(0)}'.")
                    break
            
        if Config.OCR_VALIDATE_EXISTING_FIELDS:
            suspicious_serial = re.sub(r"[^A-Z0-9]", "", str(cleaned.get("Serial Number", "")).upper())
            if (
                suspicious_serial
                and suspicious_serial.isdigit()
                and len(suspicious_serial) >= 6
                and "-" not in str(cleaned.get("Serial Number", ""))
                and not cleaned.get("Model")
                and not ocr_serial_supported
                and (
                    run_ocr_model_serial
                    or not self._has_serial_label_evidence(
                        str(cleaned.get("Serial Number", "")),
                        info.get("images", {}),
                        evidence_texts=_ensure_nameplate_evidence_cache(),
                        manufacturer_hint=cleaned.get("Manufacturer", ""),
                    )
                )
            ):
                logging.warning(f"[{qr}] Dropping unsupported digits-only Serial Number '{cleaned.get('Serial Number', '')}' because no serial-label OCR evidence was found on seq 0.")
                cleaned["Serial Number"] = ""

            llm_serial_compact = re.sub(r"[^A-Z0-9]", "", str(llm_cleaned.get("Serial Number", "")).upper())
            cleaned_serial_compact = re.sub(r"[^A-Z0-9]", "", str(cleaned.get("Serial Number", "")).upper())
            if (
                Config.SIMPLE_MODE
                and cleaned.get("Serial Number")
                and not ocr_serial_supported
                and llm_serial_compact
                and cleaned_serial_compact == llm_serial_compact
                and not self._has_serial_label_evidence(
                    str(cleaned.get("Serial Number", "")),
                    info.get("images", {}),
                    evidence_texts=_ensure_nameplate_evidence_cache(),
                    manufacturer_hint=cleaned.get("Manufacturer", ""),
                )
            ):
                serial_candidate = self._best_serial_candidate_from_texts(_ensure_nameplate_evidence_cache())
                if serial_candidate:
                    if self._normalize_serial_candidate(serial_candidate) != self._normalize_serial_candidate(
                        str(cleaned.get("Serial Number", ""))
                    ):
                        logging.warning(
                            f"[{qr}] Replacing uncorroborated Serial Number '{cleaned.get('Serial Number', '')}' "
                            f"with OCR-supported candidate '{serial_candidate}'."
                        )
                    cleaned["Serial Number"] = serial_candidate
                else:
                    logging.warning(
                        f"[{qr}] Dropping unsupported Serial Number '{cleaned.get('Serial Number', '')}' "
                        "because OCR could not corroborate it from serial-labeled text on seq 0."
                    )
                    cleaned["Serial Number"] = ""

            # Guardrail: Year
            if (
                Config.SIMPLE_MODE
                and has_nameplate_source
                and cleaned.get("Year")
                and not self._has_year_evidence(
                    str(cleaned.get("Year", "")),
                    info.get("images", {}),
                    evidence_texts=_ensure_nameplate_evidence_cache(),
                )
            ):
                year_candidate = self._best_year_candidate_from_texts(_ensure_nameplate_evidence_cache())
                if year_candidate:
                    if normalize_year(year_candidate) != normalize_year(str(cleaned.get("Year", ""))):
                        logging.warning(
                            f"[{qr}] Replacing uncorroborated Year '{cleaned.get('Year', '')}' "
                            f"with OCR-supported candidate '{year_candidate}'."
                        )
                    cleaned["Year"] = year_candidate
                else:
                    # A reread is still a model output: accept it only when the
                    # rereread year is visible in the seq-0 evidence, otherwise a
                    # model-inferred year (e.g. decoded from a serial letter code)
                    # would be saved even though no year is printed on the plate.
                    reread_year = self._reread_year_from_nameplate_llm(qr, info.get("images", {}))
                    if reread_year and self._has_year_evidence(
                        reread_year,
                        info.get("images", {}),
                        evidence_texts=_ensure_nameplate_evidence_cache(),
                    ):
                        logging.warning(
                            f"[{qr}] Replacing uncorroborated Year '{cleaned.get('Year', '')}' "
                            f"with targeted seq0 model reread '{reread_year}'."
                        )
                        cleaned["Year"] = reread_year
                    else:
                        logging.warning(
                            f"[{qr}] Dropping unsupported Year '{cleaned.get('Year', '')}' "
                            "because neither seq 0 OCR nor targeted seq0 reread could corroborate it."
                        )
                        cleaned["Year"] = ""

        # --- 4. RESCUERS (Patch missing/wiped fields with OCR) ---
        if Config.OCR_MODE != "off":
            if not cleaned.get("Model"):
                res_model = self._fallback_model_from_ocr(info.get("images", {}))
                if res_model:
                    cleaned["Model"] = res_model
                    logging.info(f"[{qr}] Rescued Model '{res_model}' using OCR fallback.")
                    
            if not cleaned.get("Serial Number"):
                fast_ocr = self._ocr_extract_model_serial_fast(info.get("images", {}))
                if fast_ocr.get("Serial Number"):
                    cleaned["Serial Number"] = fast_ocr["Serial Number"]
                    logging.info(f"[{qr}] Rescued Serial '{cleaned['Serial Number']}' using OCR fallback.")
                    
            # Always attempt to rescue Year if it's missing (helps catch tricky MM/YY dates LLM misses)
            if not cleaned.get("Year"):
                rescued_year = self._fallback_year_from_ocr(info.get("images", {}))
                if rescued_year:
                    cleaned["Year"] = rescued_year
                    logging.info(f"[{qr}] Rescued Year '{rescued_year}' using OCR fallback.")

        # --- 5. Final Heuristics ---
        if cleaned["Model"] and "FCU" in cleaned["Model"] and "MI" not in cleaned["Model"]:
            if self._ocr_shows_mi(info.get("images", {})):
                cleaned["Model"] = f"{cleaned['Model']} MI".strip()

        # --------------------------------------------------------------------------------
        # VERSION 20 SMART UI OVERRIDE:
        # If the script is running this asset, it is because ai_status=0 in the DB.
        # Since the user specifically flagged this asset for a clean run, we DO NOT
        # load or merge with any existing JSON. We output a 100% fresh extraction!
        # --------------------------------------------------------------------------------
        # --------------------------------------------------------------------------------
        # VERSION 29 MASTER VALIDATORS
        # --------------------------------------------------------------------------------
        merged_struct = cleaned.copy()
        manufacturer_lower = merged_struct["Manufacturer"].lower()
        
        # 1. UBC Tag Formatting Precision (The "FC-4C.012" Enforcer)
        if ubc_consensus:
            merged_struct["UBC Tag"] = str(
                ubc_consensus.get("final_tag") or ""
            )
        else:
            raw_tag = merged_struct["UBC Tag"].upper().replace(" ", "")
            if (
                re.fullmatch(r"\d[A-Z][\.\-]\d{2,4}", raw_tag)
                or re.fullmatch(r"[A-Z]\d[\.\-]\d{2,4}", raw_tag)
                or re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d)\d{2,4}", raw_tag)
            ):
                if re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d)\d{2,4}", raw_tag):
                    raw_tag = f"{raw_tag[:2]}.{raw_tag[2:]}"
                if "-" in raw_tag:
                    raw_tag = raw_tag.replace("-", ".")
                merged_struct["UBC Tag"] = f"FC-{raw_tag}"
            else:
                merged_struct["UBC Tag"] = raw_tag

        # 2. Brand-Gated Hallucination Filters
        # Only apply '1803' / 'FCH' wipe if the brand is Williams.
        if "williams" in manufacturer_lower:
            if "1803" in merged_struct["Model"] or "FCH" in merged_struct["Model"]:
                merged_struct["Model"] = ""
            if merged_struct["Serial Number"] == "1803" or "H03" in merged_struct["Serial Number"]:
                merged_struct["Serial Number"] = ""

        # 3. QR ID Guard: Global (Ensure Serial isn't the internal QR code ID)
        if merged_struct["Serial Number"] and (qr in merged_struct["Serial Number"]):
            merged_struct["Serial Number"] = ""
        merged_struct = self._validate_and_normalize(merged_struct)

        manufacturer_reason = "missing"
        if merged_struct.get("Manufacturer"):
            manufacturer_reason = (
                "accepted_model"
                if self._canonicalize_manufacturer_candidate(raw_manufacturer)
                else "rescued_or_corrected"
            )
        model_reason = "missing" if not merged_struct.get("Model") else (
            "accepted_model"
            if self._normalize_model_candidate(llm_model, merged_struct.get("Manufacturer", ""))
            == self._normalize_model_candidate(merged_struct.get("Model", ""), merged_struct.get("Manufacturer", ""))
            else "rescued_or_corrected"
        )
        year_reason = "missing" if not merged_struct.get("Year") else (
            "accepted_model"
            if self._normalize_year_flexible(raw_year) == self._normalize_year_flexible(merged_struct.get("Year", ""))
            else "rescued_or_corrected"
        )
        manufacturer_corroborated = bool(
            self._canonicalize_manufacturer_candidate(merged_struct.get("Manufacturer", ""))
        )
        model_corroborated = bool(
            self._is_model_code_candidate(merged_struct.get("Model", ""), merged_struct.get("Manufacturer", ""))
            and self._has_model_label_evidence(
                str(merged_struct.get("Model", "")),
                info.get("images", {}),
                evidence_texts=_ensure_nameplate_evidence_cache(),
            )
        )
        year_corroborated = bool(
            not merged_struct.get("Year")
            or not has_nameplate_source
            or self._has_year_evidence(
                str(merged_struct.get("Year", "")),
                info.get("images", {}),
                evidence_texts=_ensure_nameplate_evidence_cache(),
            )
        )
        logging.info(
            f"[{qr}] ME_DECISION field=Manufacturer model_candidate='{raw_manufacturer}' "
            f"corroborated={manufacturer_corroborated} final='{merged_struct.get('Manufacturer', '')}' "
            f"reason={manufacturer_reason}"
        )
        logging.info(
            f"[{qr}] ME_DECISION field=Model model_candidate='{llm_model}' "
            f"corroborated={model_corroborated} final='{merged_struct.get('Model', '')}' "
            f"reason={model_reason}"
        )
        logging.info(
            f"[{qr}] ME_DECISION field=Year model_candidate='{raw_year}' "
            f"corroborated={year_corroborated} final='{merged_struct.get('Year', '')}' "
            f"reason={year_reason}"
        )

        merged_score = completeness_score(
            merged_struct,
            self._me_completeness_fields(has_tsbc_source),
        )
        
        conf_scores = self._synthesize_final_confidence_scores(
            merged_struct,
            result.get("confidence_scores", {}),
            images=info.get("images", {}),
            raw_fields=raw_fields,
            has_nameplate_source=has_nameplate_source,
            has_tsbc_source=has_tsbc_source,
        )
        if ubc_consensus:
            conf_scores = self._apply_ubc_consensus_confidence(
                conf_scores,
                ubc_consensus,
            )
        avg_conf = self._compute_avg_ai_conf(conf_scores, has_tsbc_source=has_tsbc_source)

        return (
            {
                "qr_code": qr,
                "building_number": info.get("building", ""),
                "asset_type": f"- {info.get('asset_type', 'ME').upper()}",
                "structured_data": merged_struct,
                "completeness_score": merged_score,
                "confidence_scores": conf_scores,
                "Avg_ai_conf": avg_conf,
                "_score_context": {
                    "has_tsbc_source": has_tsbc_source,
                    "ocr_assisted_rescue": ocr_assisted_rescue,
                    "ocr_mode": Config.OCR_MODE,
                    "ubc_consensus": ubc_consensus,
                    "extra_reason_codes": (
                        (["model_serial_collision"] if self._last_model_serial_collision else [])
                        + list(ubc_consensus.get("reason_codes", []))
                    ),
                },
            },
            "ready_to_save_clean_overwrite"
        )

    def _build_ui_parity_struct(
        self,
        qr: str,
        info: Dict[str, Any],
        llm_cleaned: Dict[str, str],
        raw_manufacturer: str,
        llm_model: str,
        raw_year: str,
        has_nameplate_source: bool,
        has_tsbc_source: bool,
    ) -> Dict[str, str]:
        """
        UI-parity mapping:
        - Keep model extraction as primary truth.
        - Apply minimal normalization/canonicalization.
        - Use focused rereads only to fill missing year/manufacturer.
        """
        merged = dict(llm_cleaned or {})
        images = info.get("images", {})

        # Flag date-shaped serial misreads (e.g., "8102/90" = "09/2018" upside-down)
        # so the save path can add a manual-review reason code and cap confidence.
        self._last_serial_date_misread = looks_like_date_misread_serial(
            str(merged.get("Serial Number", "")),
            year_hint=str(merged.get("Year", "")),
        )
        self._last_serial_unverified = False
        self._last_pressure_vessel_unlabeled_serial = False
        self._last_pressure_vessel_context_from_reread = False
        self._last_model_serial_collision = False

        if not merged.get("Manufacturer"):
            merged["Manufacturer"] = self._normalize_manufacturer_with_context(
                raw_manufacturer, images, allow_ocr=True
            )
        if not merged.get("Manufacturer"):
            merged["Manufacturer"] = self._infer_manufacturer_from_model(
                merged.get("Model", "")
            )
        if not merged.get("Manufacturer") and has_nameplate_source:
            merged["Manufacturer"] = self._reread_manufacturer_from_nameplate_llm(
                qr, images
            )
        evidence_texts: Optional[List[str]] = None
        if has_nameplate_source:
            evidence_texts = self._collect_nameplate_evidence_texts(images)

            manufacturer_hint = (merged.get("Manufacturer", "") or "").strip().lower()
            is_williams_like = "williams" in manufacturer_hint or any(
                "WILLIAMS" in (txt or "").upper() for txt in (evidence_texts or [])[:10]
            )
            is_taco_like = "taco" in manufacturer_hint or any(
                "TACO" in (txt or "").upper() for txt in (evidence_texts or [])[:10]
            )
            is_republic_like = "republic" in manufacturer_hint or any(
                ("REPUBLIC" in (txt or "").upper())
                or (
                    "SERIAL" in (txt or "").upper()
                    and "TYPE" in (txt or "").upper()
                    and "YEAR" in (txt or "").upper()
                    and "PUMPING" in (txt or "").upper()
                )
                for txt in (evidence_texts or [])[:12]
            )
            is_greenheck_like = "greenheck" in manufacturer_hint or any(
                "GREENHECK" in (txt or "").upper() for txt in (evidence_texts or [])[:10]
            )
            is_rheem_ruud_water_heater_like = self._is_rheem_ruud_water_heater_family(
                merged.get("Manufacturer", "") or raw_manufacturer,
                evidence_texts=evidence_texts,
                merged_fields=merged,
            )
            strict_seq0_reread_family = (
                is_republic_like
                or is_taco_like
                or is_greenheck_like
                or is_rheem_ruud_water_heater_like
            )
            require_direct_model_serial_evidence = is_rheem_ruud_water_heater_like

            def _year_from_serial_date_pattern(texts: List[str]) -> str:
                for text in texts:
                    upper = (text or "").upper().replace("O", "0")
                    m = re.search(
                        r"\b\d{4,8}\b\D{0,16}(?:0?[1-9]|1[0-2])\s*[/\\-]\s*([0-9]{2})\b",
                        upper,
                    )
                    if not m:
                        continue
                    yy = int(m.group(1))
                    if 0 <= yy <= 26:
                        return f"20{yy:02d}"
                    if 50 <= yy <= 99:
                        return f"19{yy:02d}"
                return ""

            def _model_acceptable(value: str) -> bool:
                model_value = self._normalize_model_candidate(value, merged.get("Manufacturer", ""))
                if not model_value:
                    return False
                model_compact = re.sub(r"[^A-Z0-9]", "", model_value.upper())
                generic_ok = self._is_model_code_candidate(model_value, merged.get("Manufacturer", "")) or bool(
                    re.fullmatch(r"[A-Z]{1,4}\d{1,4}[A-Z]?", model_compact)
                )
                if not generic_ok:
                    return False
                if is_williams_like:
                    return bool(re.fullmatch(r"HH?[A-Z0-9]{8,24}", model_compact))
                return True

            def _serial_acceptable(value: str) -> bool:
                serial_value = self._normalize_serial_candidate(
                    value or "",
                    "Taco" if is_taco_like else "",
                )
                if not serial_value or not self._is_serial_candidate(serial_value):
                    return False
                if looks_like_date_misread_serial(
                    serial_value, year_hint=str(merged.get("Year", ""))
                ):
                    return False
                if is_williams_like:
                    compact = re.sub(r"[^A-Z0-9]", "", serial_value.upper())
                    return bool(compact.isdigit() and 5 <= len(compact) <= 10)
                if is_taco_like:
                    compact = re.sub(r"[^A-Z0-9]", "", serial_value.upper())
                    if compact.isdigit() and len(compact) >= 8 and "/" not in serial_value:
                        return False
                return True

            model_supported = bool(
                merged.get("Model")
                and self._has_model_label_evidence(
                    str(merged.get("Model", "")),
                    images,
                    evidence_texts=evidence_texts,
                )
            )
            serial_supported = bool(
                merged.get("Serial Number")
                and self._has_serial_label_evidence(
                    str(merged.get("Serial Number", "")),
                    images,
                    evidence_texts=evidence_texts,
                    manufacturer_hint=merged.get("Manufacturer", ""),
                )
            )
            initial_model_serial_collision = self._model_serial_values_collide(
                merged.get("Model", ""),
                merged.get("Serial Number", ""),
            )
            if initial_model_serial_collision:
                self._last_model_serial_collision = True
                logging.warning(
                    "[%s] Initial Model/Serial collision for value '%s'; "
                    "forcing a model reread/OCR rescue.",
                    qr,
                    merged.get("Model", ""),
                )
            current_model_compact = re.sub(
                r"[^A-Z0-9]",
                "",
                str(merged.get("Model", "")).upper(),
            )
            long_model_needs_corroboration = bool(
                len(current_model_compact) > 32 and not model_supported
            )
            model_weak = (
                initial_model_serial_collision
                or long_model_needs_corroboration
                or not _model_acceptable(merged.get("Model", ""))
            ) or (
                strict_seq0_reread_family and merged.get("Model") and not model_supported
            )
            serial_weak = not _serial_acceptable(merged.get("Serial Number", "")) or (
                strict_seq0_reread_family and merged.get("Serial Number") and not serial_supported
            )

            family_reread_model = ""
            family_reread_serial = ""

            if model_weak and evidence_texts and (
                initial_model_serial_collision or len(current_model_compact) > 32
            ):
                labeled_models: List[str] = []
                for text in evidence_texts:
                    for candidate in self._model_candidates_near_label(
                        text,
                        merged.get("Manufacturer", ""),
                    ):
                        if (
                            _model_acceptable(candidate)
                            and not self._model_serial_values_collide(
                                candidate,
                                merged.get("Serial Number", ""),
                            )
                        ):
                            labeled_models.append(candidate)
                if labeled_models:
                    def _distance_from_current(candidate: str) -> Tuple[int, int]:
                        compact = re.sub(r"[^A-Z0-9]", "", candidate.upper())
                        mismatch_count = sum(
                            left != right
                            for left, right in zip(current_model_compact, compact)
                        ) + abs(len(current_model_compact) - len(compact))
                        return mismatch_count, len(compact)

                    rescued_model = min(
                        dict.fromkeys(labeled_models),
                        key=_distance_from_current,
                    )
                    rescued_compact = re.sub(
                        r"[^A-Z0-9]",
                        "",
                        rescued_model.upper(),
                    )
                    normalized_current_model = self._normalize_model_candidate(
                        merged.get("Model", ""),
                        merged.get("Manufacturer", ""),
                    )
                    # Fast OCR can trim one or two trailing zeroes from long,
                    # zero-padded OEM configuration strings. When the primary
                    # value and label-local OCR are otherwise identical, retain
                    # the longer corroborated form instead of replacing it with
                    # the truncated read.
                    if (
                        len(current_model_compact) > 32
                        and normalized_current_model
                        and current_model_compact.startswith(rescued_compact)
                        and 0 < len(current_model_compact) - len(rescued_compact) <= 2
                        and set(current_model_compact[len(rescued_compact):]) == {"0"}
                    ):
                        rescued_model = normalized_current_model
                    logging.info(
                        "[%s] Recovered Model '%s' from label-local seq-0 OCR "
                        "before targeted reread (previous='%s').",
                        qr,
                        rescued_model,
                        merged.get("Model", ""),
                    )
                    merged["Model"] = rescued_model
                    model_supported = True
                    model_weak = False

            if model_weak or serial_weak:
                # Prefer targeted model reread from seq-0 nameplate before OCR heuristics.
                reread_ms = self._reread_model_serial_from_nameplate_llm(
                    qr,
                    images,
                    merged.get("Manufacturer", ""),
                )
                if model_weak:
                    reread_model = self._normalize_model_candidate(
                        reread_ms.get("Model", ""),
                        merged.get("Manufacturer", ""),
                    )
                    family_reread_model = reread_model
                    if reread_model and _model_acceptable(reread_model):
                        merged["Model"] = reread_model
                        if strict_seq0_reread_family:
                            model_weak = False
                if serial_weak:
                    reread_serial = self._normalize_serial_candidate(
                        reread_ms.get("Serial Number", ""),
                        merged.get("Manufacturer", ""),
                    )
                    family_reread_serial = reread_serial
                    if reread_serial and _serial_acceptable(reread_serial):
                        merged["Serial Number"] = reread_serial
                        if strict_seq0_reread_family:
                            serial_weak = False

                model_supported = bool(
                    merged.get("Model")
                    and self._has_model_label_evidence(
                        str(merged.get("Model", "")),
                        images,
                        evidence_texts=evidence_texts,
                    )
                )
                serial_supported = bool(
                    merged.get("Serial Number")
                    and self._has_serial_label_evidence(
                        str(merged.get("Serial Number", "")),
                        images,
                        evidence_texts=evidence_texts,
                        manufacturer_hint=merged.get("Manufacturer", ""),
                    )
                )
                family_model_trusted = bool(
                    family_reread_model
                    and _model_acceptable(family_reread_model)
                    and not require_direct_model_serial_evidence
                )
                family_serial_trusted = bool(
                    family_reread_serial
                    and _serial_acceptable(family_reread_serial)
                    and (
                        not require_direct_model_serial_evidence
                        or self._matches_rheem_ruud_serial_shape(family_reread_serial)
                    )
                )
                model_weak = initial_model_serial_collision or not _model_acceptable(merged.get("Model", "")) or (
                    strict_seq0_reread_family
                    and merged.get("Model")
                    and not model_supported
                    and not family_model_trusted
                )
                serial_weak = not _serial_acceptable(merged.get("Serial Number", "")) or (
                    strict_seq0_reread_family
                    and merged.get("Serial Number")
                    and not serial_supported
                    and not family_serial_trusted
                )

            if model_weak or serial_weak:
                ocr_ms: Dict[str, str] = {}
                try:
                    ocr_ms = self._ocr_extract_model_serial(images)
                except Exception:
                    ocr_ms = {}

                if model_weak:
                    model_candidate = self._normalize_model_candidate(
                        ocr_ms.get("Model", ""),
                        merged.get("Manufacturer", ""),
                    )
                    if not (model_candidate and _model_acceptable(model_candidate)) and evidence_texts:
                        for txt in evidence_texts:
                            parsed_model = self._normalize_model_candidate(
                                self._parse_nameplate_model_serial(txt).get("Model", ""),
                                merged.get("Manufacturer", ""),
                            )
                            if parsed_model and _model_acceptable(parsed_model):
                                model_candidate = parsed_model
                                break
                    if model_candidate and _model_acceptable(model_candidate):
                        merged["Model"] = model_candidate

                if serial_weak:
                    # Never let OCR-derived candidates overwrite a serial that was
                    # explicitly accepted from the targeted nameplate reread; rotated
                    # or low-quality plates produce garbage OCR that outranks it.
                    reread_serial_kept = bool(
                        family_reread_serial
                        and merged.get("Serial Number") == family_reread_serial
                        and _serial_acceptable(family_reread_serial)
                    )
                    if not reread_serial_kept:
                        serial_candidate = self._normalize_serial_candidate(
                            ocr_ms.get("Serial Number", ""),
                            merged.get("Manufacturer", ""),
                        )
                        if not (serial_candidate and _serial_acceptable(serial_candidate)) and evidence_texts:
                            serial_candidate = self._best_serial_candidate_from_texts(evidence_texts)
                        if serial_candidate and _serial_acceptable(serial_candidate):
                            merged["Serial Number"] = serial_candidate

            if strict_seq0_reread_family:
                if merged.get("Model") and not self._has_model_label_evidence(
                    str(merged.get("Model", "")),
                    images,
                    evidence_texts=evidence_texts,
                ):
                    if (
                        not require_direct_model_serial_evidence
                        and family_reread_model
                        and _model_acceptable(family_reread_model)
                    ):
                        merged["Model"] = family_reread_model
                    else:
                        merged["Model"] = ""
                if merged.get("Serial Number") and not self._has_serial_label_evidence(
                    str(merged.get("Serial Number", "")),
                    images,
                    evidence_texts=evidence_texts,
                    manufacturer_hint=merged.get("Manufacturer", ""),
                ):
                    if (
                        family_reread_serial
                        and _serial_acceptable(family_reread_serial)
                        and (
                            not require_direct_model_serial_evidence
                            or self._matches_rheem_ruud_serial_shape(family_reread_serial)
                        )
                    ):
                        merged["Serial Number"] = family_reread_serial
                    else:
                        merged["Serial Number"] = ""

            # Corroborate serials that lack OCR label evidence with the targeted
            # nameplate reread. Confident-looking confabulations (e.g. 'UISD-SEL4'
            # mashed up from TUBESIDE/SHELLSIDE table labels) otherwise pass the
            # shape checks with confidence above the review threshold.
            if merged.get("Serial Number") and not strict_seq0_reread_family:
                serial_evidenced = self._has_serial_label_evidence(
                    str(merged.get("Serial Number", "")),
                    images,
                    evidence_texts=evidence_texts,
                    manufacturer_hint=merged.get("Manufacturer", ""),
                )
                if not serial_evidenced:
                    if not family_reread_serial:
                        try:
                            reread_check = self._reread_model_serial_from_nameplate_llm(
                                qr,
                                images,
                                merged.get("Manufacturer", ""),
                            )
                        except Exception:
                            reread_check = {}
                        family_reread_serial = self._normalize_serial_candidate(
                            reread_check.get("Serial Number", ""),
                            merged.get("Manufacturer", ""),
                        )
                    cur_compact = re.sub(
                        r"[^A-Z0-9]", "", str(merged.get("Serial Number", "")).upper()
                    )
                    reread_compact = re.sub(
                        r"[^A-Z0-9]", "", (family_reread_serial or "").upper()
                    )
                    if not reread_compact or cur_compact != reread_compact:
                        self._last_serial_unverified = True
                        logging.info(
                            f"[{qr}] Serial '{merged.get('Serial Number', '')}' has no OCR evidence "
                            f"and disagrees with reread '{family_reread_serial}'; "
                            "capping confidence for manual review."
                        )

            # Williams plates are highly regular; prefer longer HH* model from seq0 OCR when serial agrees.
            if is_williams_like:
                try:
                    ocr_ms_refine = self._ocr_extract_model_serial(images)
                except Exception:
                    ocr_ms_refine = {}
                ocr_model_refine = self._normalize_model_candidate(
                    ocr_ms_refine.get("Model", ""),
                    merged.get("Manufacturer", ""),
                )
                ocr_serial_refine = self._normalize_serial_candidate(
                    ocr_ms_refine.get("Serial Number", ""),
                    merged.get("Manufacturer", ""),
                )
                merged_serial_norm = self._normalize_serial_candidate(
                    merged.get("Serial Number", ""),
                    merged.get("Manufacturer", ""),
                )
                if (
                    ocr_model_refine
                    and _model_acceptable(ocr_model_refine)
                    and ocr_serial_refine
                    and _serial_acceptable(ocr_serial_refine)
                    and merged_serial_norm
                    and ocr_serial_refine == merged_serial_norm
                ):
                    cur_model_compact = re.sub(
                        r"[^A-Z0-9]",
                        "",
                        self._normalize_model_candidate(merged.get("Model", ""), merged.get("Manufacturer", "")),
                    )
                    ocr_model_compact = re.sub(r"[^A-Z0-9]", "", ocr_model_refine)
                    if len(ocr_model_compact) > len(cur_model_compact):
                        merged["Model"] = ocr_model_refine

            if not _model_acceptable(merged.get("Model", "")):
                merged["Model"] = ""
            if not _serial_acceptable(merged.get("Serial Number", "")):
                merged["Serial Number"] = ""

            if is_williams_like:
                serial_date_year = _year_from_serial_date_pattern(evidence_texts or [])
                if serial_date_year:
                    merged["Year"] = serial_date_year

            ocr_year_candidate = self._extract_year_from_rois(images) or self._best_year_candidate_from_texts(
                evidence_texts or []
            )
            if merged.get("Year") and not self._has_year_evidence(
                str(merged.get("Year", "")),
                images,
                evidence_texts=evidence_texts,
            ):
                # A reread is still a model output: accept it only when the reread
                # year is visible in the seq-0 evidence, otherwise a model-inferred
                # year (e.g. decoded from a serial letter code) would be saved even
                # though no year is printed on the plate.
                reread_year = "" if ocr_year_candidate else self._reread_year_from_nameplate_llm(qr, images)
                if reread_year and not self._has_year_evidence(
                    reread_year, images, evidence_texts=evidence_texts
                ):
                    logging.info(
                        f"[{qr}] Discarding unevidenced Year reread '{reread_year}' "
                        f"(uncorroborated original '{merged.get('Year', '')}')."
                    )
                    reread_year = ""
                replacement_year = ocr_year_candidate or reread_year
                if replacement_year:
                    merged["Year"] = replacement_year
                else:
                    merged["Year"] = self._fallback_year_from_ocr(images)

        # Fill missing year with printed evidence only: seq0 ROIs / OCR texts first,
        # then a targeted seq0 reread that must itself be evidence-corroborated.
        if not merged.get("Year") and has_nameplate_source:
            evidence_year = self._extract_year_from_rois(images) or (
                self._best_year_candidate_from_texts(evidence_texts or []) if evidence_texts else ""
            )
            if evidence_year:
                merged["Year"] = evidence_year
            else:
                reread_year = self._reread_year_from_nameplate_llm(qr, images)
                if reread_year and self._has_year_evidence(
                    reread_year, images, evidence_texts=evidence_texts
                ):
                    merged["Year"] = reread_year
                else:
                    if reread_year:
                        logging.info(
                            f"[{qr}] Discarding unevidenced Year reread '{reread_year}' "
                            "(no printed year found on the plate)."
                        )
                    merged["Year"] = self._fallback_year_from_ocr(images)

        ubc_raw = merged.get("UBC Tag", "")
        if (
            not Config.ME_UBC_CONSENSUS_ENABLED
            and self._needs_ocr_for_ubc(ubc_raw)
        ):
            ubc_reread = self._reread_ubc_from_tag_llm(qr, images)
            if ubc_reread:
                ubc_raw = ubc_reread
        merged["UBC Tag"] = self._normalize_ubc_tag_with_context(
            ubc_raw,
            images,
            allow_ocr=False,
        )
        if not self._is_readable_source_path(images.get("1", "")):
            merged["UBC Tag"] = ""
        merged["Technical Safety BC"] = self._normalize_tsbc_unit_no(merged.get("Technical Safety BC", ""))

        # Preserve source-image gating for deterministic field ownership.
        if not has_nameplate_source:
            merged["Model"] = ""
            merged["Serial Number"] = ""
            merged["Year"] = ""
        if merged.get("Technical Safety BC") and not has_tsbc_source:
            merged["Technical Safety BC"] = ""

        if self._is_qr_like_serial(merged.get("Serial Number", ""), qr):
            merged["Serial Number"] = ""

        if looks_like_date_misread_serial(
            str(merged.get("Serial Number", "")), year_hint=str(merged.get("Year", ""))
        ):
            logging.warning(
                f"[{qr}] Removing Serial Number '{merged.get('Serial Number', '')}' "
                "because it reads as a manufacturing date (possibly rotated/upside-down)."
            )
            merged["Serial Number"] = ""
            self._last_serial_date_misread = True

        if self._model_serial_values_collide(
            merged.get("Model", ""),
            merged.get("Serial Number", ""),
        ):
            model_has_label = bool(
                evidence_texts
                and self._has_model_label_evidence(
                    str(merged.get("Model", "")),
                    images,
                    evidence_texts=evidence_texts,
                )
            )
            serial_has_label = bool(
                evidence_texts
                and self._has_serial_label_evidence(
                    str(merged.get("Serial Number", "")),
                    images,
                    evidence_texts=evidence_texts,
                    manufacturer_hint=merged.get("Manufacturer", ""),
                )
            )
            if model_has_label and not serial_has_label:
                dropped_field = "Serial Number"
            else:
                # Prefer the explicit serial field when both/neither evidence
                # checks succeed. A collision is always routed to review.
                dropped_field = "Model"
            logging.warning(
                "[%s] Model/Serial collision for value '%s'; clearing %s "
                "(model_label=%s serial_label=%s).",
                qr,
                merged.get("Model", ""),
                dropped_field,
                model_has_label,
                serial_has_label,
            )
            merged[dropped_field] = ""
            self._last_model_serial_collision = True

        # Keep key order stable for downstream consumers.
        return self._validate_and_normalize({
            "Manufacturer": merged.get("Manufacturer", ""),
            "Model": merged.get("Model", ""),
            "Serial Number": merged.get("Serial Number", ""),
            "Year": merged.get("Year", ""),
            "UBC Tag": merged.get("UBC Tag", ""),
            "Technical Safety BC": merged.get("Technical Safety BC", ""),
        })
    
    def _calculate_completeness_score(self, data: Dict[str, str], has_tsbc_source: bool = False) -> float:
        """Calculates the percentage of key fields that are present."""
        completeness_fields = self._me_completeness_fields(has_tsbc_source)
        if not completeness_fields:
            return 100.0
            
        present_count = sum(1 for field in completeness_fields if data.get(field, "").strip())
        total_fields = len(completeness_fields)
        
        return (present_count / total_fields) * 100

    def _existing_completeness(self, qr: str, building: str, asset_type: str) -> float:
        """If a JSON already exists, return its completeness_score to avoid overwriting with worse output."""
        fname = f"{qr}_{asset_type}_{building}.json"
        path = os.path.join(Config.OUTPUT_FOLDER, fname)
        if not os.path.exists(path):
            return 0.0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return float(data.get("completeness_score", 0.0))
        except Exception:
            return 0.0

    def _load_existing(self, qr: str, building: str, asset_type: str) -> Dict[str, Any]:
        fname = f"{qr}_{asset_type}_{building}.json"
        path = os.path.join(Config.OUTPUT_FOLDER, fname)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _find_existing_json_for_qr(self, qr: str, asset_type: str = "ME") -> str:
        """Returns an existing JSON path for QR+asset_type if present, else empty string."""
        if not qr or not os.path.isdir(Config.OUTPUT_FOLDER):
            return ""
        prefix = f"{qr}_{asset_type}_"
        try:
            for name in os.listdir(Config.OUTPUT_FOLDER):
                if not name.lower().endswith(".json"):
                    continue
                if not name.startswith(prefix):
                    continue
                candidate = os.path.join(Config.OUTPUT_FOLDER, name)
                if os.path.isfile(candidate):
                    return candidate
        except OSError:
            return ""
        return ""

    def _build_existing_tsbc_repair_payload(
        self,
        qr: str,
        info: Dict[str, Any],
        existing_path: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Build a save payload that only fills missing Technical Safety BC on an
        existing ME JSON. This preserves reviewed/manual fields while allowing
        seq-3 TSBC improvements to repair previously blank outputs.
        """
        images = info.get("images", {}) if isinstance(info, dict) else {}
        if not self._has_tsbc_source_image(images):
            return None

        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            logging.warning(f"[{qr}] Existing TSBC repair skipped (cannot read JSON): {e}")
            return None

        if not isinstance(existing, dict):
            return None
        structured = existing.get("structured_data", {})
        if not isinstance(structured, dict):
            return None
        if self._normalize_tsbc_unit_no(structured.get("Technical Safety BC", "")):
            return None

        repaired_tsbc = self._reread_tsbc_from_unit_no_llm(qr, images)
        if not repaired_tsbc:
            logging.info(f"[{qr}] Existing TSBC repair not applied: seq3 reread returned blank.")
            return None

        repaired = dict(existing)
        repaired_structured = dict(structured)
        repaired_structured["Technical Safety BC"] = repaired_tsbc
        repaired["structured_data"] = repaired_structured
        repaired["qr_code"] = str(existing.get("qr_code") or qr)
        repaired["building_number"] = str(existing.get("building_number") or info.get("building", ""))
        repaired["asset_type"] = str(existing.get("asset_type") or f"- {info.get('asset_type', 'ME').upper()}")

        scores = dict(repaired.get("confidence_scores", {}) or {})
        scores["Technical Safety BC"] = max(self._normalize_confidence_score(scores.get("Technical Safety BC", 0)), 88)
        repaired["confidence_scores"] = scores
        repaired["_score_context"] = {
            "has_tsbc_source": True,
            "ocr_assisted_rescue": False,
            "ocr_mode": Config.OCR_MODE,
            "image_paths": images,
            "suppress_manual_review_append": True,
        }
        logging.info(f"[{qr}] Existing JSON TSBC repair prepared: Technical Safety BC='{repaired_tsbc}'")
        return repaired

    def _build_existing_manufacturer_repair_payload(
        self,
        qr: str,
        info: Dict[str, Any],
        existing_path: str,
    ) -> Optional[Dict[str, Any]]:
        """Fill a missing manufacturer only when a confirmed model identity proves it."""
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            logging.warning(f"[{qr}] Existing manufacturer repair skipped (cannot read JSON): {e}")
            return None

        if not isinstance(existing, dict):
            return None
        structured = existing.get("structured_data", {})
        if not isinstance(structured, dict):
            return None
        if self._canonicalize_manufacturer_candidate(structured.get("Manufacturer", "")):
            return None

        manufacturer = self._infer_manufacturer_from_model(structured.get("Model", ""))
        if not manufacturer:
            return None

        repaired = dict(existing)
        repaired_structured = dict(structured)
        repaired_structured["Manufacturer"] = manufacturer
        repaired["structured_data"] = repaired_structured
        repaired["qr_code"] = str(existing.get("qr_code") or qr)
        repaired["building_number"] = str(existing.get("building_number") or info.get("building", ""))
        repaired["asset_type"] = str(existing.get("asset_type") or f"- {info.get('asset_type', 'ME').upper()}")

        scores = dict(repaired.get("confidence_scores", {}) or {})
        scores["Manufacturer"] = max(
            self._normalize_confidence_score(scores.get("Manufacturer", 0)), 78
        )
        repaired["confidence_scores"] = scores
        repaired["_score_context"] = {
            "has_tsbc_source": self._has_tsbc_source_image(info.get("images", {})),
            "ocr_assisted_rescue": False,
            "ocr_mode": Config.OCR_MODE,
            "image_paths": info.get("images", {}),
            "suppress_manual_review_append": True,
        }
        logging.info(
            f"[{qr}] Existing JSON manufacturer repair prepared: "
            f"Manufacturer='{manufacturer}' from confirmed Model='{structured.get('Model', '')}'"
        )
        return repaired

    @staticmethod
    def _is_readable_source_path(image_path: str) -> bool:
        """Fast source validation without decoding image bytes (performance-critical)."""
        if not image_path:
            return False
        if not os.path.isfile(image_path):
            return False
        try:
            # Filter zero-byte or clearly invalid placeholder paths quickly.
            return os.path.getsize(image_path) > 512
        except OSError:
            return False

    @classmethod
    def _has_tsbc_source_image(cls, images: Dict[str, str]) -> bool:
        """Returns True when a seq '3' source image exists for Technical Safety BC extraction."""
        if not images:
            return False
        return cls._is_readable_source_path(images.get("3", ""))

    @classmethod
    def _has_nameplate_source_image(cls, images: Dict[str, str]) -> bool:
        """Returns True when a seq '0' source image exists for Model/Serial/Year extraction."""
        if not images:
            return False
        return cls._is_readable_source_path(images.get("0", ""))

    def _collect_nameplate_evidence_texts(self, images: Dict[str, str]) -> List[str]:
        """Collect seq-0 OCR evidence once and reuse for serial/year corroboration checks."""
        seq0 = images.get("0") if isinstance(images, dict) else None
        if not seq0:
            return []
        texts = self._ocr_text_variants(seq0)
        texts.extend(self._ocr_text_from_dark_nameplate(seq0))
        texts.extend(self._ocr_text_from_red_nameplate(seq0))
        return list(dict.fromkeys(texts))

    @staticmethod
    def _is_rheem_ruud_water_heater_family(
        manufacturer_hint: str,
        evidence_texts: Optional[List[str]] = None,
        merged_fields: Optional[Dict[str, str]] = None,
    ) -> bool:
        hint = (manufacturer_hint or "").upper()
        texts = [(txt or "").upper() for txt in (evidence_texts or [])[:12]]
        merged_fields = merged_fields or {}
        descriptor = " ".join(
            str(merged_fields.get(key, "") or "")
            for key in ("Asset Group", "Main Asset", "Description", "Attribute")
        ).upper()

        brand_hit = (
            "RHEEM" in hint
            or "RUUD" in hint
            or any(("RHEEM" in txt or "RUUD" in txt) for txt in texts)
        )
        if not brand_hit:
            return False

        water_heater_hit = (
            "WATER HEATER" in descriptor
            or "WATER STORAGE" in descriptor
            or "DOMESTIC WATER" in descriptor
            or any(
                "WATER HEATER" in txt
                or (
                    ("MOD" in txt or "MODEL" in txt)
                    and ("SER" in txt or "SERIAL" in txt)
                    and any(marker in txt for marker in ("LITRES", "WATTS", "VAC", "MAXIMUM ADM"))
                )
                for txt in texts
            )
        )
        return water_heater_hit

    @staticmethod
    def _clean_model_preserving_separators(value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        text = text.replace("|", "I")
        text = text.replace("’", "'").replace("`", "'")
        text = re.sub(r"\s+", " ", text).strip()
        if re.fullmatch(r"[A-Z]{1,6}\s+\d{1,6}[A-Z]?", text):
            text = text.replace(" ", "")
        text = re.sub(r"\b(FCU?)\s*(M1|ML)\b", r"\1 MI", text)
        text = re.sub(r"\bFCU\s+M2\b", "FCU MI", text)
        text = re.sub(r"\bFC\s*MI\b", "FC MI", text)
        text = re.sub(r"\bECMI\b", "FCMI", text)
        text = re.sub(r"\b(\d+)\s*ECMI\b", r"\1 FCMI", text)
        text = re.sub(r"\s*([\/\.-])\s*", r"\1", text)
        text = re.sub(r"[^A-Z0-9\/\.\-\(\)\s]", "", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" -./")
        return text[:80]

    def _normalize_model_candidate(self, value: str, manufacturer_hint: str = "") -> str:
        model = self._clean_model_preserving_separators(value)
        if not model:
            return ""

        manufacturer_norm = str(manufacturer_hint or "").strip().lower()
        compact = re.sub(r"[^A-Z0-9]", "", model)

        if "williams" in manufacturer_norm:
            if compact.startswith("HHO"):
                compact = f"HH0{compact[3:]}"
            if compact.startswith("HH"):
                tail = compact[2:].replace("O", "0")
                tail = re.sub(r"[IL]", "1", tail)
                compact = f"HH{tail}"
            elif compact.startswith("H"):
                tail = compact[1:].replace("O", "0")
                tail = re.sub(r"[IL]", "1", tail)
                compact = f"H{tail}"
            return compact[:80]

        if "taco" in manufacturer_norm:
            repaired = self._repair_taco_model_candidate(model)
            return repaired[:80]

        if "greenheck" in manufacturer_norm:
            repaired = self._repair_greenheck_model_candidate(model)
            return repaired[:80]

        repaired_republic = self._repair_republic_model_candidate(model)
        if repaired_republic:
            return repaired_republic[:80]

        return model

    @staticmethod
    def _looks_generic_manufacturer_noise(text: str) -> bool:
        tokens = [tok for tok in re.findall(r"[A-Z0-9&/]+", str(text or "").upper()) if tok]
        if not tokens:
            return True
        if any(token in ME_GENERIC_MANUFACTURER_STOPWORDS for token in tokens):
            return True
        if all(token in ME_MANUFACTURER_CORPORATE_TOKENS for token in tokens):
            return True
        if len(tokens) > 6:
            return True
        return False

    def _canonicalize_manufacturer_candidate(self, text: str) -> str:
        if not text:
            return ""

        raw = re.sub(r"\s+", " ", str(text).strip())
        if not raw:
            return ""
        if "\n" in str(text) or len(raw) > 100:
            raw_lines = [re.sub(r"\s+", " ", line).strip() for line in str(text).splitlines()]
            lines = [line for line in raw_lines if line and len(line) <= 80]
            for line in lines[:16]:
                matched = self._canonicalize_manufacturer_candidate(line)
                if matched:
                    return matched
            for idx in range(len(lines) - 1):
                pair = f"{lines[idx]} {lines[idx + 1]}".strip()
                if not pair or len(pair) > 80:
                    continue
                matched = self._canonicalize_manufacturer_candidate(pair)
                if matched:
                    return matched
            return ""
        upper = raw.upper()

        for pattern, canonical in ME_MANUFACTURER_REGEX_RULES:
            if re.search(pattern, upper):
                return canonical

        compact = _compact_lookup_key(raw)
        if compact in ME_MANUFACTURER_ALIAS_LOOKUP:
            return ME_MANUFACTURER_ALIAS_LOOKUP[compact]

        cleaned = re.sub(r"[^A-Z0-9&/.,'\-\s]", " ", upper)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            compact_cleaned = _compact_lookup_key(cleaned)
            if compact_cleaned in ME_MANUFACTURER_ALIAS_LOOKUP:
                return ME_MANUFACTURER_ALIAS_LOOKUP[compact_cleaned]

        normalized_shared = normalize_manufacturer(raw)
        if normalized_shared:
            shared_key = _compact_lookup_key(normalized_shared)
            if shared_key in ME_MANUFACTURER_ALIAS_LOOKUP:
                return ME_MANUFACTURER_ALIAS_LOOKUP[shared_key]
            return re.sub(r"\s+", " ", str(normalized_shared).strip())

        return self._normalize_unknown_manufacturer_candidate(raw)

    @staticmethod
    def _normalize_unknown_manufacturer_candidate(text: str) -> str:
        """Accept a brand-like unknown supplier while rejecting label/instruction text."""
        raw = re.sub(r"\s+", " ", str(text or "").strip())
        if not raw or len(raw) > 80 or "\n" in raw:
            return ""
        upper = raw.upper()
        if any(phrase in upper for phrase in ME_UNKNOWN_MANUFACTURER_FORBIDDEN_PHRASES):
            return ""

        cleaned = re.sub(r"[^A-Z0-9&/'\.\-\s]", " ", upper)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -./")
        tokens = re.findall(r"[A-Z0-9]+", cleaned)
        if not tokens or len(tokens) > 5:
            return ""
        if any(token in ME_UNKNOWN_MANUFACTURER_HARD_TOKENS for token in tokens):
            return ""
        if not any(re.search(r"[A-Z]", token) for token in tokens):
            return ""
        distinctive = [
            token for token in tokens
            if token not in ME_UNKNOWN_MANUFACTURER_DESCRIPTOR_TOKENS
            and token not in ME_MANUFACTURER_CORPORATE_TOKENS
            and len(token) >= 3
        ]
        if not distinctive:
            return ""
        # A single alphanumeric token is more likely a model/serial than a brand.
        if len(tokens) == 1 and re.search(r"\d", tokens[0]):
            return ""

        def _display_token(token: str) -> str:
            if (
                len(token) <= 4
                and token.isalpha()
                and token not in ME_UNKNOWN_MANUFACTURER_DESCRIPTOR_TOKENS
                and token not in ME_MANUFACTURER_CORPORATE_TOKENS
            ):
                return token
            if token.isalpha():
                return token.capitalize()
            return token

        display = " ".join(_display_token(token) for token in tokens)
        return display.replace(" And ", " & ")[:80]

    def _is_known_manufacturer_candidate(self, text: str) -> bool:
        """True when the candidate is backed by the canonical dictionary/regex set."""
        raw = re.sub(r"\s+", " ", str(text or "").strip())
        if not raw:
            return False
        upper = raw.upper()
        if any(re.search(pattern, upper) for pattern, _ in ME_MANUFACTURER_REGEX_RULES):
            return True
        if _compact_lookup_key(raw) in ME_MANUFACTURER_ALIAS_LOOKUP:
            return True
        return bool(normalize_manufacturer(raw))

    @staticmethod
    def _normalize_year_flexible(value: str, allow_compact_mmyy: bool = True) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""

        normalized = normalize_year(text)
        if re.fullmatch(r"(19|20)\d{2}", normalized):
            return normalized

        compact = re.sub(r"[^A-Z0-9]", "", text).replace("O", "0")
        if allow_compact_mmyy and re.fullmatch(r"(?:0[1-9]|1[0-2])\d{2}", compact):
            yy = int(compact[-2:])
            if 0 <= yy <= 26:
                return f"20{yy:02d}"
            if 50 <= yy <= 99:
                return f"19{yy:02d}"
        return ""

    @staticmethod
    def _clean_serial_preserving_separators(value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[^A-Z0-9\-_\/\.]", "", text)
        return text[:64]

    def _normalize_serial_candidate(self, value: str, manufacturer_hint: str = "") -> str:
        cleaned = self._clean_serial_preserving_separators(value)
        if not cleaned:
            return ""

        normalized = normalize_serial(cleaned)
        compact_cleaned = re.sub(r"[^A-Z0-9]", "", cleaned)
        compact_normalized = re.sub(r"[^A-Z0-9]", "", normalized)
        candidate = cleaned
        if normalized and compact_normalized != compact_cleaned:
            candidate = normalized

        manufacturer_norm = str(manufacturer_hint or "").strip().lower()
        if "taco" in manufacturer_norm:
            candidate = self._repair_taco_order_serial(candidate)
        return candidate[:64]

    @staticmethod
    def _repair_taco_model_candidate(value: str) -> str:
        model = AssetProcessor._clean_model_preserving_separators(value or "")
        compact = re.sub(r"[^A-Z0-9]", "", model)
        if len(compact) >= 12 and re.search(r"\d{3}[DOQ]$", compact):
            compact = f"{compact[:-1]}0"
        return compact or model

    @staticmethod
    def _repair_taco_order_serial(value: str) -> str:
        serial = re.sub(r"\s+", "", str(value or "").strip().upper())
        serial = re.sub(r"[^A-Z0-9\-_\/\.]", "", serial)
        compact = re.sub(r"[^A-Z0-9]", "", serial)
        if "/" in serial and compact.isdigit() and len(compact) == 9:
            slash_idx = serial.find("/")
            if slash_idx != len(serial) - 2:
                return f"{compact[:-1]}/{compact[-1]}"
            return serial[:64]
        if "/" in serial:
            return serial[:64]
        if not compact.isdigit() or len(compact) != 9:
            return serial
        return f"{compact[:-1]}/{compact[-1]}"

    @staticmethod
    def _repair_greenheck_model_candidate(value: str) -> str:
        text = AssetProcessor._clean_model_preserving_separators(value or "")
        if not text:
            return ""

        text = re.sub(r"\s+", "-", text.upper()).strip("-")
        text = re.sub(r"-{2,}", "-", text)
        text = re.sub(r"^([A-Z]{2,6})(\d{2,4})(?=-)", r"\1-\2", text)
        text = re.sub(r"(\d)([A-Z]{1,3}\d)(?=-|$)", r"\1-\2", text)
        text = re.sub(r"([A-Z0-9])\s+([A-Z0-9])", r"\1-\2", text)
        return text[:80]

    @staticmethod
    def _repair_republic_model_candidate(value: str) -> str:
        text = AssetProcessor._clean_model_preserving_separators(value or "")
        if not text:
            return ""

        upper = text.upper()
        compact = re.sub(r"[^A-Z0-9]", "", upper)
        compact = compact.replace("O", "0").replace("Q", "0").replace("D", "0")

        # Republic vacuum pump "Type" rows are short codes like VRT3030 / VRT3080.
        match = re.search(r"([KVYWRM][R8][T7])([0-9]{4})", compact)
        if not match:
            match = re.search(r"(VRT|KRT|YRT|WRT|MRT)([0-9]{4})", compact)
        if not match:
            return ""

        prefix = match.group(1)
        digits = match.group(2)
        if len(digits) != 4:
            return ""

        prefix = prefix.replace("8", "R").replace("7", "T")
        prefix = f"V{prefix[1:]}" if len(prefix) == 3 else prefix
        if prefix != "VRT":
            return ""
        return f"{prefix}{digits}"

    @staticmethod
    def _is_qr_like_serial(serial_value: str, qr_value: str) -> bool:
        """Reject serial numbers that are actually QR code identifiers."""
        s = re.sub(r"[^A-Z0-9]", "", (serial_value or "").upper())
        q = re.sub(r"[^A-Z0-9]", "", (qr_value or "").upper())
        if not s or not q:
            return False
        if s == q:
            return True
        s_noz = s.lstrip("0")
        q_noz = q.lstrip("0")
        if s_noz and q_noz and s_noz == q_noz:
            return True
        # Common QR format: long numeric IDs with leading zeros.
        if s.isdigit() and q.isdigit() and len(s) >= 9 and len(q) >= 9:
            if s.endswith(q_noz) or q.endswith(s_noz):
                return True
        return False

    def _build_seq0_nameplate_image_content(self, seq0_path: str) -> List[Dict[str, Any]]:
        """
        Build image_url content for targeted seq0 rereads.
        Prefer a cropped nameplate when available, while still including the full seq0 photo.
        """
        content: List[Dict[str, Any]] = []
        full_bytes: Optional[bytes] = None

        try:
            with open(seq0_path, "rb") as f:
                full_bytes = f.read()
        except Exception:
            full_bytes = None

        dark_crop = self._extract_dark_nameplate_crop(seq0_path)
        light_crop = self._extract_light_nameplate_crop(seq0_path)
        crop = dark_crop
        if dark_crop is not None and dark_crop.size > 0 and light_crop is not None and light_crop.size > 0:
            dark_score = self._score_nameplate_crop(dark_crop)
            light_score = self._score_nameplate_crop(light_crop)
            if light_score > dark_score:
                crop = light_crop
        elif light_crop is not None and light_crop.size > 0:
            crop = light_crop
        if crop is not None and crop.size > 0:
            try:
                ok, encoded = cv2.imencode(".jpg", crop)
            except Exception:
                ok, encoded = False, None
            if ok and encoded is not None:
                crop_b64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{crop_b64}"},
                    }
                )

        if full_bytes:
            full_b64 = base64.b64encode(full_bytes).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{full_b64}"},
                }
            )

        return content

    def _build_pressure_vessel_header_image_content(
        self, seq0_path: str
    ) -> List[Dict[str, Any]]:
        """Return high-detail CW/CCW full-plate views for the top-edge stamp."""
        if cv2 is None:
            return []
        image = cv2.imread(seq0_path)
        if image is None or image.size == 0:
            return []

        content: List[Dict[str, Any]] = []
        for rotated in (
            cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
            cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
        ):
            blurred = cv2.GaussianBlur(rotated, (0, 0), 0.8)
            enhanced = cv2.addWeighted(rotated, 1.5, blurred, -0.5, 0)
            ok, encoded = cv2.imencode(".jpg", enhanced, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if not ok or encoded is None:
                continue
            encoded_b64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded_b64}",
                        "detail": "high",
                    },
                }
            )
        return content

    def _reread_year_from_nameplate_llm(self, qr: str, images: Dict[str, str]) -> str:
        """
        Targeted model reread for Year using seq 0 only.
        Used when an initially extracted year fails corroboration.
        """
        if not getattr(self, "client", None):
            return ""
        seq0_path = images.get("0") if isinstance(images, dict) else None
        if not self._is_readable_source_path(seq0_path or ""):
            return ""

        image_content = self._build_seq0_nameplate_image_content(seq0_path)
        if not image_content:
            logging.warning(f"[{qr}] Year reread skipped (cannot read seq0).")
            return ""

        prompt = """
Re-read ONLY the manufacturing year from this single nameplate image.
Rules:
- Use only text visible in this image.
- If a cropped plate image and the full seq0 photo are both provided, prioritize the cropped plate image.
- Return four-digit year if explicitly visible.
- If date appears as MM/YY, convert to YYYY (00-26 -> 20YY, 50-99 -> 19YY).
- If date appears as MMYY in a manufacturing field (example: MFG DATE 0623), convert it to YYYY.
- Taco example: if the plate box says 'MFG Date 0623', return '2023'. Do not turn '0623' into '2008' or '2018'.
- On Republic Manufacturing dark table-style plates, read the explicit value in the row labeled 'Year'.
- On Greenheck plates, do not infer year from the model, serial, RPM, or instruction label; if no explicit year or manufacturing date is visible, return empty.
- Do NOT use horsepower fractions like '1/3' as year.
- Prefer YEAR, MFG DATE, MANUFACTURED, or PROD DATE fields over unrelated numbers.
- Prefer MM/YY or MMYY date near serial/order fields when present (example: 07/03 -> 2003, 0623 -> 2023).
- If unreadable, return empty string.
Output must be strict JSON with exactly one key: "Year".
""".strip()

        content = [{"type": "text", "text": prompt}]
        content.extend(image_content)

        primary_models, fallback_models = self._build_model_tiers()
        model_candidates: List[str] = []
        for name in [*primary_models, *fallback_models]:
            if name and name not in model_candidates:
                model_candidates.append(name)
        for model_name in model_candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        220,
                        model_name,
                        hard=self._is_fallback_model_name(model_name),
                        targeted=True,
                    ),
                    "response_format": MEYearOnlyExtraction,
                }
                self._apply_sampling_options(kwargs, model_name)
                effort = self._reasoning_effort_for_model(
                    model_name,
                    hard=self._is_fallback_model_name(model_name),
                )
                if effort:
                    kwargs["reasoning_effort"] = effort
                resp = self.client.beta.chat.completions.parse(**kwargs)
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None) or msg.parsed is None:
                    continue
                parsed: MEYearOnlyExtraction = msg.parsed
                year_value = self._normalize_year_flexible(parsed.model_dump(by_alias=True).get("Year", ""))
                if year_value:
                    logging.info(f"[{qr}] Year reread accepted from model '{model_name}': {year_value}")
                    return year_value
            except Exception as e:
                if is_quota_error(e):
                    raise QuotaExceeded(qr) from e
                if is_auth_error(e):
                    raise AuthFailed(qr) from e
                logging.warning(f"[{qr}] Year reread attempt failed on model '{model_name}': {e}")
        return ""

    def _reread_model_serial_from_nameplate_llm(
        self,
        qr: str,
        images: Dict[str, str],
        manufacturer_hint: str = "",
    ) -> Dict[str, str]:
        """
        Targeted model reread for Model and Serial Number using seq 0 only.
        """
        out = {"Model": "", "Serial Number": ""}
        if not getattr(self, "client", None):
            return out
        seq0_path = images.get("0") if isinstance(images, dict) else None
        if not self._is_readable_source_path(seq0_path or ""):
            return out

        image_content = self._build_seq0_nameplate_image_content(seq0_path)
        if not image_content:
            logging.warning(f"[{qr}] Model/Serial reread skipped (cannot read seq0).")
            return out

        manufacturer_norm = str(manufacturer_hint or "").strip().lower()
        greenheck_block = ""
        if "greenheck" in manufacturer_norm:
            greenheck_block = """
- Greenheck-specific layout: rows are MODEL, MARK, TAG, and S/N.
- Read only the MODEL row for Model.
- Ignore MARK and TAG rows entirely; values like 'EF-1' are not model or serial.
- Read only the S/N row for Serial Number.
- Greenheck engraved example: MODEL USGF-322-5-A1-000-501-01, S/N 188527846.
""".strip()
        taco_block = ""
        if "taco" in manufacturer_norm:
            taco_block = """
- Taco-specific layout: prefer 'Model No.' over 'DOE Basic Model No.'.
- Taco pumps/equipment: Serial Number must come from an explicit 'Serial', 'Serial No.', or 'S/N' field; never use Part No., Order No., or CRN No.
- Taco tank/pressure-vessel exception: when the plate contains CRN/CRN No. plus MAWP, MDMT, ASME/NB, CERTIFIED BY, or PSI AT evidence and has no explicit serial field, read the isolated stamped identifier in the plate's top/header border as Serial Number.
- For that pressure-vessel exception, do not return the Part No., CRN value, date, MAWP/MDMT value, pressure, certification number, or any number printed inside a labeled field box.
- The pressure-vessel top identifier is visually separated from the labeled data rows; mentally rotate the image upright before reading it.
- The first two images, when present, are high-detail opposite rotations of the full plate. Inspect ONLY the small narrow rectangular identifier box on the plate's top outer border; do not use any larger interior MAWP/MDMT/Part/CRN value box.
- The accepted pressure-vessel top identifier is digits only. Do not invent letters, spaces, slashes, or hyphens.
""".strip()
            header_content = self._build_pressure_vessel_header_image_content(seq0_path)
            if header_content:
                image_content = [*header_content, *image_content]
        siemens_block = ""
        if "siemens" in manufacturer_norm:
            siemens_block = """
- Siemens-specific layout: the plate shows 'Product No.', 'Cv', and 'Model'.
- Read the all-numeric 'Model' value (example: '03134') for Model; keep leading zeros.
- These plates usually have no explicit serial — use the 'Product No.' value (example: '599-0335') as the Serial Number, preserving the hyphen.
- 'Cv' is a flow-coefficient rating; never use it as Model or Serial Number.
""".strip()

        prompt = """
Re-read ONLY Model and Serial Number from this single nameplate image.
Rules:
- Use only text visible in this image.
- If a cropped plate image and the full seq0 photo are both provided, prioritize the cropped plate image.
- Model: extract from the field labeled 'MODEL', 'MODEL NO', 'UNIT MODEL', 'TYPE', 'BASIC MODEL', or 'DOE BASIC MODEL'.
- Serial Number: extract from the field labeled 'SERIAL', 'SERIAL NO', or 'S/N'. This labeled field takes priority; work carefully on it before considering any other number.
- If the SERIAL-labeled value is genuinely unreadable, or no such field exists, use the 'PRODUCT NO.' / 'PROD. NO.' / 'ORDER NO.' value as the Serial Number.
- 'Cv' is a flow-coefficient rating (example: 'Cv 25'); never use it as Model or Serial Number.
- On Republic Manufacturing dark table-style plates, use the row labeled 'Type' for Model and 'Serial no.' for Serial Number.
- On Greenheck plates, read the engraved 'MODEL' value exactly and preserve every hyphen.
- On Greenheck plates, read the engraved 'S/N' value exactly; 'MARK' or 'TAG' values like 'EF-1' are not model or serial.
- Greenheck engraved example: Model may look like 'USGF-322-5-A1-000-501-01' and Serial Number may be a 9-digit value like '188527846'.
- If explicit serial is absent and 'Order No.' is the only unique identifier on the plate, use it as Serial Number.
- For table-style plates, read row/column labels literally and do not swap adjacent values.
- If both 'MODEL NO.' and 'DOE BASIC MODEL NO.' exist, use 'MODEL NO.' for Model.
- Preserve slashes and punctuation that are visibly printed in 'ORDER NO.' values.
- Distinguish zero from letter O or D carefully, especially in model suffixes and serial/order numbers.
- Example for this layout family: Order No. 20588225/1, Model No. KS6009DE24CXAG2370.
- Republic example: on dark table-style plates, read Model from 'Type', Serial Number from 'Serial no.', and Year from the 'Year' row.
- Keep leading zeros and trailing zeros exactly (0 is numeric zero, not letter O).
- Preserve full alphanumeric strings exactly as printed.
- Do not use date values (e.g., 07/03 or 0623) as model or serial.
- The image may be rotated 90 degrees or upside-down: mentally rotate the plate upright before reading. An upside-down MFG date can look like '8102/50' or '8102/90' - never return such date-shaped values as the Serial Number; return "" instead if no true serial is readable.
- Williams-specific guard: Model usually starts with 'HH' and is long alphanumeric.
- If unreadable, return empty string for that field.
Output must be strict JSON with exactly three keys: "Model", "Serial Number", and "Pressure Vessel Context".
Set "Pressure Vessel Context" to true only when the plate visibly contains CRN/CRN No. plus pressure-vessel evidence such as MAWP, MDMT, ASME/NB, CERTIFIED BY, or PSI AT.
""".strip()

        if greenheck_block:
            prompt = f"{prompt}\n{greenheck_block}"
        if taco_block:
            prompt = f"{prompt}\n{taco_block}"
        if siemens_block:
            prompt = f"{prompt}\n{siemens_block}"

        content = [{"type": "text", "text": prompt}]
        content.extend(image_content)

        primary_models, fallback_models = self._build_model_tiers()
        model_candidates: List[str] = []
        for name in [*primary_models, *fallback_models]:
            if name and name not in model_candidates:
                model_candidates.append(name)
        if "taco" in manufacturer_norm:
            # Dot-peened pressure-vessel header stamps are too fine for the
            # cost-controlled mini model. Use the configured stronger fallback
            # first for this targeted reread only.
            stronger_model = (Config.FALLBACK_LLM_MODEL or "").strip()
            if stronger_model:
                model_candidates = [
                    stronger_model,
                    *[name for name in model_candidates if name != stronger_model],
                ]

        for model_name in model_candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        300,
                        model_name,
                        hard=self._is_fallback_model_name(model_name),
                        targeted=True,
                    ),
                    "response_format": MEModelSerialOnlyExtraction,
                }
                self._apply_sampling_options(kwargs, model_name)
                effort = self._reasoning_effort_for_model(
                    model_name,
                    hard=self._is_fallback_model_name(model_name),
                )
                if effort:
                    kwargs["reasoning_effort"] = effort
                resp = self.client.beta.chat.completions.parse(**kwargs)
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None) or msg.parsed is None:
                    continue

                parsed: MEModelSerialOnlyExtraction = msg.parsed
                payload = parsed.model_dump(by_alias=True)
                self._last_pressure_vessel_context_from_reread = bool(
                    payload.get("Pressure Vessel Context", False)
                )
                model_value = self._normalize_model_candidate(payload.get("Model", ""))
                serial_value = self._normalize_serial_candidate(payload.get("Serial Number", ""))
                if not self._is_model_code_candidate(model_value, manufacturer_hint):
                    model_value = ""
                if not self._is_serial_candidate(serial_value):
                    serial_value = ""

                if model_value or serial_value:
                    logging.info(
                        f"[{qr}] Model/Serial reread accepted from model '{model_name}': "
                        f"model='{model_value}' serial='{serial_value}'"
                    )
                    return {"Model": model_value, "Serial Number": serial_value}
            except Exception as e:
                if is_quota_error(e):
                    raise QuotaExceeded(qr) from e
                if is_auth_error(e):
                    raise AuthFailed(qr) from e
                logging.warning(f"[{qr}] Model/Serial reread attempt failed on model '{model_name}': {e}")
        return out

    def _reread_manufacturer_from_nameplate_llm(
        self, qr: str, images: Dict[str, str]
    ) -> str:
        """Read a missing manufacturer from the seq-0 logo/brand area."""
        if not getattr(self, "client", None):
            return ""
        seq0_path = images.get("0") if isinstance(images, dict) else None
        if not self._is_readable_source_path(seq0_path or ""):
            return ""
        image_content = self._build_seq0_nameplate_image_content(seq0_path)
        if not image_content:
            logging.warning(f"[{qr}] Manufacturer reread skipped (cannot read seq0).")
            return ""

        prompt = """
Read ONLY the manufacturer or product-brand name from this mechanical asset
nameplate image. Inspect large logos, brand decals, and the nameplate header.

Rules:
- Return the brand printed on the asset, not a nearby installer, certification,
  standards body, or component supplier.
- On a PVI storage tank branded AquaPLEX, return "AquaPLEX"; PVI is the tank
  maker mark, while AquaPLEX is the asset manufacturer used by this workflow.
- Do not infer a manufacturer from the model or serial number.
- If no brand is visible, return an empty string.

Output must be strict JSON with exactly one key: "Manufacturer".
""".strip()
        content = [{"type": "text", "text": prompt}, *image_content]

        primary_models, fallback_models = self._build_model_tiers()
        model_candidates: List[str] = []
        for name in [*primary_models, *fallback_models]:
            if name and name not in model_candidates:
                model_candidates.append(name)

        for model_name in model_candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        120,
                        model_name,
                        hard=self._is_fallback_model_name(model_name),
                        targeted=True,
                    ),
                    "response_format": MEManufacturerOnlyExtraction,
                }
                self._apply_sampling_options(kwargs, model_name)
                effort = self._reasoning_effort_for_model(
                    model_name,
                    hard=self._is_fallback_model_name(model_name),
                )
                if effort:
                    kwargs["reasoning_effort"] = effort
                resp = self.client.beta.chat.completions.parse(**kwargs)
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None) or msg.parsed is None:
                    continue
                payload: MEManufacturerOnlyExtraction = msg.parsed
                manufacturer = self._canonicalize_manufacturer_candidate(payload.manufacturer)
                if manufacturer:
                    logging.info(
                        f"[{qr}] Manufacturer reread accepted from model '{model_name}': "
                        f"manufacturer='{manufacturer}'"
                    )
                    return manufacturer
            except Exception as e:
                if is_quota_error(e):
                    raise QuotaExceeded(qr) from e
                if is_auth_error(e):
                    raise AuthFailed(qr) from e
                logging.warning(f"[{qr}] Manufacturer reread attempt failed on model '{model_name}': {e}")
        return ""

    def _reread_ubc_from_tag_llm(self, qr: str, images: Dict[str, str]) -> str:
        """
        Targeted model reread for UBC Tag using seq 1 (identification tag) only.
        """
        if not getattr(self, "client", None):
            return ""
        seq1_path = images.get("1") if isinstance(images, dict) else None
        if not self._is_readable_source_path(seq1_path or ""):
            return ""

        try:
            with open(seq1_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logging.warning(f"[{qr}] UBC reread skipped (cannot read seq1): {e}")
            return ""

        prompt = """
Re-read ONLY the UBC Tag from this identification-tag image.
Rules:
- Use only text visible in this image.
- Prefer values from '<PREFIX> NO.' fields (example: 'FC NO. 1N 02').
- Preserve prefix and separators, normalized as PREFIX-CORE (example: FC-1N.02).
- Some equipment uses a large placard-style identifier instead of a '<PREFIX> NO.' plate (examples: 'DST-4', 'EF-1', 'HUM 5'). If there is no '<PREFIX> NO.' field, return the placard identifier exactly as printed.
- Do not return model, order, or serial numbers as UBC Tag.
- If unreadable, return empty string.
Output must be strict JSON with exactly one key: "UBC Tag".
""".strip()

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]

        primary_models, fallback_models = self._build_model_tiers()
        model_candidates: List[str] = []
        for name in [*primary_models, *fallback_models]:
            if name and name not in model_candidates:
                model_candidates.append(name)

        for model_name in model_candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        220,
                        model_name,
                        hard=self._is_fallback_model_name(model_name),
                        targeted=True,
                    ),
                    "response_format": MEUBCTagOnlyExtraction,
                }
                self._apply_sampling_options(kwargs, model_name)
                effort = self._reasoning_effort_for_model(
                    model_name,
                    hard=self._is_fallback_model_name(model_name),
                )
                if effort:
                    kwargs["reasoning_effort"] = effort
                resp = self.client.beta.chat.completions.parse(**kwargs)
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None) or msg.parsed is None:
                    continue

                parsed: MEUBCTagOnlyExtraction = msg.parsed
                ubc_raw = parsed.model_dump(by_alias=True).get("UBC Tag", "")
                ubc_value = self._normalize_ubc_tag_with_context(ubc_raw, images, allow_ocr=False)
                if ubc_value and not self._needs_ocr_for_ubc(ubc_value):
                    logging.info(f"[{qr}] UBC reread accepted from model '{model_name}': {ubc_value}")
                    return ubc_value
            except Exception as e:
                if is_quota_error(e):
                    raise QuotaExceeded(qr) from e
                if is_auth_error(e):
                    raise AuthFailed(qr) from e
                logging.warning(f"[{qr}] UBC reread attempt failed on model '{model_name}': {e}")
        return ""

    def _reread_tsbc_from_unit_no_llm(self, qr: str, images: Dict[str, str]) -> str:
        """
        Targeted model reread for Technical Safety BC using seq 3 only.
        The field stores the Safety Authority sticker's UNIT NO. value.
        """
        if not getattr(self, "client", None):
            return ""
        seq3_path = images.get("3") if isinstance(images, dict) else None
        if not self._is_readable_source_path(seq3_path or ""):
            return ""

        prompt = """
Re-read ONLY the Technical Safety BC unit number from this Safety Authority sticker image.
Rules:
- Use only text visible in this seq 3 image.
- The first images may be zoomed crops of the UNIT NO. row/value area; the final image is the full sticker for context.
- Find the row labeled "UNIT NO.", "UNIT NO", "BC Safety Authority Unit No.", or "Safety Authority Unit No."; return the value written in that row.
- On BPV Equipment Survey / Heating System stickers, the row may be labeled "5) BC Safety Authority Unit No."; use the value in that same row/right-side box.
- Ignore W.P., working pressure, S.O. NO., order numbers, dates, revision/form text, and the words "Safety Authority".
- Use the prefix "PV" for Pressure Vessel unit numbers; do not read this prefix as "AU".
- Preserve the visible prefix and all six visible digits for PV unit numbers (example: PV126541).
- If a PV value has fewer than six visible digits, treat it as unreadable and return empty string rather than guessing.
- If any digit is ambiguous, return empty string rather than guessing.
- If the UNIT NO. row is absent or unreadable, return empty string.
Output must be strict JSON with exactly one key: "Technical Safety BC".
""".strip()

        image_content = self._build_tsbc_unit_no_image_content(seq3_path)
        if not image_content:
            logging.warning(f"[{qr}] Technical Safety BC reread skipped (cannot read seq3 image content).")
            return ""
        content = [{"type": "text", "text": prompt}, *image_content]

        primary_models, fallback_models = self._build_model_tiers()
        model_candidates: List[str] = []
        for name in [*primary_models, *fallback_models]:
            if name and name not in model_candidates:
                model_candidates.append(name)

        for model_name in model_candidates:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        220,
                        model_name,
                        hard=self._is_fallback_model_name(model_name),
                        targeted=True,
                    ),
                    "response_format": METechnicalSafetyBCOnlyExtraction,
                }
                self._apply_sampling_options(kwargs, model_name)
                effort = self._reasoning_effort_for_model(
                    model_name,
                    hard=self._is_fallback_model_name(model_name),
                )
                if effort:
                    kwargs["reasoning_effort"] = effort
                resp = self.client.beta.chat.completions.parse(**kwargs)
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None) or msg.parsed is None:
                    continue

                parsed: METechnicalSafetyBCOnlyExtraction = msg.parsed
                tsbc_raw = parsed.model_dump(by_alias=True).get("Technical Safety BC", "")
                tsbc_value = self._normalize_tsbc_unit_no(tsbc_raw)
                if tsbc_value:
                    logging.info(f"[{qr}] Technical Safety BC reread accepted from model '{model_name}': {tsbc_value}")
                    return tsbc_value
            except Exception as e:
                if is_quota_error(e):
                    raise QuotaExceeded(qr) from e
                if is_auth_error(e):
                    raise AuthFailed(qr) from e
                logging.warning(f"[{qr}] Technical Safety BC reread attempt failed on model '{model_name}': {e}")
        return ""

    @staticmethod
    def _build_tsbc_unit_no_image_content(seq3_path: str) -> List[Dict[str, Any]]:
        """
        Build a crop-first image list for TSBC rereads.

        BPV survey stickers put the value in the lower right of the orange table,
        while older Safety Authority stickers put UNIT NO. near the upper rows of
        the blue label. These broad crops keep both layouts visible and give the
        model a zoomed row/value view before the full image.
        """
        content: List[Dict[str, Any]] = []

        if cv2 is not None:
            try:
                img = cv2.imread(seq3_path)
            except Exception:
                img = None
            if img is not None and img.size > 0:
                h, w = img.shape[:2]
                regions = (
                    # Right-side value box used by BPV Equipment Survey labels.
                    (0.36, 0.42, 0.98, 0.64),
                    # Wider row crop that keeps the left label and handwritten value together.
                    (0.04, 0.49, 0.98, 0.66),
                )
                for x0r, y0r, x1r, y1r in regions:
                    x0, x1 = int(w * x0r), int(w * x1r)
                    y0, y1 = int(h * y0r), int(h * y1r)
                    crop = img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
                    if crop.size == 0 or crop.shape[0] < 20 or crop.shape[1] < 40:
                        continue
                    max_dim = max(crop.shape[:2])
                    scale = min(2.8, max(1.0, 1800.0 / max(1, max_dim)))
                    if scale > 1.01:
                        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                    crop = cv2.addWeighted(crop, 1.6, cv2.GaussianBlur(crop, (0, 0), 2), -0.6, 0)
                    try:
                        ok, encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    except Exception:
                        ok, encoded = False, None
                    if ok and encoded is not None:
                        crop_b64 = base64.b64encode(encoded.tobytes()).decode("utf-8")
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{crop_b64}",
                                    "detail": "high",
                                },
                            }
                        )

        try:
            with open(seq3_path, "rb") as f:
                full_b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return content
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{full_b64}",
                    "detail": "high",
                },
            }
        )
        return content

    def _fallback_year_from_ocr(self, images: Dict[str, str]) -> str:
        """Seq-0-only fallback to detect formats like 08/03 and convert to 2003 if LLM missed it."""
        if cv2 is None or not images:
            return ""
        seq0_path = images.get("0") if isinstance(images, dict) else None
        if not seq0_path:
            return ""

        roi_year = self._extract_year_from_rois(images)
        if roi_year:
            return roi_year

        for text in self._ocr_text_variants(seq0_path):
            candidates = self._extract_cued_year_candidates(text)
            if candidates:
                return candidates[0]
        return ""

    def _fallback_model_from_ocr(self, images: Dict[str, str]) -> str:
        """Fallback model extraction from OCR, strict to nameplate model formats."""
        ordered_paths: List[str] = []
        for seq in ["0", "1", "3"]:
            if seq in images:
                ordered_paths.append(images[seq])
        for _, p in images.items():
            if p not in ordered_paths:
                ordered_paths.append(p)

        best = ""
        for p in ordered_paths:
            for text in self._ocr_text_variants(p):
                parsed = self._parse_nameplate_model_serial(text).get("Model", "")
                if parsed and self._is_model_code_candidate(parsed):
                    compact_parsed = re.sub(r"[^A-Z0-9]", "", parsed)
                    compact_best = re.sub(r"[^A-Z0-9]", "", best)
                    if len(compact_parsed) >= len(compact_best):
                        best = parsed
        if best:
            return best[:80]
        return ""

    def _ocr_shows_mi(self, images: Dict[str, str]) -> bool:
        """Check OCR text for MI suffix patterns (including M1/Ml misreads from handwriting)."""
        for p in images.values():
            try:
                img = cv2.imread(p)
                if img is None:
                    continue
                text = pytesseract.image_to_string(img, timeout=10).upper()
                # Match MI, M1, or ML (lowercase L) - all indicate "MI" suffix
                # Patterns: "FCU MI", "FC MI", "FCU M1", "12 FC M1", etc.
                if re.search(r"FCU?\s*(MI|M1|ML)\b", text):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _parse_manufacturer_text(text: str) -> str:
        """
        Parse manufacturer mentions from raw/ocr text.
        Key case: WILLIAMS FURNACE COMPANY -> Williams
        """
        if not text:
            return ""
        raw = str(text)
        if "\n" in raw or len(raw) > 100:
            raw_lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
            lines = [line for line in raw_lines if line and len(line) <= 80]
            for line in lines[:16]:
                parsed = AssetProcessor._parse_manufacturer_text(line)
                if parsed:
                    return parsed
            for idx in range(len(lines) - 1):
                pair = f"{lines[idx]} {lines[idx + 1]}".strip()
                if not pair or len(pair) > 80:
                    continue
                parsed = AssetProcessor._parse_manufacturer_text(pair)
                if parsed:
                    return parsed
            return ""

        t = re.sub(r"\s+", " ", raw.upper()).strip()
        for pattern, canonical in ME_MANUFACTURER_REGEX_RULES:
            if re.search(pattern, t):
                return canonical
        compact_text = _compact_lookup_key(t)
        for alias_key, canonical in ME_MANUFACTURER_ALIAS_MATCHES:
            if alias_key and compact_text == alias_key:
                return canonical
        return ""

    def _extract_dark_nameplate_crop(self, image_path: str) -> Optional[np.ndarray]:
        """
        Detect dark table-style plates that use light value boxes on the right side.
        """
        if cv2 is None or not image_path:
            return None
        try:
            img = cv2.imread(image_path)
            if img is None:
                return None

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            mask = cv2.inRange(blurred, 0, 88)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8), iterations=2)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=1)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None

            h_img, w_img = gray.shape[:2]
            img_area = float(h_img * w_img)
            best_bbox: Optional[Tuple[int, int, int, int]] = None
            best_score = float("-inf")

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = float(w * h)
                if area < img_area * 0.01 or area > img_area * 0.20:
                    continue
                aspect = max(w, h) / max(1.0, min(w, h))
                if aspect < 1.1 or aspect > 2.5:
                    continue

                fill = cv2.contourArea(contour) / max(1.0, area)
                if fill < 0.55:
                    continue

                pad = max(12, int(min(w, h) * 0.06))
                x0 = max(0, x - pad)
                y0 = max(0, y - pad)
                x1 = min(w_img, x + w + pad)
                y1 = min(h_img, y + h + pad)
                outer = gray[y0:y1, x0:x1]
                inner = gray[y:y + h, x:x + w]
                if outer.size == 0 or inner.size == 0:
                    continue

                contrast = float(outer.mean() - inner.mean())
                score = (fill * 100.0) + contrast + ((area / img_area) * 40.0)
                if x > int(w_img * 0.78):
                    score -= 5.0
                if score > best_score:
                    best_score = score
                    best_bbox = (x, y, w, h)

            if not best_bbox:
                return None

            x, y, w, h = best_bbox
            crop = img[y:y + h, x:x + w]
            if crop.size == 0:
                return None
            if crop.shape[0] > crop.shape[1]:
                crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
            return crop
        except Exception:
            return None

    def _extract_light_nameplate_crop(self, image_path: str) -> Optional[np.ndarray]:
        """
        Detect bright/metallic rectangular nameplates such as Taco and Greenheck plates.
        """
        if cv2 is None or not image_path:
            return None
        try:
            img = cv2.imread(image_path)
            if img is None:
                return None

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            h_img, w_img = gray.shape[:2]
            img_area = float(h_img * w_img)

            best_bbox: Optional[Tuple[int, int, int, int]] = None
            best_score = float("-inf")

            def consider_contours(contours: List[np.ndarray], score_bias: float = 0.0) -> None:
                nonlocal best_bbox, best_score
                for contour in contours:
                    x, y, w, h = cv2.boundingRect(contour)
                    area = float(w * h)
                    if area < img_area * 0.003 or area > img_area * 0.28:
                        continue

                    aspect = max(w, h) / max(1.0, min(w, h))
                    if aspect < 1.4 or aspect > 6.5:
                        continue

                    fill = cv2.contourArea(contour) / max(1.0, area)
                    if fill < 0.18:
                        continue

                    peri = cv2.arcLength(contour, True)
                    approx = cv2.approxPolyDP(contour, 0.03 * peri, True)
                    center_x = x + (w / 2.0)
                    center_y = y + (h / 2.0)
                    center_penalty = (
                        abs(center_x - (w_img / 2.0)) / max(1.0, w_img)
                        + abs(center_y - (h_img / 2.0)) / max(1.0, h_img)
                    ) * 18.0
                    shape_bonus = 12.0 if len(approx) == 4 else 0.0
                    portrait_penalty = 7.0 if h > (w * 2.8) else 0.0

                    score = (
                        ((area / img_area) * 80.0)
                        + (fill * 55.0)
                        + shape_bonus
                        + score_bias
                        - center_penalty
                        - portrait_penalty
                    )
                    if score > best_score:
                        best_score = score
                        best_bbox = (x, y, w, h)

            edges = cv2.Canny(blurred, 50, 150)
            edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
            contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            consider_contours(contours, score_bias=6.0)

            if best_bbox is None:
                mask = cv2.inRange(blurred, 132, 255)
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8), iterations=2)
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=1)
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                consider_contours(contours)

            if not best_bbox:
                return None

            x, y, w, h = best_bbox
            if h > (w * 2.8):
                expand_left = int(w * 4.2)
                expand_right = int(w * 0.9)
                expand_y = int(h * 0.18)
                x0 = max(0, x - expand_left)
                y0 = max(0, y - expand_y)
                x1 = min(w_img, x + w + expand_right)
                y1 = min(h_img, y + h + expand_y)
            else:
                expand_left = max(12, int(w * 0.65))
                expand_right = max(12, int(w * 0.12))
                expand_up = max(12, int(h * 0.35))
                expand_down = max(12, int(h * 0.20))
                x0 = max(0, x - expand_left)
                y0 = max(0, y - expand_up)
                x1 = min(w_img, x + w + expand_right)
                y1 = min(h_img, y + h + expand_down)

            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                return None
            if crop.shape[0] > crop.shape[1]:
                crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
            return crop
        except Exception:
            return None

    def _score_nameplate_crop(self, crop: np.ndarray) -> int:
        if cv2 is None or crop is None or crop.size == 0:
            return -1

        try:
            gray = crop if len(crop.shape) == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = self._resize_gray_for_fast_ocr(gray)
            adaptive = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 5
            )
            cue_re = re.compile(
                r"\b(?:MODEL|SER(?:IAL)?|S/?N|ORDER|MFG|YEAR|TYPE|DOE|TACO|GREENHECK|REPUBLIC)\b",
                re.IGNORECASE,
            )
            score = 0
            for variant in (gray, adaptive):
                txt = self._safe_tesseract_text(variant, "--oem 3 --psm 6")
                if not txt:
                    continue
                score += len(cue_re.findall(txt.upper()))
            return score
        except Exception:
            return -1

    def _ocr_text_from_dark_nameplate(self, image_path: str) -> List[str]:
        """
        OCR dark table-style plates and a few focused subregions for manufacturer recovery.
        """
        crop = self._extract_dark_nameplate_crop(image_path)
        if crop is None or crop.size == 0:
            return []

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = self._resize_gray_for_fast_ocr(gray)
        if not Config.SIMPLE_MODE:
            gray = cv2.resize(gray, None, fx=1.35, fy=1.35, interpolation=cv2.INTER_CUBIC)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        sharp = cv2.addWeighted(clahe, 1.7, cv2.GaussianBlur(clahe, (0, 0), 2), -0.7, 0)
        adaptive = cv2.adaptiveThreshold(
            sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 5
        )

        h, w = gray.shape[:2]
        manufacturer_roi = sharp[0:max(40, int(h * 0.30)), 0:max(80, int(w * 0.55))]
        table_roi = sharp[0:max(80, int(h * 0.55)), int(w * 0.40):w]
        variants: List[np.ndarray] = [gray, clahe, sharp, adaptive, manufacturer_roi, table_roi]

        texts: List[str] = []
        for idx, variant in enumerate(variants):
            if variant is None or variant.size == 0:
                continue
            psm_modes = (11,) if Config.SIMPLE_MODE and idx >= 4 else (6, 11)
            for psm in psm_modes:
                txt = self._safe_tesseract_text(variant, f"--oem 3 --psm {psm}")
                if txt:
                    texts.append(txt.upper())
        return list(dict.fromkeys(texts))

    @staticmethod
    def _resize_gray_for_fast_ocr(gray: np.ndarray) -> np.ndarray:
        """
        Keep OCR inputs bounded in simple mode.
        Large full-frame images can timeout OCR when upscaled aggressively.
        """
        if gray is None or gray.size == 0:
            return gray
        h, w = gray.shape[:2]
        max_dim = max(h, w)
        if max_dim > 1200:
            scale = 1200.0 / float(max_dim)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        elif max_dim < 800:
            gray = cv2.resize(gray, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_CUBIC)
        return gray

    def _ocr_text_from_red_nameplate(self, image_path: str) -> List[str]:
        """
        Targeted OCR for red metal nameplates (e.g., Rockwell plates with vertical serial text).
        Runs only a few bounded OCR calls to keep simple-mode performance stable.
        """
        if cv2 is None or not image_path:
            return []
        try:
            img = cv2.imread(image_path)
            if img is None:
                return []

            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            lower1 = np.array([0, 70, 45], dtype=np.uint8)
            upper1 = np.array([12, 255, 255], dtype=np.uint8)
            lower2 = np.array([165, 70, 45], dtype=np.uint8)
            upper2 = np.array([180, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower1, upper1) | cv2.inRange(hsv, lower2, upper2)
            mask = cv2.medianBlur(mask, 5)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return []

            h, w = img.shape[:2]
            min_area = max(1500, int(0.004 * h * w))
            best_bbox = None
            best_area = 0
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area >= min_area and area > best_area:
                    best_area = area
                    best_bbox = (x, y, bw, bh)
            if not best_bbox:
                return []

            x, y, bw, bh = best_bbox
            px = max(8, int(bw * 0.2))
            py = max(8, int(bh * 0.08))
            x0 = max(0, x - px)
            y0 = max(0, y - py)
            x1 = min(w, x + bw + px)
            y1 = min(h, y + bh + py)
            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                return []

            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = self._resize_gray_for_fast_ocr(gray)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            adaptive = cv2.adaptiveThreshold(
                enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 7
            )

            variants = [
                cv2.rotate(adaptive, cv2.ROTATE_90_CLOCKWISE),
                cv2.rotate(adaptive, cv2.ROTATE_90_COUNTERCLOCKWISE),
                cv2.rotate(enhanced, cv2.ROTATE_90_CLOCKWISE),
            ]
            if not Config.SIMPLE_MODE:
                variants.append(cv2.rotate(enhanced, cv2.ROTATE_90_COUNTERCLOCKWISE))

            texts: List[str] = []
            psm_modes = (11,) if Config.SIMPLE_MODE else (6, 11)
            for variant in variants:
                for psm in psm_modes:
                    try:
                        txt = pytesseract.image_to_string(
                            variant,
                            config=f"--oem 3 --psm {psm}",
                            timeout=5 if Config.SIMPLE_MODE else 8,
                        )
                    except TypeError:
                        try:
                            txt = pytesseract.image_to_string(variant, config=f"--oem 3 --psm {psm}")
                        except Exception:
                            txt = ""
                    except Exception:
                        txt = ""
                    if txt:
                        texts.append(txt.upper())
            return list(dict.fromkeys(texts))
        except Exception:
            return []

    def _ocr_text_candidates_for_manufacturer(self, images: Dict[str, str]) -> List[str]:
        """
        OCR scan for manufacturer recovery.
        Prioritizes asset plate image and rotated reads because manufacturer text
        is often vertical near plate edges.
        """
        if cv2 is None or not images:
            return []

        ordered_paths: List[str] = []
        seq_order = ["0", "1", "3"] if not Config.SIMPLE_MODE else ["0"]
        for seq in seq_order:
            if seq in images:
                ordered_paths.append(images[seq])
        for _, p in images.items():
            if p not in ordered_paths:
                ordered_paths.append(p)

        texts: List[str] = []
        if Config.SIMPLE_MODE and "0" in images:
            red_texts = self._ocr_text_from_red_nameplate(images["0"])
            if red_texts:
                texts.extend(red_texts)
                if any(self._parse_manufacturer_text(t) for t in red_texts):
                    return texts
            dark_texts = self._ocr_text_from_dark_nameplate(images["0"])
            if dark_texts:
                texts.extend(dark_texts)
                if any(self._parse_manufacturer_text(t) for t in dark_texts):
                    return texts

        rotations = [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]
        if Config.SIMPLE_MODE:
            rotations = [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]
        for p in ordered_paths:
            try:
                img = cv2.imread(p)
                if img is None:
                    continue
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                gray = self._resize_gray_for_fast_ocr(gray)
                for rot in rotations:
                    candidate = gray if rot is None else cv2.rotate(gray, rot)
                    thresh = cv2.adaptiveThreshold(
                        candidate, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
                    )
                    psm_list = (11,) if Config.SIMPLE_MODE else (6,)
                    text = ""
                    for psm in psm_list:
                        text = self._safe_tesseract_text(thresh, f"--oem 3 --psm {psm}")
                        if text:
                            break
                    if text:
                        texts.append(text.upper())
            except Exception:
                continue
        if "0" in images:
            texts.extend(self._ocr_text_from_dark_nameplate(images["0"]))
            texts.extend(self._ocr_text_from_red_nameplate(images["0"]))
        return list(dict.fromkeys(texts))

    def _normalize_manufacturer_with_context(
        self,
        raw_manufacturer: str,
        images: Dict[str, str],
        allow_ocr: bool = True,
    ) -> str:
        """
        Normalize manufacturer with OCR fallback for edge text on asset plates.
        """
        parsed_raw = self._canonicalize_manufacturer_candidate(raw_manufacturer)
        if parsed_raw:
            return parsed_raw

        if not allow_ocr:
            return ""

        for text in self._ocr_text_candidates_for_manufacturer(images):
            recovered = self._parse_manufacturer_text(text)
            if recovered:
                logging.info(f"[Manufacturer] Recovered from OCR context: {recovered}")
                return recovered

        return ""

    def _infer_manufacturer_from_model(self, model_value: str) -> str:
        """Return a confirmed manufacturer only for an exact known model identity."""
        compact_model = re.sub(r"[^A-Z0-9]", "", str(model_value or "").upper())
        if not compact_model:
            return ""
        for pattern, manufacturer in ME_MODEL_MANUFACTURER_RULES:
            if re.fullmatch(pattern, compact_model):
                return self._canonicalize_manufacturer_candidate(manufacturer)
        return ""

    @staticmethod
    def _is_tag_like_model_candidate(value: str) -> bool:
        """
        Detect short UBC/tag-style tokens that should not be treated as equipment model.
        Examples: FC MI, FCU MI, HUM 5, FC-5C.14
        """
        v = re.sub(r"\s+", " ", (value or "").upper()).strip()
        if not v:
            return False
        compact = re.sub(r"[^A-Z0-9]", "", v)
        if re.fullmatch(r"(?:\d+\s*)?FCU?\s*(?:MI|M1|ML)", v):
            return True
        if re.fullmatch(r"HUM\s*\d{1,4}", v):
            return True
        if re.fullmatch(r"[A-Z]{1,4}[-\s]?\d[A-Z]?\.\d{1,4}", v):
            return True
        # Allow models with at least 2 digits (e.g., ZT15) instead of requiring 4.
        if len(compact) <= 7 and not re.search(r"\d{2}", compact):
            return True
        return False

    def _manufacturer_allows_numeric_model(self, manufacturer_hint: str) -> bool:
        """True if the manufacturer is known to use all-numeric model numbers."""
        if not manufacturer_hint:
            return False
        canonical = self._canonicalize_manufacturer_candidate(manufacturer_hint) or str(manufacturer_hint)
        return _compact_lookup_key(canonical) in ME_NUMERIC_MODEL_MANUFACTURER_KEYS

    def _is_model_code_candidate(self, value: str, manufacturer_hint: str = "") -> bool:
        """
        Detect likely equipment model code from nameplate.

        A model normally must contain both a letter and a digit. The lone
        exception is manufacturers in ME_NUMERIC_MODEL_MANUFACTURERS (e.g.
        Siemens), whose model numbers are all digits; callers pass
        manufacturer_hint so those survive instead of being discarded.
        """
        v = (value or "").strip().upper()
        if not v:
            return False
        if self._is_tag_like_model_candidate(v):
            return False
        compact = re.sub(r"[^A-Z0-9]", "", v)
        if len(compact) < 5 or len(compact) > ME_MAX_MODEL_CODE_LENGTH:
            return False
        if len(compact) > 32:
            # Long values must remain code-like. This admits dense OEM
            # configuration strings while keeping prose/instructions out.
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9\/\.\-\(\) ]*", v):
                return False
            if len(re.findall(r"[A-Z]", compact)) < 2 or len(re.findall(r"\d", compact)) < 8:
                return False
            if len(v.split()) > 4:
                return False
        if re.search(
            r"\b(PUMP\w*|SPEE?\w*|MOT\w*|VACU\w*|PRES\w*|CAPAC\w*|INLET|R\.?P\.?M\.?)\b",
            v,
        ):
            return False
        if not re.search(r"\d", compact):
            return False
        if not re.search(r"[A-Z]", compact):
            # All-numeric model: accept only for whitelisted manufacturers, and
            # only in a model-like length band that excludes year/date misreads.
            if not self._manufacturer_allows_numeric_model(manufacturer_hint):
                return False
            if not (5 <= len(compact) <= 12):
                return False
            if re.fullmatch(r"(19|20)\d{2}", compact) or re.fullmatch(r"(19|20)\d{6}", compact):
                return False
            return True
        # Accept common short tool/equipment model forms (e.g., LS1016, C372)
        # and tank models with a short alphabetic suffix (e.g., L 600A-TR).
        # Separators are removed into ``compact`` above, so L 600A-TR becomes
        # L600ATR and requires up to three trailing letters.
        if len(compact) < 8:
            return bool(
                re.fullmatch(r"[A-Z]{1,3}\d{3,5}[A-Z]{0,3}", compact)
                or re.fullmatch(r"[A-Z]{3,6}\d{2,4}", compact)
                or re.fullmatch(r"[A-Z]{2,5}\d[A-Z]\d{2,3}", compact)
            )
        return True

    def _model_candidates_near_label(
        self,
        text: str,
        manufacturer_hint: str = "",
    ) -> List[str]:
        """Return only model-shaped values immediately following a model label."""
        upper = re.sub(r"[|]", "I", str(text or "").upper())
        if not upper:
            return []

        cue_re = re.compile(
            r"(?:"
            r"\b(?:DOE\s+)?BASIC\s+MODEL\b|"
            r"\bUNIT\s+MODEL\b|"
            r"(?<!BASIC )\bMODEL\b|"
            r"\bMOD(?:EL)?\b|"
            r"\bTYPE\b|"
            r"\bCAT(?:ALOG)?\b|"
            r"\bITEM\b"
            r")\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*",
            re.IGNORECASE,
        )
        boundary_re = re.compile(
            r"\b(?:SALES\s+ORDER|SERIAL|SER|S/?N|ORDER|VOLTS|AMPS|HZ|HERTZ|"
            r"PHASE|HP|KW|QUANTITY|QTY|MAX|MIN|DESIGN|PRESS|PRESSURE|DATE|"
            r"MFG|PROD|CAPACITY|GPM|RPM|HEAD|MOTOR|VACUUM|INLET)\b",
            re.IGNORECASE,
        )
        value_re = re.compile(
            rf"^\s*([A-Z0-9][A-Z0-9\/\.\-\(\)\s]{{3,{ME_MAX_MODEL_CODE_LENGTH - 1}}})",
            re.IGNORECASE,
        )

        candidates: List[str] = []
        for cue in cue_re.finditer(upper):
            tail = upper[cue.end(): cue.end() + ME_MAX_MODEL_CODE_LENGTH + 80]
            tail = boundary_re.split(tail, maxsplit=1)[0]
            match = value_re.match(tail)
            if not match:
                continue
            candidate = self._normalize_model_candidate(
                match.group(1),
                manufacturer_hint,
            )
            if candidate and self._is_model_code_candidate(candidate, manufacturer_hint):
                candidates.append(candidate)
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _matches_rheem_ruud_serial_shape(value: str) -> bool:
        """
        Rheem/Ruud water-heater serials are letter-prefixed digit runs
        (example: A221812671). Used to trust a targeted reread result even
        when rotated/garbled OCR cannot corroborate it.
        """
        compact = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
        return bool(re.fullmatch(r"[A-Z]\d{8,10}", compact))

    @staticmethod
    def _model_serial_values_collide(model_value: Any, serial_value: Any) -> bool:
        model_compact = re.sub(r"[^A-Z0-9]", "", str(model_value or "").upper())
        serial_compact = re.sub(r"[^A-Z0-9]", "", str(serial_value or "").upper())
        return bool(
            len(model_compact) >= 4
            and model_compact == serial_compact
        )

    @staticmethod
    def _is_serial_candidate(value: str) -> bool:
        v = (value or "").strip().upper()
        if not v:
            return False
        compact = re.sub(r"[^A-Z0-9]", "", v)
        if len(compact) < 4 or len(compact) > 24:
            return False
        if not re.search(r"\d", compact):
            return False
        # Reject date-like OCR misreads (e.g., 20201010).
        if re.fullmatch(r"(19|20)\d{6}", compact):
            return False
        # Reject date-shaped values, including upside-down/rotated misreads
        # (e.g., "8102/90" is "09/2018" read from a rotated nameplate).
        if looks_like_date_misread_serial(v):
            return False
        # Very short serials should include a letter (e.g., 4762A).
        if len(compact) < 6 and not re.search(r"[A-Z]", compact):
            return False
        return True

    def _needs_ocr_for_model_serial(self, llm_model: str, llm_serial: str) -> bool:
        model_weak = not self._is_model_code_candidate(llm_model)
        serial_weak = not self._is_serial_candidate(llm_serial)
        
        # Williams models are notoriously long and often truncated by LLM (e.g. 'HH 022W4R41 1L 000' -> 'HH02W4R41 1L 000')
        # If it's a Williams HH series, always trigger OCR to cross-check length.
        if "HH" in llm_model.upper() and len(llm_model) > 8:
            model_weak = True

        return model_weak or serial_weak

    def _has_serial_label_evidence(
        self,
        serial_value: str,
        images: Dict[str, str],
        evidence_texts: Optional[List[str]] = None,
        manufacturer_hint: str = "",
    ) -> bool:
        """
        Verify that a proposed serial has OCR support near serial cues on seq 0.
        Used as a guard for suspicious digits-only serials.
        """
        serial_compact = re.sub(r"[^A-Z0-9]", "", (serial_value or "").upper())
        if len(serial_compact) < 4:
            return False

        texts = evidence_texts if evidence_texts is not None else self._collect_nameplate_evidence_texts(images)
        if not texts:
            return False

        manufacturer_key = _compact_lookup_key(manufacturer_hint)
        label_patterns = [
            r"\bSERIAL\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\s/\.]{2,30})",
            r"\bSER\.?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\s/\.]{2,30})",
            r"\bS/?N\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\s/\.]{2,30})",
        ]
        # Taco plates frequently contain PART No., CRN No., and an unlabeled
        # certification identifier but no serial. Do not promote Order/Part/CRN
        # values for this manufacturer. Preserve the intentional Siemens Product
        # No. exception and the legacy low-confidence Order No. fallback elsewhere.
        if manufacturer_key != "TACO":
            label_patterns.append(
                r"\bORDER\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\s/\.]{2,30})"
            )
        if self._manufacturer_allows_numeric_model(manufacturer_hint):
            label_patterns.append(
                r"\bPROD(?:UCT)?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\s/\.]{2,30})"
            )

        for text in texts:
            upper_text = str(text or "").upper()
            for pattern in label_patterns:
                for match in re.finditer(pattern, upper_text):
                    raw_candidate = re.split(
                        r"\b(?:MODEL|MOD|TYPE|PART|CRN|BASIC|CAT|ITEM|VOLTS|AMPS|PSI|DATE|MFG|PROD|MADE)\b",
                        match.group(1),
                    )[0]
                    candidate = self._normalize_serial_candidate(
                        self._clean_labeled_serial_value(raw_candidate),
                        manufacturer_hint,
                    )
                    candidate_compact = re.sub(r"[^A-Z0-9]", "", candidate.upper())
                    if candidate_compact == serial_compact:
                        return True
        return False

    @staticmethod
    def _has_pressure_vessel_context(evidence_texts: Optional[List[str]]) -> bool:
        """Require CRN plus independent pressure-vessel/nameplate evidence."""
        joined = "\n".join(str(text or "").upper() for text in (evidence_texts or []))
        has_crn = bool(
            re.search(r"\bC\s*[ER]?\s*N\s*(?:NO\.?|NUMBER|#)?\b", joined)
            or re.search(r"\bCERN", joined)
        )
        context_cues = (
            r"\bMAWP(?:-S|-T)?\b",
            r"\bMDMT\b",
            r"\bASME\b",
            r"\bCERTIFIED\s+BY\b",
            r"\bNATIONAL\s+BOARD\b",
            r"\bPSI\s+AT\b",
        )
        cue_count = sum(bool(re.search(pattern, joined)) for pattern in context_cues)
        return (has_crn and cue_count >= 1) or cue_count >= 3

    def _is_pressure_vessel_unlabeled_serial_candidate(
        self,
        serial_value: str,
        evidence_texts: Optional[List[str]],
        *,
        vision_confirmed_context: bool = False,
    ) -> bool:
        """Validate a vision-read, unlabeled top identifier on a CRN vessel plate."""
        value = self._normalize_serial_candidate(serial_value)
        compact = re.sub(r"[^A-Z0-9]", "", value.upper())
        if not (
            vision_confirmed_context
            or self._has_pressure_vessel_context(evidence_texts)
        ):
            return False
        if not re.fullmatch(r"\d{4,10}", value):
            return False
        if not re.search(r"\d", compact) or looks_like_date_misread_serial(value):
            return False

        # If OCR can place the same value after a forbidden field label, it is
        # that labeled value—not the unlabeled top identifier described by vision.
        for text in evidence_texts or []:
            upper = str(text or "").upper()
            forbidden = re.compile(
                r"\b(?:PART|CRN|DATE|MAWP|MDMT)\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*"
                + re.escape(value.upper())
                + r"\b"
            )
            if forbidden.search(upper):
                return False
        return True

    def _has_model_label_evidence(
        self,
        model_value: str,
        images: Dict[str, str],
        evidence_texts: Optional[List[str]] = None,
    ) -> bool:
        """
        Verify that a proposed model is supported by seq 0 OCR evidence near model-like cues.
        """
        model_norm = self._normalize_model_candidate(model_value or "")
        model_compact = re.sub(r"[^A-Z0-9]", "", model_norm.upper())
        if len(model_compact) < 5:
            return False

        texts = evidence_texts if evidence_texts is not None else self._collect_nameplate_evidence_texts(images)
        if not texts:
            return False

        for text in texts:
            upper = (text or "").upper()
            parsed_model = self._normalize_model_candidate(self._parse_nameplate_model_serial(upper).get("Model", ""))
            parsed_compact = re.sub(r"[^A-Z0-9]", "", parsed_model.upper())
            if parsed_compact and parsed_compact == model_compact:
                return True

            for labeled_candidate in self._model_candidates_near_label(upper):
                labeled_compact = re.sub(r"[^A-Z0-9]", "", labeled_candidate.upper())
                if labeled_compact == model_compact:
                    return True

        return False

    def _has_year_evidence(
        self,
        year_value: str,
        images: Dict[str, str],
        evidence_texts: Optional[List[str]] = None,
    ) -> bool:
        """
        Verify that a proposed year appears in current seq 0 OCR evidence.
        Allows two-digit representations matching the four digit year.
        """
        year = self._normalize_year_flexible(year_value or "")
        if not year or not re.fullmatch(r"(19|20)\d{2}", str(year)):
            return False
        texts = evidence_texts if evidence_texts is not None else self._collect_nameplate_evidence_texts(images)
        if not texts:
            return False

        two_digit = str(year)[-2:]
        year_re = re.compile(rf"(?<!\d){re.escape(str(year))}(?!\d)")
        date_cue_re = re.compile(r"\b(?:MFG|MFG\.?\s*DATE|MANUF(?:ACTURED)?|PROD(?:UCTION)?|DATE|YEAR)\b", re.IGNORECASE)
        
        for text in texts:
            upper = (text or "").upper()
            if year_re.search(upper):
                return True
                
            # Extremely robust verification against noisy OCR MM/YY formats
            text_clean = re.sub(r"[\s\.]+", "", upper)
            # Replace letter O with 0 for robustness
            text_clean = text_clean.replace('O', '0')
            if re.search(rf"(?:0?[1-9]|1[0-2])[/\\\-]{two_digit}(?:\D|$)", text_clean):
                return True

            if date_cue_re.search(upper) and re.search(rf"(?:0?[1-9]|1[0-2]){two_digit}", text_clean):
                return True

            # Looser MM YY evidence only when serial/order cue exists in same OCR chunk.
            if re.search(r"\bSER(?:IAL)?\b|\bORDER\b", upper):
                if re.search(rf"(?:0?[1-9]|1[0-2])\s+{two_digit}(?:\D|$)", upper):
                    return True
                
        return False

    def _best_serial_candidate_from_texts(self, texts: List[str]) -> str:
        """
        Choose the most plausible serial from OCR evidence texts using weighted voting.
        Prefers values tied to SERIAL/ORDER cues and common numeric plate patterns.
        """
        if not texts:
            return ""
        scores: Dict[str, int] = {}

        def add_score(candidate: str, weight: int) -> None:
            cand = self._normalize_serial_candidate(candidate or "")
            if not self._is_serial_candidate(cand):
                return
            scores[cand] = scores.get(cand, 0) + weight

        for text in texts:
            upper = (text or "").upper()
            if not upper:
                continue

            parsed = self._parse_nameplate_model_serial(upper).get("Serial Number", "")
            if parsed:
                add_score(parsed, 4)

            # SERIAL-cued candidates outrank ORDER/PRODUCT-cued fallbacks.
            for m in re.finditer(
                r"\bSER(?:IAL)?\.?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/\.\s]{2,30})",
                upper,
            ):
                add_score(self._clean_labeled_serial_value(m.group(1)), 4)
            for m in re.finditer(
                r"\b(?:ORDER|PROD(?:UCT)?)\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/\.\s]{2,30})",
                upper,
            ):
                add_score(self._clean_labeled_serial_value(m.group(1)), 3)

            m_num_date = re.search(
                r"\b(\d{4,8})\b\s*(?:0?[1-9]|1[0-2])\s*[/\\\-]\s*[0-9O]{2}\b",
                upper,
            )
            if m_num_date:
                add_score(m_num_date.group(1), 2)

        if not scores:
            return ""

        def rank_key(item: Tuple[str, int]) -> Tuple[int, int, int]:
            cand, score = item
            compact = re.sub(r"[^A-Z0-9]", "", cand)
            # Prefer 6-8 digit numeric serials for Williams-style plates.
            numeric_bonus = 1 if compact.isdigit() and 6 <= len(compact) <= 8 else 0
            return (score, numeric_bonus, len(compact))

        return max(scores.items(), key=rank_key)[0]

    def _best_year_candidate_from_texts(self, texts: List[str]) -> str:
        """
        Choose the best year candidate from OCR evidence texts.
        Only accepts years attached to explicit manufacturing/year cues.
        """
        if not texts:
            return ""
        scores: Dict[str, int] = {}

        def add_year(year_candidate: str, weight: int) -> None:
            y = self._normalize_year_flexible(year_candidate or "")
            if re.fullmatch(r"(19|20)\d{2}", y):
                scores[y] = scores.get(y, 0) + weight

        for text in texts:
            for year_candidate in self._extract_cued_year_candidates(text):
                weight = 4 if re.search(r"\bYEAR\b", str(text or "").upper()) else 3
                add_year(year_candidate, weight)

        if not scores:
            return ""
        return max(scores.items(), key=lambda kv: kv[1])[0]

    @staticmethod
    def _coerce_two_digit_year(yy_str: str) -> str:
        yy_norm = str(yy_str or "").upper().replace("O", "0")
        if not re.fullmatch(r"\d{2}", yy_norm):
            return ""
        yy = int(yy_norm)
        if 0 <= yy <= 26:
            return f"20{yy:02d}"
        if 50 <= yy <= 99:
            return f"19{yy:02d}"
        return ""

    def _extract_cued_year_candidates(self, text: str) -> List[str]:
        if not text:
            return []

        upper = re.sub(r"\s+", " ", str(text or "").upper()).strip()
        if not upper:
            return []
        compact = re.sub(r"[\s\.]+", "", upper).replace("O", "0")
        years: List[str] = []

        def add(candidate: str) -> None:
            year_value = self._normalize_year_flexible(candidate or "")
            if year_value and re.fullmatch(r"(19|20)\d{2}", year_value) and year_value not in years:
                years.append(year_value)

        for m in re.finditer(r"\bYEAR\b[^0-9]{0,12}(19\d{2}|20\d{2})(?!\d)", upper):
            add(m.group(1))
        m = re.search(r"YEAR[^0-9]{0,12}((?:0?[1-9]|1[0-2])\d{2})", compact)
        if m:
            add(m.group(1))

        cue_text_re = re.compile(
            r"\b(?:MFG(?:\.?\s*DATE)?|MANUF(?:ACTURED)?|PROD(?:UCTION)?(?:\s*DATE)?)\b",
            re.IGNORECASE,
        )
        cue_compact_re = re.compile(r"(?:MFG(?:DATE)?|MANUF(?:ACTURED)?|PROD(?:UCTION)?(?:DATE)?)")
        if cue_text_re.search(upper):
            for m in re.finditer(
                r"\b(?:MFG(?:\.?\s*DATE)?|MANUF(?:ACTURED)?|PROD(?:UCTION)?(?:\s*DATE)?)\b[^0-9]{0,16}(19\d{2}|20\d{2})(?!\d)",
                upper,
                re.IGNORECASE,
            ):
                add(m.group(1))
            for m in re.finditer(
                r"\b(?:MFG(?:\.?\s*DATE)?|MANUF(?:ACTURED)?|PROD(?:UCTION)?(?:\s*DATE)?)\b[^0-9]{0,16}(?:0?[1-9]|1[0-2])\s*[/\\\-]\s*([0-9O]{2})(?:\D|$)",
                upper,
                re.IGNORECASE,
            ):
                coerced = self._coerce_two_digit_year(m.group(1))
                if coerced:
                    add(coerced)
        if cue_compact_re.search(compact):
            m = re.search(
                r"(?:MFG(?:DATE)?|MANUF(?:ACTURED)?|PROD(?:UCTION)?(?:DATE)?)[^0-9]{0,12}((?:0?[1-9]|1[0-2])\d{2})",
                compact,
            )
            if m:
                add(m.group(1))

        return years

    def _needs_ocr_for_manufacturer(self, raw_manufacturer: str) -> bool:
        return not bool(self._canonicalize_manufacturer_candidate(raw_manufacturer))

    def _needs_ocr_for_ubc(self, raw_ubc_tag: str) -> bool:
        raw = (raw_ubc_tag or "").upper().strip()
        if not raw:
            return True

        # If parser can confidently normalize with prefix/context, OCR is not needed.
        if self._parse_ubc_tag_from_text(raw):
            return False

        compact = normalize_ubc_tag(raw)
        if not compact:
            return True

        compact_no_sep = re.sub(r"[^A-Z0-9]", "", compact)
        if not re.search(r"\d", compact_no_sep):
            # UBC tags are expected to contain numbers; pure alpha tags are weak.
            return True

        # Strong forms that should not trigger OCR.
        if re.fullmatch(r"[A-Z]{1,4}-[A-Z0-9]{1,8}(?:[-\.][A-Z0-9]{1,8}){0,2}", raw):
            return False
        if re.fullmatch(r"HUM\s+[A-Z0-9]{1,8}", raw) and re.search(r"\d", raw):
            return False

        # Weak forms that typically miss prefix or separators (e.g., 2E-06 / 5C14 / 5C.14).
        if raw[0].isdigit():
            return True
        if re.fullmatch(r"\d[A-Z]\d{2,4}", compact_no_sep):
            return True
        if re.fullmatch(r"\d[A-Z][\.\-]\d{2,4}", raw):
            return True
        if re.fullmatch(r"[A-Z]\d[\.\-]?\d{2,4}", raw):
            return True

        # If it contains both letters and digits but doesn't start with alpha prefix tag style, run OCR.
        if re.search(r"[A-Z]", compact_no_sep) and re.search(r"\d", compact_no_sep):
            if not re.match(r"^[A-Z]{1,4}[-\s]", raw):
                return True

        return False

    def _resolve_ocr_flags(
        self,
        llm_model: str,
        llm_serial: str,
        raw_manufacturer: str,
        raw_ubc_tag: str,
    ) -> Tuple[bool, bool, bool]:
        """
        Return per-field OCR usage flags:
        (model_serial, manufacturer, ubc_tag)
        """
        if Config.OCR_MODE == "off":
            return (False, False, False)
        if Config.OCR_MODE == "full":
            return (True, True, True)
        # light
        return (
            self._needs_ocr_for_model_serial(llm_model, llm_serial),
            self._needs_ocr_for_manufacturer(raw_manufacturer),
            self._needs_ocr_for_ubc(raw_ubc_tag),
        )

    def _select_model_value(self, llm_value: str, ocr_value: str) -> str:
        """
        VERSION 18 FIX: Trust LLM if valid, fallback to OCR.
        """
        llm_clean = self._normalize_model_candidate(llm_value or "")
        ocr_clean = self._normalize_model_candidate(ocr_value or "")

        llm_is_model = self._is_model_code_candidate(llm_clean)
        ocr_is_model = self._is_model_code_candidate(ocr_clean)
        llm_is_tag = self._is_tag_like_model_candidate(llm_clean)

        if llm_is_model and not llm_is_tag:
            return llm_clean

        if ocr_is_model and (not llm_is_model or llm_is_tag):
            logging.info(f"[Model] Using OCR nameplate model '{ocr_clean}' over LLM '{llm_clean}'")
            return ocr_clean

        if llm_is_tag and not ocr_clean:
            return ""

        return self._ensemble_best_value(llm_clean, ocr_clean, "Model")

    def _select_serial_value(self, llm_value: str, ocr_value: str) -> str:
        """
        VERSION 18 FIX: Trust LLM if valid, fallback to OCR.
        """
        llm_clean = self._normalize_serial_candidate(llm_value or "")
        ocr_clean = self._normalize_serial_candidate(ocr_value or "")
        llm_valid = self._is_serial_candidate(llm_clean)
        ocr_valid = self._is_serial_candidate(ocr_clean)

        if llm_valid:
            # Engraved priority check: If OCR correctly captures an engraved format and LLM didn't, 
            # we can use OCR. Otherwise always default to LLM.
            engraved_pattern = r"\d{3,6}[A-Z]?-\d{2,6}[A-Z]?"
            ocr_is_engraved = bool(re.fullmatch(engraved_pattern, ocr_clean))
            llm_is_engraved = bool(re.fullmatch(engraved_pattern, llm_clean))
            
            if ocr_is_engraved and not llm_is_engraved:
                logging.info(f"[Serial Number] Preferring OCR engraved format '{ocr_clean}' over LLM '{llm_clean}'")
                return ocr_clean
            
            return llm_clean

        if ocr_valid and not llm_valid:
            logging.info(f"[Serial Number] Using OCR value '{ocr_clean}' over weak LLM '{llm_clean}'")
            return ocr_clean

        return self._ensemble_best_value(llm_clean, ocr_clean, "Serial Number")

    def _ocr_text_variants(self, image_path: str) -> List[str]:
        """
        OCR text variants using multiple rotations and threshold strategies.
        This improves reads from portrait/rotated nameplates.
        """
        if cv2 is None:
            return []
        img = cv2.imread(image_path)
        if img is None:
            return []

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = self._resize_gray_for_fast_ocr(gray)
        if not Config.SIMPLE_MODE:
            gray = cv2.resize(gray, None, fx=1.3, fy=1.3, interpolation=cv2.INTER_CUBIC)
        if Config.SIMPLE_MODE:
            # Fast OCR profile for simple mode: keep calls bounded.
            base = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
            )
            variants = [
                # Read upright grayscale first. Thresholding can erase the last
                # zero in long, tightly printed model strings (confirmed on
                # Trane QR 0000186301).
                (gray, (6,)),
                (base, (11, 6)),
                (cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE), (11,)),
                (cv2.rotate(base, cv2.ROTATE_90_COUNTERCLOCKWISE), (11,)),
                (cv2.rotate(base, cv2.ROTATE_180), (11,)),
            ]
            texts: List[str] = []
            for variant, psm_list in variants:
                for psm in psm_list:
                    t = self._safe_tesseract_text(variant, f"--oem 3 --psm {psm}")
                    if t:
                        texts.append(t.upper())
            return list(dict.fromkeys(texts))

        rotations = [
            gray,
            cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE),
            cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE),
            cv2.rotate(gray, cv2.ROTATE_180),
        ]
        variants: List[np.ndarray] = []
        for g in rotations:
            variants.append(g)
            variants.append(
                cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
            )
            _, otsu = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(otsu)

        texts: List[str] = []
        for variant in variants:
            for psm in (6, 11):
                t = self._safe_tesseract_text(variant, f"--oem 3 --psm {psm}")
                if t:
                    texts.append(t.upper())
        # Deduplicate while preserving order
        return list(dict.fromkeys(texts))

    @staticmethod
    def _safe_tesseract_text(image_data: np.ndarray, config: str) -> str:
        """Run Tesseract with timeout when available; return empty string on failure."""
        timeout_primary = 3 if Config.SIMPLE_MODE else 8
        timeout_fallback = 4 if Config.SIMPLE_MODE else 10
        try:
            return pytesseract.image_to_string(image_data, config=config, timeout=timeout_primary)
        except TypeError:
            # Some pytesseract builds do not support timeout kwarg.
            try:
                return pytesseract.image_to_string(image_data, config=config, timeout=timeout_fallback)
            except Exception:
                return ""
        except Exception:
            return ""

    @staticmethod
    def _normalize_ocr_lines(raw_text: str) -> List[str]:
        """Normalize OCR output into compact candidate lines."""
        if not raw_text:
            return []
        cleaned: List[str] = []
        seen: Set[str] = set()
        for line in raw_text.splitlines():
            s = re.sub(r"\s+", " ", line.strip().upper())
            if len(s) < 2:
                continue
            if not re.search(r"[A-Z0-9]", s):
                continue
            if len(s) > 140:
                s = s[:140]
            if s not in seen:
                seen.add(s)
                cleaned.append(s)
        return cleaned

    def _hybrid_ocr_variants(self, image_bgr: np.ndarray) -> List[Tuple[str, np.ndarray]]:
        """
        Pre-process image for OCR:
        - deskew/perspective correction
        - ROI crop
        - contrast enhancement
        - thresholding and rotations
        """
        variants: List[Tuple[str, np.ndarray]] = []
        if image_bgr is None or image_bgr.size == 0:
            return variants

        corrected = self._correct_perspective(image_bgr)
        variants.append(("deskew", corrected))

        h, w = corrected.shape[:2]
        y1, y2 = int(h * 0.04), int(h * 0.96)
        x1, x2 = int(w * 0.04), int(w * 0.96)
        roi = corrected[y1:y2, x1:x2]
        if roi.size == 0:
            roi = corrected
        variants.append(("roi", roi))

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        variants.append(("gray", gray))

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        clahe_img = clahe.apply(gray)
        variants.append(("clahe", clahe_img))

        adap = cv2.adaptiveThreshold(
            clahe_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        variants.append(("adaptive", adap))

        _, otsu = cv2.threshold(clahe_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(("otsu", otsu))

        variants.append(("gray_rot_cw", cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)))
        variants.append(("gray_rot_ccw", cv2.rotate(gray, cv2.ROTATE_90_COUNTERCLOCKWISE)))
        variants.append(("adaptive_rot_cw", cv2.rotate(adap, cv2.ROTATE_90_CLOCKWISE)))
        variants.append(("adaptive_rot_ccw", cv2.rotate(adap, cv2.ROTATE_90_COUNTERCLOCKWISE)))
        return variants

    @staticmethod
    def _safe_tesseract_data(image_data: np.ndarray, config: str) -> Dict[str, Any]:
        """Run Tesseract image_to_data with timeout when available."""
        timeout_data = 4 if Config.SIMPLE_MODE else 8
        try:
            return pytesseract.image_to_data(
                image_data,
                config=config,
                output_type=pytesseract.Output.DICT,
                timeout=timeout_data,
            )
        except TypeError:
            try:
                return pytesseract.image_to_data(
                    image_data,
                    config=config,
                    output_type=pytesseract.Output.DICT,
                )
            except Exception:
                return {}
        except Exception:
            return {}

    @staticmethod
    def _ocr_lines_from_data(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Group tesseract word boxes into line-level entries with bounding boxes."""
        texts = data.get("text", []) if isinstance(data, dict) else []
        if not texts:
            return []
        n = len(texts)
        lines: Dict[Tuple[int, int, int], Dict[str, Any]] = {}

        for i in range(n):
            raw = str(texts[i] or "").strip()
            if not raw:
                continue
            try:
                conf = float((data.get("conf", ["0"] * n)[i]) or 0)
            except Exception:
                conf = 0.0
            if conf < 20:
                continue

            block = int((data.get("block_num", [0] * n)[i]) or 0)
            par = int((data.get("par_num", [0] * n)[i]) or 0)
            line = int((data.get("line_num", [0] * n)[i]) or 0)
            key = (block, par, line)

            x = int((data.get("left", [0] * n)[i]) or 0)
            y = int((data.get("top", [0] * n)[i]) or 0)
            w = int((data.get("width", [0] * n)[i]) or 0)
            h = int((data.get("height", [0] * n)[i]) or 0)
            if w <= 0 or h <= 0:
                continue

            if key not in lines:
                lines[key] = {
                    "tokens": [],
                    "min_x": x,
                    "min_y": y,
                    "max_x": x + w,
                    "max_y": y + h,
                }
            rec = lines[key]
            rec["tokens"].append(raw)
            rec["min_x"] = min(rec["min_x"], x)
            rec["min_y"] = min(rec["min_y"], y)
            rec["max_x"] = max(rec["max_x"], x + w)
            rec["max_y"] = max(rec["max_y"], y + h)

        out: List[Dict[str, Any]] = []
        for rec in lines.values():
            text = re.sub(r"\s+", " ", " ".join(rec["tokens"]).upper()).strip()
            if not text:
                continue
            out.append(
                {
                    "text": text,
                    "bbox": (
                        int(rec["min_x"]),
                        int(rec["min_y"]),
                        int(rec["max_x"] - rec["min_x"]),
                        int(rec["max_y"] - rec["min_y"]),
                    ),
                }
            )
        return out

    def _extract_labeled_value_candidates(
        self,
        variant: np.ndarray,
        label_patterns: List[str],
        right_ratio: float = 0.58,
    ) -> List[str]:
        """
        Use label line bounding boxes to crop right-side value regions and OCR them.
        """
        if variant is None or variant.size == 0:
            return []
        gray = variant if len(variant.shape) == 2 else cv2.cvtColor(variant, cv2.COLOR_BGR2GRAY)
        data = self._safe_tesseract_data(gray, "--oem 3 --psm 6")
        lines = self._ocr_lines_from_data(data)
        if not lines:
            return []

        h, w = gray.shape[:2]
        regexes = [re.compile(p, re.IGNORECASE) for p in label_patterns]
        candidates: List[str] = []

        for rec in lines:
            line_text = rec["text"]
            if not any(r.search(line_text) for r in regexes):
                continue

            x, y, bw, bh = rec["bbox"]
            x0 = min(w - 1, x + bw + max(2, int(w * 0.01)))
            x1 = min(w, x0 + int(w * right_ratio))
            y0 = max(0, y - int(max(6, bh * 0.7)))
            y1 = min(h, y + bh + int(max(6, bh * 0.7)))
            if x1 - x0 < 18 or y1 - y0 < 8:
                continue

            crop = gray[y0:y1, x0:x1]
            if crop.size == 0:
                continue

            # OCR the cropped value area using tight and sparse modes.
            for psm in (7, 6, 11):
                txt = self._safe_tesseract_text(crop, f"--oem 3 --psm {psm}")
                for ln in self._normalize_ocr_lines(txt):
                    candidates.append(ln)
                    # Preserve label context for downstream regex parsers.
                    candidates.append(f"{line_text} {ln}")

            # Also keep same-line text as fallback.
            candidates.append(line_text)

        return list(dict.fromkeys(candidates))

    def _extract_model_serial_from_rois(self, image_path: str) -> Dict[str, str]:
        """
        Field-level extraction from label ROIs (UNIT MODEL / SERIAL NO).
        """
        out = {"Model": "", "Serial Number": ""}
        if cv2 is None:
            return out
        img = cv2.imread(image_path)
        if img is None:
            return out

        model_candidates: List[str] = []
        serial_candidates: List[str] = []
        model_patterns = [
            r"\b(?:DOE\s+)?BASIC\s+MODEL\b",
            r"\bUNIT\s+MODEL\b",
            r"\bMOD(?:EL)?\s*(?:NO\.?|NUMBER|#)?\b",
            r"\bMODEL\b",
            r"\bTYPE\b",
            r"\bCAT(?:ALOG)?\b",
            r"\bITEM\b",
        ]
        serial_patterns = [
            r"\bSERIAL\s*(?:NO\.?|NUMBER|#)?\b",
            r"\bSER\.?\s*(?:NO\.?|NUMBER|#)?\b",
            r"\bS/?N\b",
            r"\bORDER\s*(?:NO\.?|NUMBER|#)?\b",
            r"\bPROD(?:UCT)?\s*(?:NO\.?|NUMBER|#)?\b",
        ]

        for _, variant in self._hybrid_ocr_variants(img):
            for text in self._extract_labeled_value_candidates(variant, model_patterns):
                parsed = self._parse_nameplate_model_serial(text).get("Model", "")
                if parsed and self._is_model_code_candidate(parsed):
                    model_candidates.append(parsed)
                    continue
                for token in re.findall(r"\b[A-Z0-9][A-Z0-9\-\/\.\(\)]{4,31}\b", text.upper()):
                    cand = self._normalize_model_candidate(token)
                    if self._is_model_code_candidate(cand):
                        model_candidates.append(cand)

            for text in self._extract_labeled_value_candidates(variant, serial_patterns):
                parsed = self._parse_nameplate_model_serial(text).get("Serial Number", "")
                if parsed and self._is_serial_candidate(parsed):
                    serial_candidates.append(parsed)
                    continue
                for token in re.findall(r"\b[A-Z0-9][A-Z0-9\-\/\.]{3,23}\b", text.upper()):
                    cand = self._normalize_serial_candidate(token)
                    if self._is_serial_candidate(cand):
                        serial_candidates.append(cand)

        if model_candidates:
            out["Model"] = max(
                model_candidates,
                key=lambda s: len(re.sub(r"[^A-Z0-9]", "", s)),
            )
        if serial_candidates:
            out["Serial Number"] = max(
                serial_candidates,
                key=lambda s: len(re.sub(r"[^A-Z0-9]", "", s)),
            )
        return out

    def _extract_year_from_rois(self, images: Dict[str, str]) -> str:
        """
        Field-level extraction for YEAR / MFG DATE style labels on seq 0.
        Keeps year inference constrained to explicit nameplate cues.
        """
        if cv2 is None or not images:
            return ""
        seq0_path = images.get("0")
        if not seq0_path:
            return ""

        img = cv2.imread(seq0_path)
        if img is None:
            return ""

        year_patterns = [
            r"\bYEAR\b",
            r"\bMFG(?:\.?\s*DATE)?\b",
            r"\bMANUF(?:ACTURED)?\b",
            r"\bPROD(?:UCTION)?(?:\s*DATE)?\b",
        ]
        scores: Dict[str, int] = {}

        for _, variant in self._hybrid_ocr_variants(img):
            for text in self._extract_labeled_value_candidates(variant, year_patterns):
                for year_candidate in self._extract_cued_year_candidates(text):
                    scores[year_candidate] = scores.get(year_candidate, 0) + 1

        if not scores:
            return ""
        return max(scores.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def _extract_ubc_from_fc_no_rois(self, images: Dict[str, str]) -> str:
        """
        Field-level UBC extraction from label ROIs around '<PREFIX> NO.' lines.
        """
        if cv2 is None or not images:
            return ""
        ordered_paths: List[str] = []
        for seq in ["1", "0", "3"]:
            if seq in images:
                ordered_paths.append(images[seq])
        for _, p in images.items():
            if p not in ordered_paths:
                ordered_paths.append(p)

        patterns = [r"\b[A-Z]{1,4}\s*(?:NO\.?|N0\.?|NUMBER|#)\b"]
        for path in ordered_paths:
            img = cv2.imread(path)
            if img is None:
                continue
            for _, variant in self._hybrid_ocr_variants(img):
                for text in self._extract_labeled_value_candidates(variant, patterns, right_ratio=0.62):
                    parsed = self._parse_ubc_tag_from_text(text)
                    if parsed:
                        return parsed
        return ""

    def _build_hybrid_ocr_context(self, images: List[Dict[str, str]]) -> str:
        """
        Build OCR candidate context for VLM correction.
        Uses bounded output size to control performance and token usage.
        """
        if not Config.HYBRID_OCR_AGENT_ENABLED or Config.OCR_MODE == "off" or cv2 is None:
            return ""
        if not images:
            return ""

        seq_priority = {"0": 0, "1": 1, "3": 2, "2": 3}
        ordered = sorted(images, key=lambda i: (seq_priority.get(i["seq"], 9), i["seq"]))
        context_lines: List[str] = []
        used_lines = 0

        for img_meta in ordered:
            if used_lines >= Config.OCR_CONTEXT_MAX_LINES:
                break
            path = img_meta["path"]
            seq = img_meta["seq"]
            img = cv2.imread(path)
            if img is None:
                continue

            line_bucket: List[str] = []
            for variant_name, variant in self._hybrid_ocr_variants(img):
                for psm in (6, 11):
                    txt = self._safe_tesseract_text(variant, f"--oem 3 --psm {psm}")
                    if not txt:
                        continue
                    for ln in self._normalize_ocr_lines(txt):
                        # Keep source hint for diagnostics in reflective retries.
                        line_bucket.append(f"{variant_name}: {ln}")

            dedup_bucket = list(dict.fromkeys(line_bucket))
            if not dedup_bucket:
                continue

            context_lines.append(f"[Image seq {seq}: {os.path.basename(path)}]")
            used_lines += 1
            for ln in dedup_bucket:
                if used_lines >= Config.OCR_CONTEXT_MAX_LINES:
                    break
                context_lines.append(f"- {ln}")
                used_lines += 1

        if not context_lines:
            return ""

        context = "\n".join(context_lines)
        if len(context) > Config.OCR_CONTEXT_MAX_CHARS:
            context = context[: Config.OCR_CONTEXT_MAX_CHARS] + "\n...[truncated OCR context]"
        return context

    @staticmethod
    def _clean_labeled_serial_value(raw_value: str) -> str:
        """
        Clean serial value text extracted after SERIAL/ORDER labels.
        Keeps the first plausible serial token and removes trailing date/count fragments.
        """
        if not raw_value:
            return ""
        v = re.sub(r"\s+", " ", str(raw_value).upper()).strip()
        # Drop trailing production/date fragments (e.g., '279308 08/03 2 OF 3').
        v = re.sub(r"\b(?:0?[1-9]|1[0-2]|[O]?[1-9])\s*[/\\\-]\s*[0-9O]{2}\b.*$", "", v)
        v = re.sub(r"\b\d+\s+OF\s+\d+\b.*$", "", v)
        v = re.sub(
            r"\b(?:HP|VOLTS?|AMPS?|HZ|HERTZ|PHASE|KW|QTY|QUANTITY|PUMPING|SPEED|RPM|R\.?P\.?M\.?|MOTOR|VACUUM|PRESSURE|CAPACITY|INLET)\b.*$",
            "",
            v,
        )
        # Preserve short-prefix hyphenated product/catalog numbers (e.g. Siemens
        # 'Product No. 599-0335'). The generic token search below requires a >=4 char
        # leading group and would otherwise drop the 3-digit prefix ('599-0335' -> '0335').
        m_product = re.search(r"\b(\d{3}-\d{3,6})\b", v)
        if m_product:
            return m_product.group(1)
        # Take the first compact token that looks like a serial.
        m = re.search(r"\b([A-Z0-9]{4,16}(?:[\/\.-][A-Z0-9]{1,12}){0,2})\b", v)
        if m:
            return m.group(1)
        # OCR often inserts a space inside numeric serials (e.g., '279 308').
        m_spaced_digits = re.search(r"\b(\d{2,4}\s+\d{2,4})\b", v)
        if m_spaced_digits:
            return re.sub(r"\s+", "", m_spaced_digits.group(1))
        return v

    def _parse_nameplate_model_serial(self, text: str) -> Dict[str, str]:
        """
        Parse model/serial candidates from OCR text with label-aware regex.
        """
        out = {"Model": "", "Serial Number": ""}
        if not text:
            return out
        t = re.sub(r"[|]", "I", text.upper())
        t = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", t)
        t = re.sub(r"(\d)\s*/\s*(\d)", r"\1/\2", t)
        t = re.sub(r"\s+", " ", t).strip()

        # Allow spaces in model patterns to catch 'HH 012...' but use split to prevent runaway captures
        model_patterns = [
            rf"\bUNIT\s+MODEL\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{{3,{ME_MAX_MODEL_CODE_LENGTH - 1}}})",
            rf"(?<!BASIC )\bMODEL\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{{3,{ME_MAX_MODEL_CODE_LENGTH - 1}}})",
            r"\bTYPE\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{2,30})",
            r"\bCAT(?:ALOG)?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{2,30})",
            r"\bITEM\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{2,30})",
            rf"\b(?:DOE\s+)?BASIC\s+MODEL\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{{3,{ME_MAX_MODEL_CODE_LENGTH - 1}}})",
            rf"\bMOD(?:EL)?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \.\/\(\)]{{3,{ME_MAX_MODEL_CODE_LENGTH - 1}}})",
        ]
        for pat in model_patterns:
            for m in re.finditer(pat, t):
                val = m.group(1).strip()
                # Expanded boundary split to stop at words like QUANTITY, QTY, MAX, MIN
                val = re.split(
                    r'\b(MODEL|MOD|TYPE|CAT|CATALOG|ITEM|SERIAL|SER|ORDER|VOLTS|AMPS|HZ|HERTZ|PHASE|HP|KW|QUANTITY|QTY|MAX|MIN|DESIGN|PRESS|PRESSURE|DATE|MFG|PROD|CAPACITY|GPM|RPM|R\.?P\.?M\.?|HEAD|IMP|MOTOR|PEI|PUMPING|SPEED|VACUUM|INLET)\b',
                    val,
                )[0].strip()
                candidate = self._normalize_model_candidate(val)
                if self._is_model_code_candidate(candidate):
                    out["Model"] = candidate
                    break
            if out["Model"]:
                break

        # SERIAL-labeled patterns are tried first; ORDER / PRODUCT NO. are
        # lower-priority fallbacks used when no SERIAL value could be read.
        serial_patterns = [
            r"\bSERIAL\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \/\.]{2,30})",
            r"\bSER\.?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \/\.]{2,30})",
            r"\bS/?N\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \/\.]{2,30})",
            r"\bORDER\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \/\.]{2,30})",
            r"\bPROD(?:UCT)?\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\- \/\.]{2,30})",
        ]
        for pat in serial_patterns:
            for m in re.finditer(pat, t):
                val = m.group(1).strip()
                # Stop at adjacent table headers to avoid strings like "279308 QUANTITY"
                val = re.split(
                    r"\b(MODEL|MOD|TYPE|BASIC|CAT|CATALOG|ITEM|VOLTS|AMPS|HZ|HERTZ|PHASE|HP|KW|QUANTITY|QTY|MAX|MIN|DESIGN|PRESS|PRESSURE|DATE|MFG|PROD|OF|PUMPING|SPEED|RPM|R\.?P\.?M\.?|MOTOR|VACUUM|CAPACITY|INLET)\b",
                    val,
                )[0].strip()
                val = self._clean_labeled_serial_value(val)
                candidate = self._normalize_serial_candidate(val)
                if self._is_serial_candidate(candidate):
                    out["Serial Number"] = candidate
                    break
            if out["Serial Number"]:
                break

        return out

    @staticmethod
    def _normalize_ubc_core(core: str) -> str:
        """Normalize UBC core segment (e.g., '1N 02' -> '1N.02')."""
        if not core:
            return ""
        c = re.sub(r"\s+", " ", str(core).upper()).strip(" .-")
        c = c.replace("N0", "NO")
        c = re.sub(r"\s*([.-])\s*", r"\1", c)
        if " " in c:
            parts = [p for p in c.split(" ") if p]
            if len(parts) == 2 and re.search(r"\d", parts[0] + parts[1]):
                left = re.sub(r"[^A-Z0-9]", "", parts[0])
                right = re.sub(r"[^A-Z0-9]", "", parts[1])
                if re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d|[A-Z0-9]{2,4})", left):
                    c = f"{left}.{right}"
                else:
                    c = f"{left}-{right}"
            else:
                c = "".join(parts)
        c = re.sub(r"[^A-Z0-9\.\-]", "", c)
        if re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d)\d{2,4}", c):
            c = f"{c[:2]}.{c[2:]}"
        return c[:24]

    @staticmethod
    def _parse_ubc_tag_from_text(text: str) -> str:
        """
        Parse UBC tag patterns while preserving meaningful separators.
        Example handled: 'FC NO. 5C.14' -> 'FC-5C.14'
        """
        if not text:
            return ""

        t = re.sub(r"\s+", " ", str(text).upper()).strip()
        stop_prefixes = {
            "MODEL", "ORDER", "ROOM", "FLOOR", "RISER", "CABINET", "TYPE",
            "SERIAL", "YEAR", "DATE", "NO", "N0"
        }

        # Pattern: PREFIX NO. VALUE  -> PREFIX-VALUE
        # Supports multi-segment cores like CHB-01 / 5C.14 / B124-1.
        m = re.search(
            r"\b([A-Z]{1,6})\s*(?:NO\.?|N0\.?|NUMBER|#)\s*[:\-]?\s*([A-Z0-9]{1,8}(?:[\s\-\.][A-Z0-9]{1,8}){0,2})\b",
            t,
        )
        if m:
            prefix, core = m.group(1), m.group(2)
            core_norm = AssetProcessor._normalize_ubc_core(core)
            if prefix not in stop_prefixes and core_norm and re.search(r"\d", core_norm):
                return f"{prefix}-{core_norm}"[:32]

        # Pattern: HUM 5 style should remain space separated
        m = re.search(r"\b(HUM)\s+([A-Z0-9]{1,8})\b", t)
        if m:
            return f"{m.group(1)} {m.group(2)}"[:32]

        # Pattern: PREFIX VALUE or PREFIX-VALUE
        # Keep full core when there are additional separators (e.g., T-CHB-01).
        # Prefix up to 6 letters so placard tags like CHWBT-W-4 keep their prefix.
        m = re.search(
            r"\b([A-Z]{1,6})[-\s]+([A-Z0-9]{1,8}(?:[\s\-\.][A-Z0-9]{1,8}){0,2})\b",
            t,
        )
        if m:
            prefix, core = m.group(1), m.group(2)
            core_norm = AssetProcessor._normalize_ubc_core(core)
            if prefix not in stop_prefixes and core_norm and re.search(r"\d", core_norm):
                return f"{prefix}-{core_norm}"[:32]

        # Already-normalized form
        m = re.search(r"\b([A-Z]{1,6}-[A-Z0-9]{1,8}(?:[\s\-\.][A-Z0-9]{1,8}){0,2})\b", t)
        if m:
            candidate = m.group(1)
            prefix = candidate.split("-", 1)[0]
            core = candidate.split("-", 1)[1] if "-" in candidate else ""
            core_norm = AssetProcessor._normalize_ubc_core(core)
            if prefix not in stop_prefixes and core_norm and re.search(r"\d", core_norm):
                return f"{prefix}-{core_norm}"[:32]

        return ""

    @staticmethod
    def _split_ubc_tag_components(tag: str) -> Dict[str, str]:
        """Return normalized UBC prefix/core components without inventing either."""
        normalized = AssetProcessor._parse_ubc_tag_from_text(tag)
        if not normalized:
            return {"tag": "", "prefix": "", "core": "", "separator": "-"}
        if normalized.startswith("HUM "):
            prefix, core = normalized.split(" ", 1)
            return {
                "tag": normalized,
                "prefix": prefix,
                "core": core,
                "separator": " ",
            }
        prefix, separator, core = normalized.partition("-")
        if not separator or not prefix or not core:
            return {"tag": "", "prefix": "", "core": "", "separator": "-"}
        return {
            "tag": normalized,
            "prefix": prefix,
            "core": core,
            "separator": "-",
        }

    @staticmethod
    def _join_ubc_tag_components(prefix: str, core: str, separator: str = "-") -> str:
        prefix = str(prefix or "").strip().upper()
        core = str(core or "").strip().upper()
        if not prefix or not core:
            return ""
        actual_separator = " " if prefix == "HUM" else "-"
        return f"{prefix}{actual_separator}{core}"[:32]

    @staticmethod
    def _load_me_ubc_prefixes() -> Set[str]:
        """AST-safely load dictionary prefixes that are valid for ME assets."""
        candidates: List[str] = []
        for env_name in ("MECH_DICT_PATH", "MECHANICAL_DICT_PATH"):
            configured = os.environ.get(env_name, "").strip()
            if configured:
                candidates.append(os.path.normpath(configured))
        candidates.extend(
            [
                os.path.normpath("/home/developer/dictionary/mechanical_dictionary.py"),
                os.path.normpath(
                    os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "..",
                        "dictionary",
                        "mechanical_dictionary.py",
                    )
                ),
            ]
        )

        for path in dict.fromkeys(candidates):
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig") as dictionary_file:
                    tree = ast.parse(dictionary_file.read(), filename=path)
                asset_dictionary: Dict[str, Any] = {}
                for node in tree.body:
                    if not isinstance(node, ast.Assign):
                        continue
                    if any(
                        isinstance(target, ast.Name) and target.id == "ASSET_DICTIONARY"
                        for target in node.targets
                    ):
                        parsed = ast.literal_eval(node.value)
                        if isinstance(parsed, dict):
                            asset_dictionary = parsed
                        break

                prefixes: Set[str] = set()
                for raw_key, entry in asset_dictionary.items():
                    key = str(raw_key or "").strip().upper()
                    if not key:
                        continue
                    if "|" in key:
                        prefix, discipline = key.rsplit("|", 1)
                        if discipline == "ME" and prefix:
                            prefixes.add(prefix.rstrip("- ."))
                        continue
                    if isinstance(entry, dict):
                        entry_type = str(
                            entry.get("asset_type") or entry.get("type") or ""
                        ).strip().upper()
                        if entry_type == "ME":
                            prefixes.add(key.rstrip("- ."))
                return {prefix for prefix in prefixes if prefix}
            except Exception as exc:
                logging.warning("Failed to AST-load ME UBC prefixes from %s: %s", path, exc)
        return set()

    @staticmethod
    def _detect_ubc_placard(image: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        """Select the dominant dark, elongated placard; fall back to the full frame."""
        if cv2 is None or image is None or not getattr(image, "size", 0):
            return image, (0, 0, 0, 0)
        height, width = image.shape[:2]
        fallback = (0, 0, width, height)
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            dark_mask = cv2.inRange(gray, 0, 85)
            kernel_side = max(5, int(round(min(height, width) * 0.03)))
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (kernel_side, kernel_side)
            )
            closed = cv2.morphologyEx(
                dark_mask, cv2.MORPH_CLOSE, kernel, iterations=2
            )
            contours, _ = cv2.findContours(
                closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            frame_area = float(max(1, width * height))
            best: Optional[Tuple[float, Tuple[int, int, int, int]]] = None
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                box_area = float(box_width * box_height)
                area_ratio = box_area / frame_area
                short_side = max(1, min(box_width, box_height))
                elongation = max(box_width, box_height) / short_side
                fill = float(cv2.contourArea(contour)) / max(1.0, box_area)
                if not (0.03 <= area_ratio <= 0.75):
                    continue
                if elongation < 1.6 or fill < 0.5:
                    continue
                score = box_area * fill
                if best is None or score > best[0]:
                    best = (score, (x, y, box_width, box_height))
            if best is None:
                return image, fallback
            x, y, box_width, box_height = best[1]
            return image[y : y + box_height, x : x + box_width], best[1]
        except Exception as exc:
            logging.warning("UBC placard detection failed; using full seq-1 image: %s", exc)
            return image, fallback

    def _extract_local_ubc_vote(
        self,
        images: Dict[str, str],
        known_prefixes: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """Return one local OCR source vote after four bounded placard reads."""
        empty = {"prefix": "", "core": "", "prefix_votes": 0, "core_votes": 0}
        if cv2 is None or not isinstance(images, dict):
            return empty
        seq1_path = str(images.get("1") or "")
        if not self._is_readable_source_path(seq1_path):
            return empty
        image = cv2.imread(seq1_path)
        if image is None:
            return empty
        placard, _ = self._detect_ubc_placard(image)
        if placard is None or not placard.size:
            return empty

        prefixes = {
            str(prefix or "").strip().upper()
            for prefix in (known_prefixes if known_prefixes is not None else self._load_me_ubc_prefixes())
            if str(prefix or "").strip()
        }
        if placard.shape[0] > placard.shape[1]:
            orientations = [
                cv2.rotate(placard, cv2.ROTATE_90_CLOCKWISE),
                cv2.rotate(placard, cv2.ROTATE_90_COUNTERCLOCKWISE),
            ]
        else:
            orientations = [placard, cv2.rotate(placard, cv2.ROTATE_180)]

        prefix_counts: Dict[str, int] = defaultdict(int)
        core_counts: Dict[str, int] = defaultdict(int)
        config = (
            "--oem 3 --psm 11 "
            "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
        )
        for oriented in orientations:
            start = int(oriented.shape[1] * 0.15)
            end = int(oriented.shape[1] * 0.85)
            centered = oriented[:, start:end] if end > start else oriented
            gray = cv2.cvtColor(centered, cv2.COLOR_BGR2GRAY)
            scale = max(1.0, 500.0 / max(1, gray.shape[0]))
            if scale > 1.0:
                gray = cv2.resize(
                    gray,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_CUBIC,
                )
            enhanced = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
            _, otsu = cv2.threshold(
                enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            for variant in (gray, otsu):
                raw_text = self._safe_tesseract_ubc_text(variant, config).upper()
                parsed_vote = self._split_ubc_tag_components(raw_text)
                if parsed_vote.get("tag"):
                    prefix_counts[parsed_vote["prefix"]] += 1
                    core_counts[parsed_vote["core"]] += 1
                    continue
                compact = re.sub(r"[^A-Z0-9]", "", raw_text)
                if not compact:
                    continue
                reverse_compact = compact[::-1]
                matched_prefix = ""
                matched_view = compact
                for prefix in sorted(prefixes, key=len, reverse=True):
                    if prefix in compact:
                        matched_prefix = prefix
                        matched_view = compact
                        break
                    if prefix in reverse_compact:
                        matched_prefix = prefix
                        matched_view = reverse_compact
                        break
                if not matched_prefix:
                    continue
                prefix_counts[matched_prefix] += 1
                if matched_view.startswith(matched_prefix):
                    tail = matched_view[len(matched_prefix) :]
                    if re.fullmatch(r"[IL][A-Z]", tail):
                        tail = f"1{tail[1]}"
                    elif re.fullmatch(r"[OQ][A-Z0-9]", tail):
                        tail = f"0{tail[1]}"
                    normalized_core = self._normalize_ubc_core(tail)
                    if normalized_core and re.search(r"\d", normalized_core):
                        core_counts[normalized_core] += 1

        def winner(counts: Dict[str, int]) -> Tuple[str, int]:
            if not counts:
                return "", 0
            value, votes = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
            return (value, votes) if votes >= 2 else ("", votes)

        prefix, prefix_votes = winner(prefix_counts)
        core, core_votes = winner(core_counts)
        return {
            "prefix": prefix,
            "core": core,
            "prefix_votes": prefix_votes,
            "core_votes": core_votes,
        }

    @staticmethod
    def _safe_tesseract_ubc_text(image_data: np.ndarray, config: str) -> str:
        """Run a local UBC read with the fixed four-second cost/time guard."""
        try:
            return pytesseract.image_to_string(
                image_data,
                config=config,
                timeout=4,
            )
        except Exception:
            return ""

    @staticmethod
    def _normalize_ubc_judge_failure_category(exc: BaseException) -> str:
        """Map a judge failure to a stable category without retaining details."""
        message = str(exc).lower()
        class_name = exc.__class__.__name__.lower()
        if "timeout" in class_name or isinstance(exc, TimeoutError):
            return "timeout"
        if any(
            marker in message
            for marker in (
                "insufficient_quota",
                "exceeded your current quota",
                "billing_hard_limit_reached",
            )
        ):
            return "quota"
        if is_auth_error(exc):
            return "auth"
        status_code = getattr(exc, "status_code", None) or getattr(
            exc, "http_status", None
        )
        if isinstance(exc, RateLimitError) or status_code == 429:
            return "rate_limit"
        if isinstance(exc, (APIConnectionError, APIStatusError, BadRequestError)):
            return "api"
        return "parse"

    def _reread_ubc_consensus_judge(
        self,
        qr: str,
        images: Dict[str, str],
    ) -> Dict[str, Any]:
        """Run the one-shot independent UBC judge on two placard orientations."""
        empty_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        failure: Dict[str, Any] = {
            "tag": "",
            "model": Config.ME_UBC_JUDGE_MODEL,
            "call_count": 0,
            "usage": dict(empty_usage),
            "error": "parse",
        }
        if cv2 is None or not getattr(self, "client", None):
            return failure
        seq1_path = str((images or {}).get("1") or "")
        if not self._is_readable_source_path(seq1_path):
            return failure

        try:
            image = cv2.imread(seq1_path)
            if image is None or not image.size:
                return failure
            placard, _ = self._detect_ubc_placard(image)
            if placard is None or not placard.size:
                placard = image

            if placard.shape[0] > placard.shape[1]:
                orientations = (
                    cv2.rotate(placard, cv2.ROTATE_90_CLOCKWISE),
                    cv2.rotate(placard, cv2.ROTATE_90_COUNTERCLOCKWISE),
                )
            else:
                orientations = (
                    placard,
                    cv2.rotate(placard, cv2.ROTATE_180),
                )

            prepared_urls: List[str] = []
            max_edge = Config.ME_UBC_JUDGE_MAX_INPUT_EDGE
            for oriented in orientations:
                height, width = oriented.shape[:2]
                scale = min(1.0, float(max_edge) / max(height, width, 1))
                prepared = oriented
                if scale < 1.0:
                    prepared = cv2.resize(
                        oriented,
                        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                        interpolation=cv2.INTER_AREA,
                    )
                prepared_urls.append(self._encode_image_from_data(prepared))

            prompt = (
                "Read ONLY the printed UBC Tag on this identification placard. "
                "The two images show the same placard in opposite orientations. "
                "Preserve every visible letter, digit, dot, and tag segment. "
                "Normalize a standard tag as PREFIX-CORE; preserve HUM tags as HUM CORE. "
                "Do not return a QR number, model, order number, or serial number. "
                "If the placard text is unreadable, return an empty string. "
                'Return strict JSON with exactly one key: "UBC Tag".'
            )
            content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": data_url,
                        "detail": Config.ME_UBC_JUDGE_DETAIL,
                    },
                }
                for data_url in prepared_urls
            )
            failure["call_count"] = 1
            response = self.client.beta.chat.completions.parse(
                model=Config.ME_UBC_JUDGE_MODEL,
                messages=[{"role": "user", "content": content}],
                max_completion_tokens=Config.ME_UBC_JUDGE_MAX_COMPLETION_TOKENS,
                reasoning_effort=Config.ME_UBC_JUDGE_REASONING_EFFORT,
                response_format=MEUBCTagOnlyExtraction,
            )
            message = response.choices[0].message
            if getattr(message, "refusal", None) or getattr(message, "parsed", None) is None:
                raise ValueError("judge response was not parsed")

            parsed: MEUBCTagOnlyExtraction = message.parsed
            raw_tag = parsed.model_dump(by_alias=True).get("UBC Tag", "")
            normalized_tag = self._split_ubc_tag_components(str(raw_tag or "")).get(
                "tag", ""
            )
            usage_obj = getattr(response, "usage", None)
            usage = {
                "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                "completion_tokens": int(
                    getattr(usage_obj, "completion_tokens", 0) or 0
                ),
                "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
            }
            return {
                "tag": normalized_tag,
                "model": Config.ME_UBC_JUDGE_MODEL,
                "call_count": 1,
                "usage": usage,
                "error": "" if normalized_tag else "parse",
            }
        except Exception as exc:
            category = self._normalize_ubc_judge_failure_category(exc)
            logging.warning(
                "[%s] ME UBC consensus judge failed: category=%s exception_type=%s",
                qr,
                category,
                exc.__class__.__name__,
            )
            failure["error"] = category
            return failure

    def _resolve_ubc_consensus(
        self,
        *,
        qr: str,
        primary_tag: str,
        primary_confidence: Any,
        images: Dict[str, str],
    ) -> Dict[str, Any]:
        """Resolve challenged UBC components with a strict two-source quorum."""
        primary = self._split_ubc_tag_components(primary_tag)
        try:
            primary_score = self._normalize_confidence_score(primary_confidence)
        except Exception:
            primary_score = 0
        metadata: Dict[str, Any] = {
            "status": "accepted_primary",
            "triggers": [],
            "primary": {
                **primary,
                "confidence": primary_score,
            },
            "local": {"prefix": "", "core": "", "prefix_votes": 0, "core_votes": 0},
            "judge": {
                "tag": "",
                "prefix": "",
                "core": "",
                "model": Config.ME_UBC_JUDGE_MODEL,
                "call_count": 0,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "error": "",
            },
            "final_tag": primary.get("tag", ""),
            "confidence_floor": 0,
            "confidence_cap": 100,
            "reason_codes": [],
        }
        seq1_path = str((images or {}).get("1") or "")
        if not self._is_readable_source_path(seq1_path):
            metadata["final_tag"] = ""
            return metadata

        try:
            known_prefixes = self._load_me_ubc_prefixes()
        except Exception as exc:
            known_prefixes = set()
            logging.warning("[%s] ME UBC dictionary validation unavailable: %s", qr, exc)
        local = self._extract_local_ubc_vote(images, known_prefixes)
        metadata["local"] = local

        challenged: Set[str] = set()
        if not primary.get("tag"):
            metadata["triggers"].append("primary_missing_or_malformed")
            challenged.update(("prefix", "core"))
        if primary.get("prefix") and known_prefixes and primary["prefix"] not in known_prefixes:
            metadata["triggers"].append("prefix_unrecognized")
            challenged.add("prefix")
        if primary.get("tag") and 0 < primary_score < 70:
            metadata["triggers"].append("primary_low_confidence")
            challenged.update(("prefix", "core"))
        for component in ("prefix", "core"):
            local_value = str(local.get(component) or "")
            primary_value = str(primary.get(component) or "")
            if local_value and local_value != primary_value:
                metadata["triggers"].append(f"local_{component}_disagreement")
                challenged.add(component)

        if not challenged:
            return metadata

        judge_result = self._reread_ubc_consensus_judge(qr, images)
        if not isinstance(judge_result, dict):
            judge_result = {"tag": "", "usage": {}, "error": "parse"}
        judge_parts = self._split_ubc_tag_components(str(judge_result.get("tag") or ""))
        metadata["judge"] = {
            "tag": judge_parts.get("tag", ""),
            "prefix": judge_parts.get("prefix", ""),
            "core": judge_parts.get("core", ""),
            "model": str(judge_result.get("model") or ""),
            "call_count": min(
                max(int(judge_result.get("call_count", 0) or 0), 0),
                Config.ME_UBC_JUDGE_MAX_CALLS,
            ),
            "usage": judge_result.get("usage") if isinstance(judge_result.get("usage"), dict) else {},
            "error": str(judge_result.get("error") or ""),
        }

        final_components = {
            "prefix": str(primary.get("prefix") or ""),
            "core": str(primary.get("core") or ""),
        }
        unresolved = False
        for component in challenged:
            votes = [
                str(primary.get(component) or ""),
                str(local.get(component) or ""),
                str(judge_parts.get(component) or ""),
            ]
            counts: Dict[str, int] = defaultdict(int)
            for vote in votes:
                if vote:
                    counts[vote] += 1
            quorum_value = next(
                (value for value, count in counts.items() if count >= 2), ""
            )
            if quorum_value:
                final_components[component] = quorum_value
            else:
                unresolved = True

        final_tag = self._join_ubc_tag_components(
            final_components["prefix"],
            final_components["core"],
            str(primary.get("separator") or "-"),
        )
        metadata["final_tag"] = final_tag or primary.get("tag", "")
        if unresolved:
            metadata["status"] = "unresolved"
            metadata["confidence_cap"] = 65
            metadata["reason_codes"].append("ubc_consensus_unresolved")
            if (
                primary.get("prefix")
                and known_prefixes
                and primary["prefix"] not in known_prefixes
            ):
                metadata["reason_codes"].append("ubc_prefix_unrecognized")
        else:
            metadata["confidence_floor"] = 92
            metadata["status"] = (
                "corrected_by_quorum"
                if metadata["final_tag"] != primary.get("tag", "")
                else "confirmed_by_quorum"
            )
        metadata["triggers"] = sorted(set(metadata["triggers"]))
        metadata["reason_codes"] = sorted(set(metadata["reason_codes"]))
        return metadata

    def _maybe_resolve_ubc_consensus(
        self,
        *,
        qr: str,
        primary_tag: str,
        primary_confidence: Any,
        images: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any]]:
        """Feature-gated bridge that leaves the legacy UBC path untouched."""
        if not Config.ME_UBC_CONSENSUS_ENABLED:
            return str(primary_tag or ""), {}
        consensus = self._resolve_ubc_consensus(
            qr=qr,
            primary_tag=primary_tag,
            primary_confidence=primary_confidence,
            images=images,
        )
        judge = consensus.get("judge", {})
        local = consensus.get("local", {})
        primary = consensus.get("primary", {})
        usage = judge.get("usage", {}) if isinstance(judge, dict) else {}
        logging.info(
            "[%s] ME_UBC_CONSENSUS status=%s triggers=%s primary=(%s,%s) "
            "local=(%s,%s) judge=(%s,%s) final=%s calls=%s tokens=%s error=%s",
            qr,
            consensus.get("status", ""),
            consensus.get("triggers", []),
            primary.get("prefix", "") if isinstance(primary, dict) else "",
            primary.get("core", "") if isinstance(primary, dict) else "",
            local.get("prefix", "") if isinstance(local, dict) else "",
            local.get("core", "") if isinstance(local, dict) else "",
            judge.get("prefix", "") if isinstance(judge, dict) else "",
            judge.get("core", "") if isinstance(judge, dict) else "",
            consensus.get("final_tag", ""),
            judge.get("call_count", 0) if isinstance(judge, dict) else 0,
            usage.get("total_tokens", 0) if isinstance(usage, dict) else 0,
            judge.get("error", "") if isinstance(judge, dict) else "",
        )
        return str(consensus.get("final_tag") or ""), consensus

    def _ocr_text_candidates_for_ubc(self, images: Dict[str, str]) -> List[str]:
        """Collect OCR text (tag-first ordering) for UBC tag recovery."""
        if cv2 is None or not images:
            return []

        ordered_paths: List[str] = []
        seq_order = ["1", "0", "3"] if not Config.SIMPLE_MODE else ["1", "0"]
        for seq in seq_order:
            if seq in images:
                ordered_paths.append(images[seq])
        for _, p in images.items():
            if p not in ordered_paths:
                ordered_paths.append(p)

        texts: List[str] = []
        for p in ordered_paths:
            try:
                img = cv2.imread(p)
                if img is None:
                    continue
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, None, fx=1.7, fy=1.7, interpolation=cv2.INTER_CUBIC)
                thresh = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
                )
                text = self._safe_tesseract_text(thresh, "--oem 3 --psm 6")
                if text:
                    texts.append(text.upper())
                
                # In simple mode, we MUST explicitly search rotated images because the primary text
                # often contains garbage background noise and won't trigger an 'if not text' fallback.
                if Config.SIMPLE_MODE:
                    text_cw = self._safe_tesseract_text(cv2.rotate(thresh, cv2.ROTATE_90_CLOCKWISE), "--oem 3 --psm 6")
                    if text_cw:
                        texts.append(text_cw.upper())
                    text_ccw = self._safe_tesseract_text(cv2.rotate(thresh, cv2.ROTATE_90_COUNTERCLOCKWISE), "--oem 3 --psm 6")
                    if text_ccw:
                        texts.append(text_ccw.upper())
            except Exception:
                continue
        return texts

    def _detect_ubc_prefix_from_images(self, images: Dict[str, str], core: str) -> str:
        """Try to recover missing UBC tag prefix by scanning OCR text like 'FC NO. 5C.14'."""
        target = re.sub(r"[^A-Z0-9]", "", (core or "").upper())
        if not target:
            return ""

        for text in self._ocr_text_candidates_for_ubc(images):
            # 1. Extremely robust token search ignoring all whitespace and punctuation
            text_clean = re.sub(r"[^A-Z0-9]", "", text.upper())
            if f"FCNO{target}" in text_clean or f"FCN0{target}" in text_clean or f"FC{target}" in text_clean:
                return "FC"
            if f"HUM{target}" in text_clean:
                return "HUM"
            
            # 2. Original fallback standard regex match
            for m in re.finditer(
                r"\b([A-Z]{1,4})\s*(?:NO\.?|N0\.?|NUMBER|#)\s*[:\-]?\s*([A-Z0-9]{1,8}(?:[\s\.\-][A-Z0-9]{1,8}){0,2})\b",
                text,
            ):
                prefix, value = m.group(1), m.group(2)
                normalized_value = re.sub(
                    r"[^A-Z0-9]",
                    "",
                    AssetProcessor._normalize_ubc_core(value).upper(),
                )
                if normalized_value == target:
                    return prefix
        return ""

    def _extract_ubc_tag_from_images(self, images: Dict[str, str]) -> str:
        """Fallback extraction for UBC tag from OCR text."""
        for text in self._ocr_text_candidates_for_ubc(images):
            parsed = self._parse_ubc_tag_from_text(text)
            if parsed:
                return parsed
        return ""

    def _normalize_ubc_tag_with_context(
        self,
        raw_tag: str,
        images: Dict[str, str],
        allow_ocr: bool = True,
    ) -> str:
        """
        Normalize UBC tag with context-aware recovery.
        Avoids collapsing tags like FC-5C.14 into 5C14.
        """
        raw = (raw_tag or "").upper().strip()
        raw = raw.replace("N0.", "NO.").replace(" N0 ", " NO ")

        if raw:
            parsed = self._parse_ubc_tag_from_text(raw)
            if parsed:
                return parsed

        compact = re.sub(r"[^A-Z0-9\.\-]", "", raw)
        if compact:
            core_like = self._normalize_ubc_core(compact)
            weak_raw = self._needs_ocr_for_ubc(raw) or compact.isdigit()
            # If the LLM returned "2W04" without the dot, and it matches the format, add the dot back.
            if "." not in compact and re.fullmatch(r"\d[A-Z]\d{2,4}", compact):
                compact = f"{compact[:2]}.{compact[2:]}"
            if core_like and re.fullmatch(r"(?:\d[A-Z]|[A-Z]\d)(?:[\.\-]?\d{2,4})", core_like):
                if "." not in core_like and "-" not in core_like and len(core_like) >= 4:
                    core_like = f"{core_like[:2]}.{core_like[2:]}"
                core_like = core_like.replace("-", ".")
                if allow_ocr:
                    roi_candidate = self._extract_ubc_from_fc_no_rois(images)
                    if roi_candidate:
                        return roi_candidate
                    prefix = self._detect_ubc_prefix_from_images(images, core_like)
                    if prefix:
                        return f"{prefix}-{core_like}"[:32]
                return f"FC-{core_like}"[:32]
                
            if allow_ocr:
                roi_candidate = self._extract_ubc_from_fc_no_rois(images)
                if roi_candidate and (
                    weak_raw
                    or compact.replace(".", "") in roi_candidate.replace(".", "")
                ):
                    return roi_candidate
                    
                prefix = self._detect_ubc_prefix_from_images(images, compact)
                if prefix:
                    return f"{prefix}-{compact}"[:32]
                return compact

        if allow_ocr:
            ocr_candidate = self._extract_ubc_tag_from_images(images)
            if ocr_candidate:
                return ocr_candidate

        # Last fallback to shared normalizer
        return normalize_ubc_tag(raw_tag)

    def _ocr_extract_model_serial_fast(self, images: Dict[str, str]) -> Dict[str, str]:
        """
        Fast OCR fallback for simple mode.
        Uses a bounded number of OCR calls focused on seq 0 nameplate.
        """
        result = {"Model": "", "Serial Number": ""}
        if cv2 is None or not images:
            return result

        primary_path = images.get("0")
        if not primary_path:
            for seq in sorted(images.keys()):
                primary_path = images[seq]
                if primary_path:
                    break
        if not primary_path:
            return result

        # Model/Serial are plate fields; keep fast OCR constrained to seq 0 path.
        candidate_paths: List[str] = [primary_path]

        best_model = ""
        best_serial = ""

        def _pick_better_model(current: str, new_val: str) -> str:
            if not new_val:
                return current
            if not current:
                return new_val
            cur_compact = re.sub(r"[^A-Z0-9]", "", current)
            new_compact = re.sub(r"[^A-Z0-9]", "", new_val)
            if len(cur_compact) > 32 and len(new_compact) > 32:
                # Long configuration strings are not improved merely by gaining
                # more OCR characters. Preserve the first upright label read.
                return current
            if len(new_compact) > len(cur_compact):
                return new_val
            if len(new_compact) == len(cur_compact):
                if new_compact.startswith("LS") and not cur_compact.startswith("LS"):
                    return new_val
            return current

        def _pick_better_serial(current: str, new_val: str) -> str:
            if not new_val:
                return current
            if not current:
                return new_val
            cur_compact = re.sub(r"[^A-Z0-9]", "", current)
            new_compact = re.sub(r"[^A-Z0-9]", "", new_val)
            if re.search(r"[A-Z]", cur_compact) and re.search(r"\d", cur_compact):
                # Do not replace a complete alphanumeric serial from the
                # upright plate with a longer rotated/noisy OCR token.
                return current
            if len(new_compact) > len(cur_compact):
                return new_val
            return current

        for path in candidate_paths:
            text_candidates = self._ocr_text_variants(path)
            if path == primary_path:
                # Special-case red engraved plates (e.g., Rockwell) where full-frame OCR is weak.
                text_candidates.extend(self._ocr_text_from_dark_nameplate(path))
                text_candidates.extend(self._ocr_text_from_red_nameplate(path))
                text_candidates = list(dict.fromkeys(text_candidates))
            for text in text_candidates:
                parsed = self._parse_nameplate_model_serial(text)

                model_candidate = parsed.get("Model", "")
                if "MAKITA" in text and re.fullmatch(r"HS\d{3,5}[A-Z]?", model_candidate):
                    # Common OCR confusion on Makita labels: leading 'L' read as 'H'.
                    model_candidate = f"L{model_candidate[1:]}"
                if model_candidate and self._is_model_code_candidate(model_candidate):
                    best_model = _pick_better_model(best_model, model_candidate)

                serial_candidate = parsed.get("Serial Number", "")
                if serial_candidate and self._is_serial_candidate(serial_candidate):
                    best_serial = _pick_better_serial(best_serial, serial_candidate)

                if not best_model:
                    for cand in self._model_candidates_near_label(text):
                        if "MAKITA" in text and re.fullmatch(r"HS\d{3,5}[A-Z]?", cand):
                            cand = f"L{cand[1:]}"
                        if self._is_model_code_candidate(cand):
                            best_model = _pick_better_model(best_model, cand)

                if not best_serial:
                    m = re.search(
                        r"\b(?:SER(?:IAL)?\.?|ORDER|PROD(?:UCT)?)\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/\.\s]{2,30})",
                        text.upper(),
                    )
                    if m:
                        cand = self._normalize_serial_candidate(self._clean_labeled_serial_value(m.group(1)))
                        if self._is_serial_candidate(cand):
                            best_serial = _pick_better_serial(best_serial, cand)
                    if not best_serial:
                        # Common Williams layout: serial immediately followed by MM/YY (e.g., 279308 08/03).
                        m_num_date = re.search(
                            r"\b(\d{4,8})\b\s*(?:0?[1-9]|1[0-2])\s*[/\\\-]\s*[0-9O]{2}\b",
                            text.upper(),
                        )
                        if m_num_date:
                            cand = self._normalize_serial_candidate(m_num_date.group(1))
                            if self._is_serial_candidate(cand):
                                best_serial = _pick_better_serial(best_serial, cand)
                    serial_cue = bool(
                        re.search(
                            r"\bSER(?:IAL)?\.?\s*(?:NO\.?|NUMBER|#)?\b|\bS/?N\b|\bORDER\b",
                            text.upper(),
                        )
                    )
                    for token in re.findall(r"\b[A-Z0-9][A-Z0-9\-\/\.]{3,23}\b", text.upper()):
                        if not serial_cue and not re.search(r"\d", token):
                            continue
                        if not serial_cue and "-" not in token and "/" not in token and "." not in token:
                            # Avoid random unlabeled tokens (e.g., QR/sticker strings).
                            continue
                        cand = self._normalize_serial_candidate(token)
                        if self._is_serial_candidate(cand):
                            best_serial = _pick_better_serial(best_serial, cand)

            if best_model and best_serial:
                break

        result["Model"] = best_model
        result["Serial Number"] = best_serial
        return result

    def _ocr_extract_model_serial(self, images: Dict[str, str]) -> Dict[str, str]:
        """
        HYBRID ENSEMBLE: Extract Model and Serial Number using Tesseract OCR.
        Focuses on seq "0" (Asset Plate) and uses rotated OCR variants.
        """
        if Config.SIMPLE_MODE:
            return self._ocr_extract_model_serial_fast(images)

        result = {"Model": "", "Serial Number": ""}
        
        # Safety check: if cv2 is not available, skip OCR entirely
        if cv2 is None:
            logging.warning("OpenCV (cv2) not available - skipping OCR extraction")
            return result

        ordered_paths: List[str] = []
        for seq in ["0", "1", "3"]:
            if seq in images:
                ordered_paths.append(images[seq])
        if not ordered_paths:
            return result

        best_model = ""
        best_serial = ""

        # Pass 1: field-level ROI extraction using label bounding boxes.
        for p in ordered_paths:
            roi_out = self._extract_model_serial_from_rois(p)
            model_candidate = roi_out.get("Model", "")
            serial_candidate = roi_out.get("Serial Number", "")
            if model_candidate and self._is_model_code_candidate(model_candidate):
                model_compact = re.sub(r"[^A-Z0-9]", "", model_candidate)
                best_model_compact = re.sub(r"[^A-Z0-9]", "", best_model)
                if len(model_compact) >= len(best_model_compact):
                    best_model = model_candidate
            if serial_candidate and self._is_serial_candidate(serial_candidate):
                serial_compact = re.sub(r"[^A-Z0-9]", "", serial_candidate)
                best_serial_compact = re.sub(r"[^A-Z0-9]", "", best_serial)
                if len(serial_compact) >= len(best_serial_compact):
                    best_serial = serial_candidate

        # If both critical fields are already found from ROIs, return early.
        if best_model and best_serial:
            result["Model"] = best_model
            result["Serial Number"] = best_serial
            return result

        # Pass 2: full-text OCR variants as fallback.
        for p in ordered_paths:
            for text in self._ocr_text_variants(p):
                parsed = self._parse_nameplate_model_serial(text)

                model_candidate = parsed.get("Model", "")
                if model_candidate and self._is_model_code_candidate(model_candidate):
                    model_compact = re.sub(r"[^A-Z0-9]", "", model_candidate)
                    best_model_compact = re.sub(r"[^A-Z0-9]", "", best_model)
                    if len(model_compact) >= len(best_model_compact):
                        best_model = model_candidate

                serial_candidate = parsed.get("Serial Number", "")
                if serial_candidate and self._is_serial_candidate(serial_candidate):
                    serial_compact = re.sub(r"[^A-Z0-9]", "", serial_candidate)
                    best_serial_compact = re.sub(r"[^A-Z0-9]", "", best_serial)
                    if len(serial_compact) >= len(best_serial_compact):
                        best_serial = serial_candidate

        # Fallback serial rescue: if label parser missed, take longest numeric token from plate OCR.
        if not best_serial:
            for p in ordered_paths:
                for text in self._ocr_text_variants(p):
                    for m in re.finditer(r"\b\d{6,12}\b", text):
                        candidate = self._normalize_serial_candidate(m.group(0))
                        if self._is_serial_candidate(candidate) and len(candidate) >= len(best_serial):
                            best_serial = candidate

        result["Model"] = best_model
        result["Serial Number"] = best_serial
        return result

    def _ensemble_best_value(self, llm_value: str, ocr_value: str, field_name: str) -> str:
        """
        HYBRID ENSEMBLE: Pick the best value between LLM and OCR outputs.
        VERSION 18 FIX: Trust the Vision Model (LLM) first. Use OCR strictly as a fallback.
        """
        llm_clean = (llm_value or "").strip()
        ocr_clean = (ocr_value or "").strip()

        # Case 1: Both empty
        if not llm_clean and not ocr_clean:
            return ""

        # Case 2: Only one has value
        if not llm_clean:
            logging.info(f"[{field_name}] Using OCR value (LLM empty): '{ocr_clean}'")
            return ocr_clean
        if not ocr_clean:
            return llm_clean  # LLM is primary, no need to log

        # Case 3: Both have values. TRUST THE LLM UNLESS IT HAS UNCERTAINTY.
        if "?" in llm_clean:
            logging.info(f"[{field_name}] LLM value has uncertainty ('{llm_clean}'). Falling back to OCR: '{ocr_clean}'")
            return ocr_clean

        # Trust LLM over OCR completely. Tesseract often hallucinates random chars on glossy plates.
        llm_stripped = re.sub(r'[\s\-/]', '', llm_clean)
        ocr_stripped = re.sub(r'[\s\-/]', '', ocr_clean)

        if llm_stripped.upper() != ocr_stripped.upper():
            logging.info(f"[{field_name}] LLM/OCR disagree. Trusting LLM: '{llm_clean}' over OCR: '{ocr_clean}'.")

        return llm_clean

    def _evaluate_llm_candidate(self, candidate: Dict[str, str]) -> Tuple[List[str], bool]:
        """
        Build reflective feedback and determine whether critical fields are valid.
        Returns: (feedback_lines, critical_valid)
        """
        feedback: List[str] = []

        model_val = self._normalize_model_candidate(candidate.get("Model", ""), candidate.get("Manufacturer", ""))
        serial_val = self._normalize_serial_candidate(
            candidate.get("Serial Number", ""),
            candidate.get("Manufacturer", ""),
        )
        manufacturer_val = (candidate.get("Manufacturer", "") or "").strip()
        year_val = (candidate.get("Year", "") or "").strip()
        ubc_val = (candidate.get("UBC Tag", "") or "").strip()

        model_ok = self._is_model_code_candidate(model_val, candidate.get("Manufacturer", ""))
        serial_ok = self._is_serial_candidate(serial_val)
        model_serial_collision = self._model_serial_values_collide(model_val, serial_val)
        if model_serial_collision:
            model_ok = False
        manufacturer_ok = bool(self._canonicalize_manufacturer_candidate(manufacturer_val))
        year_ok = bool(self._normalize_year_flexible(year_val))

        if model_serial_collision:
            feedback.append(
                f"Model and Serial Number both contain '{serial_val}'. Re-read the two labeled rows "
                "independently; never copy the Serial Number into Model."
            )

        if not model_ok:
            if model_val:
                feedback.append(
                    f"Previous Model '{model_val}' appears weak or tag-like. Re-read only the nameplate "
                    "field labeled 'MODEL', 'MODEL NO', 'UNIT MODEL', 'TYPE', 'BASIC MODEL', or 'DOE BASIC MODEL'."
                )
            else:
                feedback.append(
                    "Model is missing. Re-read Image seq 0 and extract only from nameplate model field."
                )

        if not serial_ok:
            if serial_val:
                feedback.append(
                    f"Previous Serial Number '{serial_val}' appears partial/weak. Re-read only the nameplate "
                    "field labeled 'SERIAL', 'SERIAL NO', 'S/N', or 'ORDER NO' and keep the full alphanumeric value."
                )
            else:
                feedback.append(
                    "Serial Number is missing. Re-read Image seq 0 and extract from 'SERIAL NO', 'S/N', or 'ORDER NO'. "
                    "If no explicit serial exists, use the 'PRODUCT NO.' value as the Serial Number."
                )

        if not manufacturer_ok:
            feedback.append(
                "Manufacturer is weak/missing. Re-check vertical and edge text on the plate; if it says "
                "'WILLIAMS FURNACE COMPANY', return 'Williams'; if it says 'ROCKWELL MANUFACTURING CO', "
                "return 'Rockwell'; if it says 'TACO CANADA LTD', return 'Taco'; if it says "
                "'REPUBLIC MANUFACTURING', return 'Republic Manufacturing'."
            )

        if not year_ok:
            feedback.append(
                "Year is weak/missing. Re-check YEAR, MFG DATE, MANUFACTURED, or PROD DATE and return only four-digit year."
            )

        # UBC is not a core hard-fail, but provide reflective guidance when weak.
        if ubc_val and self._needs_ocr_for_ubc(ubc_val):
            feedback.append(
                f"UBC Tag '{ubc_val}' looks incomplete. Re-check tag label for '<PREFIX> NO.' pattern and "
                "preserve prefix and dot separators (example: FC-2E.06)."
            )
        elif not ubc_val:
            feedback.append(
                "UBC Tag is missing. Re-check separate tag sticker/label; return blank only if truly unreadable."
            )

        critical_valid = model_ok and serial_ok and manufacturer_ok and year_ok
        return feedback, critical_valid

    @staticmethod
    def _format_reflective_feedback(feedback_lines: List[str]) -> str:
        if not feedback_lines:
            return ""
        return "\n".join([f"- {line}" for line in feedback_lines[:6]])

    @staticmethod
    def _model_supports_reasoning_effort(model_name: str) -> bool:
        normalized = (model_name or "").strip().lower()
        return normalized.startswith("gpt-5") or normalized.startswith("o")

    @staticmethod
    def _model_supports_custom_temperature(model_name: str) -> bool:
        normalized = (model_name or "").strip().lower()
        # GPT-5 reasoning models reject non-default temperature values.
        return not normalized.startswith("gpt-5")

    def _apply_sampling_options(self, kwargs: Dict[str, Any], model_name: str) -> None:
        if self._model_supports_custom_temperature(model_name):
            kwargs["temperature"] = Config.TEMPERATURE
            kwargs["seed"] = Config.SEED

    @staticmethod
    def _max_completion_tokens_for_model(
        base_tokens: int,
        model_name: str,
        *,
        hard: bool = False,
        targeted: bool = False,
    ) -> int:
        try:
            base = int(base_tokens)
        except (TypeError, ValueError):
            base = 0
        normalized = (model_name or "").strip().lower()
        if not normalized.startswith("gpt-5"):
            return base

        # GPT-5 reasoning tokens count against max_completion_tokens. Keep enough
        # room for strict JSON output after the internal reasoning budget.
        minimum = 1200 if targeted else 3000
        if hard:
            minimum = 1600 if targeted else 4000
        return max(base, minimum)

    @staticmethod
    def _is_fallback_model_name(model_name: str) -> bool:
        normalized = (model_name or "").strip()
        fallback_names = {
            (Config.FALLBACK_LLM_MODEL or "").strip(),
            (Config.PREMIUM_LLM_MODEL or "").strip(),
        }
        return normalized in fallback_names and normalized != ""

    def _reasoning_effort_for_model(
        self,
        model_name: str,
        *,
        hard: bool = False,
        simple: bool = False,
    ) -> Optional[str]:
        if not self._model_supports_reasoning_effort(model_name):
            return None
        if hard:
            effort = Config.HARD_REASONING_EFFORT
        else:
            effort = Config.SIMPLE_REASONING_EFFORT if simple else Config.NORMAL_REASONING_EFFORT
        return None if effort == "none" else effort

    @staticmethod
    def _generic_nameplate_mapping_guidance() -> str:
        return """
GENERIC NAMEPLATE MAPPING:
- Manufacturer: read the brand/company text on the nameplate body, footer, or edge text.
- Model: read fields labeled MODEL, MODEL NO, UNIT MODEL, TYPE, BASIC MODEL, DOE BASIC MODEL, CATALOG, or ITEM.
- Serial Number: read the field labeled SERIAL, SERIAL NO, or S/N first — it always takes priority. Only when that field is absent or its value is genuinely unreadable may ORDER NO / ORDER # (or an explicitly permitted PRODUCT NO.) be used as the Serial Number; when you fall back this way, give Serial Number a LOW confidence score (below 70) so the record is routed to manual review.
- PART NO., PART NUMBER, CRN, CRN NO., certification/registration numbers, and unlabeled stamped identifiers are NEVER Serial Number values, except for the narrowly defined CRN pressure-vessel top/header identifier rule below.
- Pressure-vessel exception: if a tank/vessel plate has CRN/CRN NO. plus MAWP, MDMT, ASME/NB, CERTIFIED BY, or PSI AT evidence, no explicit serial field, and a single isolated identifier stamped in the top/header border, use that isolated top identifier as Serial Number. Never use the Part No., CRN value, date, pressure values, certification number, or a number inside another labeled field.
- The photo may be rotated 90 degrees or upside-down. Mentally rotate the plate upright before reading any field; never transcribe characters in the rotated orientation.
- NEVER output a date as the Serial Number. Values shaped like MM/YY, MM/YYYY, or YYYY/MM (example: 05/2018) are manufacturing dates. An upside-down date can look like '8102/50' or '8102/90' - such values are misreads, not serials.
- The field explicitly labeled SERIAL / SERIAL NO / S/N always takes priority. If that labeled value is unreadable, fall back to ORDER NO. / PRODUCT NO. with LOW confidence (below 70); never report a substituted value with high confidence, and never use a date.
- If no explicit serial is present, use the value labeled PRODUCT NO. / PRODUCT NUMBER / PROD. NO. (or ORDER NO. if that is the only unique identifier) as the Serial Number, preserving any printed hyphen.
- This PRODUCT NO. / ORDER NO. fallback does NOT apply when the plate shows a SERIAL, SERIAL NO, or S/N field: if such a field exists, that field is the Serial Number even if its value is hard to read.
- Do NOT confuse PRODUCT NO. with MODEL: when a plate shows BOTH a MODEL field and a PRODUCT NO. field, MODEL is the Model, and PRODUCT NO. becomes the Serial Number only when no explicit serial exists.
- Ignore flow-coefficient values labeled "Cv" (example: "Cv 25"); a Cv value is never a Model or Serial Number.
- Year: read YEAR, MFG DATE, MANUFACTURED, or PROD DATE. Convert MM/YY or MMYY to YYYY (example: 07/03 -> 2003, 0623 -> 2023).
- UBC Tag: read the identification tag photo (seq 1). It is either a '<PREFIX> NO.' plate (example: 'FC NO. 1N 02' -> 'FC-1N.02') or a large placard-style equipment identifier (examples: 'DST-4', 'EF-1', 'HUM 5'). Return placard identifiers exactly as printed; never leave UBC Tag blank when a placard identifier is clearly visible in seq 1.
- For table-style plates, read row/column labels literally. Do not swap neighboring values or infer from unrelated rows.
- For dark table-style plates with light gray value boxes, read the right-hand value box aligned to each label row (e.g., YEAR, SERIAL NO., TYPE).
- If both MODEL NO. and DOE BASIC MODEL NO. exist, prefer MODEL NO. for the output Model field.
- Preserve slashes in ORDER NO. values when they are visibly printed.
- Distinguish numeric 0 from letter O or D carefully in long equipment codes.
""".strip()

    @staticmethod
    def _technical_safety_bc_mapping_guidance() -> str:
        return """
TECHNICAL SAFETY BC STICKER MAPPING:
- For the "Technical Safety BC" output field, read ONLY the value on the seq 3 sticker row labeled "UNIT NO.", "UNIT NO", "BC Safety Authority Unit No.", or "Safety Authority Unit No.".
- On BPV Equipment Survey / Heating System stickers, the row may be labeled "5) BC Safety Authority Unit No."; use the value in that same row/right-side box.
- Ignore W.P., working pressure, S.O. NO., order numbers, dates, revision/form text, and the words "Safety Authority".
- Use the prefix "PV" for Pressure Vessel unit numbers; do not read this prefix as "AU".
- Preserve the visible unit number exactly, including the prefix and all six visible digits for PV values (example: "PV126541").
- If a PV value has fewer than six visible digits, return an empty string for Technical Safety BC rather than guessing.
- If any digit is ambiguous, return an empty string for Technical Safety BC rather than guessing.
- If the UNIT NO. row is absent or unreadable, return an empty string for Technical Safety BC.
""".strip()

    def _build_model_tiers(self) -> Tuple[List[str], List[str]]:
        """Return (primary_models, fallback_models) derived from the cost-controlled plan.

        - primary tier = [PRIMARY_LLM_MODEL]
        - fallback tier = remaining entries from get_llm_model_plan() (FALLBACK + PREMIUM if enabled)
        Honors ENABLE_LLM_FALLBACK / ENABLE_PREMIUM_FALLBACK / MAX_LLM_ATTEMPTS_PER_ASSET.
        """
        plan = get_llm_model_plan(Config)
        if not plan:
            return [], []
        return [plan[0]], plan[1:]

    @staticmethod
    def _should_fallback_to_heavier_model(
        score: float, missing_count: int,
        confidence_scores: Optional[Dict[str, int]] = None,
        extracted_data: Optional[Dict[str, str]] = None,
    ) -> bool:
        if score < Config.FALLBACK_MIN_SCORE or missing_count > Config.FALLBACK_MAX_MISSING:
            return True
        # Check per-field confidence from AI response
        if confidence_scores:
            critical_fields = ["Manufacturer", "Model", "Serial Number", "Year"]
            for field in critical_fields:
                if confidence_scores.get(field, 100) < 70:
                    return True
        # Check for '?' markers indicating uncertain characters
        if extracted_data:
            for field in Config.EXPECTED_FIELDS:
                if "?" in str(extracted_data.get(field, "")):
                    return True
        return False

    def _llm_multi_image_simple(
        self,
        qr: str,
        info: Dict[str, Any],
        has_nameplate_source: Optional[bool] = None,
        has_tsbc_source: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        BF/EL-like fast extraction path:
        - single structured call profile
        - bounded images/tokens
        - no reflective retry loop
        """
        required_fields = Config.EXPECTED_FIELDS
        seq_priority = {"0": 0, "1": 1, "3": 2, "2": 3}

        loaded_images: List[Dict[str, str]] = []
        for seq, path in sorted(info["images"].items(), key=lambda item: (seq_priority.get(item[0], 9), item[0])):
            try:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                loaded_images.append({"seq": seq, "path": path, "data": f"data:image/jpeg;base64,{b64}"})
            except Exception as e:
                logging.warning(f"Could not read image {path}: {e}")

        if not loaded_images:
            return {}
        if has_nameplate_source is None:
            has_nameplate_source = self._has_nameplate_source_image(info.get("images", {}))
        if has_tsbc_source is None:
            has_tsbc_source = self._has_tsbc_source_image(info.get("images", {}))

        selected_images = loaded_images[: Config.SIMPLE_MAX_IMAGES]
        role_map = {"0": "Nameplate", "1": "UBC Tag", "2": "General Asset", "3": "Technical Safety BC", "4": "Extra Photo"}
        role_lines = "\n".join(
            [
                f"- seq {img['seq']} ({role_map.get(img['seq'], 'Photo')}): {os.path.basename(img['path'])}"
                for img in selected_images
            ]
        )

        prompt = f"""
Extract technical data for mechanical asset {qr} with HIGH FIDELITY.

IMAGES PROVIDED:
{role_lines}

IMAGE PRIORITY:
- PRIORITIZE Image seq 0 for MODEL, SERIAL NUMBER, and YEAR.
- PRIORITIZE Image seq 1 for UBC TAG.
- Image seq 3 (Technical Safety BC sticker) must be used ONLY for the Technical Safety BC field.
- Do NOT use seq 3 as evidence for Manufacturer, Model, Serial Number, Year, or UBC Tag — not even for corroboration.
- If seq 0 or seq 1 is unclear, do not substitute text from seq 3; return "" for the affected field instead.

{self._generic_nameplate_mapping_guidance()}

{self._technical_safety_bc_mapping_guidance()}

BRAND-SPECIFIC OVERRIDES:
1. Identify Brand/Manufacturer first (e.g., Williams, Atlas Copco, Taco).
2. IF Atlas Copco:
   - Manufacturer: HARDCODE EXACTLY as 'Atlas Copco'. Do not search for a label.
   - Model: Look for 'Type'. (e.g., 'ZT15').
   - Serial Number: Look for 'Serial nº'.
     CRITICAL: Characters 'API' must be distinguished from 'APV'. Check pixels carefully! (e.g., 'API799655').
   - Year: Look for 'Manufacturing year'. Focus ONLY on seq 0.
3. IF Williams:
   - Manufacturer: HARDCODE EXACTLY as 'Williams'. Do not search for a label.
   - Model: Look for 'UNIT MODEL'. CRITICAL: Do NOT use the '07/03' date box.
   - Serial Number: Look for 'SERIAL NO.'.
   - Year: Find the MM/YY box and convert to YYYY. Focus ONLY on seq 0.
4. IF Taco:
   - Manufacturer: return 'Taco'.
   - Model: prefer 'Model No.'; if only 'DOE Basic Model No.' is present, use that value.
   - Serial Number: normally read ONLY a field explicitly labeled 'Serial', 'Serial No.', or 'S/N'. Never use 'Part No.', 'Order No.', or the CRN value.
   - Taco tank/pressure-vessel exception: when CRN/CRN No. is present with MAWP, MDMT, ASME/NB, CERTIFIED BY, or PSI AT evidence and no explicit serial field exists, use the single isolated identifier stamped in the plate's top/header border. Do not use values inside PART NO., CRN NO., DATE, MAWP, or MDMT fields.
   - Year: infer from 'MFG Date' including compact MMYY values such as '0623' -> '2023'.
5. IF Republic Manufacturing:
   - Manufacturer: return 'Republic Manufacturing'.
   - Model: read the field labeled 'Type'.
   - Serial Number: read the field labeled 'Serial no.'.
   - Year: read the field labeled 'Year'.
   - These are often dark table-style plates with light value boxes on the right.
6. IF Greenheck:
   - Manufacturer: return 'Greenheck'.
   - Model: read the engraved field labeled 'MODEL' and preserve every hyphen. Example family: 'USGF-322-5-A1-000-501-01'.
   - Serial Number: read the engraved field labeled 'S/N' exactly; digits are often 8-10 characters and can be 9 digits.
   - 'MARK' or 'TAG' values like 'EF-1' are not Model or Serial Number.
   - If no explicit year or manufacture date is visible, return Year as "".
7. IF Rheem or Ruud electric water heater:
   - Manufacturer: return the visible brand on the plate, usually 'Ruud' or 'Rheem'.
   - Model: read ONLY the field labeled 'MOD. NO.' or 'MODEL NO.'.
   - Serial Number: read ONLY the field labeled 'SER.' or 'SERIAL' / 'SERIAL NO.'.
   - Rheem/Ruud serials are letter-prefixed alphanumerics (example shape: 'A221812671'). The MFG DATE row is never the Serial Number.
   - Do not substitute a nearby sibling water-heater model or serial from memory.
   - Year: return blank unless a YEAR, MFG DATE, or other explicit manufacturing date is clearly visible on seq 0.
8. IF Siemens (plate text shows 'SIEMENS' or 'Siemens Building Technologies'):
   - Manufacturer: return 'Siemens'.
   - Model: read the field labeled 'Model' exactly; it is usually an all-numeric code (example: '03134', '011749'). Keep leading zeros.
   - Serial Number: these plates usually have NO explicit serial — use the 'Product No.' value (example: '599-0335') as the Serial Number, preserving the hyphen.
   - Do NOT swap them: the 'Model' field is the Model, and 'Product No.' is the Serial Number.
   - 'Cv' is a flow-coefficient rating (example: 'Cv 25'); never use it as Model or Serial Number.
   - Year: these valve plates usually have no manufacture date; return "" unless a clear YEAR or date is visible on seq 0.

TRANSCRIPTION RULES:
- Transcribe EXACTLY what is printed. No guesses.
- Use best evidence across seq 0 and seq 1 when a prioritized image is blurry or obstructed.
- Never use seq 3 as a source for any field other than Technical Safety BC.
- Return "" only when a field is not reliably visible in its allowed source images.
"""
        if not has_nameplate_source:
            prompt += "\n- Model, Serial Number, Year: return empty string because no valid seq 0 (Asset Plate) image is provided.\n"
        if not has_tsbc_source:
            prompt += "\n- Technical Safety BC: return empty string because no valid seq 3 image is provided.\n"
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt.strip()}]
        for img in selected_images:
            image_url: Dict[str, Any] = {"url": img["data"]}
            if Config.SIMPLE_IMAGE_DETAIL != "auto":
                image_url["detail"] = Config.SIMPLE_IMAGE_DETAIL
            content.append({"type": "image_url", "image_url": image_url})

        primary_models, fallback_models = self._build_model_tiers()
        best_data: Optional[Dict[str, str]] = None
        best_score = -1.0
        best_missing = len(required_fields)
        best_confidence: Optional[Dict[str, int]] = None

        tiers: List[Tuple[str, List[str]]] = [("primary", primary_models)]
        if fallback_models:
            tiers.append(("fallback", fallback_models))

        for tier_name, tier_models in tiers:
            if not tier_models:
                continue
            if tier_name == "fallback" and not self._should_fallback_to_heavier_model(
                best_score, best_missing, best_confidence, best_data
            ):
                continue
            if tier_name == "fallback":
                logging.info(
                    f"[{qr}] Escalating to fallback model tier due to low-quality primary extraction "
                    f"(best_score={best_score:.0f}%, missing={best_missing})."
                )

            for model_name in tier_models:
                model_had_structured_success = False
                for attempt in range(1, Config.MAX_LLM_ATTEMPTS_PER_MODEL + 1):
                    try:
                        kwargs: Dict[str, Any] = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": content}],
                            "max_completion_tokens": self._max_completion_tokens_for_model(
                                Config.SIMPLE_MAX_TOKENS,
                                model_name,
                                hard=tier_name == "fallback",
                            ),
                            "response_format": MEStructuredExtraction,
                        }
                        self._apply_sampling_options(kwargs, model_name)
                        effort = self._reasoning_effort_for_model(
                            model_name,
                            hard=tier_name == "fallback",
                            simple=True,
                        )
                        if effort:
                            kwargs["reasoning_effort"] = effort

                        resp = self.client.beta.chat.completions.parse(**kwargs)
                        msg = resp.choices[0].message
                        if getattr(msg, "refusal", None):
                            raise ValueError(f"Model refusal: {msg.refusal}")
                        if msg.parsed is None:
                            raise ValueError("No parsed structured payload returned")

                        parsed: MEStructuredExtraction = msg.parsed
                        extracted = parsed.model_dump(by_alias=True)
                        candidate = {field: str(extracted.get(field, "")).strip() for field in required_fields}
                        missing_fields = [field for field in required_fields if not candidate.get(field)]
                        candidate_scores = extracted.get("Confidence Scores", {})
                        if not isinstance(candidate_scores, dict):
                            candidate_scores = {}
                        
                        score = completeness_score(
                            candidate,
                            self._me_completeness_fields(bool(has_tsbc_source)),
                        )
                        model_had_structured_success = True

                        is_better = score > best_score or (score == best_score and len(missing_fields) < best_missing)
                        if is_better:
                            best_data = candidate
                            best_score = score
                            best_missing = len(missing_fields)
                            best_confidence = candidate_scores

                        logging.info(
                            f"[{model_name}] strategy 'simple' succeeded for {qr} "
                            f"(tier={tier_name}, completeness={score:.0f}%, missing={len(missing_fields)}, images={len(selected_images)})"
                        )

                        if (
                            score >= Config.EARLY_ACCEPT_SCORE
                            and len(missing_fields) <= Config.EARLY_ACCEPT_MAX_MISSING
                            and not self._should_fallback_to_heavier_model(score, len(missing_fields), candidate_scores, candidate)
                        ):
                            return {"extracted_data": candidate, "confidence_scores": candidate_scores}
                        break
                    except Exception as e:
                        if is_quota_error(e):
                            logging.error(
                                f"[{qr}] Quota exceeded on {model_name} (simple). "
                                f"Stopping all LLM attempts for this asset."
                            )
                            raise QuotaExceeded(qr) from e
                        if is_auth_error(e):
                            logging.error(
                                f"[{qr}] Auth failed on {model_name} (simple). "
                                f"Stopping all LLM attempts for this asset."
                            )
                            raise AuthFailed(qr) from e
                        if isinstance(
                            e,
                            (APIConnectionError, RateLimitError, APIStatusError,
                             BadRequestError, ValidationError, ValueError),
                        ):
                            logging.warning(
                                f"[{model_name}] strategy 'simple' failed on attempt {attempt} for {qr}: {e}"
                            )
                            if attempt < Config.MAX_LLM_ATTEMPTS_PER_MODEL:
                                time.sleep(Config.API_RETRY_DELAY * attempt)
                        else:
                            logging.error(f"Unexpected error in simple extraction for {qr}: {e}", exc_info=True)
                            break

                if model_had_structured_success and tier_name == "primary" and not self._should_fallback_to_heavier_model(
                    best_score, best_missing, best_confidence, best_data
                ):
                    # Primary tier already produced acceptable quality; do not invoke heavier fallback tier.
                    break

        if best_data is not None:
            logging.warning(
                f"Simple multi-image extraction returning best partial candidate for {qr} "
                f"(completeness={best_score:.0f}%, missing={best_missing})."
            )
            return {
                "extracted_data": best_data,
                "confidence_scores": best_confidence or {},
            }

        logging.error(f"Simple multi-image extraction failed for {qr}")
        return {}

    def _llm_multi_image(
        self,
        qr: str,
        info: Dict[str, Any],
        has_nameplate_source: Optional[bool] = None,
        has_tsbc_source: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Multi-image extraction with strict structured outputs and adaptive retries.

        Retry attempts are intentionally different (image ordering + targeted guidance)
        instead of repeating the same prompt.
        """
        if has_nameplate_source is None:
            has_nameplate_source = self._has_nameplate_source_image(info.get("images", {}))
        if has_tsbc_source is None:
            has_tsbc_source = self._has_tsbc_source_image(info.get("images", {}))
        if Config.SIMPLE_MODE:
            return self._llm_multi_image_simple(qr, info, has_nameplate_source, has_tsbc_source)

        required_fields = Config.EXPECTED_FIELDS
        images: List[Dict[str, str]] = []
        for seq in sorted(info["images"].keys()):
            p = info["images"][seq]
            try:
                with open(p, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                images.append({"seq": seq, "path": p, "data": f"data:image/jpeg;base64,{b64}"})
            except Exception as e:
                logging.warning(f"Could not read image {p}: {e}")

        if not images:
            return {}

        role_map = {"0": "Asset Plate", "1": "UBC Tag", "2": "Main Asset Photo", "3": "Technical Safety BC", "4": "Extra Photo"}
        retry_profiles = [
            {
                "name": "baseline",
                "priority": ["0", "1", "3", "2"],
                "reasoning_effort": "medium",
                "max_completion_tokens": 700,
                "focus_note": "General pass over all images.",
            },
            {
                "name": "nameplate_focus",
                "priority": ["0", "3", "1", "2"],
                "reasoning_effort": "high",
                "max_completion_tokens": 1200,
                "focus_note": "Prioritize nameplate and barcode text for Manufacturer, Model, Serial Number, and Year.",
            },
            {
                "name": "tag_focus",
                "priority": ["1", "3", "0", "2"],
                "reasoning_effort": "high",
                "max_completion_tokens": 1200,
                "focus_note": "Prioritize stickers/tags for UBC Tag and Technical Safety BC.",
            },
        ]
        retry_profiles = retry_profiles[: max(1, Config.MAX_LLM_ATTEMPTS_PER_MODEL)]

        def order_images(source: List[Dict[str, str]], priority: List[str]) -> List[Dict[str, str]]:
            rank = {seq: idx for idx, seq in enumerate(priority)}
            return sorted(source, key=lambda img: (rank.get(img["seq"], len(rank)), img["seq"]))

        primary_models, fallback_models = self._build_model_tiers()
        best_data: Optional[Dict[str, str]] = None
        best_score = -1.0
        best_missing_count = len(required_fields)
        best_confidence: Optional[Dict[str, int]] = None
        missing_fields: List[str] = []
        reflective_hint = ""
        ocr_context_cache: Optional[str] = None

        tiers: List[Tuple[str, List[str]]] = [("primary", primary_models)]
        if fallback_models:
            tiers.append(("fallback", fallback_models))

        for tier_name, tier_models in tiers:
            if not tier_models:
                continue
            if tier_name == "fallback" and not self._should_fallback_to_heavier_model(best_score, best_missing_count):
                continue
            if tier_name == "fallback":
                logging.info(
                    f"[{qr}] Escalating to fallback model tier due to low-quality primary extraction "
                    f"(best_score={best_score:.0f}%, missing={best_missing_count})."
                )

            for model_name in tier_models:
                for attempt, profile in enumerate(retry_profiles, start=1):
                    ordered = order_images(images, profile["priority"])
                    role_lines = "\n".join([
                        f"- Image {i + 1}: seq {img['seq']} ({role_map.get(img['seq'], 'Photo')}) -> {os.path.basename(img['path'])}"
                        for i, img in enumerate(ordered)
                    ])
                    missing_hint = ""
                    if missing_fields:
                        missing_hint = f"\nTarget missing fields from prior pass: {', '.join(missing_fields)}."
                    reflective_block = ""
                    if reflective_hint:
                        reflective_block = (
                            "\nReflective corrections from previous attempt "
                            "(fix these before finalizing values):\n"
                            f"{reflective_hint}\n"
                        )
                    ocr_context_block = ""
                    use_hybrid_ocr_context = (
                        Config.HYBRID_OCR_AGENT_ENABLED
                        and Config.OCR_MODE != "off"
                        and (Config.OCR_MODE == "full" or attempt > 1 or bool(reflective_hint))
                    )
                    if use_hybrid_ocr_context:
                        if ocr_context_cache is None:
                            ocr_context_cache = self._build_hybrid_ocr_context(images)
                        if ocr_context_cache:
                            ocr_context_block = f"""
Noisy OCR candidate text (character-level reference; verify against image):
{ocr_context_cache}
Use OCR candidates to resolve ambiguous characters (0/O, 1/I/l, 5/S, 8/B) and preserve exact Model, Serial Number, and UBC Tag strings.
"""

                    prompt = f"""
Extract technical data for mechanical asset {qr} with HIGH FIDELITY.

IMAGES PROVIDED:
{role_lines}

ATTEMPT FOCUS:
- {profile['focus_note']}{missing_hint}
{reflective_block}{ocr_context_block}

IMAGE PRIORITY:
- PRIORITIZE Image seq 0 for MODEL, SERIAL NUMBER, and YEAR.
- PRIORITIZE Image seq 1 for UBC TAG.
- Image seq 3 (Technical Safety BC sticker) must be used ONLY for the Technical Safety BC field.
- Do NOT use seq 3 as evidence for Manufacturer, Model, Serial Number, Year, or UBC Tag — not even for corroboration.
- If seq 0 or seq 1 is unclear, do not substitute text from seq 3; return "" for the affected field instead.

{self._generic_nameplate_mapping_guidance()}

{self._technical_safety_bc_mapping_guidance()}

BRAND-SPECIFIC OVERRIDES:
1. Identify Brand/Manufacturer first (e.g., Williams, Atlas Copco, Taco).
2. IF Atlas Copco:
   - Manufacturer: HARDCODE EXACTLY as 'Atlas Copco'. Do not search for a label.
   - Model: Look for 'Type'. (e.g., 'ZT15').
   - Serial Number: Look for 'Serial nº'.
     CRITICAL: Characters 'API' must be distinguished from 'APV'. Check pixels carefully! (e.g., 'API799655').
   - Year: Look for 'Manufacturing year'. Focus ONLY on seq 0.
3. IF Williams:
   - Manufacturer: HARDCODE EXACTLY as 'Williams'. Do not search for a label.
   - Model: Look for 'UNIT MODEL'. CRITICAL: Do NOT use the '07/03' date box.
   - Serial Number: Look for 'SERIAL NO.'.
   - Year: Find the MM/YY box and convert to YYYY. Focus ONLY on seq 0.
4. IF Taco:
   - Manufacturer: return 'Taco'.
   - Model: prefer 'Model No.'; if only 'DOE Basic Model No.' is present, use that value.
   - Serial Number: normally read ONLY a field explicitly labeled 'Serial', 'Serial No.', or 'S/N'. Never use 'Part No.', 'Order No.', or the CRN value.
   - Taco tank/pressure-vessel exception: when CRN/CRN No. is present with MAWP, MDMT, ASME/NB, CERTIFIED BY, or PSI AT evidence and no explicit serial field exists, use the single isolated identifier stamped in the plate's top/header border. Do not use values inside PART NO., CRN NO., DATE, MAWP, or MDMT fields.
   - Year: infer from 'MFG Date' including compact MMYY values such as '0623' -> '2023'.
5. IF Republic Manufacturing:
   - Manufacturer: return 'Republic Manufacturing'.
   - Model: read the field labeled 'Type'.
   - Serial Number: read the field labeled 'Serial no.'.
   - Year: read the field labeled 'Year'.
   - These are often dark table-style plates with light value boxes on the right.
6. IF Greenheck:
   - Manufacturer: return 'Greenheck'.
   - Model: read the engraved field labeled 'MODEL' and preserve every hyphen. Example family: 'USGF-322-5-A1-000-501-01'.
   - Serial Number: read the engraved field labeled 'S/N' exactly; digits are often 8-10 characters and can be 9 digits.
   - 'MARK' or 'TAG' values like 'EF-1' are not Model or Serial Number.
   - If no explicit year or manufacture date is visible, return Year as "".
7. IF Rheem or Ruud electric water heater:
   - Manufacturer: return the visible brand on the plate, usually 'Ruud' or 'Rheem'.
   - Model: read ONLY the field labeled 'MOD. NO.' or 'MODEL NO.'.
   - Serial Number: read ONLY the field labeled 'SER.' or 'SERIAL' / 'SERIAL NO.'.
   - Rheem/Ruud serials are letter-prefixed alphanumerics (example shape: 'A221812671'). The MFG DATE row is never the Serial Number.
   - Do not substitute a nearby sibling water-heater model or serial from memory.
   - Year: return blank unless a YEAR, MFG DATE, or other explicit manufacturing date is clearly visible on seq 0.
8. IF Siemens (plate text shows 'SIEMENS' or 'Siemens Building Technologies'):
   - Manufacturer: return 'Siemens'.
   - Model: read the field labeled 'Model' exactly; it is usually an all-numeric code (example: '03134', '011749'). Keep leading zeros.
   - Serial Number: these plates usually have NO explicit serial — use the 'Product No.' value (example: '599-0335') as the Serial Number, preserving the hyphen.
   - Do NOT swap them: the 'Model' field is the Model, and 'Product No.' is the Serial Number.
   - 'Cv' is a flow-coefficient rating (example: 'Cv 25'); never use it as Model or Serial Number.
   - Year: these valve plates usually have no manufacture date; return "" unless a clear YEAR or date is visible on seq 0.

TRANSCRIPTION RULES:
- Transcribe EXACTLY what is printed. No guesses.
- Use best evidence across seq 0 and seq 1 when a prioritized image is blurry or obstructed.
- Never use seq 3 as a source for any field other than Technical Safety BC.
- Return "" only when a field is not reliably visible in its allowed source images.
"""
                    if not has_nameplate_source:
                        prompt += "\n- Model, Serial Number, Year: return empty string because no valid seq 0 (Asset Plate) image is provided.\n"
                    if not has_tsbc_source:
                        prompt += "\n- Technical Safety BC: return empty string because no valid seq 3 image is provided.\n"

                    content = [{"type": "text", "text": prompt}]
                    content.extend([{"type": "image_url", "image_url": {"url": img["data"]}} for img in ordered])

                    try:
                        kwargs = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": content}],
                            "max_completion_tokens": self._max_completion_tokens_for_model(
                                profile["max_completion_tokens"],
                                model_name,
                                hard=tier_name == "fallback",
                            ),
                            "response_format": MEStructuredExtraction,
                        }
                        self._apply_sampling_options(kwargs, model_name)
                        effort = self._reasoning_effort_for_model(
                            model_name,
                            hard=tier_name == "fallback",
                        )
                        if effort:
                            kwargs["reasoning_effort"] = effort

                        resp = self.client.beta.chat.completions.parse(**kwargs)
                        msg = resp.choices[0].message
                        if getattr(msg, "refusal", None):
                            raise ValueError(f"Model refusal: {msg.refusal}")
                        if msg.parsed is None:
                            raise ValueError("No parsed structured payload returned")

                        parsed: MEStructuredExtraction = msg.parsed
                        extracted = parsed.model_dump(by_alias=True)
                        candidate = {field: str(extracted.get(field, "")).strip() for field in required_fields}
                        missing_fields = [field for field in required_fields if not candidate.get(field)]
                        candidate_scores = extracted.get("Confidence Scores", {})
                        if not isinstance(candidate_scores, dict):
                            candidate_scores = {}

                        feedback_lines, critical_valid = self._evaluate_llm_candidate(candidate)
                        reflective_hint = self._format_reflective_feedback(feedback_lines)

                        score = completeness_score(
                            candidate,
                            self._me_completeness_fields(bool(has_tsbc_source)),
                        )
                        logging.info(
                            f"[{model_name}] strategy '{profile['name']}' succeeded for {qr} "
                            f"(tier={tier_name}, completeness={score:.0f}%, missing={len(missing_fields)}, "
                            f"critical_valid={critical_valid})"
                        )
                        if feedback_lines:
                            logging.info(
                                f"[{model_name}] reflective feedback prepared for {qr}: "
                                + " | ".join(feedback_lines[:3])
                            )

                        if score > best_score or (score == best_score and len(missing_fields) < best_missing_count):
                            best_data = candidate
                            best_score = score
                            best_missing_count = len(missing_fields)
                            best_confidence = candidate_scores

                        # Early return when output quality is already high enough for production throughput.
                        if score >= Config.EARLY_ACCEPT_SCORE and (
                            critical_valid or len(missing_fields) <= Config.EARLY_ACCEPT_MAX_MISSING
                        ) and not self._should_fallback_to_heavier_model(score, len(missing_fields), candidate_scores, candidate):
                            return {"extracted_data": candidate, "confidence_scores": candidate_scores}
                    except Exception as e:
                        if is_quota_error(e):
                            logging.error(
                                f"[{qr}] Quota exceeded on {model_name}. "
                                f"Stopping all LLM attempts for this asset."
                            )
                            raise QuotaExceeded(qr) from e
                        if is_auth_error(e):
                            logging.error(
                                f"[{qr}] Auth failed on {model_name}. "
                                f"Stopping all LLM attempts for this asset."
                            )
                            raise AuthFailed(qr) from e
                        if isinstance(
                            e,
                            (APIConnectionError, RateLimitError, APIStatusError,
                             BadRequestError, ValidationError, ValueError),
                        ):
                            logging.warning(
                                f"[{model_name}] strategy '{profile['name']}' failed on attempt {attempt} for {qr}: {e}"
                            )
                            time.sleep(Config.API_RETRY_DELAY * attempt)
                            continue
                        logging.error(f"Unexpected error calling model {model_name}: {e}", exc_info=True)
                        break

        if best_data is not None:
            logging.warning(f"Using best partial structured extraction for {qr} (completeness={best_score:.0f}%).")
            return {"extracted_data": best_data, "confidence_scores": best_confidence}

        logging.error(f"LLM multi-image extraction failed for {qr}")
        return {}

    def _resolve_uncertain_chars(self, field_name: str, ai_value: str, ocr_value: str) -> str:
        """Replace '?' markers in AI value using OCR evidence when available."""
        if "?" not in ai_value or not ocr_value:
            return ai_value.replace("?", "")
        
        # Align AI and OCR strings and substitute uncertain positions
        resolved = list(ai_value)
        ocr_clean = re.sub(r"[^A-Z0-9]", "", ocr_value.upper())
        ai_clean = re.sub(r"[^A-Z0-9?]", "", ai_value.upper())
        
        ocr_idx = 0
        for i, char in enumerate(ai_clean):
            if char == "?" and ocr_idx < len(ocr_clean):
                # Find the position in the original string
                resolved_pos = ai_value.upper().index("?")
                resolved[resolved_pos] = ocr_clean[ocr_idx]
                ai_value = ai_value[:resolved_pos] + ocr_clean[ocr_idx] + ai_value[resolved_pos + 1:]
            if ocr_idx < len(ocr_clean):
                ocr_idx += 1
        
        return ai_value.replace("?", "")

    def _decision_engine(self, field: str, tess_result: Tuple[str, float], llm_result: Dict[str, Any]) -> str:
        """Intelligently chooses the best value between Tesseract and LLM outputs."""
        tess_val, tess_conf = tess_result
        llm_val, llm_conf = llm_result.get("value", ""), llm_result.get("confidence", 0)

        tess_norm = re.sub(r'[\s-]', '', (tess_val or "")).lower()
        llm_norm = re.sub(r'[\s-]', '', (llm_val or "")).lower()

        if llm_conf >= 80:
            logging.info(f"[{field}] High confidence LLM result: '{llm_val}' (Conf: {llm_conf}%)")
            return llm_val
            
        if tess_norm and tess_norm == llm_norm:
            logging.info(f"[{field}] Agreement between OCR & LLM: '{tess_val}'")
            return tess_val

        if tess_conf >= Config.TESSERACT_MIN_CONFIDENCE:
            logging.info(f"[{field}] High confidence OCR result: '{tess_val}' (Conf: {tess_conf:.1f}%)")
            return tess_val
        
        final_choice = llm_val or tess_val
        logging.warning(f"[{field}] Conflicting results. OCR: '{tess_val}' ({tess_conf:.1f}%), "
                        f"LLM: '{llm_val}' ({llm_conf}%). Choosing: '{final_choice}'")
        return final_choice

    def _validate_and_normalize(self, data: Dict[str, str]) -> Dict[str, str]:
        """Performs final validation and normalization on the extracted data."""
        cleaned = dict(data or {})

        if manufacturer := cleaned.get("Manufacturer"):
            cleaned["Manufacturer"] = self._canonicalize_manufacturer_candidate(manufacturer)

        if model_value := cleaned.get("Model"):
            normalized_model = self._normalize_model_candidate(model_value, cleaned.get("Manufacturer", ""))
            cleaned["Model"] = normalized_model

        if serial_value := cleaned.get("Serial Number"):
            normalized_serial = self._normalize_serial_candidate(serial_value, cleaned.get("Manufacturer", ""))
            cleaned["Serial Number"] = normalized_serial

        if year_str := cleaned.get("Year"):
            normalized_year = self._normalize_year_flexible(year_str)
            if normalized_year:
                year = int(normalized_year)
                min_y, max_y = Config.YEAR_VALIDATION_RANGE
                if min_y <= year <= max_y:
                    cleaned["Year"] = normalized_year
                else:
                    logging.warning(
                        f"Year '{year}' is outside valid range {Config.YEAR_VALIDATION_RANGE}. Discarding."
                    )
                    cleaned["Year"] = ""
            else:
                cleaned["Year"] = ""

        if ubc_tag := cleaned.get("UBC Tag"):
            cleaned["UBC Tag"] = self._canonicalize_ubc_tag(ubc_tag)

        return cleaned

    @staticmethod
    def _field_has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return bool(value)

    @staticmethod
    def _normalize_tsbc_unit_no(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"(?i)\btechnical\s+safety\s*bc\b\s*[:#\-]*\s*", "", text)
        text = re.sub(r"(?i)\bbc\s+safety\s+authority\b\s*[:#\-]*\s*", "", text)
        text = re.sub(r"(?i)\bsafety\s+authority\b\s*[:#\-]*\s*", "", text)
        text = re.sub(r"(?i)\bunit\s*no\.?\s*[:#\-]*\s*", "", text)
        text = re.split(r"(?i)(?:\bw\.?\s*p\.?|\bworking\s+pressure\b|\bs\.?\s*o\.?\s*no\.?|\bdate\b)", text, maxsplit=1)[0]
        text = re.sub(r"[^A-Za-z0-9./# -]", "", text)
        text = re.sub(r"\s+", " ", text).strip(" -:#./")
        pv_match = re.search(r"(?i)\b(?:p\s*v|a\s*u)\s*([0-9][A-Za-z0-9./# -]*)", text)
        if pv_match:
            suffix = re.sub(r"\s+", "", pv_match.group(1)).strip(" -:#./")
            text = f"PV{suffix}"
        compact = re.sub(r"[^A-Za-z0-9]", "", text)
        if compact.upper().startswith("PV"):
            pv_digits = re.sub(r"\D", "", compact[2:])
            if len(pv_digits) < 6:
                return ""
        elif compact.isdigit() and len(compact) < 6:
            return ""
        if not re.search(r"\d", text):
            return ""
        if len(compact) < 3:
            return ""
        return text[:80]

    @staticmethod
    def _me_completeness_fields(has_tsbc_source: bool = False) -> Set[str]:
        fields = set(Config.COMPLETENESS_SCORE_FIELDS)
        if has_tsbc_source:
            fields.add("Technical Safety BC")
        return fields

    @staticmethod
    def _normalize_confidence_score(value: Any) -> int:
        try:
            score = int(round(float(value)))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, score))

    def _reconcile_confidence_scores(
        self,
        structured_data: Dict[str, Any],
        confidence_scores: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, int]:
        """Keep per-field confidence aligned with the final saved field values."""
        reconciled: Dict[str, int] = {}
        provided = confidence_scores if isinstance(confidence_scores, dict) else {}

        for field in Config.EXPECTED_FIELDS:
            if self._field_has_value(structured_data.get(field, "")):
                reconciled[field] = self._normalize_confidence_score(provided.get(field, 0))
            else:
                reconciled[field] = 0

        return reconciled

    def _has_manufacturer_evidence(
        self,
        manufacturer_value: str,
        images: Dict[str, str],
        raw_manufacturer: str = "",
    ) -> bool:
        canonical = self._canonicalize_manufacturer_candidate(manufacturer_value)
        if not canonical:
            return False

        if self._canonicalize_manufacturer_candidate(raw_manufacturer) == canonical:
            return True

        for text in self._ocr_text_candidates_for_manufacturer(images)[:16]:
            if self._canonicalize_manufacturer_candidate(text) == canonical:
                return True

        return False

    def _has_ubc_label_evidence(
        self,
        ubc_value: str,
        images: Dict[str, str],
        raw_ubc_tag: str = "",
    ) -> bool:
        target = self._canonicalize_ubc_tag(ubc_value) or self._parse_ubc_tag_from_text(ubc_value)
        if not target:
            return False

        roi_candidate = self._extract_ubc_from_fc_no_rois(images)
        if roi_candidate == target:
            return True

        compact_target = re.sub(r"[^A-Z0-9]", "", target.upper())
        prefix = target.split("-", 1)[0] if "-" in target else ""
        for text in self._ocr_text_candidates_for_ubc(images):
            parsed = self._parse_ubc_tag_from_text(text)
            if parsed == target:
                return True
            compact_text = re.sub(r"[^A-Z0-9]", "", str(text or "").upper())
            if compact_target and compact_target in compact_text:
                if not prefix or prefix in compact_text:
                    return True

        return False

    def _synthesize_final_confidence_scores(
        self,
        structured_data: Dict[str, Any],
        confidence_scores: Optional[Dict[str, Any]] = None,
        *,
        images: Optional[Dict[str, str]] = None,
        raw_fields: Optional[Dict[str, Any]] = None,
        has_nameplate_source: Optional[bool] = None,
        has_tsbc_source: Optional[bool] = None,
    ) -> Dict[str, int]:
        """
        Derive confidence for final merged values when the source model did not
        provide a usable confidence score.
        """
        merged = dict(structured_data or {})
        raw = raw_fields if isinstance(raw_fields, dict) else {}
        image_map = images if isinstance(images, dict) else {}
        scores = self._reconcile_confidence_scores(merged, confidence_scores)

        if has_nameplate_source is None:
            has_nameplate_source = self._has_nameplate_source_image(image_map)
        if has_tsbc_source is None:
            has_tsbc_source = self._has_tsbc_source_image(image_map)
        has_ubc_source = self._is_readable_source_path(image_map.get("1", ""))
        evidence_texts = self._collect_nameplate_evidence_texts(image_map) if has_nameplate_source else []

        final_manufacturer = self._canonicalize_manufacturer_candidate(merged.get("Manufacturer", ""))
        final_model = self._normalize_model_candidate(merged.get("Model", ""), merged.get("Manufacturer", ""))
        final_serial = self._normalize_serial_candidate(merged.get("Serial Number", ""), merged.get("Manufacturer", ""))
        final_year = self._normalize_year_flexible(merged.get("Year", ""))
        final_ubc = merged.get("UBC Tag", "")
        final_tsbc = self._normalize_tsbc_unit_no(merged.get("Technical Safety BC", ""))

        raw_manufacturer = str(raw.get("Manufacturer", "") or "")
        raw_model = self._normalize_model_candidate(raw.get("Model", ""), merged.get("Manufacturer", ""))
        raw_serial = self._normalize_serial_candidate(raw.get("Serial Number", ""), merged.get("Manufacturer", ""))
        raw_year = self._normalize_year_flexible(raw.get("Year", ""))
        raw_ubc = str(raw.get("UBC Tag", "") or "")
        raw_tsbc = self._normalize_tsbc_unit_no(raw.get("Technical Safety BC", ""))

        def _boost(field: str, derived_score: int) -> None:
            if not self._field_has_value(merged.get(field, "")):
                scores[field] = 0
                return
            scores[field] = max(scores.get(field, 0), self._normalize_confidence_score(derived_score))

        if final_manufacturer and scores.get("Manufacturer", 0) <= 0:
            if self._has_manufacturer_evidence(final_manufacturer, image_map, raw_manufacturer):
                _boost("Manufacturer", 96)
            else:
                _boost("Manufacturer", 78)
        if final_manufacturer and not self._is_known_manufacturer_candidate(final_manufacturer):
            # Preserve the unseen brand, but never let it silently clear the
            # production review threshold before a human confirms it.
            scores["Manufacturer"] = min(
                self._normalize_confidence_score(scores.get("Manufacturer", 0)) or 65,
                65,
            )

        if final_model and scores.get("Model", 0) <= 0:
            if has_nameplate_source and self._has_model_label_evidence(
                final_model,
                image_map,
                evidence_texts=evidence_texts,
            ):
                _boost("Model", 96)
            elif raw_model and raw_model == final_model:
                _boost("Model", 92)
            elif self._is_model_code_candidate(final_model, merged.get("Manufacturer", "")):
                _boost("Model", 78)

        if final_serial and scores.get("Serial Number", 0) <= 0:
            if has_nameplate_source and self._has_serial_label_evidence(
                final_serial,
                image_map,
                evidence_texts=evidence_texts,
                manufacturer_hint=merged.get("Manufacturer", ""),
            ):
                _boost("Serial Number", 94)
            elif raw_serial and raw_serial == final_serial:
                _boost("Serial Number", 90)
            elif self._is_serial_candidate(final_serial):
                _boost("Serial Number", 76)

        if final_year and scores.get("Year", 0) <= 0:
            if has_nameplate_source and self._has_year_evidence(
                final_year,
                image_map,
                evidence_texts=evidence_texts,
            ):
                _boost("Year", 90)
            elif raw_year and raw_year == final_year:
                _boost("Year", 88)

        if final_ubc and scores.get("UBC Tag", 0) <= 0:
            if self._has_ubc_label_evidence(final_ubc, image_map, raw_ubc):
                _boost("UBC Tag", 95)
            elif has_ubc_source:
                _boost("UBC Tag", 84)

        if final_tsbc and scores.get("Technical Safety BC", 0) <= 0:
            if raw_tsbc and raw_tsbc == final_tsbc and has_tsbc_source:
                _boost("Technical Safety BC", 88)
            elif has_tsbc_source:
                _boost("Technical Safety BC", 72)

        return scores

    def _apply_ubc_consensus_confidence(
        self,
        confidence_scores: Optional[Dict[str, Any]],
        consensus: Optional[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Apply the documented UBC confidence floor/cap after final synthesis."""
        scores = {
            str(field): self._normalize_confidence_score(score)
            for field, score in (confidence_scores or {}).items()
        }
        metadata = consensus if isinstance(consensus, dict) else {}
        final_tag = str(metadata.get("final_tag") or "").strip()
        if not final_tag:
            scores["UBC Tag"] = 0
            return scores

        status = str(metadata.get("status") or "accepted_primary")
        current = self._normalize_confidence_score(scores.get("UBC Tag", 0))
        if status in {"confirmed_by_quorum", "corrected_by_quorum"}:
            scores["UBC Tag"] = max(current, 92)
        elif status == "unresolved":
            scores["UBC Tag"] = min(current or 65, 65)
        elif self._normalize_confidence_score(
            (metadata.get("primary") or {}).get("confidence", 0)
            if isinstance(metadata.get("primary"), dict)
            else 0
        ) <= 0:
            scores["UBC Tag"] = 84
        else:
            scores["UBC Tag"] = current
        return scores

    @staticmethod
    def _compute_avg_ai_conf(
        confidence_scores: Optional[Dict[str, Any]],
        *,
        has_tsbc_source: bool = False,
    ) -> float:
        if not isinstance(confidence_scores, dict) or not confidence_scores:
            return 0.0
        normalized_scores = [
            AssetProcessor._normalize_confidence_score(score)
            for field, score in confidence_scores.items()
            if has_tsbc_source or field != "Technical Safety BC"
        ]
        if not normalized_scores:
            return 0.0
        return sum(normalized_scores) / len(normalized_scores)

    def _build_manual_review_metadata(
        self,
        structured_data: Dict[str, Any],
        confidence_scores: Optional[Dict[str, Any]],
        completeness: float,
        *,
        has_tsbc_source: bool,
        score_context: Optional[Dict[str, Any]] = None,
        existing_manual_review: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = score_context if isinstance(score_context, dict) else {}
        existing = existing_manual_review if isinstance(existing_manual_review, dict) else {}
        structured = structured_data if isinstance(structured_data, dict) else {}
        scores = confidence_scores if isinstance(confidence_scores, dict) else {}

        def field_code(field_name: str) -> str:
            return re.sub(r"[^a-z0-9]+", "_", field_name.lower()).strip("_")

        reason_codes: List[str] = []
        required_fields = self._me_completeness_fields(has_tsbc_source)

        if completeness < Config.MANUAL_REVIEW_MIN_SCORE:
            reason_codes.append("low_completeness")

        for field in sorted(required_fields):
            if not self._field_has_value(structured.get(field, "")):
                reason_codes.append(f"missing_{field_code(field)}")

        critical_fields = ["Manufacturer", "Model", "Serial Number", "Year", "UBC Tag"]
        if has_tsbc_source:
            critical_fields.append("Technical Safety BC")

        for field in critical_fields:
            if not self._field_has_value(structured.get(field, "")):
                continue
            score = self._normalize_confidence_score(scores.get(field, 0))
            if score < Config.MANUAL_REVIEW_MIN_CONFIDENCE:
                reason_codes.append(f"low_confidence_{field_code(field)}")

        for field in Config.EXPECTED_FIELDS:
            if "?" in str(structured.get(field, "")):
                reason_codes.append(f"uncertain_character_{field_code(field)}")

        manufacturer = str(structured.get("Manufacturer", "") or "").strip()
        if manufacturer and not self._is_known_manufacturer_candidate(manufacturer):
            reason_codes.append("manufacturer_unrecognized")

        extra_codes = context.get("extra_reason_codes", [])
        if isinstance(extra_codes, list):
            reason_codes.extend(str(code) for code in extra_codes if code)

        reason_codes = sorted(set(reason_codes))
        ubc_consensus = context.get("ubc_consensus")
        if not isinstance(ubc_consensus, dict):
            ubc_consensus = existing.get("ubc_consensus", {})
        if not isinstance(ubc_consensus, dict):
            ubc_consensus = {}
        return {
            "flag_for_review": 1 if reason_codes else 0,
            "reason_codes": reason_codes,
            "ocr_assisted_rescue": bool(context.get("ocr_assisted_rescue")),
            "ocr_mode": context.get("ocr_mode", Config.OCR_MODE),
            "thresholds": {
                "min_completeness_score": Config.MANUAL_REVIEW_MIN_SCORE,
                "min_critical_confidence": Config.MANUAL_REVIEW_MIN_CONFIDENCE,
            },
            "raw_ocr": existing.get("raw_ocr", []),
            "ubc_consensus": ubc_consensus,
        }

    def _save_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Writes JSON and returns final save status after DB update handling."""
        score_context = data.pop("_score_context", {}) if isinstance(data, dict) else {}
        has_tsbc_source = False
        if isinstance(score_context, dict):
            has_tsbc_source = bool(score_context.get("has_tsbc_source"))
        suppress_manual_review_append = bool(
            isinstance(score_context, dict) and score_context.get("suppress_manual_review_append")
        )

        qr, asset_type, building = data["qr_code"], data["asset_type"].replace("- ", ""), data["building_number"]
        core_structured_fields = (
            "Manufacturer",
            "Model",
            "Serial Number",
            "Year",
            "UBC Tag",
            "Technical Safety BC",
        )
        structured = data.get("structured_data", {})
        if isinstance(structured, dict):
            existing_payload = self._load_existing(qr, building, asset_type)
            existing_structured = existing_payload.get("structured_data", {}) if isinstance(existing_payload, dict) else {}
            if isinstance(existing_structured, dict):
                preserved_extras = {
                    key: value
                    for key, value in existing_structured.items()
                    if key not in core_structured_fields
                }
                if preserved_extras:
                    merged_structured: Dict[str, Any] = {}
                    for key in core_structured_fields:
                        merged_structured[key] = structured.get(key, "")
                    for key, value in preserved_extras.items():
                        merged_structured[key] = value
                    structured = merged_structured
                    data["structured_data"] = structured

            serial_value = structured.get("Serial Number", "")
            if self._is_qr_like_serial(serial_value, qr):
                logging.warning(
                    f"[{qr}] Final save guard removed Serial Number '{serial_value}' because it matches QR code pattern."
                )
                structured["Serial Number"] = ""
            if self._model_serial_values_collide(
                structured.get("Model", ""),
                structured.get("Serial Number", ""),
            ):
                logging.warning(
                    "[%s] Final save guard removed duplicate Model '%s' because it "
                    "matches Serial Number.",
                    qr,
                    structured.get("Model", ""),
                )
                structured["Model"] = ""
                if isinstance(score_context, dict):
                    extra_codes = score_context.get("extra_reason_codes", [])
                    if not isinstance(extra_codes, list):
                        extra_codes = []
                    score_context["extra_reason_codes"] = sorted(
                        set([*extra_codes, "model_serial_collision"])
                    )
            data["structured_data"] = structured
            data["completeness_score"] = completeness_score(
                structured,
                self._me_completeness_fields(has_tsbc_source),
            )
            data["confidence_scores"] = self._reconcile_confidence_scores(
                structured,
                data.get("confidence_scores", {}),
            )
            data["Avg_ai_conf"] = self._compute_avg_ai_conf(
                data["confidence_scores"],
                has_tsbc_source=has_tsbc_source,
            )
            manual_review = self._build_manual_review_metadata(
                structured,
                data["confidence_scores"],
                data["completeness_score"],
                has_tsbc_source=has_tsbc_source,
                score_context=score_context,
                existing_manual_review=data.get("manual_review", {}),
            )
            data["manual_review"] = manual_review
            if manual_review.get("flag_for_review") == 1:
                structured["Flagged"] = "true"
                structured["Approved"] = ""
                data["structured_data"] = structured
                logging.warning(
                    "[%s] Marked ME extraction for manual review: %s",
                    qr,
                    ", ".join(manual_review.get("reason_codes", [])),
                )
                try:
                    review_image_paths: List[str] = []
                    sc_images = score_context.get("image_paths") if isinstance(score_context, dict) else None
                    if isinstance(sc_images, (list, tuple)):
                        review_image_paths = [str(p) for p in sc_images if p]
                    elif isinstance(sc_images, dict):
                        review_image_paths = [str(v) for v in sc_images.values() if v]
                    if not suppress_manual_review_append:
                        append_manual_review(
                            Config.MANUAL_REVIEW_QUEUE_FILE,
                            qr=qr, building=str(building), asset_type="ME",
                            image_paths=review_image_paths,
                            failure_reason=",".join(manual_review.get("reason_codes", [])) or "low_quality_extraction",
                            missing_fields=[
                                f for f in Config.EXPECTED_FIELDS
                                if not str(structured.get(f, "") or "").strip()
                            ],
                            attempted_models=list(get_llm_model_plan(Config)),
                            status=STATUS_LOW_QUALITY,
                        )
                except Exception as e:
                    logging.warning(f"[{qr}] Failed to append manual review entry: {e}")

        json_filename = f"{qr}_{asset_type}_{building}.json"
        final_path = os.path.join(Config.OUTPUT_FOLDER, json_filename)

        try:
            self._write_json_atomically(final_path, data)
        except Exception as e:
            logging.error(f"Failed to write JSON for {qr} at {final_path}: {e}")
            return {"saved": False, "path": "", "reason": f"write_failed: {e}"}

        api_mirror_path = self._get_existing_api_mirror_path(json_filename)
        if api_mirror_path:
            try:
                self._write_json_atomically(api_mirror_path, data)
            except Exception as e:
                logging.warning(
                    f"Saved primary JSON for {qr}, but failed to refresh API mirror at {api_mirror_path}: {e}"
                )

        # 4. DB Update inside strictly enforced transaction
        db_ok, db_status = self._update_ai_status(qr, final_path)
        file_exists = os.path.exists(final_path)

        if db_ok and file_exists:
            return {"saved": True, "path": final_path, "reason": "saved_and_ai_status_updated"}
        if file_exists:
            logging.warning(f"JSON kept for {qr} at {final_path}, but ai_status update returned: {db_status}")
            return {"saved": True, "path": final_path, "reason": f"saved_but_ai_status_not_updated:{db_status}"}
        logging.warning(f"JSON not retained for {qr}; DB update status={db_status}; expected_path={final_path}")
        return {"saved": False, "path": "", "reason": f"save_rolled_back:{db_status}"}

    @staticmethod
    def _write_json_atomically(path: str, data: Dict[str, Any]) -> None:
        """Writes a JSON file atomically, replacing the destination only after validation."""
        target_dir = os.path.dirname(path)
        os.makedirs(target_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=target_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            if os.path.getsize(temp_path) == 0:
                raise ValueError(f"Generated JSON is 0 bytes for path: {path}")

            os.replace(temp_path, path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    @staticmethod
    def _get_existing_api_mirror_path(filename: str) -> str:
        """Returns a sidecar API JSON path only when that file already exists."""
        api_dir = os.path.join(Config.ROOT_DEV_PATH, "API")
        mirror_path = os.path.join(api_dir, filename)
        if os.path.isfile(mirror_path):
            return mirror_path
        return ""

    # --- [4] Image Processing and Extraction Methods ---
    @staticmethod
    def _resolve_column(columns: List[str], candidates: List[str]) -> Optional[str]:
        """Finds a column name using case/spacing-insensitive matching."""
        def _norm(name: str) -> str:
            return name.lower().replace(" ", "").replace("_", "")

        normalized = { _norm(col): col for col in columns }
        for cand in candidates:
            key = _norm(cand)
            if key in normalized:
                return normalized[key]
        return None

    def _update_ai_status(self, qr_code: str, final_path: str = "") -> Tuple[bool, str]:
        """Marks the QR code as processed by setting ai_status=1 in QR_codes."""
        if not qr_code:
            return False, "missing_qr"
        if final_path:
            if not os.path.exists(final_path):
                logging.warning(f"ai_status update blocked for {qr_code}: JSON path not found: {final_path}")
                return False, "json_missing_at_final_path"
        else:
            existing_me_json = self._find_existing_json_for_qr(qr_code, "ME")
            if not existing_me_json:
                logging.warning(f"ai_status update blocked for {qr_code}: no ME JSON exists on disk.")
                return False, "json_missing_for_qr"
        if not os.path.exists(Config.DB_PATH):
            logging.warning(f"Database not found for ai_status update: {Config.DB_PATH}")
            return False, "db_missing"

        try:
            # timeout=10.0 to avoid SQLite 'database is locked' errors
            with closing(qrdb.get_connection(sqlite_path=Config.DB_PATH, timeout=10.0)) as conn:
                with closing(conn.cursor()) as cur:
                    columns = qrdb.table_columns(conn, Config.AI_STATUS_TABLE)  # backend-agnostic (PRAGMA on SQLite, information_schema on PG)
                    qr_col = self._resolve_column(columns, [Config.AI_STATUS_QR_COLUMN, "QR Code", "QR", "QRCode", "QR_code"])
                    ai_col = self._resolve_column(columns, [Config.AI_STATUS_COLUMN, "AI Status", "aiStatus"])
                    if not qr_col or not ai_col:
                        logging.warning(
                            f"ai_status update skipped: columns not found in {Config.AI_STATUS_TABLE}. "
                            f"available_columns={columns}"
                        )
                        return False, "missing_columns"

                    if not qrdb.is_postgres():
                        cur.execute('BEGIN IMMEDIATE')  # SQLite-only; PG uses MVCC
                    cur.execute(f'UPDATE "{Config.AI_STATUS_TABLE}" SET "{ai_col}" = ? WHERE "{qr_col}" = ?', ('1', qr_code))
                    conn.commit()
                    if cur.rowcount == 0:
                        logging.warning(f"No matching QR found to update ai_status for QR: {qr_code}")
                        return False, "qr_not_found"
            return True, "updated"
        except qrdb.DatabaseError as e:
            logging.error(f"Failed to update ai_status for QR {qr_code}: {e}. Rolling back file creation.")
            if final_path and os.path.exists(final_path):
                os.remove(final_path)
            return False, f"sqlite_error:{e}"

    def _preprocess_for_ocr(self, image_data: np.ndarray, original_filename: str) -> np.ndarray:
        """Applies a suite of pre-processing filters optimized for Tesseract OCR."""
        corrected_img = self._correct_perspective(image_data)
        gray = cv2.cvtColor(corrected_img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        _, bw_img = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        debug_path = os.path.join(Config.DEBUG_FOLDER, f"preprocessed_{original_filename}")
        cv2.imwrite(debug_path, bw_img)
        return bw_img
        
    def _correct_perspective(self, image: np.ndarray) -> np.ndarray:
        """Finds the largest quadrilateral and transforms it to a top-down view."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 150)

        contours, _ = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours: return image
        
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
        
        screenCnt = None
        for c in contours:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4:
                screenCnt = approx
                break
        
        if screenCnt is None:
            return image

        pts = screenCnt.reshape(4, 2)
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        
        (tl, tr, br, bl) = rect
        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))
        
        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))
        
        dst = np.array([
            [0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]
        ], dtype="float32")
        
        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, M, (maxWidth, maxHeight))

    def _tesseract_read_all(self, bw_img: np.ndarray, fields: List[str]) -> Dict[str, Tuple[str, float]]:
        """Runs Tesseract once and attempts to parse all required fields."""
        results = {}
        try:
            full_text = pytesseract.image_to_string(bw_img, config='--psm 6', timeout=10)

            for field in fields:
                if field == "UBC Tag":
                    val = self._canonicalize_ubc_tag(full_text)
                    results[field] = (val, 80.0 if val else 0.0)
                
        except pytesseract.TesseractError as e:
            logging.warning(f"Tesseract error: {e}")
        return results

    def _llm_extract_fields(self, original_image: np.ndarray, fields: List[str], original_path: str) -> Dict[str, Any]:
        """Uses the LLM with the original image for robust extraction."""
        fields_list = ", ".join(f'"{f}"' for f in fields)
        
        prompt = f"""
Analyze the provided image of an industrial asset. Your primary task is to extract information for these fields: {fields_list}.

Follow these steps with high precision:
1.  **Reasoning**: Systematically scan the entire image. Describe what you see, including the main nameplate AND any other stickers, tags, or labels. Note any ambiguities, glare, or unreadable text. If a field is not present, state that clearly.

2.  **Special Instructions for Key Fields**:
    * **'Year'**: This may be labeled 'Year', 'Mfg. Date', 'Manufactured Date', or '**Production Date**'. You must extract only the four-digit year from the value (e.g., if the date is '2023/07', the year is '2023').
    * **'Serial Number'**: This may be labeled as 'Serial No.', 'S/N', 'Serial', or similar. **Crucially, if the field next to the label is blank, look for a barcode. The number printed directly below a barcode is almost always the Serial Number.**
    * **'UBC Tag'**: This field is CRITICAL. It is usually on a separate sticker (white, silver, yellow) and not on the main metal nameplate. Look for formats like 'FH-B124-1', 'FC-6.32', or '**HUM 5**' (note the space instead of a hyphen).

3.  **Confidence Score**: For each field, provide a confidence score from 0 (not found/guess) to 100 (perfectly clear and certain).

4.  **Extraction**: Provide the final extracted data in a strict JSON object. If a value cannot be found for any reason, use an empty string "" for that field.

Your final output MUST be a single JSON object with three keys: "reasoning", "confidence_scores", and "extracted_data".

Example format:
{{
  "reasoning": "The image shows a main nameplate. The 'Production Date' is listed as 2023/07...",
  "confidence_scores": {{ "Manufacturer": 95, "Model": 100, "Serial Number": 98, "Year": 100, "UBC Tag": 0 }},
  "extracted_data": {{ "Manufacturer": "Polar Air", "Model": "PDWA...", "Serial Number": "6902307100180", "Year": "2023", "UBC Tag": "" }}
}}
"""
        # UPDATED: Increased max_tokens to 2500 to accommodate reasoning
        response = self._call_vision_api(prompt, original_path, original_image, max_tokens=2500)
        
        data = response.get("extracted_data", {})
        confidences = response.get("confidence_scores", {})
        
        # Clean the returned data from the LLM
        cleaned_data = {}
        if isinstance(data, dict):
            for field, value in data.items():
                str_val = str(value).strip()
                if str_val.lower() in ["none", "n/a", "null", ""]:
                    cleaned_data[field] = ""
                else:
                    cleaned_data[field] = str_val
        else:
            logging.warning(f"LLM returned malformed 'extracted_data': {data}")

        return {
            field: {"value": cleaned_data.get(field, ""), "confidence": confidences.get(field, 0)}
            for field in fields
        }

    def _call_vision_api(self, prompt: str, image_path: str, image_data: np.ndarray, max_tokens: int) -> Dict[str, Any]:
        """Robust wrapper for OpenAI Vision API calls with retry logic and updated parameters."""
        try:
            b64_image = self._encode_image_from_data(image_data)
        except Exception as e:
            logging.error(f"Could not encode image data from {image_path}: {e}")
            return {}

        content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": b64_image}}]
        
        # Keep this legacy single-image helper on the configured primary model.
        model_name = Config.PRIMARY_LLM_MODEL
        for attempt in range(Config.MAX_LLM_ATTEMPTS_PER_MODEL):
            try:
                kwargs = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        max_tokens,
                        model_name,
                    ),
                    "response_format": MEReasonedExtraction,
                }
                self._apply_sampling_options(kwargs, model_name)
                effort = self._reasoning_effort_for_model(model_name)
                if effort:
                    kwargs["reasoning_effort"] = effort

                response = self.client.beta.chat.completions.parse(**kwargs)
                msg = response.choices[0].message
                if getattr(msg, "refusal", None):
                    raise ValueError(f"Model refusal: {msg.refusal}")
                if msg.parsed is None:
                    raise ValueError("No parsed structured payload returned")

                parsed: MEReasonedExtraction = msg.parsed
                return parsed.model_dump()
            except Exception as e:
                if is_quota_error(e):
                    logging.error(f"Quota exceeded in vision API call for {image_path}. Aborting asset.")
                    raise QuotaExceeded(image_path) from e
                if is_auth_error(e):
                    logging.error(f"Auth failed in vision API call for {image_path}. Aborting asset.")
                    raise AuthFailed(image_path) from e
                logging.warning(f"API/schema error on attempt {attempt + 1}: {e}. Retrying...")
                time.sleep(Config.API_RETRY_DELAY * (attempt + 1))

        logging.error(f"API call failed after {Config.MAX_LLM_ATTEMPTS_PER_MODEL} attempts for {image_path}.")
        return {}

    @staticmethod
    def _encode_image_from_data(image_data: np.ndarray, format: str = ".jpg") -> str:
        """Encodes an in-memory np.ndarray image to a base64 data URI."""
        success, buffer = cv2.imencode(format, image_data)
        if not success:
            raise IOError("Could not encode image data.")
        encoded = base64.b64encode(buffer).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"

    @staticmethod
    def _normalize_year(value: str) -> str:
        if not value:
            return ""
        text = str(value).upper()
        match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        if match:
            return match.group(0)
        compact = re.sub(r"[^A-Z0-9]", "", text).replace("O", "0")
        if re.fullmatch(r"(?:0[1-9]|1[0-2])\d{2}", compact):
            yy = int(compact[-2:])
            if 0 <= yy <= 26:
                return f"20{yy:02d}"
            if 50 <= yy <= 99:
                return f"19{yy:02d}"
        return ""

    @staticmethod
    def _canonicalize_ubc_tag(text: str) -> str:
        """Finds a UBC tag in text by trying multiple regex patterns."""
        if not text: return ""

        parsed = AssetProcessor._parse_ubc_tag_from_text(str(text))
        if parsed:
            return parsed
        
        for pattern in Config.UBC_TAG_PATTERNS:
            if match := re.search(pattern, text, re.IGNORECASE):
                candidate = match.group(1).upper()
                if candidate.startswith("HUM "):
                    return candidate
                return candidate.replace(" ", "")
        
        return ""

    def _ensemble_best_value(self, llm_value: str, ocr_value: str, field_name: str) -> str:
        """VERSION 20 STRICT TRUST HIERARCHY:
        Always trust the Vision Model (LLM) over Tesseract, unless the LLM returned a '?'
        """
        llm_clean = (llm_value or "").strip()
        ocr_clean = (ocr_value or "").strip()

        if not llm_clean and not ocr_clean: return ""
        if not llm_clean: return ocr_clean
        if not ocr_clean: return llm_clean

        # If LLM is uncertain, use OCR fallback. Otherwise, LLM is always primary.
        if "?" in llm_clean:
            return ocr_clean

        return llm_clean

# --- [5] Main Execution Block ---
if __name__ == "__main__":
    try:
        setup_environment()
        processor = AssetProcessor()
        processor.run()
    except Exception as e:
        logging.critical(f"A critical error terminated the script: {e}", exc_info=True)
