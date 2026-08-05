#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Refactored EL (Electrical) extraction script.
Standardized to match 'ME' architecture: OOP, ThreadPoolExecutor, Config class, and Shared Validators.
Now includes Hybrid OCR / VLM Strategy AND Completeness Guard.

Preserves EL-specific features:
- Adaptive Multi-Image Retries.
- PNL Tag Formatting Rules.
- CLI Arguments (overriding Config).
- OCR: Feed preprocessed OCR text as context to the LLM.
- Completeness Guard: Prevents overwriting existing better data.
"""

import os
import re
import json
import tempfile
import time
import base64
import shutil
import sqlite3
import argparse
import logging
import platform
import importlib.util
from collections import defaultdict
from contextlib import closing
import db as qrdb  # backend-agnostic QR_codes DB layer (Phase C / C4)

# Legacy EL flow (Buildings.Process = 'Legacy'): shared rules module deployed
# with the EL review app. Optional at import time — if missing, Legacy QRs are
# skipped with a warning while Standard processing continues unaffected.
# Loaded by explicit path (no sys.path change) to avoid shadowing API modules
# with review-app modules of the same name (e.g. a local validators_shared copy).
_LEGACY_FLOW_PATH = os.environ.get("EL_LEGACY_FLOW_PATH", "").strip() or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "review", "Asset_dashboard_browser_EL", "legacy_flow.py")
)
# Review round-1 Minor-1: capture the actual import failure so a prod
# mis-deploy (missing file, syntax error in legacy_flow.py, bad
# EL_LEGACY_FLOW_PATH) is distinguishable in logs from any other reason the
# module might be unavailable, without needing manual repro on the VM.
_EL_LEGACY_FLOW_IMPORT_ERROR = ""
try:
    _legacy_spec = importlib.util.spec_from_file_location("el_legacy_flow", _LEGACY_FLOW_PATH)
    el_legacy_flow = importlib.util.module_from_spec(_legacy_spec)
    _legacy_spec.loader.exec_module(el_legacy_flow)
except Exception as _legacy_import_exc:  # pragma: no cover - deploy-environment guard
    el_legacy_flow = None
    _EL_LEGACY_FLOW_IMPORT_ERROR = str(_legacy_import_exc)

from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import Dict, List, Tuple, Any, Optional, Set

# Third-party
try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np
import pytesseract
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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

# Shared validators
try:
    from validators_shared import (
        normalize_ampere,
        normalize_explicit_ampere,
        normalize_power_rating_pair,
        normalize_explicit_power_rating_pair,
        normalize_volts,
        normalize_supply_from,
        normalize_el_supply_from_tag,
        normalize_ubc_tag,
        completeness_score,
    )
except ImportError:
    logging.warning("validators_shared module not found. Using local fallbacks.")
    def normalize_ampere(v):
        s = (v or "").strip().upper()
        m = re.search(r"(\d{1,4})(?:\s*A|AMP|AMPS)?", s)
        return m.group(1) if m else ""
    def normalize_explicit_ampere(value, *sources):
        normalized = normalize_ampere(value)
        if not normalized:
            return ""
        # Also treat the raw value itself as a source (e.g., "225A").
        all_sources = [str(value or "").strip()] + list(sources)
        for source in all_sources:
            text = str(source or "").strip().upper()
            if not text:
                continue
            if re.search(rf"(?<!\d){re.escape(normalized)}\s*(?:A|AMP|AMPS|AMPERE|AMPERES)\b", text):
                return normalized
            if re.search(rf"\b(?:A|AMP|AMPS|AMPERE|AMPERES)\s*{re.escape(normalized)}(?!\d)", text):
                return normalized
        return ""
    def normalize_power_rating_pair(value, uom=""):
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
            match = re.search(r"(?<![\d.])([1-9]\d{0,4})\s*(KVA|KW|VA)\b", text)
            if match:
                return match.group(1), match.group(2).upper()
            match = re.search(r"\b(KVA|KW|VA)\s*([1-9]\d{0,4})(?![\d.])", text)
            if match:
                return match.group(2), match.group(1).upper()
        normalized_uom = str(uom or "").strip().upper()
        value_text = str(value or "").strip()
        if normalized_uom in {"KVA", "KW", "VA"} and re.fullmatch(r"[1-9]\d{0,4}", value_text):
            return value_text, normalized_uom
        normalized_value_uom = str(value or "").strip().upper()
        uom_text = str(uom or "").strip()
        if normalized_value_uom in {"KVA", "KW", "VA"} and re.fullmatch(r"[1-9]\d{0,4}", uom_text):
            return uom_text, normalized_value_uom
        return "", ""
    def normalize_explicit_power_rating_pair(value, uom="", *sources):
        normalized_value, normalized_uom = normalize_power_rating_pair(value, uom)
        if not normalized_value or not normalized_uom:
            return "", ""
        for source in sources:
            text = str(source or "").strip().upper()
            if not text:
                continue
            if re.search(
                rf"(?<![\d.]){re.escape(normalized_value)}\s*{re.escape(normalized_uom)}\b",
                text,
            ):
                return normalized_value, normalized_uom
            if re.search(
                rf"\b{re.escape(normalized_uom)}\s*{re.escape(normalized_value)}(?![\d.])",
                text,
            ):
                return normalized_value, normalized_uom
        return "", ""
    def normalize_volts(v):
        s = (v or "").strip().upper()
        if not s:
            return ""
        s = re.sub(r"\s+", " ", s)
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
        m_wye = re.search(r"\b(208|480|600)\s*Y\s*/\s*(120|208|240|277|347|480|600)\s*V?\b", s)
        if m_wye:
            return f"{m_wye.group(1)}Y/{m_wye.group(2)}"
        m = re.search(r"(208|480|600)[/ ](120|277|347)\s*V?", s)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
        m2 = re.search(r"\b(208|480|600)V\b", s)
        if m2:
            return m2.group(1)
        return ""
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
        "FED", "FEED", "FROM", "SUPPLY", "SOURCE", "PANEL", "PANELBOARD", "BOARD",
        "EQUIPMENT", "ID", "IN", "AT", "ROOM", "RM", "SPACE", "SPC", "AREA",
        "LEVEL", "FLOOR", "MAIN", "ELECTRICAL", "ELEC",
    }
    _SUPPLY_FROM_JOINABLE_PREFIXES = {"MDP", "CDP", "SPL", "MCC", "PNL", "SWBD", "ATS", "MDC", "TX"}
    def _strip_supply_from_lead(text):
        cleaned = str(text or "").strip()
        previous = None
        while cleaned and cleaned != previous:
            previous = cleaned
            cleaned = _SUPPLY_FROM_LEAD_RE.sub("", cleaned).strip(" -:;,/")
        return cleaned
    def normalize_supply_from(v):
        s = str(v or "").upper()
        if not s:
            return ""
        s = s.replace("\n", " ").replace("\r", " ")
        s = re.sub(r"[|]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        match = _SUPPLY_FROM_CUE_RE.search(s)
        if match and match.start() > 0:
            s = s[match.start():]
        s = _strip_supply_from_lead(s)
        if not s:
            return ""
        match = _SUPPLY_FROM_STOP_RE.search(s)
        if match and match.start() > 0:
            s = s[:match.start()].strip(" -:;,/")
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
    def normalize_el_supply_from_tag(v):
        clean_tag = normalize_supply_from(v).upper().replace("EQUIPMENT NAME:", "").replace("MAIN", "").strip()
        if not clean_tag:
            return ""
        if clean_tag.startswith("TX") or (clean_tag.startswith("T") and len(clean_tag) > 1 and clean_tag[1].isdigit()):
            return clean_tag
        if clean_tag[0].isdigit():
            return f"PNL-{clean_tag}"
        for abbr in ("MDP", "CDP", "SPL", "MCC", "PNL", "SWBD", "ATS", "MDC"):
            if clean_tag.startswith(abbr):
                remainder = clean_tag[len(abbr):].lstrip(" -_")
                return f"{abbr}-{remainder}" if remainder else abbr
        return clean_tag
    def normalize_ubc_tag(v): return v.strip()
    def completeness_score(d, f): return 0.0

# -------------------- Constants / Config --------------------

class Config:
    """Centralized configuration for EL asset processing."""
    # --- Paths ---
    ROOT_DEV_PATH = os.getenv("DEV_PATH", "/home/developer")
    # Default paths (can be overridden by CLI)
    IMAGE_FOLDER = os.path.join(ROOT_DEV_PATH, "Capture_photos_upload")
    OUTPUT_FOLDER = os.path.join(ROOT_DEV_PATH, "Output_jason_api")
    DB_PATH = os.path.join(ROOT_DEV_PATH, "asset_capture_app_dev/data/QR_codes.db")
    ENV_PATH = os.path.join(ROOT_DEV_PATH, "API/OpenAI_key_giba.env")

    # --- Database ---
    DB_TABLE = "sdi_dataset_EL"
    AI_STATUS_TABLE = "QR_codes"

    # --- File Matching ---
    VALID_SUFFIXES = {"0", "1", "2"}
    VALID_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    
    # Regex: QR - Building - EL - Sequence
    # Regex accepts 0-3 so discovery can log Extra Photo (seq 3) as invalid_seq
    # rather than name_mismatch. VALID_SUFFIXES above is the actual gate.
    FILENAME_PATTERN = re.compile(
        r"^([T]?\d+)\s+"       # QR
        r"(\d+(?:-\d+)?)\s+"   # Building
        r"(EL)\s*-\s*([0-3])$", # Sequence (0-3; only 0,1,2 fed to LLM via VALID_SUFFIXES)
        re.IGNORECASE
    )

    # --- Fields ---
    STRUCTURED_FIELDS = ["UBC Asset Tag", "Branch Panel", "Ampere", "Power Rating", "Power Rating (UoM)", "Supply From", "Volts", "Location"]
    EL_TRANSFORMER_ASSET_GROUP = "Interior Distribution Transformers"
    EL_UNKNOWN_ASSET_GROUP = "Unknown"
    EL_BASE_SCORING_FIELDS = ("UBC Asset Tag", "Volts")
    EL_TRANSFORMER_SCORING_FIELDS = EL_BASE_SCORING_FIELDS + ("Power Rating", "Power Rating (UoM)")
    EL_NON_TRANSFORMER_SCORING_FIELDS = EL_BASE_SCORING_FIELDS + ("Ampere", "Supply From")
    EXTRACTION_RULE_VERSION = 15
    
    # --- OpenAI: cost-controlled model plan ---
    PRIMARY_LLM_MODEL = os.getenv("EL_PRIMARY_LLM_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    FALLBACK_LLM_MODEL = os.getenv("EL_FALLBACK_LLM_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    PREMIUM_LLM_MODEL = os.getenv("EL_PREMIUM_LLM_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    ENABLE_LLM_FALLBACK = os.getenv("EL_ENABLE_LLM_FALLBACK", "false").strip().lower() == "true"
    ENABLE_PREMIUM_FALLBACK = os.getenv("EL_ENABLE_PREMIUM_FALLBACK", "false").strip().lower() == "true"
    try:
        _max_attempts_asset_env = int(os.getenv("EL_MAX_LLM_ATTEMPTS_PER_ASSET", "1"))
    except ValueError:
        _max_attempts_asset_env = 1
    MAX_LLM_ATTEMPTS_PER_ASSET = max(1, min(_max_attempts_asset_env, 3))
    try:
        _max_attempts_model_env = int(os.getenv("EL_MAX_LLM_ATTEMPTS_PER_MODEL", "1"))
    except ValueError:
        _max_attempts_model_env = 1
    MAX_LLM_ATTEMPTS_PER_MODEL = max(1, min(_max_attempts_model_env, 3))
    OVERWRITE_EXISTING_JSON = os.getenv("EL_OVERWRITE_EXISTING_JSON", "false").strip().lower() == "true"
    MANUAL_REVIEW_QUEUE_FILE = os.getenv(
        "EL_MANUAL_REVIEW_QUEUE_FILE",
        os.path.join(OUTPUT_FOLDER, "manual_review_queue_EL.jsonl"),
    )
    _REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
    NORMAL_REASONING_EFFORT = os.getenv("EL_NORMAL_REASONING_EFFORT", "low").strip().lower()
    if NORMAL_REASONING_EFFORT not in _REASONING_EFFORTS:
        NORMAL_REASONING_EFFORT = "low"
    HARD_REASONING_EFFORT = os.getenv("EL_HARD_REASONING_EFFORT", "high").strip().lower()
    if HARD_REASONING_EFFORT not in _REASONING_EFFORTS:
        HARD_REASONING_EFFORT = "high"
    try:
        _fallback_min_score_env = float(os.getenv("EL_FALLBACK_MIN_SCORE", "95"))
    except ValueError:
        _fallback_min_score_env = 95.0
    FALLBACK_MIN_SCORE = max(0.0, min(_fallback_min_score_env, 100.0))
    try:
        _fallback_max_missing_env = int(os.getenv("EL_FALLBACK_MAX_MISSING", "1"))
    except ValueError:
        _fallback_max_missing_env = 1
    FALLBACK_MAX_MISSING = max(0, min(_fallback_max_missing_env, len(STRUCTURED_FIELDS)))
    try:
        _manual_review_min_score_env = float(os.getenv("EL_MANUAL_REVIEW_MIN_SCORE", "95"))
    except ValueError:
        _manual_review_min_score_env = 95.0
    MANUAL_REVIEW_MIN_SCORE = max(0.0, min(_manual_review_min_score_env, 100.0))
    try:
        _manual_review_min_conf_env = int(os.getenv("EL_MANUAL_REVIEW_MIN_CONFIDENCE", "70"))
    except ValueError:
        _manual_review_min_conf_env = 70
    MANUAL_REVIEW_MIN_CONFIDENCE = max(0, min(_manual_review_min_conf_env, 100))
    try:
        _retries_env = int(os.getenv("EL_API_MAX_RETRIES", "1"))
    except ValueError:
        _retries_env = 1
    API_MAX_RETRIES = max(1, min(_retries_env, 3))
    try:
        _retry_delay_env = float(os.getenv("EL_API_RETRY_DELAY", "0.5"))
    except ValueError:
        _retry_delay_env = 0.5
    API_RETRY_DELAY = max(0.0, _retry_delay_env)
    try:
        _timeout_env = float(os.getenv("EL_API_TIMEOUT", "45"))
    except ValueError:
        _timeout_env = 45.0
    API_TIMEOUT = max(10.0, _timeout_env)

    # --- Concurrency & OCR ---
    try:
        _workers_env = int(os.getenv("EL_MAX_WORKERS", "1"))
    except ValueError:
        _workers_env = 1
    MAX_WORKERS = max(1, min(_workers_env, 2))
    OCR_MODE = os.getenv("EL_OCR_MODE", "light").strip().lower()
    _hybrid_env = os.getenv("EL_HYBRID_OCR_AGENT")
    if _hybrid_env is None:
        HYBRID_OCR_AGENT_ENABLED = OCR_MODE != "off"
    else:
        HYBRID_OCR_AGENT_ENABLED = _hybrid_env.strip().lower() not in {"0", "false", "no"}
    
    # --- Constants ---
    ABBREVIATIONS = {
        "MDP": "MAIN DISTRIBUTION PANEL",
        "CDP": "CENTRAL DISTRIBUTION PANEL",
        "SPL": "SPLITTER",
        "MCC": "MOTOR CONTROL CENTRE",
        "PNL": "PANEL",
        "SWBD": "SWITCHBOARD",
        "ATS": "AUTOMATIC TRANSFER SWITCH",
        "MDC": "MAIN DISTRIBUTION CABINET",
    }

# -------------------- Logging & Setup --------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

def setup_environment():
    """Configures Tesseract and basic env."""
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        resolved = next((p for p in candidates if os.path.exists(p)), None)
        pytesseract.pytesseract.tesseract_cmd = resolved or (shutil.which("tesseract") or "tesseract")
    else:
        pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"

# -------------------- Pydantic Models --------------------

class ELConfidenceScores(BaseModel):
    """Per-field confidence scores for EL extraction."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ubc_asset_tag: int = Field(default=0, alias="UBC Asset Tag")
    branch_panel: int = Field(default=0, alias="Branch Panel")
    ampere: int = Field(default=0, alias="Ampere")
    power_rating: int = Field(default=0, alias="Power Rating")
    power_rating_uom: int = Field(default=0, alias="Power Rating (UoM)")
    fed: int = Field(default=0, alias="Fed")
    fed_from: int = Field(default=0, alias="Fed From")
    volts: int = Field(default=0, alias="Volts")
    location: int = Field(default=0, alias="Location")


class ELStructuredExtraction(BaseModel):
    """Schema for EL asset extraction."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    ubc_asset_tag: str = Field(default="", alias="UBC Asset Tag")
    branch_panel: str = Field(default="", alias="Branch Panel")
    ampere: str = Field(default="", alias="Ampere")
    ampere_source_text: str = Field(default="", alias="Ampere Source Text")
    power_rating: str = Field(default="", alias="Power Rating")
    power_rating_uom: str = Field(default="", alias="Power Rating (UoM)")
    power_rating_source_text: str = Field(default="", alias="Power Rating Source Text")
    fed: str = Field(default="", alias="Fed")
    fed_from: str = Field(default="", alias="Fed From")
    volts: str = Field(default="", alias="Volts")
    location: str = Field(default="", alias="Location")
    confidence_scores: ELConfidenceScores = Field(
        default_factory=ELConfidenceScores,
        alias="Confidence Scores",
        description="Per-field confidence 0-100."
    )

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_to_string(cls, value: Any) -> Any:
        if isinstance(value, (dict, BaseModel)):
            return value
        if value is None: return ""
        return str(value).strip()

    @field_validator("ampere")
    @classmethod
    def validate_ampere(cls, v: str) -> str:
        # Keep raw value (e.g., "225A") so normalize_explicit_ampere can
        # use the embedded unit as corroborating evidence.  Final
        # normalization happens after the explicit-ampere guard.
        return str(v or "").strip()

    @field_validator("volts")
    @classmethod
    def validate_volts(cls, v: str) -> str:
        return normalize_volts(v)
    
    @field_validator("fed", "fed_from")
    @classmethod
    def validate_supply(cls, v: str) -> str:
        return normalize_supply_from(v)

    @model_validator(mode="after")
    def validate_power_rating_fields(self):
        rating, uom = normalize_power_rating_pair(self.power_rating, self.power_rating_uom)
        self.power_rating = rating
        self.power_rating_uom = uom
        return self


class ELLocationScheduleExtraction(BaseModel):
    """Seq-2 only reread for panel location."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    location: str = Field(default="", alias="Location")
    location_source_text: str = Field(default="", alias="Location Source Text")
    location_confidence: int = Field(default=0, alias="Location Confidence")

    @field_validator("location", mode="before")
    @classmethod
    def _coerce_location_to_string(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("location_source_text", mode="before")
    @classmethod
    def _coerce_location_source_to_string(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("location_confidence", mode="before")
    @classmethod
    def _coerce_location_confidence(cls, value: Any) -> int:
        try:
            return max(0, min(100, int(round(float(value)))))
        except (TypeError, ValueError):
            return 0


# Legacy extraction (Buildings.Process = 'Legacy'): verbatim transcription only.
# Never normalizes or fabricates a PNL- prefix — that is the standard-flow
# behavior el_legacy_flow.legacy_structured_from_raw exists to avoid.
EL_LEGACY_PROMPT = """
Extract Electrical equipment data from LEGACY lamacoid/nameplate labels.
Rules:
- Label Text: transcribe ALL printed label text verbatim, preserving line breaks as \\n. Include every line of the black lamacoid plate(s).
- UBC Asset Tag: the equipment's own printed identity exactly as printed (e.g., `DIST. CTRE. #1`, `PANEL U`, `MCC NO. 2`, `MAIN DIST. CTRE.`). Copy verbatim — never reformat, never add prefixes, never normalize.
- UBC Asset Tag: never use the QR code, never use the long numeric value from an `Asset Identification` sticker, and never echo the file QR identifier.
- Supply From: the complete fed-from sentence verbatim (e.g., `FED FROM MAIN DIST. CTRE. TRANS. RM. 0060 THROUGH TRANS. "T1" 112.5 K.V.A.`). Do NOT shorten it to an identifier — copy the full printed phrase.
- Volts: the voltage text exactly as printed, including pairs (e.g., `120/208V`, `600/347V`). Leave blank if not printed.
- Ampere: the amperage text exactly as printed including the unit (e.g., `400A`, `100 AMPS`). Only when explicitly printed on the LAMACOID; never infer from KVA/KW or transformer specs.
- Ampere: NEVER take a value from a manufacturer nameplate's winding-current table (the column headed `COURANT CURRENT (A)`, or any `%`-tap row such as `100% 69.4`, or an `H.T.`/`B.T.`/`H.V.`/`L.V.` winding row). Those are per-tap per-winding currents, not the asset's amperage. Leave Ampere blank in that case.
- Nameplate Text: if EL-0 shows a MANUFACTURER nameplate (an engraved or stamped metal data plate, e.g. ABB / Westinghouse / Square D, carrying KVA, H.V./L.V. voltages, serial number), transcribe ALL of it verbatim, table cells row by row, preserving line breaks as \\n. Leave blank when EL-0 is not a manufacturer nameplate.
- Nameplate Text vs Label Text: keep them strictly separate. `Label Text` is the BLACK LAMACOID plate(s) only; manufacturer-nameplate text must NEVER be merged into it. Do not copy nameplate figures into Volts or Ampere -- transcribe them into Nameplate Text and let post-processing decide.
- Source precedence: EL-1 (lamacoid) is the PRIMARY source for identity and for any printed Volts/Ampere; EL-0 (Asset Plate, optional) is SECONDARY and supplies manufacturer-nameplate technical specs only. Never use the blue QR sticker text for any field.
"""

# Fields scored for legacy completeness / manual-review thresholds. Separate
# from Config.STRUCTURED_FIELDS (standard flow) because legacy candidates are
# post-processed dicts from el_legacy_flow.legacy_structured_from_raw, not raw
# model output — Branch Panel/Equipment ID/etc. are derived, not scored inputs.
EL_LEGACY_SCORING_FIELDS = ("UBC Asset Tag", "Volts", "Ampere", "Supply From")
# Transformer variant, mirroring the standard flow's split at Config
# EL_TRANSFORMER_SCORING_FIELDS / EL_NON_TRANSFORMER_SCORING_FIELDS (:317-319).
# A transformer contributes no Ampere (its nameplate prints per-tap winding
# currents, not a service rating) and frequently prints no fed-from clause, so
# scoring it against the flat set above capped assets like TX-MAIN at 50% and
# left them permanently flagged 'low_completeness' no matter how good the
# extraction was.
EL_LEGACY_BASE_SCORING_FIELDS = ("UBC Asset Tag", "Volts")
EL_LEGACY_TRANSFORMER_SCORING_FIELDS = EL_LEGACY_BASE_SCORING_FIELDS + (
    "Power Rating", "Power Rating (UoM)",
)


def _el_legacy_scoring_fields(structured):
    """Pick the legacy scoring set for one asset (transformer-aware).

    Falls back to the flat legacy set whenever the legacy_flow module is
    unavailable or version-skewed, so scoring never hard-fails on deploy skew.
    """
    if el_legacy_flow is not None and hasattr(el_legacy_flow, "is_legacy_transformer"):
        data = structured if isinstance(structured, dict) else {}
        if el_legacy_flow.is_legacy_transformer(
            str(data.get("UBC Asset Tag") or "").strip(),
            str(data.get("Equipment ID") or "").strip(),
        ):
            return EL_LEGACY_TRANSFORMER_SCORING_FIELDS
    return EL_LEGACY_SCORING_FIELDS


def _el_legacy_conf_scores(structured, raw_conf, scoring_fields):
    """Per-field confidence for one legacy asset, aware of value provenance.

    The model scores `Volts` / `Ampere` against the LAMACOID. On a transformer
    it correctly finds no volts there, reports a low score for its own blank
    read, and the stored value is derived from the EL-0 nameplate instead --
    so that field must carry the nameplate transcription's confidence, not the
    lamacoid's. Without this, QR 0000186132 extracts perfectly (100%
    completeness) yet still raises `low_confidence_volts`, and every
    correctly-read transformer would be flagged for manual review.

    `Power Rating` / `(UoM)` are never scored by the model at all -- they are
    composed by legacy_structured_from_raw -- so they inherit the same source.
    Fields the lamacoid genuinely supplied keep the model's own score.
    """
    data = structured if isinstance(structured, dict) else {}
    conf = raw_conf if isinstance(raw_conf, dict) else {}
    nameplate_conf = int(conf.get("Nameplate Text", 0) or 0)

    derived = set()
    if el_legacy_flow is not None and hasattr(el_legacy_flow, "legacy_nameplate_specs"):
        specs = el_legacy_flow.legacy_nameplate_specs(data.get("nameplate_text", "") or "")
        if specs.get("voltage") and str(data.get("Volts") or "").strip() == specs["voltage"]:
            derived.add("Volts")
        if specs.get("kva"):
            derived.update(("Power Rating", "Power Rating (UoM)"))

    scores = {}
    for field in scoring_fields:
        if not str(data.get(field, "") or "").strip():
            scores[field] = 0
            continue
        direct = int(conf.get(field, 0) or 0)
        if field in derived or not direct:
            direct = nameplate_conf or direct
        scores[field] = max(0, min(100, direct))
    return scores

# Legacy JSON schema/rule version, independent of Config.EXTRACTION_RULE_VERSION
# (the standard flow's rule version, which legacy payloads also stamp unchanged
# for compatibility but never key staleness decisions off of). Bump this when
# el_legacy_flow's composition rules change in a way that should trigger a
# rescore of previously-written legacy JSONs. See
# _existing_el_legacy_output_needs_rescore (review round-1 Important-1).
# v2 (2026-07-29): hyphen-form MDC/DCC identifiers ('DCC-1', was 'DCC #1') in
# Equipment ID / Supply From / Fed From Equipment ID, and Description
# composed as "<type word> - <Equipment ID>" ("Distribution - DCC-1").
# v3 (2026-07-30): UBC Asset Tag stores the equipment identity ('DCC-1'), not
# the X-composed structure ('DCC-2XXD1') — the X-tag remains the internal
# dictionary-decode structure only.
# v4 (2026-07-30): schema-driven ident normalization (dotted abbreviations
# 'U.P.S.1' -> 'UPS1'; plate-typo equivalence 'USP' -> 'UPS') and UPS as a
# recognized fed-from feeder ('FED FROM U.P.S. ...' -> 'UPS').
# v5 (2026-08-04): manufacturer-nameplate support. New 'Nameplate Text' raw
# field (EL-0 Asset Plate) parsed by legacy_nameplate_specs() into a
# transformer-only Power Rating pair and a primary-secondary voltage pair;
# lamacoid (EL-1) stays the primary source and is never overridden by it;
# the amperage scan now rejects nameplate winding-current tables.
EL_LEGACY_RULE_VERSION = 5


class ELLegacyConfidenceScores(BaseModel):
    """Per-field confidence scores for legacy EL extraction."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    label_text: int = Field(default=0, alias="Label Text")
    ubc_asset_tag: int = Field(default=0, alias="UBC Asset Tag")
    supply_from: int = Field(default=0, alias="Supply From")
    volts: int = Field(default=0, alias="Volts")
    ampere: int = Field(default=0, alias="Ampere")
    nameplate_text: int = Field(default=0, alias="Nameplate Text")


class ELLegacyStructuredExtraction(BaseModel):
    """Raw-preserving legacy extraction schema.

    Values are verbatim transcriptions, normalized afterward by
    el_legacy_flow.legacy_structured_from_raw. Deliberately attaches NONE of
    the standard-flow field validators that reshape supply-from / voltage
    text — those destroy legacy phrasing like 'MAIN DIST. CTRE.' before the
    legacy parser ever sees it. Only plain string coercion is applied here.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    label_text: str = Field(default="", alias="Label Text")
    ubc_asset_tag: str = Field(default="", alias="UBC Asset Tag")
    supply_from: str = Field(default="", alias="Supply From")
    volts: str = Field(default="", alias="Volts")
    ampere: str = Field(default="", alias="Ampere")
    # EL-0 manufacturer nameplate, kept out of label_text so the lamacoid
    # parse stays uncontaminated (see legacy_flow.legacy_nameplate_specs).
    nameplate_text: str = Field(default="", alias="Nameplate Text")
    confidence_scores: ELLegacyConfidenceScores = Field(
        default_factory=ELLegacyConfidenceScores,
        alias="Confidence Scores",
        description="Per-field confidence 0-100.",
    )

    @field_validator("label_text", "ubc_asset_tag", "supply_from", "volts", "ampere", "nameplate_text", mode="before")
    @classmethod
    def _coerce_to_string(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)


# -------------------- Helper Logic --------------------

def _apply_tag_formatting(raw_tag: str) -> str:
    """Formats the tag based on EL specific rules (PNL prefix, etc)."""
    clean_tag = raw_tag.upper().replace("EQUIPMENT NAME:", "").replace("MAIN", "").strip()
    clean_tag = _restore_el_decimal_floor_marker(clean_tag)
    if not clean_tag:
        return ""

    if clean_tag.startswith("TX") or (clean_tag.startswith("T") and len(clean_tag) > 1 and clean_tag[1].isdigit()):
        return clean_tag

    if clean_tag[0].isdigit():
        return f"PNL-{clean_tag}"

    found_abbr = None
    remainder = clean_tag
    for abbr in Config.ABBREVIATIONS.keys():
        if clean_tag.startswith(abbr):
            found_abbr = abbr
            remainder = clean_tag[len(abbr):].lstrip(" -_")
            break
    
    if found_abbr:
        return f"{found_abbr}-{remainder}" if remainder else found_abbr
    else:
        return f"PNL-{clean_tag}"


def _compact_alnum(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


_EL_PANEL_DECIMAL_FLOOR_RE = re.compile(
    r"^(?P<abbr>(?:MDP|CDP|SPL|MCC|PNL|SWBD|ATS)-?)?"
    r"(?P<head>[26][NES])15(?P<tail>[LPMD]\d{1,3})$",
    re.IGNORECASE,
)
_EL_TRANSFORMER_DECIMAL_FLOOR_RE = re.compile(
    r"^(?P<prefix>TX-?[NES])15(?P<tail>[A-Z]\d{1,3})$",
    re.IGNORECASE,
)


def _restore_el_decimal_floor_marker(raw_tag: str) -> str:
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


def _looks_like_qr_identifier(candidate: str, qr_value: str = "") -> bool:
    """Reject long numeric sticker IDs and values that mirror the asset QR code."""
    token = _compact_alnum(candidate)
    qr_token = _compact_alnum(qr_value)
    if not token:
        return False

    if qr_token:
        qr_candidates = {qr_token}
        if qr_token.startswith("T") and qr_token[1:].isdigit():
            qr_candidates.add(qr_token[1:])

        token_noz = token.lstrip("0")
        for qr_candidate in qr_candidates:
            if token == qr_candidate:
                return True
            qr_noz = qr_candidate.lstrip("0")
            if token_noz and qr_noz and token_noz == qr_noz:
                return True

    return bool(re.fullmatch(r"T?\d{6,}", token))


def _sanitize_el_asset_tag(candidate: str, qr_value: str = "") -> str:
    normalized = normalize_ubc_tag(candidate).upper()
    normalized = _restore_el_decimal_floor_marker(normalized)
    if not normalized:
        return ""
    if _looks_like_qr_identifier(normalized, qr_value):
        return ""
    return normalized


_EL_PANEL_IDENTIFIER_RE = re.compile(
    r"\b(?:EQUIPMENT\s+NAME|PANEL|PNL|SWBD|SWITCHBOARD|MCC|ATS|CDP|MDP|SPL)\b[\s|:#-]*([A-Z0-9]+(?:[\-.][A-Z0-9]+){0,15})",
    re.IGNORECASE,
)
_EL_LOCATION_CUE_RE = re.compile(
    r"\b(?:LOCATION|LOC(?:ATION)?|ROOM|RM|SPACE|SPC|AREA|LEVEL|FLOOR)\b",
    re.IGNORECASE,
)


def _extract_non_qr_panel_identifier(text: str, qr_value: str = "") -> str:
    """Extract a panel/tag identifier while ignoring QR-sticker IDs."""
    source = str(text or "").upper().strip()
    if not source:
        return ""

    normalized_source = re.sub(r"\s+", " ", source.replace("\n", " | "))
    for match in _EL_PANEL_IDENTIFIER_RE.finditer(normalized_source):
        candidate = _sanitize_el_asset_tag(match.group(1), qr_value)
        if candidate:
            return candidate

    if "|" not in normalized_source and " " not in normalized_source:
        return _sanitize_el_asset_tag(normalized_source, qr_value)

    return ""


def _resolve_el_asset_tag(raw_tag: str, branch_panel: str, qr_value: str = "", ocr_context: str = "") -> Tuple[str, str]:
    """Prefer non-QR panel identifiers for the EL UBC Asset Tag field."""
    clean_tag = _sanitize_el_asset_tag(raw_tag, qr_value)
    branch_candidate = _extract_non_qr_panel_identifier(branch_panel, qr_value)
    raw_panel_candidate = _extract_non_qr_panel_identifier(raw_tag, qr_value)

    if not branch_candidate and raw_panel_candidate:
        branch_candidate = raw_panel_candidate

    if raw_panel_candidate and (not clean_tag or clean_tag.startswith("PANEL")):
        clean_tag = raw_panel_candidate

    if not clean_tag:
        clean_tag = raw_panel_candidate or branch_candidate

    if not clean_tag and ocr_context:
        ocr_candidate = _extract_non_qr_panel_identifier(ocr_context, qr_value)
        if ocr_candidate:
            clean_tag = ocr_candidate
            if not branch_candidate:
                branch_candidate = ocr_candidate

    return clean_tag, branch_candidate


def _build_flexible_text_pattern(text: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", str(text or "").upper())
    if not tokens:
        return ""
    return r"\b" + r"\W*".join(re.escape(token) for token in tokens) + r"\b"


def _location_has_cue_prefix(location: str, source_text: str) -> bool:
    """Accept a location only when its source text includes a location cue before the value."""
    location_pattern = _build_flexible_text_pattern(location)
    if not location_pattern:
        return False

    source = re.sub(r"\s+", " ", str(source_text or "").upper()).strip()
    if not source or not _EL_LOCATION_CUE_RE.search(source):
        return False

    return bool(re.search(rf"{_EL_LOCATION_CUE_RE.pattern}.*?{location_pattern}", source, re.IGNORECASE))


def _looks_like_panel_identifier_fragment(candidate: str, panel_identifier: str = "") -> bool:
    candidate_token = _compact_alnum(candidate)
    panel_token = _compact_alnum(panel_identifier)
    if not candidate_token or not panel_token:
        return False
    if candidate_token == panel_token:
        return True
    if len(candidate_token) >= 4 and candidate_token in panel_token:
        return True
    return False


def _mechanical_dictionary_candidates() -> List[str]:
    candidates: List[str] = []
    for env_name in ("MECH_DICT_PATH", "MECHANICAL_DICT_PATH"):
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        normalized = os.path.normpath(raw)
        if normalized not in candidates:
            candidates.append(normalized)
    default_candidates = [
        os.path.normpath("/home/developer/dictionary/mechanical_dictionary.py"),
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dictionary", "mechanical_dictionary.py")
        ),
    ]
    for candidate in default_candidates:
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


@lru_cache(maxsize=1)
def _load_mechanical_asset_dictionary() -> Dict[str, Any]:
    for path in _mechanical_dictionary_candidates():
        if not path or not os.path.exists(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("mechanical_dictionary_runtime", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            asset_dict = getattr(module, "ASSET_DICTIONARY", {})
            if isinstance(asset_dict, dict) and asset_dict:
                return asset_dict
        except Exception as e:
            logging.warning("Failed to load mechanical dictionary from %s: %s", path, e)
    logging.warning("Mechanical dictionary not found or empty. EL Description prefix will fall back to 'Panel'.")
    return {}


def _el_description_prefix_from_tag(tag: str) -> str:
    """Return the description prefix that matches `tag`'s prefix in the mechanical
    dictionary (CDP -> 'Distribution', TX -> 'Transformer', ATS -> 'Transfer Switch',
    PNL -> 'Panel', ...). Falls back to 'Panel' so behavior is unchanged when the
    dictionary is unavailable or has no matching entry."""
    fallback = "Panel"
    clean_tag = (tag or "").strip().upper()
    if not clean_tag:
        return fallback
    asset_dict = _load_mechanical_asset_dictionary()
    if not asset_dict:
        return fallback
    composite_key = f"{clean_tag}|EL"
    entry = asset_dict.get(composite_key)
    if entry is None:
        for key in sorted((k for k in asset_dict.keys() if "|" in k), key=len, reverse=True):
            tag_prefix, _, key_type = key.partition("|")
            if key_type.upper() == "EL" and clean_tag.startswith(tag_prefix.upper()):
                entry = asset_dict.get(key)
                break
    if entry is None:
        for key in sorted((k for k in asset_dict.keys() if "|" not in k), key=len, reverse=True):
            if clean_tag.startswith(key.upper()):
                entry = asset_dict.get(key)
                break
    if not isinstance(entry, dict):
        return fallback
    description = str(entry.get("description") or "").strip()
    return description or fallback


def _electrical_dictionary_candidates() -> List[str]:
    candidates: List[str] = []
    for env_name in ("ELEC_DICT_PATH", "ELECTRICAL_DICT_PATH"):
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        normalized = os.path.normpath(raw)
        if normalized not in candidates:
            candidates.append(normalized)

    default_candidates = [
        os.path.normpath("/home/developer/dictionary/electrical.dictionary.py"),
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dictionary", "electrical.dictionary.py")
        ),
    ]
    for candidate in default_candidates:
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


@lru_cache(maxsize=1)
def _load_electrical_label_schema() -> Dict[str, Any]:
    for path in _electrical_dictionary_candidates():
        if not path or not os.path.exists(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("electrical_dictionary_runtime", path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            schema = getattr(module, "label_schema", {})
            if isinstance(schema, dict) and schema:
                return schema
        except Exception as e:
            logging.warning("Failed to load electrical dictionary from %s: %s", path, e)
    logging.warning("Electrical dictionary not found or empty. EL Volts will use current extraction only.")
    return {}


def _derive_dictionary_panel_volts(tag: str) -> str:
    """
    Parse panel-style UBC tags using electrical.dictionary.py and return the mapped voltage text.
    Prefer a leading voltage code (2 or 6) immediately after a recognized panel prefix.
    """
    schema = _load_electrical_label_schema()
    panel_schema = schema.get("panel") if isinstance(schema, dict) else None
    if not isinstance(panel_schema, dict):
        return ""

    clean_tag = str(tag or "").strip().upper()
    if not clean_tag:
        return ""

    clean_tag = re.sub(r"\s+", "-", clean_tag)
    clean_tag = re.sub(r"-{2,}", "-", clean_tag).strip("-")
    parts = clean_tag.split("-")

    # Do not derive volts from transformer tags.
    if parts and parts[0] == "TX":
        return ""

    panel_codes = panel_schema.get("codes", {}) if isinstance(panel_schema.get("codes", {}), dict) else {}
    volt_map = panel_codes.get("voltage", {}) if isinstance(panel_codes.get("voltage", {}), dict) else {}
    panel_prefixes = {abbr.upper() for abbr in Config.ABBREVIATIONS.keys()}

    prefix = ""
    remainder = clean_tag
    for candidate in sorted(panel_prefixes, key=len, reverse=True):
        if not clean_tag.startswith(candidate):
            continue
        next_char_index = len(candidate)
        if len(clean_tag) > next_char_index and clean_tag[next_char_index] not in {"-", "2", "6"}:
            continue
        prefix = candidate
        remainder = clean_tag[len(candidate):].lstrip("-")
        break

    if not prefix or not remainder:
        return ""

    volt_code_match = re.match(r"^([26])", remainder)
    if not volt_code_match:
        return ""

    volts = str(volt_map.get(volt_code_match.group(1), "") or "").strip()

    return normalize_volts(volts)


def _normalize_el_structured_fields(data: Dict[str, Any]) -> Dict[str, str]:
    normalized = dict(data or {})

    supply_val = ""
    for key in ("Supply From", "Fed From", "Fed"):
        candidate = normalize_el_supply_from_tag(str(normalized.get(key, "") or "").strip())
        if candidate:
            supply_val = candidate
            break

    output = {field: str(normalized.get(field, "") or "").strip() for field in Config.STRUCTURED_FIELDS}
    output["Supply From"] = supply_val
    power_rating, power_rating_uom = normalize_power_rating_pair(
        str(normalized.get("Power Rating", "") or "").strip(),
        str(normalized.get("Power Rating (UoM)", "") or "").strip(),
    )
    output["Power Rating"] = power_rating
    output["Power Rating (UoM)"] = power_rating_uom
    return output


_EL_TRANSFORMER_TAG_RE = re.compile(r"^(?:TX|T)-", re.IGNORECASE)


def _normalize_el_tag_for_scoring(tag: str) -> str:
    clean_tag = _sanitize_el_asset_tag(tag)
    if not clean_tag:
        return ""
    if _EL_TRANSFORMER_TAG_RE.match(clean_tag):
        return clean_tag
    return _apply_tag_formatting(clean_tag)


def _classify_el_asset_group(tag: str) -> str:
    normalized_tag = _normalize_el_tag_for_scoring(tag)
    if not normalized_tag:
        return Config.EL_UNKNOWN_ASSET_GROUP
    if _EL_TRANSFORMER_TAG_RE.match(normalized_tag):
        return Config.EL_TRANSFORMER_ASSET_GROUP
    return "Other"


def _el_scoring_fields_for_tag(tag: str) -> Tuple[str, ...]:
    asset_group = _classify_el_asset_group(tag)
    if asset_group == Config.EL_TRANSFORMER_ASSET_GROUP:
        return Config.EL_TRANSFORMER_SCORING_FIELDS
    if asset_group == Config.EL_UNKNOWN_ASSET_GROUP:
        return Config.EL_BASE_SCORING_FIELDS
    return Config.EL_NON_TRANSFORMER_SCORING_FIELDS


def _normalize_el_confidence_score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _el_completeness_score(data: Dict[str, Any]) -> float:
    normalized = _normalize_el_structured_fields(data)
    score_fields = _el_scoring_fields_for_tag(normalized.get("UBC Asset Tag", ""))
    return completeness_score(normalized, set(score_fields))


def _project_el_confidence_scores(
    structured_data: Dict[str, Any],
    confidence_scores: Optional[Dict[str, Any]],
) -> Dict[str, int]:
    normalized_data = _normalize_el_structured_fields(structured_data)
    normalized_conf = _normalize_el_confidence_scores(confidence_scores or {})
    score_fields = _el_scoring_fields_for_tag(normalized_data.get("UBC Asset Tag", ""))
    projected: Dict[str, int] = {}

    for field in score_fields:
        if str(normalized_data.get(field, "") or "").strip():
            projected[field] = _normalize_el_confidence_score(normalized_conf.get(field, 0))
        else:
            projected[field] = 0

    return projected


def _normalize_el_power_rating_fields(
    tag: str,
    value: str,
    uom: str,
    source_text: str = "",
    ocr_context: str = "",
) -> Tuple[str, str]:
    if _classify_el_asset_group(tag) != Config.EL_TRANSFORMER_ASSET_GROUP:
        return "", ""
    return normalize_explicit_power_rating_pair(value, uom, source_text, ocr_context)


def _normalize_el_confidence_scores(scores: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(scores, dict):
        return {}

    normalized: Dict[str, int] = {}
    supply_score = 0
    saw_supply_key = False

    for key, raw_score in scores.items():
        try:
            float(raw_score)
        except (TypeError, ValueError):
            continue
        score = _normalize_el_confidence_score(raw_score)

        if key in {"Fed", "Fed From", "Supply From"}:
            saw_supply_key = True
            supply_score = max(supply_score, score)
            continue

        normalized[str(key)] = score

    if saw_supply_key:
        normalized["Supply From"] = supply_score

    return normalized


def _avg_ai_conf_from_scores(scores: Dict[str, Any]) -> float:
    if not isinstance(scores, dict):
        return 0.0

    values: List[float] = []
    for field, raw_score in scores.items():
        try:
            values.append(float(raw_score))
        except (TypeError, ValueError):
            continue

    if not values:
        return 0.0
    return sum(values) / len(values)


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _existing_el_output_needs_rescore(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False

    # Human-reviewed payloads are authoritative. A stale metadata mismatch on a
    # reviewed JSON must not trigger AI reprocessing, because a new extraction
    # can erase fields the reviewer corrected in the form.
    if payload.get("modified") is True:
        return False

    try:
        stored_rule_version = int(payload.get("el_extraction_rule_version", 0) or 0)
    except (TypeError, ValueError):
        stored_rule_version = 0
    if stored_rule_version < Config.EXTRACTION_RULE_VERSION:
        return True

    structured = payload.get("structured_data")
    conf_scores = payload.get("confidence_scores")
    if not isinstance(structured, dict):
        structured = {}
    if not isinstance(conf_scores, dict):
        conf_scores = {}
    if str(structured.get("supply_from_manual_override") or "").strip() == "1":
        return False
    if str(structured.get("volts_manual_override") or "").strip() == "1":
        return False

    # Older EL payloads used Fed/Fed From and stored completeness/Avg AI Conf
    # using Volts and Location. Treat those as stale so a rerun rewrites them.
    if {"Fed", "Fed From"} & set(structured.keys()):
        return True
    if {"Fed", "Fed From"} & set(conf_scores.keys()):
        return True
    if "Power Rating" not in structured or "Power Rating (UoM)" not in structured:
        return True

    normalized_power_rating, normalized_power_rating_uom = normalize_power_rating_pair(
        str(structured.get("Power Rating", "") or "").strip(),
        str(structured.get("Power Rating (UoM)", "") or "").strip(),
    )
    if normalized_power_rating != str(structured.get("Power Rating", "") or "").strip():
        return True
    if normalized_power_rating_uom != str(structured.get("Power Rating (UoM)", "") or "").strip():
        return True

    final_tag = str(structured.get("UBC Asset Tag", "") or "").strip()
    if _classify_el_asset_group(final_tag) != Config.EL_TRANSFORMER_ASSET_GROUP:
        if str(structured.get("Power Rating", "") or "").strip() or str(structured.get("Power Rating (UoM)", "") or "").strip():
            return True
    for tag_key in ("UBC Asset Tag", "Branch Panel", "Supply From"):
        stored_tag = str(structured.get(tag_key, "") or "").strip()
        if stored_tag and _restore_el_decimal_floor_marker(stored_tag) != stored_tag.upper():
            return True
    stored_volts = normalize_volts(str(structured.get("Volts", "") or "").strip())
    expected_dictionary_volts = _derive_dictionary_panel_volts(final_tag)
    if expected_dictionary_volts and stored_volts != expected_dictionary_volts:
        return True

    normalized_structured = _normalize_el_structured_fields(structured)
    expected_score = _el_completeness_score(normalized_structured)
    stored_score = _coerce_float(payload.get("completeness_score"))
    if stored_score is None or abs(stored_score - expected_score) > 0.01:
        return True

    expected_conf = _project_el_confidence_scores(normalized_structured, conf_scores)
    if expected_conf != conf_scores:
        return True
    expected_avg = _avg_ai_conf_from_scores(expected_conf)
    stored_avg = _coerce_float(payload.get("Avg_ai_conf"))
    if stored_avg is None or abs(stored_avg - expected_avg) > 0.01:
        return True

    return False


def _existing_el_legacy_output_needs_rescore(payload: Dict[str, Any]) -> bool:
    """Legacy-appropriate staleness check (review round-1 Important-1).

    _existing_el_output_needs_rescore() recomputes completeness/confidence
    using STANDARD scoring rules (_el_completeness_score /
    _el_scoring_fields_for_tag / _classify_el_asset_group / tag-formatting
    normalization) and compares them against the stored values. A legacy
    JSON's stored completeness/confidence is computed with the 4-field
    EL_LEGACY_SCORING_FIELDS instead, so that comparison mismatches even for
    a flawless, 100%-complete legacy extraction — flagging every legacy JSON
    stale forever and causing a self-perpetuating, billable re-extraction
    loop across every Legacy building QR. Callers must route any payload
    with payload["process"] == "Legacy" here instead; a payload without that
    key predates this feature and should still fall back to
    _existing_el_output_needs_rescore() for one rescore.
    """
    if not isinstance(payload, dict):
        return False

    # Same human-reviewed protections as _existing_el_output_needs_rescore,
    # mirrored (not shared) because the two functions read different stored
    # score shapes and must stay independently correct.
    if payload.get("modified") is True:
        return False

    structured = payload.get("structured_data")
    if not isinstance(structured, dict):
        structured = {}
    if str(structured.get("supply_from_manual_override") or "").strip() == "1":
        return False
    if str(structured.get("volts_manual_override") or "").strip() == "1":
        return False

    try:
        stored_rule_version = int(payload.get("el_legacy_rule_version", 0) or 0)
    except (TypeError, ValueError):
        stored_rule_version = 0
    return stored_rule_version < EL_LEGACY_RULE_VERSION


# -------------------- Asset Processor Class --------------------

class AssetProcessor:
    def __init__(self, debug: bool = False, qr_filter: str = None):
        if Config.ENV_PATH and os.path.exists(Config.ENV_PATH):
            load_dotenv(dotenv_path=Config.ENV_PATH)
        self._refresh_runtime_config_from_env()
        warn_legacy_env_vars(
            "EL",
            (
                "EL_OPENAI_MODEL",
                "EL_OPENAI_PRIMARY_MODEL",
                "EL_PRIMARY_MODELS",
                "EL_FALLBACK_MODELS",
            ),
        )
        # We control retries ourselves; disable the SDK's retry layer to avoid double-billing on transient errors.
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=Config.API_TIMEOUT, max_retries=0)
        self.debug = debug
        self.qr_filter = qr_filter
        if Config.OCR_MODE not in {"off", "light", "full"}:
            logging.warning(f"Invalid EL_OCR_MODE '{Config.OCR_MODE}', defaulting to 'light'.")
            Config.OCR_MODE = "light"

        self.db_path = Config.DB_PATH
        self.qrs_to_ignore = self._load_qrs_to_ignore()
        self.ai_processed_qrs = self._load_ai_processed_qrs()
        self.building_process_map = self._load_building_process_map()
        logging.info(
            "EL runtime profile: workers=%s, ocr=%s, hybrid=%s, api_timeout=%ss, api_retries=%s",
            Config.MAX_WORKERS,
            Config.OCR_MODE,
            Config.HYBRID_OCR_AGENT_ENABLED,
            Config.API_TIMEOUT,
            Config.API_MAX_RETRIES,
        )
        logging.info(
            "EL model plan: %s | per_asset_cap=%s | per_model_cap=%s | premium_enabled=%s | fallback_trigger=(score<%s or missing>%s)",
            get_llm_model_plan(Config),
            Config.MAX_LLM_ATTEMPTS_PER_ASSET,
            Config.MAX_LLM_ATTEMPTS_PER_MODEL,
            Config.ENABLE_PREMIUM_FALLBACK,
            f"{Config.FALLBACK_MIN_SCORE:.0f}",
            Config.FALLBACK_MAX_MISSING,
        )
        logging.info(
            "EL reasoning tiers: normal=%s | hard=%s | manual_review=(score<%s or critical_confidence<%s)",
            Config.NORMAL_REASONING_EFFORT,
            Config.HARD_REASONING_EFFORT,
            f"{Config.MANUAL_REVIEW_MIN_SCORE:.0f}",
            Config.MANUAL_REVIEW_MIN_CONFIDENCE,
        )
        logging.info(f"EL Processor initialized. Ignored: {len(self.qrs_to_ignore)}, Processed: {len(self.ai_processed_qrs)}")

    def _refresh_runtime_config_from_env(self) -> None:
        Config.PRIMARY_LLM_MODEL = os.getenv("EL_PRIMARY_LLM_MODEL", Config.PRIMARY_LLM_MODEL).strip() or Config.PRIMARY_LLM_MODEL
        Config.FALLBACK_LLM_MODEL = os.getenv("EL_FALLBACK_LLM_MODEL", Config.FALLBACK_LLM_MODEL).strip() or Config.FALLBACK_LLM_MODEL
        Config.PREMIUM_LLM_MODEL = os.getenv("EL_PREMIUM_LLM_MODEL", Config.PREMIUM_LLM_MODEL).strip() or Config.PREMIUM_LLM_MODEL
        Config.ENABLE_LLM_FALLBACK = os.getenv(
            "EL_ENABLE_LLM_FALLBACK", "true" if Config.ENABLE_LLM_FALLBACK else "false"
        ).strip().lower() == "true"
        Config.ENABLE_PREMIUM_FALLBACK = os.getenv(
            "EL_ENABLE_PREMIUM_FALLBACK", "true" if Config.ENABLE_PREMIUM_FALLBACK else "false"
        ).strip().lower() == "true"
        try:
            Config.MAX_LLM_ATTEMPTS_PER_ASSET = max(
                1, min(int(os.getenv("EL_MAX_LLM_ATTEMPTS_PER_ASSET", str(Config.MAX_LLM_ATTEMPTS_PER_ASSET))), 3)
            )
        except ValueError:
            pass
        try:
            Config.MAX_LLM_ATTEMPTS_PER_MODEL = max(
                1, min(int(os.getenv("EL_MAX_LLM_ATTEMPTS_PER_MODEL", str(Config.MAX_LLM_ATTEMPTS_PER_MODEL))), 3)
            )
        except ValueError:
            pass
        Config.OVERWRITE_EXISTING_JSON = os.getenv(
            "EL_OVERWRITE_EXISTING_JSON", "true" if Config.OVERWRITE_EXISTING_JSON else "false"
        ).strip().lower() == "true"
        Config.MANUAL_REVIEW_QUEUE_FILE = os.getenv("EL_MANUAL_REVIEW_QUEUE_FILE", Config.MANUAL_REVIEW_QUEUE_FILE)

        normal_effort = os.getenv("EL_NORMAL_REASONING_EFFORT", Config.NORMAL_REASONING_EFFORT).strip().lower()
        if normal_effort in Config._REASONING_EFFORTS:
            Config.NORMAL_REASONING_EFFORT = normal_effort
        hard_effort = os.getenv("EL_HARD_REASONING_EFFORT", Config.HARD_REASONING_EFFORT).strip().lower()
        if hard_effort in Config._REASONING_EFFORTS:
            Config.HARD_REASONING_EFFORT = hard_effort
        try:
            Config.FALLBACK_MIN_SCORE = max(
                0.0,
                min(float(os.getenv("EL_FALLBACK_MIN_SCORE", str(Config.FALLBACK_MIN_SCORE))), 100.0),
            )
        except ValueError:
            pass
        try:
            Config.FALLBACK_MAX_MISSING = max(
                0,
                min(int(os.getenv("EL_FALLBACK_MAX_MISSING", str(Config.FALLBACK_MAX_MISSING))), len(Config.STRUCTURED_FIELDS)),
            )
        except ValueError:
            pass
        try:
            Config.MANUAL_REVIEW_MIN_SCORE = max(
                0.0,
                min(float(os.getenv("EL_MANUAL_REVIEW_MIN_SCORE", str(Config.MANUAL_REVIEW_MIN_SCORE))), 100.0),
            )
        except ValueError:
            pass
        try:
            Config.MANUAL_REVIEW_MIN_CONFIDENCE = max(
                0,
                min(int(os.getenv("EL_MANUAL_REVIEW_MIN_CONFIDENCE", str(Config.MANUAL_REVIEW_MIN_CONFIDENCE))), 100),
            )
        except ValueError:
            pass

        ocr_mode = os.getenv("EL_OCR_MODE", Config.OCR_MODE).strip().lower()
        if ocr_mode in {"off", "light", "full"}:
            Config.OCR_MODE = ocr_mode

        hybrid_raw = os.getenv("EL_HYBRID_OCR_AGENT")
        if hybrid_raw is None:
            Config.HYBRID_OCR_AGENT_ENABLED = Config.OCR_MODE != "off"
        else:
            Config.HYBRID_OCR_AGENT_ENABLED = hybrid_raw.strip().lower() not in {"0", "false", "no"}

    def _score_ocr_text(self, text: str) -> Tuple[int, int, int, int]:
        source = str(text or "").upper()
        if not source.strip():
            return (0, 0, 0, 0)

        amp_hits = len(re.findall(r"(?<!\d)\d{1,4}\s*(?:A|AMP|AMPS|AMPERE|AMPERES)\b", source))
        volts_hits = len(
            re.findall(
                r"\b(?:208Y/120V|208/120V|480Y/277V|480/277V|600/347V|600V|480V|208V)\b",
                source,
            )
        )
        word_hits = len(re.findall(r"\b[A-Z0-9][A-Z0-9/-]{2,}\b", source))
        return (amp_hits, volts_hits, word_hits, len(source))

    def _extract_best_ocr_text(self, image_data: np.ndarray) -> Tuple[str, str]:
        best_text = ""
        best_orientation = "orig"
        best_score = (0, 0, 0, 0)

        orientation_candidates = [("orig", image_data)]
        if image_data is not None:
            orientation_candidates.append(("rot180", cv2.rotate(image_data, cv2.ROTATE_180)))
            orientation_candidates.append(("rot90cw", cv2.rotate(image_data, cv2.ROTATE_90_CLOCKWISE)))
            orientation_candidates.append(("rot90ccw", cv2.rotate(image_data, cv2.ROTATE_90_COUNTERCLOCKWISE)))

        for orientation, candidate_img in orientation_candidates:
            processed = self._preprocess_for_ocr(candidate_img)
            try:
                text = pytesseract.image_to_string(processed, config='--oem 3 --psm 6', timeout=10)
            except RuntimeError:
                continue

            score = self._score_ocr_text(text)
            if score > best_score:
                best_text = text
                best_orientation = orientation
                best_score = score

        return best_text, best_orientation

    def _load_qrs_to_ignore(self) -> Set[str]:
        to_ignore = set()
        if not os.path.exists(self.db_path): return to_ignore
        try:
            with closing(qrdb.get_connection(sqlite_path=self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                with closing(conn.cursor()) as cur:
                    try:
                        cur.execute(f'SELECT * FROM "{Config.DB_TABLE}" LIMIT 1')
                        cols = [description[0] for description in cur.description]
                        approved_col = next((c for c in cols if "approved" in c.lower()), "Approved")
                        qr_col = next((c for c in cols if "qr" in c.lower()), "QR Code")
                        
                        sql = f'SELECT "{qr_col}" as qr FROM "{Config.DB_TABLE}" WHERE "{approved_col}" = \'1\''
                        cur.execute(sql)
                        for row in cur.fetchall():
                            if val := str(row["qr"]).strip():
                                to_ignore.add(val)
                    except Exception:
                         pass 
        except Exception as e:
             logging.error(f"DB Error: {e}")
        return to_ignore

    def _load_ai_processed_qrs(self) -> Set[str]:
        processed = set()
        stale_processed: List[str] = []
        el_qrs_with_images = self._qrs_with_el_images()
        if not os.path.exists(self.db_path): return processed
        try:
            with closing(qrdb.get_connection(sqlite_path=self.db_path)) as conn:
                with closing(conn.cursor()) as cur:
                    cols = {n.lower(): n for n in qrdb.table_columns(conn, Config.AI_STATUS_TABLE)}  # backend-agnostic
                    qr_col = cols.get("qr_code_id") or cols.get("qr code") or cols.get("qr")
                    ai_col = cols.get("ai_status") or cols.get("aistatus")
                    if qr_col and ai_col:
                        cur.execute(f'SELECT "{qr_col}", "{ai_col}" FROM "{Config.AI_STATUS_TABLE}"')
                        for qr, status in cur.fetchall():
                            if str(status).strip() == "1":
                                q = str(qr).strip()
                                if not q:
                                    continue
                                if q in self.qrs_to_ignore:
                                    processed.add(q)
                                    continue
                                # Safety rule: only treat ai_status=1 as processed when a current EL JSON exists.
                                existing_json_path = self._find_existing_json_for_qr(q, "EL")
                                if existing_json_path:
                                    try:
                                        with open(existing_json_path, "r", encoding="utf-8") as f:
                                            existing_payload = json.load(f)
                                    except Exception:
                                        existing_payload = {}
                                    # Review round-1 Important-1 routing (mirrored from the
                                    # process_single_asset call site ~line 2326): a payload
                                    # carrying "process": "Legacy" must be staleness-checked
                                    # with the legacy-appropriate function, or the standard
                                    # check's disagreement with legacy scoring flaps
                                    # ai_status 1->0 on every run for every legacy JSON.
                                    needs_rescore = (
                                        _existing_el_legacy_output_needs_rescore(existing_payload)
                                        if existing_payload.get("process") == "Legacy"
                                        else _existing_el_output_needs_rescore(existing_payload)
                                    )
                                    if needs_rescore:
                                        stale_processed.append(q)
                                    else:
                                        processed.add(q)
                                elif q in el_qrs_with_images:
                                    # Reset only rows that correspond to EL assets in current image set.
                                    stale_processed.append(q)
                                else:
                                    # Shared ai_status table: keep rows for non-EL assets untouched.
                                    processed.add(q)
        except Exception:
            pass
        if stale_processed:
            updated = self._bulk_set_ai_status(stale_processed, 0)
            logging.warning(
                "Detected %d stale EL ai_status=1 rows without current usable EL JSON; reset %d row(s) back to 0. Examples: %s",
                len(stale_processed),
                updated,
                stale_processed[:10],
            )
        return processed

    def _load_building_process_map(self) -> Dict[str, str]:
        """Buildings.Process flow gate (2026-07-29): 'Standard' -> existing EL
        flow; 'Legacy' -> _process_legacy_asset; blank/missing/unreadable ->
        the QR is skipped with a warning (never silently Standard)."""
        try:
            conn = qrdb.get_connection(sqlite_path=self.db_path)
            try:
                cur = conn.execute('SELECT "Code", "Process" FROM "Buildings"')
                return {
                    str(code or "").strip(): str(process or "").strip()
                    for code, process in cur.fetchall()
                }
            finally:
                conn.close()
        except Exception as exc:
            logging.error(
                "Could not load Buildings.Process map (%s). All assets will be "
                "skipped until the Buildings table is reachable — the flow gate "
                "never defaults to Standard.", exc,
            )
            return {}

    def _qrs_with_el_images(self) -> Set[str]:
        """Scans image folder and returns QRs that currently have EL image files."""
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
                if asset_type.upper() != "EL":
                    continue
                if seq not in Config.VALID_SUFFIXES:
                    continue
                qr_val = str(qr).strip()
                if qr_val:
                    qrs.add(qr_val)
        except OSError:
            return qrs
        return qrs

    def _find_existing_json_for_qr(self, qr: str, asset_type: str = "EL") -> str:
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

    def _bulk_set_ai_status(self, qrs: List[str], value: int) -> int:
        """Bulk updates ai_status for a list of QR codes. Returns rows affected."""
        if not qrs or not os.path.exists(self.db_path):
            return 0
        try:
            with closing(qrdb.get_connection(sqlite_path=self.db_path, timeout=10.0)) as conn:
                with closing(conn.cursor()) as cur:
                    cols = {n.lower(): n for n in qrdb.table_columns(conn, Config.AI_STATUS_TABLE)}  # backend-agnostic
                    qr_col = cols.get("qr_code_id") or cols.get("qr code") or cols.get("qr")
                    ai_col = cols.get("ai_status") or cols.get("aistatus")
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
        except Exception as e:
            logging.error(f"Failed bulk ai_status update to {value}: {e}")
            return 0

    def _update_ai_status(self, qr: str, final_path: str = "") -> None:
        if not qr or not os.path.exists(self.db_path):
            return
        # Rule: do not set ai_status=1 unless a matching EL JSON exists.
        if final_path:
            if not os.path.exists(final_path):
                logging.warning(f"[{qr}] ai_status update blocked: JSON path missing: {final_path}")
                return
        else:
            if not self._find_existing_json_for_qr(qr, "EL"):
                logging.warning(f"[{qr}] ai_status update blocked: no EL JSON exists on disk.")
                return
        try:
            with closing(qrdb.get_connection(sqlite_path=self.db_path, timeout=10.0)) as conn:
                with closing(conn.cursor()) as cur:
                    cols = {n.lower(): n for n in qrdb.table_columns(conn, Config.AI_STATUS_TABLE)}  # backend-agnostic
                    qr_col = cols.get("qr_code_id") or cols.get("qr code") or cols.get("qr")
                    ai_col = cols.get("ai_status") or cols.get("aistatus")
                    if qr_col and ai_col:
                        if not qrdb.is_postgres():
                            cur.execute('BEGIN IMMEDIATE')  # SQLite-only
                        cur.execute(f'UPDATE "{Config.AI_STATUS_TABLE}" SET "{ai_col}" = \'1\' WHERE "{qr_col}" = ?', (qr,))
                        conn.commit()
        except Exception as e:
            logging.error(f"Failed to update ai_status for {qr}: {e}. Rolling back file creation.")
            if final_path and os.path.exists(final_path):
                os.remove(final_path)

    def discover_assets(self) -> Dict[str, Dict[str, Any]]:
        grouped = defaultdict(lambda: {"images": {}, "building": "", "asset_type": "EL"})
        if not os.path.exists(Config.IMAGE_FOLDER): return grouped

        for fn in os.listdir(Config.IMAGE_FOLDER):
            if not any(fn.lower().endswith(ext) for ext in Config.VALID_EXTS): continue
            
            m = Config.FILENAME_PATTERN.match(os.path.splitext(fn)[0])
            if not m: continue
            
            qr, building, atype, seq = m.groups()
            if seq not in Config.VALID_SUFFIXES or atype.upper() != "EL": continue
            if self.qr_filter and qr != self.qr_filter: continue
            if not self.qr_filter and (qr in self.qrs_to_ignore or qr in self.ai_processed_qrs): continue
            
            grouped[qr]["building"] = building
            grouped[qr]["images"][seq] = os.path.join(Config.IMAGE_FOLDER, fn)
            
        logging.info(f"Discovered {len(grouped)} new EL assets.")
        return grouped

    def _preprocess_for_ocr(self, image_data: np.ndarray) -> np.ndarray:
        """Applies filters to improve OCR accuracy."""
        if len(image_data.shape) == 3:
            gray = cv2.cvtColor(image_data, cv2.COLOR_BGR2GRAY)
        else:
            gray = image_data
        
        gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        blur = cv2.GaussianBlur(enhanced, (5, 5), 0)
        _, bw_img = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return bw_img

    def _extract_ocr_context(self, images: List[Dict]) -> str:
        """Runs Tesseract on specific images (Tag, Plate) to provide context hints."""
        if not cv2 or Config.OCR_MODE == "off":
            return ""

        context_lines = []
        
        for img_info in images:
            seq = img_info["seq"]
            # Include Tag (1), Asset Plate (0), and Panel Schedule header (2).
            if seq not in ["0", "1", "2"]: continue
            
            try:
                img = cv2.imread(img_info["path"])
                if img is None: continue

                text, orientation = self._extract_best_ocr_text(img)
                if not text:
                    continue

                clean_lines = [line.strip() for line in text.split('\n') if len(line.strip()) > 3]
                if clean_lines:
                    label_map = {"0": "Asset Plate (EL-0)", "1": "Tag (EL-1)", "2": "Panel Schedule (EL-2)"}
                    label = label_map.get(seq, seq)
                    if orientation != "orig":
                        label = f"{label}, {orientation}"
                    # Limit EL-2 lines to header area to avoid noise from dense circuit tables.
                    max_lines = 8 if seq == "2" else 12
                    block = " | ".join(clean_lines[:max_lines]) 
                    context_lines.append(f"[OCR {label}]: {block}")
            except Exception:
                pass
        
        return "\n".join(context_lines)

    def _order_images(self, images: List[Dict], priority: List[str]) -> List[Dict]:
        rank = {seq: idx for idx, seq in enumerate(priority)}
        return sorted(images, key=lambda img: (rank.get(img["seq"], 99), img["seq"]))

    def _build_multimodal_content(self, prompt, ordered_imgs, ocr_context=""):
        content = [{"type": "text", "text": prompt}]
        if ocr_context:
             content.append({"type": "text", "text": f"\n\nOCR Reference Text (use to resolve ambiguous characters):\n{ocr_context}"})
             
        header_map = {"0": "EL-0 (Asset Plate/Label)", "1": "EL-1 (Identification Label / Tag)", "2": "EL-2 (Panel Schedule)"}
        for img in ordered_imgs:
            seq = img["seq"]
            content.append({"type": "text", "text": f"\n--- {header_map.get(seq, seq)} ---"})
            with open(img["path"], "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        return content

    @staticmethod
    def _model_supports_reasoning_effort(model_name: str) -> bool:
        normalized = (model_name or "").strip().lower()
        return normalized.startswith("gpt-5") or normalized.startswith("o")

    def _reasoning_effort_for_model(self, model_name: str, *, hard: bool = False) -> Optional[str]:
        if not self._model_supports_reasoning_effort(model_name):
            return None
        effort = Config.HARD_REASONING_EFFORT if hard else Config.NORMAL_REASONING_EFFORT
        return None if effort == "none" else effort

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
        minimum = 1200 if targeted else 3000
        if hard:
            minimum = 1600 if targeted else 4000
        return max(base, minimum)

    def _missing_scoring_field_count(self, structured_data: Dict[str, Any]) -> int:
        normalized = _normalize_el_structured_fields(structured_data)
        score_fields = _el_scoring_fields_for_tag(normalized.get("UBC Asset Tag", ""))
        return sum(1 for field in score_fields if not str(normalized.get(field, "") or "").strip())

    def _should_fallback_to_heavier_model(
        self,
        score: float,
        structured_data: Optional[Dict[str, Any]] = None,
        confidence_scores: Optional[Dict[str, Any]] = None,
    ) -> bool:
        structured = structured_data if isinstance(structured_data, dict) else {}
        missing_count = self._missing_scoring_field_count(structured)
        if score < Config.FALLBACK_MIN_SCORE or missing_count > Config.FALLBACK_MAX_MISSING:
            return True

        projected_conf = _project_el_confidence_scores(structured, confidence_scores or {})
        for field, conf in projected_conf.items():
            if str(structured.get(field, "") or "").strip() and conf < Config.MANUAL_REVIEW_MIN_CONFIDENCE:
                return True

        for field in Config.STRUCTURED_FIELDS:
            if "?" in str(structured.get(field, "")):
                return True
        return False

    def _build_manual_review_metadata(
        self,
        structured_data: Dict[str, Any],
        confidence_scores: Dict[str, Any],
        score: float,
        *,
        ocr_assisted_retry: bool,
    ) -> Dict[str, Any]:
        structured = structured_data if isinstance(structured_data, dict) else {}
        projected_conf = _project_el_confidence_scores(structured, confidence_scores or {})
        reason_codes: List[str] = []

        if score < Config.MANUAL_REVIEW_MIN_SCORE:
            reason_codes.append("low_completeness")

        normalized = _normalize_el_structured_fields(structured)
        for field in _el_scoring_fields_for_tag(normalized.get("UBC Asset Tag", "")):
            if not str(normalized.get(field, "") or "").strip():
                reason_codes.append("missing_" + re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_"))
                continue
            if projected_conf.get(field, 0) < Config.MANUAL_REVIEW_MIN_CONFIDENCE:
                reason_codes.append("low_confidence_" + re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_"))

        for field in Config.STRUCTURED_FIELDS:
            if "?" in str(structured.get(field, "")):
                reason_codes.append("uncertain_character_" + re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_"))

        reason_codes = sorted(set(reason_codes))
        return {
            "flag_for_review": 1 if reason_codes else 0,
            "reason_codes": reason_codes,
            "ocr_assisted_retry": 1 if ocr_assisted_retry else 0,
            "thresholds": {
                "min_completeness_score": Config.MANUAL_REVIEW_MIN_SCORE,
                "min_critical_confidence": Config.MANUAL_REVIEW_MIN_CONFIDENCE,
            },
        }

    def _reread_location_from_el2(
        self,
        qr: str,
        images: Dict[str, str],
        branch_panel: str = "",
        ubc_tag: str = "",
    ) -> Tuple[str, int]:
        """
        Enforce Location sourcing from EL-2 only.
        Returns a room/area string only when the seq-2 image explicitly shows one.
        """
        seq2_path = str(images.get("2", "") or "").strip()
        if not seq2_path or not os.path.exists(seq2_path):
            return "", 0

        try:
            with open(seq2_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logging.warning(f"[{qr}] EL-2 location reread skipped (cannot read seq2): {e}")
            return "", 0

        prompt = """
Read only this EL-2 panel schedule image and extract the panel Location.

Rules:
- EL-2 is a spreadsheet-like panel schedule. Read the header area of the page and look only for labeled fields.
- Location means room/area text for the panel itself.
- Only extract a Location when the header shows a label such as `Location`, `Room`, `Space`, `Area`, `Level`, or `Floor`.
- Return the exact visible label-and-value snippet in `Location Source Text`, such as `Location: Mechanical Room`, `Room: Electrical Room`, or `Space: 1101 Corridor South`.
- Use only text that is explicitly visible on this EL-2 image.
- Do not use any data from EL-0 or EL-1.
- Do not infer location from circuit descriptions, feeder notes, panel name, source voltage, breaker sizes, or branch loads.
- If no labeled room/area header field is visible on EL-2, return an empty Location, empty `Location Source Text`, and Location Confidence = 0.
"""

        content = [
            {"type": "text", "text": prompt},
            {"type": "text", "text": "\n--- EL-2 (Panel Schedule Only) ---"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]

        # Use the primary model only for the EL-2 location reread — this is a narrow,
        # cheap secondary query. We do not escalate this side-call to fallback/premium models.
        for n, model_name in enumerate(get_llm_model_plan(Config), start=1):
            try:
                kwargs = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "response_format": ELLocationScheduleExtraction,
                    "max_completion_tokens": self._max_completion_tokens_for_model(
                        180,
                        model_name,
                        hard=n > 1,
                        targeted=True,
                    ),
                }
                effort = self._reasoning_effort_for_model(model_name, hard=n > 1)
                if effort:
                    kwargs["reasoning_effort"] = effort

                completion = self.client.beta.chat.completions.parse(**kwargs)
                parsed = completion.choices[0].message.parsed
                if not parsed:
                    continue

                payload = parsed.model_dump(by_alias=True)
                location = re.sub(r"\s+", " ", str(payload.get("Location", "") or "").strip())
                source_text = re.sub(r"\s+", " ", str(payload.get("Location Source Text", "") or "").strip())
                confidence = 0
                try:
                    confidence = max(0, min(100, int(payload.get("Location Confidence", 0) or 0)))
                except (TypeError, ValueError):
                    confidence = 0

                location_upper = location.upper()
                if (
                    not location
                    or not source_text
                    or _looks_like_qr_identifier(location, qr)
                    or location_upper.startswith("PANEL BOARD")
                    or location_upper.startswith("PANELBOARD")
                    or location_upper.startswith("SOURCE")
                    or not _location_has_cue_prefix(location, source_text)
                    or _looks_like_panel_identifier_fragment(location, branch_panel)
                    or _looks_like_panel_identifier_fragment(location, ubc_tag)
                ):
                    continue

                return location, confidence if confidence > 0 else 80
            except Exception as e:
                # Reread is best-effort: bail on quota/auth so we don't burn calls,
                # but otherwise just log and try the next model.
                if is_quota_error(e) or is_auth_error(e):
                    logging.warning(f"[{qr}] EL-2 location reread halted on {model_name}: {e}")
                    break
                logging.warning(f"[{qr}] EL-2 location reread failed on {model_name}: {e}")
        return "", 0

    def _load_existing_json(self, qr: str, building: str) -> Optional[Dict[str, Any]]:
        """Loads existing JSON file to check completeness score."""
        fname = f"{qr}_EL_{building}.json"
        path = os.path.join(Config.OUTPUT_FOLDER, fname)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def process_single_asset(self, qr: str, info: Dict[str, Any]) -> str:
        """Processes one asset with retry logic."""
        images = [{"seq": k, "path": v} for k, v in info["images"].items()]
        building = info["building"]

        # Buildings.Process flow gate (2026-07-29) — must run before any
        # billing/LLM work. Standard falls through the pre-existing body
        # byte-identical; Legacy routes to the legacy extraction path; blank/
        # unknown stops this QR with a warning (no JSON, ai_status untouched).
        process = self.building_process_map.get(building, "")
        if process == "Legacy":
            return self._process_legacy_asset(qr, info)
        if process != "Standard":
            logging.warning(
                "[%s] Building %r has no 'Process' value (Standard/Legacy) — "
                "skipping. Set \"Buildings\".\"Process\" and re-run.", qr, building,
            )
            return f"- QR: {qr} | EL | SKIPPED (No Process value for building {building})"

        # Skip-if-exists guard: avoid duplicate billing for already-processed assets.
        existing_json_path = os.path.join(Config.OUTPUT_FOLDER, f"{qr}_EL_{building}.json")
        if os.path.exists(existing_json_path) and not Config.OVERWRITE_EXISTING_JSON:
            existing_payload = self._load_existing_json(qr, building)
            existing_needs_rescore = (
                True if existing_payload is None else _existing_el_output_needs_rescore(existing_payload)
            )
            if not existing_needs_rescore:
                logging.info(f"[{qr}] Final status: {STATUS_SKIPPED_EXISTS} (existing JSON found, overwrite disabled).")
                return f"- QR: {qr} | EL | SKIPPED (Existing JSON) | Building: {building}"
            logging.info(
                f"[{qr}] Existing EL JSON requires refresh under current rules; continuing despite overwrite disabled."
            )

        # Hybrid OCR Context extraction (once per asset)
        ocr_context = ""
        if Config.HYBRID_OCR_AGENT_ENABLED:
            ocr_context = self._extract_ocr_context(images)
        
        retry_profiles = [
            {"name": "baseline", "priority": ["1", "0", "2"], "tokens": 900, "effort": "medium", 
             "note": "General pass. Use EL-1 as the primary source for the printed panel identifier, but never use QR-code sticker text as the UBC Asset Tag. Use EL-0 as the primary source for technical specs such as explicit amp and power rating."},
            {"name": "plate_focus", "priority": ["0", "1", "2"], "tokens": 1100, "effort": "high",
             "note": "Deep focus on EL-0 for any explicitly printed ampere, power rating, volts, and fed-from text."},
            {"name": "tag_focus", "priority": ["1", "0", "2"], "tokens": 1100, "effort": "high",
             "note": "Deep focus on EL-1 for the printed panel/tag identifier and any power rating text next to it, but never treat the blue Asset Identification QR sticker number as the UBC Asset Tag, and do not infer amperage from transformer specs."}
        ]
        # Per-model retry budget controls how many profiles we sweep through.
        retry_profiles = retry_profiles[: Config.MAX_LLM_ATTEMPTS_PER_MODEL]

        best_score = -1.0
        best_candidate = {}
        best_confidence = {}

        plan = get_llm_model_plan(Config)
        attempted_models: List[str] = []
        stop_extraction = False

        try:
            for n, model_name in enumerate(plan, start=1):
                role = role_for_position(n)
                logging.info(f"[{qr}] LLM attempt {n}/{len(plan)} using {role} model {model_name}")
                attempted_models.append(model_name)

                # Gate: skip second/third model unless quality of best-so-far is below threshold.
                if n > 1 and not self._should_fallback_to_heavier_model(
                    best_score, best_candidate, best_confidence,
                ):
                    logging.info(
                        f"[{qr}] Fallback not needed (best_score={best_score:.0f}%, "
                        f"missing={self._missing_scoring_field_count(best_candidate)}). Accepting result."
                    )
                    break
                if n > 1:
                    logging.info(
                        "[%s] Fallback enabled. Retrying with %s (best_score=%.0f%%, missing=%s).",
                        qr, model_name, best_score, self._missing_scoring_field_count(best_candidate),
                    )

                for profile in retry_profiles:
                    try:
                        ordered = self._order_images(images, profile["priority"])
                        prompt = f"""
Extract Electrical Panel data.
Rules:
- {profile['note']}
- UBC Asset Tag: printed equipment/panel identifier (e.g., PNL-1A, 2SRM1, or 2N1.5P1).
- UBC Asset Tag: copy every printed character verbatim, including decimal points, periods, and hyphens (e.g., `2N1.5P1`, `SWBD-6N1.5D1`). Never delete, merge, or normalize decimals — `2N1.5P1` must stay `2N1.5P1`, not `2N15P1`.
- UBC Asset Tag: never use the QR code, never use the long numeric value from an `Asset Identification` sticker, and never echo the file QR identifier.
- If the image shows both a QR sticker and a printed panel label, use the printed panel label as `UBC Asset Tag`.
- Branch Panel: extract the panel identifier printed next to labels such as `PANEL`, `PNL`, `SWBD`, `MCC`, `ATS`, `CDP`, `MDP`, or `SPL` (e.g., `2SRM1`, `2N1.5P1`). Preserve decimals exactly as printed.
- Ampere: return an integer only when amperage is explicitly printed in the image with an amp unit or amp label.
- Ampere: panelboard labels commonly show amperage as `Main/Omnibus: 225A` or `Neut: 225A` — extract the integer (e.g., `225`) from `Main/Omnibus` as the Ampere value.
- Ampere: do not infer or calculate amperage from `KVA`, `KW`, voltage, transformer size, or other specs.
- Ampere: if the image only shows transformer specs like `75KVA`, `600V`, or `208Y/120V`, leave `Ampere` blank.
- Ampere Source Text: copy the exact visible text snippet that proves the amperage value, always including the amp unit (e.g., `Main/Omnibus: 225A`, `100 AMPS`). Never omit the unit from the source text. Leave blank if amperage is not explicitly printed.
- Power Rating: transformer-only field. Only populate it when the finalized `UBC Asset Tag` is a transformer tag like `TX-*`.
- Power Rating: extract only when `KVA`, `KW`, or `VA` is immediately preceded or followed by a positive whole number, such as `75 KVA`, `KVA 75`, `15 KW`, or `500 VA`.
- Power Rating: never use panel labels, warning labels, voltage strings, `VAC`, or `kVac` text as Power Rating.
- Power Rating Source Text: copy the exact visible proof snippet for the power rating, always including both the whole number and the unit. Leave blank if no explicit transformer power rating is shown.
- Power Rating: return the integer only in `Power Rating` and the normalized unit only in `Power Rating (UoM)`.
- Ignore voltage strings like `600V`, `208Y/120V`, or `600V-208Y/120V` when extracting Power Rating.
- Volts: preserve panel voltages like `208/120V` and transformer voltages like `600V-208Y/120V`.
- Fed/Fed From: return only the upstream equipment identifier, not the full descriptive phrase.
- Fed/Fed From: for labels like `FED FROM MDC IN MAIN ELEC. RM`, return `MDC`.
- Fed/Fed From: for labels like `FED FROM PANEL 2N0D1`, return `2N0D1`.
- Fed/Fed From: if the identifier includes a real equipment prefix, keep it (e.g., `TX-N0N1`, `CDP-2N1`).
- Fed/Fed From: preserve decimals exactly as printed — `FED FROM PANEL 2N1.5P1` must return `2N1.5P1`, not `2N15P1`.
- Location: Room/Area from EL-2 only.
- Location: if EL-2 does not explicitly show room/area text for the panel, leave `Location` blank.
- Location: ignore location-like text from EL-0 and EL-1.
"""
                        content = self._build_multimodal_content(prompt, ordered, ocr_context)

                        kwargs = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": content}],
                            "response_format": ELStructuredExtraction,
                            "max_completion_tokens": self._max_completion_tokens_for_model(
                                profile["tokens"],
                                model_name,
                                hard=n > 1,
                            ),
                        }
                        effort = self._reasoning_effort_for_model(model_name, hard=n > 1)
                        if effort:
                            kwargs["reasoning_effort"] = effort

                        completion = self.client.beta.chat.completions.parse(**kwargs)
                        parsed = completion.choices[0].message.parsed
                        if not parsed:
                            continue

                        parsed_payload = parsed.model_dump(by_alias=True)
                        candidate = _normalize_el_structured_fields(parsed_payload)
                        raw_ampere = candidate.get("Ampere", "")
                        candidate["Ampere"] = normalize_explicit_ampere(
                            raw_ampere,
                            parsed_payload.get("Ampere Source Text", ""),
                            ocr_context,
                        )
                        resolved_tag, resolved_branch = _resolve_el_asset_tag(
                            candidate.get("UBC Asset Tag", ""),
                            candidate.get("Branch Panel", ""),
                            qr,
                            ocr_context,
                        )
                        candidate["UBC Asset Tag"] = resolved_tag
                        if resolved_branch:
                            candidate["Branch Panel"] = resolved_branch
                        power_rating, power_rating_uom = _normalize_el_power_rating_fields(
                            candidate.get("UBC Asset Tag", ""),
                            candidate.get("Power Rating", ""),
                            candidate.get("Power Rating (UoM)", ""),
                            parsed_payload.get("Power Rating Source Text", ""),
                            ocr_context,
                        )
                        candidate["Power Rating"] = power_rating
                        candidate["Power Rating (UoM)"] = power_rating_uom

                        # Confidence-aware scoring
                        raw_conf = parsed_payload.get("Confidence Scores", {})
                        conf = _normalize_el_confidence_scores(raw_conf)
                        if not candidate.get("Ampere"):
                            conf["Ampere"] = 0
                        if not candidate.get("Power Rating") or not candidate.get("Power Rating (UoM)"):
                            conf["Power Rating"] = 0
                            conf["Power Rating (UoM)"] = 0
                        if not candidate.get("UBC Asset Tag"):
                            conf["UBC Asset Tag"] = 0
                        score = _el_completeness_score(candidate)
                        logging.info(
                            "[%s] Extraction completed: completeness=%.0f%%, missing_fields=%s (model=%s, role=%s, profile=%s).",
                            qr,
                            score,
                            self._missing_scoring_field_count(candidate),
                            model_name,
                            role,
                            profile["name"],
                        )

                        if score > best_score:
                            best_score = score
                            best_candidate = candidate
                            best_confidence = conf if isinstance(conf, dict) else {}

                        if not self._should_fallback_to_heavier_model(score, candidate, conf):
                            stop_extraction = True
                            break
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
                        logging.warning(f"[{qr}] EL attempt failed on model '{model_name}': {e}")
                        time.sleep(Config.API_RETRY_DELAY)
                if stop_extraction:
                    break
        except QuotaExceeded:
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="EL",
                image_paths=[img["path"] for img in images],
                failure_reason="llm_quota_exceeded",
                missing_fields=[
                    f for f in Config.STRUCTURED_FIELDS
                    if not str(best_candidate.get(f, "") or "").strip()
                ],
                attempted_models=attempted_models, status=STATUS_QUOTA,
            )
            logging.info(f"[{qr}] Final status: {STATUS_QUOTA}")
            return f"- QR: {qr} | EL | QUOTA EXCEEDED | Building: {building}"
        except AuthFailed:
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="EL",
                image_paths=[img["path"] for img in images],
                failure_reason="llm_auth_failed",
                missing_fields=[
                    f for f in Config.STRUCTURED_FIELDS
                    if not str(best_candidate.get(f, "") or "").strip()
                ],
                attempted_models=attempted_models, status=STATUS_AUTH,
            )
            logging.info(f"[{qr}] Final status: {STATUS_AUTH}")
            return f"- QR: {qr} | EL | AUTH FAILED | Building: {building}"

        final_data = best_candidate
        final_confidence = best_confidence if isinstance(best_confidence, dict) else {}
        if not final_data: # Fallback empty
             final_data = {k: "" for k in Config.STRUCTURED_FIELDS}
             final_confidence = {}

        # Post-Process Tag (PNL formatting)
        raw_tag, branch = _resolve_el_asset_tag(
            final_data.get("UBC Asset Tag", ""),
            final_data.get("Branch Panel", ""),
            qr,
            ocr_context,
        )
        if branch:
            final_data["Branch Panel"] = branch
            
        final_tag = _apply_tag_formatting(raw_tag)
        final_data["UBC Asset Tag"] = final_tag
        supply_from_raw = (final_data.get("Supply From") or "").strip()
        if supply_from_raw:
            final_data["Supply From"] = normalize_el_supply_from_tag(supply_from_raw)
        # Resolve the description prefix from the mechanical dictionary so CDP-/TX-/ATS-
        # tags get the correct family label ('Distribution', 'Transformer', 'Transfer
        # Switch') instead of the legacy hardcoded 'Panel'. Falls back to 'Panel' when
        # the dictionary is unavailable or the tag has no matching entry.
        _desc_prefix = _el_description_prefix_from_tag(final_tag)
        final_data["Description"] = f"{_desc_prefix} - {final_tag}" if final_tag else f"{_desc_prefix} - "
        if not final_tag:
            final_confidence["UBC Asset Tag"] = 0
        if _classify_el_asset_group(final_tag) != Config.EL_TRANSFORMER_ASSET_GROUP:
            final_data["Power Rating"] = ""
            final_data["Power Rating (UoM)"] = ""
            final_confidence["Power Rating"] = 0
            final_confidence["Power Rating (UoM)"] = 0
        elif not final_data.get("Power Rating") or not final_data.get("Power Rating (UoM)"):
            final_data["Power Rating"] = ""
            final_data["Power Rating (UoM)"] = ""
            final_confidence["Power Rating"] = 0
            final_confidence["Power Rating (UoM)"] = 0

        dictionary_volts = _derive_dictionary_panel_volts(final_tag)
        if dictionary_volts:
            final_data["Volts"] = dictionary_volts
            final_confidence["Volts"] = 100

        location_from_el2, location_confidence = self._reread_location_from_el2(
            qr,
            info.get("images", {}),
            final_data.get("Branch Panel", ""),
            final_tag,
        )
        final_data["Location"] = location_from_el2
        final_confidence["Location"] = location_confidence if location_from_el2 else 0
        
        # Keep confidence aligned with the saved amperage value.
        if not str(final_data.get("Ampere", "") or "").strip():
            final_confidence["Ampere"] = 0
        
        # Calculate Score
        score = _el_completeness_score(final_data)
        
        # Completeness Guard: Check existing
        existing = self._load_existing_json(qr, building)
        if existing:
            existing_struct = existing.get("structured_data", {}) if isinstance(existing, dict) else {}
            existing_needs_rescore = _existing_el_output_needs_rescore(existing)
            if isinstance(existing_struct, dict) and existing_struct:
                existing_score = _el_completeness_score(existing_struct)
            else:
                existing_score = existing.get("completeness_score", 0)
            existing_avg_conf = existing.get("Avg_ai_conf")
            existing_conf_scores = existing.get("confidence_scores")
            existing_has_conf = (
                existing_avg_conf not in (None, "")
                or (isinstance(existing_conf_scores, dict) and len(existing_conf_scores) > 0)
            )
            same_payload = isinstance(existing_struct, dict) and existing_struct == final_data
            if existing_score > score and not existing_needs_rescore and not (same_payload and not existing_has_conf):
                logging.info(f"[{qr}] Existing completeness ({existing_score}%) > New ({score}%). Skipping save.")
                self._update_ai_status(qr)
                return f"- QR: {qr} | EL | SKIPPED (Existing Better) | Building: {building}"
            if existing_score == score and existing_has_conf and not existing_needs_rescore:
                logging.info(f"[{qr}] Existing completeness ({existing_score}%) >= New ({score}%). Skipping save.")
                self._update_ai_status(qr)
                return f"- QR: {qr} | EL | SKIPPED (Existing Better) | Building: {building}"
        
        conf_scores = _project_el_confidence_scores(final_data, final_confidence)
        avg_conf = _avg_ai_conf_from_scores(conf_scores)
        manual_review = self._build_manual_review_metadata(
            final_data,
            conf_scores,
            score,
            ocr_assisted_retry=bool(ocr_context),
        )
        flagged_for_review = manual_review.get("flag_for_review") == 1
        if flagged_for_review:
            final_data["Flagged"] = "true"
            final_data["Approved"] = ""
            logging.warning(
                "[%s] Marked EL extraction for manual review: %s",
                qr,
                ", ".join(manual_review.get("reason_codes", [])),
            )
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="EL",
                image_paths=[img["path"] for img in images],
                failure_reason=",".join(manual_review.get("reason_codes", [])) or "low_quality_extraction",
                missing_fields=[
                    f for f in Config.STRUCTURED_FIELDS
                    if not str(final_data.get(f, "") or "").strip()
                ],
                attempted_models=attempted_models,
                status=STATUS_LOW_QUALITY,
            )
        
        output = {
            "qr_code": qr,
            "building_number": building,
            "asset_type": "- EL",
            "el_extraction_rule_version": Config.EXTRACTION_RULE_VERSION,
            "structured_data": final_data,
            "completeness_score": score,
            "confidence_scores": conf_scores,
            "Avg_ai_conf": avg_conf,
            "manual_review": manual_review,
        }
        
        fname = f"{qr}_EL_{building}.json"
        
        # 1. Atomic File Write via Temp File
        fd, temp_path = tempfile.mkstemp(dir=Config.OUTPUT_FOLDER, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())  # Force OS buffers to write to the physical disk layer
                
            # 2. Validation: Verify non-empty
            if os.path.getsize(temp_path) == 0:
                raise ValueError(f"Generated JSON is 0 bytes for QR: {qr}")
                
            # 3. Atomic Rename
            final_path = os.path.join(Config.OUTPUT_FOLDER, fname)
            os.replace(temp_path, final_path)
            
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logging.error(f"Failed to write JSON for {qr}: {e}")
            return f"- QR: {qr} | EL | ERROR (File Save Failed) | Building: {building}"
            
        self._update_ai_status(qr, final_path)
        conf_scores = output.get("confidence_scores", {})
        avg_conf = _avg_ai_conf_from_scores(conf_scores)
        final_status = STATUS_LOW_QUALITY if flagged_for_review else STATUS_SUCCESS
        logging.info(f"Successfully processed and saved asset QR: {qr} (Completeness: {score:.0f}% | Avg Conf: {avg_conf:.0f}%)")
        logging.info(f"[{qr}] Final status: {final_status}")
        return f"- QR: {qr} | EL | {time.strftime('%Y-%m-%d %H:%M:%S')} | Building: {building} | Avg Conf: {avg_conf:.0f}%"

    def _process_legacy_asset(self, qr: str, info: Dict[str, Any]) -> str:
        """Legacy-building extraction (Buildings.Process = 'Legacy').

        Same model plan and retry conventions as the standard path, but with a
        verbatim-transcription prompt, a raw-preserving response model, and
        post-processing through el_legacy_flow.legacy_structured_from_raw.
        Never runs the standard-flow tag-formatting step (no PNL- fabrication).
        """
        building = info["building"]
        # Review round-1 Minor-2: also fail before burning a paid LLM call when
        # a version-skewed legacy_flow.py loaded but doesn't expose the function
        # this method needs — not just when the import itself failed outright.
        if el_legacy_flow is None or not hasattr(el_legacy_flow, "legacy_structured_from_raw"):
            if el_legacy_flow is None:
                reason = _EL_LEGACY_FLOW_IMPORT_ERROR or "unknown import failure"
            else:
                reason = "loaded module has no legacy_structured_from_raw (version skew)"
            logging.error(
                "[%s] legacy_flow module unavailable (%s) — cannot run Legacy "
                "extraction for building %s. Skipping.", qr, reason, building,
            )
            return f"- QR: {qr} | EL | SKIPPED (legacy_flow unavailable) | Building: {building}"

        images = [{"seq": k, "path": v} for k, v in info["images"].items()]

        existing_json_path = os.path.join(Config.OUTPUT_FOLDER, f"{qr}_EL_{building}.json")
        existing_payload = self._load_existing_json(qr, building) if os.path.exists(existing_json_path) else None

        # Review round-1 Important-2: a human-reviewed (or field-level manually
        # overridden) legacy JSON must never be silently clobbered by a re-run,
        # even when EL_OVERWRITE_EXISTING_JSON bypasses the skip-if-exists check
        # below (CLAUDE.md High-Risk Invariant 6 — never erase human overrides).
        # Checked before any billing work so a protected QR never burns a paid
        # LLM call either.
        if existing_payload is not None:
            existing_structured = existing_payload.get("structured_data")
            if not isinstance(existing_structured, dict):
                existing_structured = {}
            is_human_protected = (
                existing_payload.get("modified") is True
                # An APPROVED legacy JSON is reviewer-owned even when nothing
                # was edited afterwards. The review app already refuses to
                # reprocess it (_reprocess_json_protected, Asset_dashboard_EL.py
                # :4622), but the extractor did not -- so a command-line rerun
                # with EL_OVERWRITE_EXISTING_JSON=true could overwrite approved
                # work. Same predicate as the review app, deliberately.
                or str(existing_structured.get("Approved") or "").strip() == "True"
                or str(existing_structured.get("supply_from_manual_override") or "").strip() == "1"
                or str(existing_structured.get("volts_manual_override") or "").strip() == "1"
            )
            if is_human_protected:
                logging.info(
                    "[%s] Existing legacy JSON is human-reviewed/manually overridden — "
                    "refusing to overwrite regardless of EL_OVERWRITE_EXISTING_JSON.", qr,
                )
                return f"- QR: {qr} | EL | SKIPPED (Human Reviewed) | Building: {building}"

        if os.path.exists(existing_json_path) and not Config.OVERWRITE_EXISTING_JSON:
            # Review round-1 Important-1: route legacy payloads to the
            # legacy-appropriate staleness check (_existing_el_legacy_output_needs_rescore)
            # instead of the standard one, which recomputes completeness/confidence
            # with standard scoring rules and would flag every legacy JSON stale
            # forever — see that function's docstring for the full explanation.
            # A payload without "process": "Legacy" predates this feature and
            # still gets one standard-path rescore to pick up legacy behavior.
            if existing_payload is None:
                existing_needs_rescore = True
            elif existing_payload.get("process") == "Legacy":
                existing_needs_rescore = _existing_el_legacy_output_needs_rescore(existing_payload)
            else:
                existing_needs_rescore = _existing_el_output_needs_rescore(existing_payload)
            if not existing_needs_rescore:
                logging.info(f"[{qr}] Final status: {STATUS_SKIPPED_EXISTS} (existing JSON found, overwrite disabled).")
                return f"- QR: {qr} | EL | SKIPPED (Existing JSON) | Building: {building}"
            logging.info(
                f"[{qr}] Existing EL JSON requires refresh under current rules; continuing despite overwrite disabled."
            )

        ocr_context = ""
        if Config.HYBRID_OCR_AGENT_ENABLED:
            ocr_context = self._extract_ocr_context(images)

        best_raw = None
        best_conf: Dict[str, Any] = {}
        best_score = -1.0
        plan = get_llm_model_plan(Config)
        attempted_models: List[str] = []
        for n, model_name in enumerate(plan, start=1):
            role = role_for_position(n)
            if best_raw is not None and best_score >= Config.FALLBACK_MIN_SCORE:
                break
            attempted_models.append(model_name)
            logging.info(f"[{qr}] LEGACY LLM attempt {n}/{len(plan)} using {role} model {model_name}")
            try:
                ordered = self._order_images(images, ["1", "0", "2"])
                content = self._build_multimodal_content(EL_LEGACY_PROMPT, ordered, ocr_context)
                kwargs = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "response_format": ELLegacyStructuredExtraction,
                    "max_completion_tokens": self._max_completion_tokens_for_model(1100, model_name, hard=n > 1),
                }
                effort = self._reasoning_effort_for_model(model_name, hard=n > 1)
                if effort:
                    kwargs["reasoning_effort"] = effort
                completion = self.client.beta.chat.completions.parse(**kwargs)
                parsed = completion.choices[0].message.parsed
                if not parsed:
                    continue
                raw_payload = parsed.model_dump(by_alias=True)
                conf = raw_payload.pop("Confidence Scores", {}) or {}
                candidate = el_legacy_flow.legacy_structured_from_raw(raw_payload)
                score = completeness_score(candidate, list(_el_legacy_scoring_fields(candidate)))
                logging.info(
                    "[%s] Legacy extraction completed: completeness=%.0f%% (model=%s, role=%s).",
                    qr, score, model_name, role,
                )
                if score > best_score:
                    best_raw, best_conf, best_score = candidate, conf, score
            except Exception as exc:
                # Quota/auth failures are billing-relevant and would fail identically
                # on every remaining model — stop the sweep instead of burning
                # further attempts (mirrors _reread_location_from_el2's guard).
                if is_quota_error(exc) or is_auth_error(exc):
                    logging.error(f"[{qr}] Legacy extraction halted on {model_name}: {exc}")
                    break
                logging.warning(f"[{qr}] Legacy extraction attempt with {model_name} failed: {exc}")
                # Review round-1 Minor-3: back off between attempts, matching the
                # standard path's behavior (useful once fallback models are enabled).
                time.sleep(Config.API_RETRY_DELAY)
                continue

        if best_raw is None:
            logging.error(f"[{qr}] All legacy extraction attempts failed.")
            return f"- QR: {qr} | EL | ERROR (Legacy extraction failed) | Building: {building}"

        final_data = best_raw
        scoring_fields = _el_legacy_scoring_fields(final_data)
        conf_scores = _el_legacy_conf_scores(final_data, best_conf, scoring_fields)
        score = completeness_score(final_data, list(scoring_fields))
        avg_conf = _avg_ai_conf_from_scores(conf_scores)

        reason_codes = []
        if score < Config.MANUAL_REVIEW_MIN_SCORE:
            reason_codes.append("low_completeness")
        for f in scoring_fields:
            if str(final_data.get(f, "") or "").strip() and conf_scores[f] < Config.MANUAL_REVIEW_MIN_CONFIDENCE:
                reason_codes.append("low_confidence_" + re.sub(r"[^a-z0-9]+", "_", f.lower()).strip("_"))
        flagged_for_review = bool(reason_codes)
        manual_review = {
            "flag_for_review": 1 if flagged_for_review else 0,
            "reason_codes": reason_codes,
            "ocr_assisted_retry": 1 if ocr_context else 0,
            "thresholds": {
                "min_completeness_score": Config.MANUAL_REVIEW_MIN_SCORE,
                "min_critical_confidence": Config.MANUAL_REVIEW_MIN_CONFIDENCE,
            },
        }
        if flagged_for_review:
            final_data["Flagged"] = "true"
            final_data["Approved"] = ""
            logging.warning(
                "[%s] Marked LEGACY EL extraction for manual review: %s",
                qr, ", ".join(reason_codes),
            )
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="EL",
                image_paths=[img["path"] for img in images],
                failure_reason=",".join(reason_codes) or "low_quality_extraction",
                missing_fields=[
                    f for f in scoring_fields
                    if not str(final_data.get(f, "") or "").strip()
                ],
                attempted_models=attempted_models,
                status=STATUS_LOW_QUALITY,
            )

        output = {
            "qr_code": qr,
            "building_number": building,
            "asset_type": "- EL",
            "process": "Legacy",
            "el_extraction_rule_version": Config.EXTRACTION_RULE_VERSION,
            "el_legacy_rule_version": EL_LEGACY_RULE_VERSION,
            "structured_data": final_data,
            "completeness_score": score,
            "confidence_scores": conf_scores,
            "Avg_ai_conf": avg_conf,
            "manual_review": manual_review,
        }

        fname = f"{qr}_EL_{building}.json"
        fd, temp_path = tempfile.mkstemp(dir=Config.OUTPUT_FOLDER, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            if os.path.getsize(temp_path) == 0:
                raise ValueError(f"Generated JSON is 0 bytes for QR: {qr}")
            final_path = os.path.join(Config.OUTPUT_FOLDER, fname)
            os.replace(temp_path, final_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logging.error(f"Failed to write JSON for {qr}: {e}")
            return f"- QR: {qr} | EL | ERROR (File Save Failed) | Building: {building}"

        self._update_ai_status(qr, final_path)
        final_status = STATUS_LOW_QUALITY if flagged_for_review else STATUS_SUCCESS
        logging.info(f"Successfully processed LEGACY asset QR: {qr} (Completeness: {score:.0f}% | Avg Conf: {avg_conf:.0f}%)")
        logging.info(f"[{qr}] Final status: {final_status}")
        return f"- QR: {qr} | EL | {time.strftime('%Y-%m-%d %H:%M:%S')} | Building: {building} | Avg Conf: {avg_conf:.0f}% | LEGACY"

    def run(self):
        assets = self.discover_assets()
        if not assets:
            msg = "No EL assets found for AI processing. Skipping EL run."
            logging.info(msg)
            print(msg)
            return

        print(f"Starting EL processing ({len(assets)} assets)...")
        summary = []
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as ex:
            futures = {ex.submit(self.process_single_asset, qr, info): qr for qr, info in assets.items()}
            for f in as_completed(futures):
                try:
                    if res := f.result(): 
                        summary.append(res)
                except Exception as e:
                    logging.error(f"Error: {e}")

        print("\n--- SUMMARY ---\nSuccessfully saved: " + str(len([s for s in summary if "SKIPPED" not in s])))
        for s in sorted(summary): print(s)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qr", dest="qr_filter")
    parser.add_argument("--db", dest="db_path")
    parser.add_argument("--images-dir", dest="images_dir")
    parser.add_argument("--output-dir", dest="output_dir")
    parser.add_argument("--env", dest="dotenv_path")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # CLI Overrides
    if args.db_path: Config.DB_PATH = args.db_path
    if args.images_dir: Config.IMAGE_FOLDER = args.images_dir
    if args.output_dir: Config.OUTPUT_FOLDER = args.output_dir
    if args.dotenv_path: Config.ENV_PATH = args.dotenv_path
    
    setup_environment()
    processor = AssetProcessor(debug=args.debug, qr_filter=args.qr_filter)
    processor.run()

if __name__ == "__main__":
    main()
