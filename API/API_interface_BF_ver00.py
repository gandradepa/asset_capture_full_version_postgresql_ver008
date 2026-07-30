#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Refactored BF (Backflow) extraction script.
Standardized to match 'ME' architecture: OOP, ThreadPoolExecutor, Config class, and Shared Validators.
Now includes Hybrid OCR / VLM Strategy AND Completeness Guard.

Changes:
- Class-based `AssetProcessor` structure.
- Concurrent processing (ThreadPoolExecutor).
- Integration with `validators_shared.py`.
- Restricts processing to image sequences "0" and "1" (excludes "3").
- Hybrid OCR: Preprocesses images, extracts text via Tesseract, and feeds it as context to LLM.
- Completeness Guard: Prevents overwriting existing better data.
"""

import os
import re
import json
import time
import base64
import tempfile
import logging
import platform
import shutil
import sqlite3
from collections import defaultdict
from contextlib import closing
import db as qrdb  # backend-agnostic QR_codes DB layer (Phase C / C4)
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Shared validators
try:
    from validators_shared import (
        normalize_manufacturer,
        normalize_model,
        normalize_serial,
        normalize_diameter,
        completeness_score,
        looks_like_date_misread_serial,
    )
except ImportError:
    logging.warning("validators_shared module not found. Using local fallbacks.")
    def normalize_manufacturer(v): return v.strip()
    def normalize_model(v): return v.strip()
    def normalize_serial(v): return v.strip()
    def normalize_diameter(v): return v.strip()
    def completeness_score(d, f): return 0.0
    def looks_like_date_misread_serial(serial, year_hint=""): return False


# --- [1] Configuration ---
class Config:
    """Centralized configuration for BF asset processing."""
    # --- Paths ---
    ROOT_DEV_PATH = os.getenv("DEV_PATH", "/home/developer")
    # Windows fallback logic for paths usually handled by OS check, but keeping default str simple
    IMAGE_FOLDER = os.path.join(ROOT_DEV_PATH, "Capture_photos_upload")
    OUTPUT_FOLDER = os.path.join(ROOT_DEV_PATH, "Output_jason_api")
    DB_PATH = os.path.join(ROOT_DEV_PATH, "asset_capture_app_dev/data/QR_codes.db")
    ENV_PATH = os.path.join(ROOT_DEV_PATH, "API/OpenAI_key_giba.env")

    # --- Database ---
    DB_TABLE = "sdi_dataset"
    AI_STATUS_TABLE = "QR_codes"
    
    # --- File Matching ---
    # RESTRICTION: Only 0 and 1 sequences for BF
    VALID_SUFFIXES = {"0", "1"}
    VALID_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    
    # Regex: QR - Building - BF - Sequence
    # Regex accepts 0-3 so discovery can log non-LLM seqs (2=Main, 3=Extra) as invalid_seq
    # rather than name_mismatch. VALID_SUFFIXES above is the actual gate.
    FILENAME_PATTERN = re.compile(
        r"^([T]?\d+)\s+"       # QR
        r"(\d+(?:-\d+)?)\s+"   # Building
        r"(BF)\s*-\s*([0-3])$", # Sequence (0-3; only 0,1 fed to LLM via VALID_SUFFIXES)
        re.IGNORECASE
    )

    # --- Fields ---
    COMPLETENESS_FIELDS = ["Manufacturer", "Model", "Serial Number", "Diameter"]

    # --- OpenAI: cost-controlled model plan ---
    PRIMARY_LLM_MODEL = os.getenv("BF_PRIMARY_LLM_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini"
    FALLBACK_LLM_MODEL = os.getenv("BF_FALLBACK_LLM_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    PREMIUM_LLM_MODEL = os.getenv("BF_PREMIUM_LLM_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    ENABLE_LLM_FALLBACK = os.getenv("BF_ENABLE_LLM_FALLBACK", "false").strip().lower() == "true"
    ENABLE_PREMIUM_FALLBACK = os.getenv("BF_ENABLE_PREMIUM_FALLBACK", "false").strip().lower() == "true"
    try:
        _max_attempts_asset_env = int(os.getenv("BF_MAX_LLM_ATTEMPTS_PER_ASSET", "1"))
    except ValueError:
        _max_attempts_asset_env = 1
    MAX_LLM_ATTEMPTS_PER_ASSET = max(1, min(_max_attempts_asset_env, 3))
    try:
        _max_attempts_model_env = int(os.getenv("BF_MAX_LLM_ATTEMPTS_PER_MODEL", "1"))
    except ValueError:
        _max_attempts_model_env = 1
    MAX_LLM_ATTEMPTS_PER_MODEL = max(1, min(_max_attempts_model_env, 3))
    OVERWRITE_EXISTING_JSON = os.getenv("BF_OVERWRITE_EXISTING_JSON", "false").strip().lower() == "true"
    MANUAL_REVIEW_QUEUE_FILE = os.getenv(
        "BF_MANUAL_REVIEW_QUEUE_FILE",
        os.path.join(OUTPUT_FOLDER, "manual_review_queue_BF.jsonl"),
    )
    _REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
    NORMAL_REASONING_EFFORT = os.getenv("BF_NORMAL_REASONING_EFFORT", "low").strip().lower()
    if NORMAL_REASONING_EFFORT not in _REASONING_EFFORTS:
        NORMAL_REASONING_EFFORT = "low"
    HARD_REASONING_EFFORT = os.getenv("BF_HARD_REASONING_EFFORT", "high").strip().lower()
    if HARD_REASONING_EFFORT not in _REASONING_EFFORTS:
        HARD_REASONING_EFFORT = "high"
    try:
        _fallback_min_score_env = float(os.getenv("BF_FALLBACK_MIN_SCORE", "95"))
    except ValueError:
        _fallback_min_score_env = 95.0
    FALLBACK_MIN_SCORE = max(0.0, min(_fallback_min_score_env, 100.0))
    try:
        _fallback_max_missing_env = int(os.getenv("BF_FALLBACK_MAX_MISSING", "1"))
    except ValueError:
        _fallback_max_missing_env = 1
    FALLBACK_MAX_MISSING = max(0, min(_fallback_max_missing_env, len(COMPLETENESS_FIELDS)))
    try:
        _manual_review_min_score_env = float(os.getenv("BF_MANUAL_REVIEW_MIN_SCORE", "95"))
    except ValueError:
        _manual_review_min_score_env = 95.0
    MANUAL_REVIEW_MIN_SCORE = max(0.0, min(_manual_review_min_score_env, 100.0))
    try:
        _manual_review_min_conf_env = int(os.getenv("BF_MANUAL_REVIEW_MIN_CONFIDENCE", "70"))
    except ValueError:
        _manual_review_min_conf_env = 70
    MANUAL_REVIEW_MIN_CONFIDENCE = max(0, min(_manual_review_min_conf_env, 100))
    try:
        _retries_env = int(os.getenv("BF_API_MAX_RETRIES", "1"))
    except ValueError:
        _retries_env = 1
    API_MAX_RETRIES = max(1, min(_retries_env, 3))
    try:
        _retry_delay_env = float(os.getenv("BF_API_RETRY_DELAY", "0.5"))
    except ValueError:
        _retry_delay_env = 0.5
    API_RETRY_DELAY = max(0.0, _retry_delay_env)
    try:
        _timeout_env = float(os.getenv("BF_API_TIMEOUT", "45"))
    except ValueError:
        _timeout_env = 45.0
    API_TIMEOUT = max(10.0, _timeout_env)

    # --- Concurrency & OCR ---
    try:
        _workers_env = int(os.getenv("BF_MAX_WORKERS", "1"))
    except ValueError:
        _workers_env = 1
    MAX_WORKERS = max(1, min(_workers_env, 2))
    OCR_MODE = os.getenv("BF_OCR_MODE", "light").strip().lower()  # off, light, full
    HYBRID_OCR_AGENT_ENABLED = os.getenv("BF_HYBRID_OCR_AGENT", "0").strip().lower() not in {"0", "false", "no"}

    @classmethod
    def refresh_from_env(cls) -> None:
        cls.PRIMARY_LLM_MODEL = os.getenv("BF_PRIMARY_LLM_MODEL", cls.PRIMARY_LLM_MODEL).strip() or cls.PRIMARY_LLM_MODEL
        cls.FALLBACK_LLM_MODEL = os.getenv("BF_FALLBACK_LLM_MODEL", cls.FALLBACK_LLM_MODEL).strip() or cls.FALLBACK_LLM_MODEL
        cls.PREMIUM_LLM_MODEL = os.getenv("BF_PREMIUM_LLM_MODEL", cls.PREMIUM_LLM_MODEL).strip() or cls.PREMIUM_LLM_MODEL
        cls.ENABLE_LLM_FALLBACK = os.getenv(
            "BF_ENABLE_LLM_FALLBACK", "true" if cls.ENABLE_LLM_FALLBACK else "false"
        ).strip().lower() == "true"
        cls.ENABLE_PREMIUM_FALLBACK = os.getenv(
            "BF_ENABLE_PREMIUM_FALLBACK", "true" if cls.ENABLE_PREMIUM_FALLBACK else "false"
        ).strip().lower() == "true"
        try:
            cls.MAX_LLM_ATTEMPTS_PER_ASSET = max(
                1, min(int(os.getenv("BF_MAX_LLM_ATTEMPTS_PER_ASSET", str(cls.MAX_LLM_ATTEMPTS_PER_ASSET))), 3)
            )
        except ValueError:
            pass
        try:
            cls.MAX_LLM_ATTEMPTS_PER_MODEL = max(
                1, min(int(os.getenv("BF_MAX_LLM_ATTEMPTS_PER_MODEL", str(cls.MAX_LLM_ATTEMPTS_PER_MODEL))), 3)
            )
        except ValueError:
            pass
        cls.OVERWRITE_EXISTING_JSON = os.getenv(
            "BF_OVERWRITE_EXISTING_JSON", "true" if cls.OVERWRITE_EXISTING_JSON else "false"
        ).strip().lower() == "true"
        cls.MANUAL_REVIEW_QUEUE_FILE = os.getenv("BF_MANUAL_REVIEW_QUEUE_FILE", cls.MANUAL_REVIEW_QUEUE_FILE)

        normal_effort = os.getenv("BF_NORMAL_REASONING_EFFORT", cls.NORMAL_REASONING_EFFORT).strip().lower()
        if normal_effort in cls._REASONING_EFFORTS:
            cls.NORMAL_REASONING_EFFORT = normal_effort
        hard_effort = os.getenv("BF_HARD_REASONING_EFFORT", cls.HARD_REASONING_EFFORT).strip().lower()
        if hard_effort in cls._REASONING_EFFORTS:
            cls.HARD_REASONING_EFFORT = hard_effort
        try:
            cls.FALLBACK_MIN_SCORE = max(
                0.0,
                min(float(os.getenv("BF_FALLBACK_MIN_SCORE", str(cls.FALLBACK_MIN_SCORE))), 100.0),
            )
        except ValueError:
            pass
        try:
            cls.FALLBACK_MAX_MISSING = max(
                0,
                min(int(os.getenv("BF_FALLBACK_MAX_MISSING", str(cls.FALLBACK_MAX_MISSING))), len(cls.COMPLETENESS_FIELDS)),
            )
        except ValueError:
            pass
        try:
            cls.MANUAL_REVIEW_MIN_SCORE = max(
                0.0,
                min(float(os.getenv("BF_MANUAL_REVIEW_MIN_SCORE", str(cls.MANUAL_REVIEW_MIN_SCORE))), 100.0),
            )
        except ValueError:
            pass
        try:
            cls.MANUAL_REVIEW_MIN_CONFIDENCE = max(
                0,
                min(int(os.getenv("BF_MANUAL_REVIEW_MIN_CONFIDENCE", str(cls.MANUAL_REVIEW_MIN_CONFIDENCE))), 100),
            )
        except ValueError:
            pass
        cls.OCR_MODE = os.getenv("BF_OCR_MODE", cls.OCR_MODE).strip().lower()
        cls.HYBRID_OCR_AGENT_ENABLED = (
            os.getenv("BF_HYBRID_OCR_AGENT", "1" if cls.HYBRID_OCR_AGENT_ENABLED else "0")
            .strip()
            .lower()
            not in {"0", "false", "no"}
        )


# --- [2] Pydantic Models for Structured Output ---

class BFConfidenceScores(BaseModel):
    """Per-field confidence scores for BF extraction."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    manufacturer: int = Field(default=0, alias="Manufacturer")
    model: int = Field(default=0, alias="Model")
    serial_number: int = Field(default=0, alias="Serial Number")
    diameter: int = Field(default=0, alias="Diameter")


class BFStructuredExtraction(BaseModel):
    """Strict schema for BF asset extraction."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    manufacturer: str = Field(default="", alias="Manufacturer")
    model: str = Field(default="", alias="Model")
    serial_number: str = Field(default="", alias="Serial Number")
    diameter: str = Field(default="", alias="Diameter")
    confidence_scores: BFConfidenceScores = Field(
        default_factory=BFConfidenceScores,
        alias="Confidence Scores",
        description="Per-field confidence 0-100."
    )

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_to_string(cls, value: Any) -> Any:
        if isinstance(value, (dict, BaseModel)):
            return value
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("manufacturer")
    @classmethod
    def validate_manufacturer(cls, v: str) -> str:
        return normalize_manufacturer(v)

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        return normalize_model(v)

    @field_validator("serial_number")
    @classmethod
    def validate_serial(cls, v: str) -> str:
        return normalize_serial(v)

    @field_validator("diameter")
    @classmethod
    def validate_diameter(cls, v: str) -> str:
        return normalize_diameter(v)


# --- [3] Setup Logging & Environment ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

def setup_environment():
    """Configures API keys and Tesseract."""
    load_dotenv(dotenv_path=Config.ENV_PATH)
    Config.refresh_from_env()
    if not os.getenv("OPENAI_API_KEY"):
        logging.warning("OPENAI_API_KEY not found in env. OpenAI calls will fail.")

    # Tesseract configuration for Windows/Linux
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        resolved = next((p for p in candidates if os.path.exists(p)), None)
        if resolved:
            pytesseract.pytesseract.tesseract_cmd = resolved
        else:
            pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"
    else:
        pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract") or "tesseract"

    os.makedirs(Config.OUTPUT_FOLDER, exist_ok=True)


# --- [4] Asset Processor Class ---

class AssetProcessor:
    """Orchestrates BF asset extraction using OOP and concurrency."""

    def __init__(self):
        warn_legacy_env_vars(
            "BF",
            (
                "BF_OPENAI_MODEL",
                "BF_OPENAI_PRIMARY_MODEL",
                "BF_PRIMARY_MODELS",
                "BF_FALLBACK_MODELS",
            ),
        )
        # We control retries ourselves; disable the SDK's retry layer to avoid double-billing on transient errors.
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=Config.API_TIMEOUT, max_retries=0)
        if Config.OCR_MODE not in {"off", "light", "full"}:
            logging.warning(f"Invalid BF_OCR_MODE '{Config.OCR_MODE}', defaulting to 'light'.")
            Config.OCR_MODE = "light"
        self.db_path = Config.DB_PATH
        self.qrs_to_ignore = self._load_qrs_to_ignore()
        self.ai_processed_qrs = self._load_ai_processed_qrs()
        logging.info(
            "BF runtime profile: workers=%s, ocr=%s, hybrid=%s, api_timeout=%ss, api_retries=%s",
            Config.MAX_WORKERS,
            Config.OCR_MODE,
            Config.HYBRID_OCR_AGENT_ENABLED,
            Config.API_TIMEOUT,
            Config.API_MAX_RETRIES,
        )
        logging.info(
            "BF model plan: %s | per_asset_cap=%s | per_model_cap=%s | premium_enabled=%s | fallback_trigger=(score<%s or missing>%s)",
            get_llm_model_plan(Config),
            Config.MAX_LLM_ATTEMPTS_PER_ASSET,
            Config.MAX_LLM_ATTEMPTS_PER_MODEL,
            Config.ENABLE_PREMIUM_FALLBACK,
            f"{Config.FALLBACK_MIN_SCORE:.0f}",
            Config.FALLBACK_MAX_MISSING,
        )
        logging.info(
            "BF reasoning tiers: normal=%s | hard=%s | manual_review=(score<%s or critical_confidence<%s)",
            Config.NORMAL_REASONING_EFFORT,
            Config.HARD_REASONING_EFFORT,
            f"{Config.MANUAL_REVIEW_MIN_SCORE:.0f}",
            Config.MANUAL_REVIEW_MIN_CONFIDENCE,
        )
        logging.info(f"BF Processor initialized. Filtered QRs: {len(self.qrs_to_ignore)} (DB), {len(self.ai_processed_qrs)} (AI Status).")

    def _load_qrs_to_ignore(self) -> Set[str]:
        """Loads SKIPPED QRs (Approved=1)."""
        to_ignore = set()
        if not os.path.exists(self.db_path):
            return to_ignore
        try:
            with closing(qrdb.get_connection(sqlite_path=self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                with closing(conn.cursor()) as cur:
                    try:
                        sql = f'''SELECT "QR Code" FROM "{Config.DB_TABLE}"
                                  WHERE "Approved" = '1' '''
                        cur.execute(sql)
                        for row in cur.fetchall():
                            if val := str(row["QR Code"]).strip():
                                to_ignore.add(val)
                    except qrdb.DatabaseError:
                        pass
        except Exception as e:
            logging.error(f"DB Error loading ignore list: {e}")
        return to_ignore

    def _load_ai_processed_qrs(self) -> Set[str]:
        """Loads QRs with ai_status=1."""
        processed = set()
        stale_processed: List[str] = []
        bf_qrs_with_images = self._qrs_with_bf_images()
        if not os.path.exists(self.db_path):
            return processed
        try:
            with closing(qrdb.get_connection(sqlite_path=self.db_path)) as conn:
                with closing(conn.cursor()) as cur:
                    cols = {n.lower(): n for n in qrdb.table_columns(conn, Config.AI_STATUS_TABLE)}  # backend-agnostic
                    
                    qr_col = cols.get("qr_code_id") or cols.get("qr code") or cols.get("qr")
                    ai_col = cols.get("ai_status") or cols.get("aistatus")

                    if not qr_col or not ai_col:
                        return processed
                    
                    cur.execute(f'SELECT "{qr_col}", "{ai_col}" FROM "{Config.AI_STATUS_TABLE}"')
                    for qr, status in cur.fetchall():
                        if str(status).strip() == "1":
                            qrid = str(qr).strip()
                            if not qrid:
                                continue
                            # Safety rule: only treat ai_status=1 as processed when BF JSON exists on disk.
                            if self._find_existing_json_for_qr(qrid, "BF"):
                                processed.add(qrid)
                            elif qrid in bf_qrs_with_images:
                                # Reset only rows that correspond to BF assets in current image set.
                                stale_processed.append(qrid)
                            else:
                                # Shared ai_status table: keep rows for non-BF assets untouched.
                                processed.add(qrid)
        except Exception:
            pass 
        if stale_processed:
            updated = self._bulk_set_ai_status(stale_processed, 0)
            logging.warning(
                "Detected %d stale BF ai_status=1 rows without JSON; reset %d row(s) back to 0. Examples: %s",
                len(stale_processed),
                updated,
                stale_processed[:10],
            )
        return processed

    def _qrs_with_bf_images(self) -> Set[str]:
        """Scans image folder and returns QRs that currently have BF image files."""
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
                if asset_type.upper() != "BF":
                    continue
                if seq not in Config.VALID_SUFFIXES:
                    continue
                qr_val = str(qr).strip()
                if qr_val:
                    qrs.add(qr_val)
        except OSError:
            return qrs
        return qrs

    def _find_existing_json_for_qr(self, qr: str, asset_type: str = "BF") -> str:
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
        # Best-effort audit logger import (sibling `audit/` package)
        _audit_log = None
        try:
            _proj = "/home/developer"
            if _proj not in sys.path:
                sys.path.insert(0, _proj)
            from audit.logger import log_change as _audit_log
        except Exception:
            _audit_log = None
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
                        old_row = cur.execute(
                            f'SELECT "{ai_col}" FROM "{Config.AI_STATUS_TABLE}" WHERE "{qr_col}" = ?',
                            (qr,),
                        ).fetchone()
                        old_val = old_row[0] if old_row else None
                        cur.execute(
                            f'UPDATE "{Config.AI_STATUS_TABLE}" SET "{ai_col}" = ? WHERE "{qr_col}" = ?',
                            (str(value), qr),
                        )
                        updated += max(0, cur.rowcount)
                        if _audit_log and str(old_val or "") != str(value):
                            try:
                                _audit_log(
                                    conn,
                                    qr_code=qr,
                                    app_name="api_bf",
                                    table_name=Config.AI_STATUS_TABLE,
                                    record_pk=qr,
                                    op_type="UPDATE",
                                    field_changes={ai_col: (old_val, value)},
                                    modified_by="ai-pipeline",
                                    source="ai:gpt-5.5",
                                    description="API_interface_BF._bulk_set_ai_status",
                                )
                            except Exception as audit_exc:
                                logging.warning(f"[audit] BF bulk ai_status audit failed: {audit_exc}")
                    conn.commit()
                    return updated
        except Exception as e:
            logging.error(f"Failed bulk ai_status update to {value}: {e}")
            return 0

    def _update_ai_status(self, qr: str, final_path: str = ""):
        """Sets ai_status=1 for the QR."""
        if not qr or not os.path.exists(self.db_path):
            return
        # Rule: do not set ai_status=1 unless a matching BF JSON exists.
        if final_path:
            if not os.path.exists(final_path):
                logging.warning(f"[{qr}] ai_status update blocked: JSON path missing: {final_path}")
                return
        else:
            if not self._find_existing_json_for_qr(qr, "BF"):
                logging.warning(f"[{qr}] ai_status update blocked: no BF JSON exists on disk.")
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
        """Scans image folder and groups by QR (Sequences 0, 1 ONLY)."""
        grouped = defaultdict(lambda: {"images": {}, "building": "", "asset_type": "BF"})
        
        if not os.path.exists(Config.IMAGE_FOLDER):
            logging.warning(f"Image folder not found: {Config.IMAGE_FOLDER}")
            return grouped

        for fn in os.listdir(Config.IMAGE_FOLDER):
            if not any(fn.lower().endswith(ext) for ext in Config.VALID_EXTS):
                continue
            
            m = Config.FILENAME_PATTERN.match(os.path.splitext(fn)[0])
            if not m:
                continue
                
            qr, building, atype, seq = m.groups()
            
            # Double check restrictions
            if seq not in Config.VALID_SUFFIXES: 
                continue 
            if atype.upper() != "BF":
                continue

            if qr in self.qrs_to_ignore or qr in self.ai_processed_qrs:
                continue
            
            grouped[qr]["building"] = building
            grouped[qr]["images"][seq] = os.path.join(Config.IMAGE_FOLDER, fn)
            
        logging.info(f"Discovered {len(grouped)} new BF assets to process.")
        return grouped

    def encode_image(self, path: str) -> str:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        ext = os.path.splitext(path)[1].lower()
        mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png"
        return f"data:{mime};base64,{b64}"

    def _preprocess_for_ocr(self, image_data: np.ndarray) -> np.ndarray:
        """Applies filters to improve OCR accuracy on metallic/dirty plates."""
        # Check if color or grayscale
        if len(image_data.shape) == 3:
            gray = cv2.cvtColor(image_data, cv2.COLOR_BGR2GRAY)
        else:
            gray = image_data
            
        # Scaling
        gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        
        # CLAHE (Contrast Limited Adaptive Histogram Equalization)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        
        # Binary Threshold check
        # For BF plates, sometimes simple GaussianBlur + Otsu works best
        blur = cv2.GaussianBlur(enhanced, (5, 5), 0)
        _, bw_img = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return bw_img

    def _ocr_text_variants(self, image_path: str) -> List[str]:
        """
        Extreme Optimization: reduced to 4 key OCR calls per image.
        1. Adaptive 0 deg: PSM 6 (Block) + PSM 11 (Sparse/Verticalish)
        2. Adaptive 90 CW: PSM 6 (Becomes horizontal)
        3. Adaptive 90 CCW: PSM 6 (Becomes horizontal)
        Total: 4 calls vs ~10 previously.
        """
        if cv2 is None:
            return []
        img = cv2.imread(image_path)
        if img is None:
            return []

        # Resize
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
        
        # Base Adaptive (Crucial for blurry text)
        base = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        
        texts: List[str] = []
        
        # Helper to run tesseract safely
        def run_tess(img_variant, psm_mode):
            config = f"--oem 3 --psm {psm_mode}"
            try:
                try:
                    t = pytesseract.image_to_string(img_variant, config=config, timeout=5)
                except RuntimeError:
                    return None
                except TypeError:
                    t = pytesseract.image_to_string(img_variant, config=config, timeout=10)
                if t:
                    return t.strip()
            except Exception:
                return None
            return None

        # 1. Base Image (0 deg) - Check both Block (6) and Sparse (11)
        # PSM 11 is critical for the "Vertical Serial" in natural orientation
        for psm in (6, 11):
            txt = run_tess(base, psm)
            if txt: texts.append(txt)

        # 2. Rotations - Check ONLY Block (6)
        # Rotation makes vertical text horizontal, so PSM 6 is sufficient and faster
        rot_cw = cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE)
        rot_ccw = cv2.rotate(base, cv2.ROTATE_90_COUNTERCLOCKWISE)
        rot_180 = cv2.rotate(base, cv2.ROTATE_180)

        for rot in [rot_cw, rot_ccw, rot_180]:
            txt = run_tess(rot, 6)
            if txt: texts.append(txt)

        # Deduplicate
        unique_texts = []
        seen = set()
        for t in texts:
            clean = " ".join(t.split())
            if len(clean) < 4: continue
            if clean not in seen:
                seen.add(clean)
                unique_texts.append(clean)
        return unique_texts

    def _extract_ocr_context(self, images: Dict[str, str]) -> str:
        """Runs Advanced OCR on all images to provide context hints."""
        if not cv2 or Config.OCR_MODE == "off":
            return ""

        context_lines = []
        # Process all available images (usually 0 and 1)
        for seq, path in images.items():
            try:
                # Use advanced OCR variants
                texts = self._ocr_text_variants(path)
                if texts:
                    # Provide top 5 unique variants to ensure LLM gets the rotated reads
                    block = " | ".join(texts[:5]) 
                    context_lines.append(f"[Image {seq} OCR Candidates]: {block}")
            except Exception as e:
                logging.warning(f"OCR failed for {path}: {e}")
        
        return "\n".join(context_lines)

    def _load_existing_json(self, qr: str, building: str) -> Optional[Dict[str, Any]]:
        """Loads existing JSON file to check completeness score."""
        filename = f"{qr}_BF_{building}.json"
        path = os.path.join(Config.OUTPUT_FOLDER, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_confidence_score(value: Any) -> int:
        try:
            return max(0, min(100, int(round(float(value)))))
        except (TypeError, ValueError):
            return 0

    def _normalize_confidence_scores(self, scores: Optional[Dict[str, Any]]) -> Dict[str, int]:
        if not isinstance(scores, dict):
            return {}
        return {
            field: self._normalize_confidence_score(scores.get(field, 0))
            for field in Config.COMPLETENESS_FIELDS
        }

    @staticmethod
    def _avg_confidence(scores: Dict[str, Any]) -> float:
        if not isinstance(scores, dict) or not scores:
            return 0.0
        values: List[float] = []
        for raw_score in scores.values():
            try:
                values.append(float(raw_score))
            except (TypeError, ValueError):
                continue
        return sum(values) / len(values) if values else 0.0

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
    ) -> int:
        try:
            base = int(base_tokens)
        except (TypeError, ValueError):
            base = 0
        normalized = (model_name or "").strip().lower()
        if not normalized.startswith("gpt-5"):
            return base
        return max(base, 4000 if hard else 3000)

    @staticmethod
    def _missing_field_count(structured_data: Dict[str, Any]) -> int:
        return sum(1 for field in Config.COMPLETENESS_FIELDS if not str(structured_data.get(field, "") or "").strip())

    def _should_fallback_to_heavier_model(
        self,
        score: float,
        structured_data: Optional[Dict[str, Any]] = None,
        confidence_scores: Optional[Dict[str, Any]] = None,
    ) -> bool:
        structured = structured_data if isinstance(structured_data, dict) else {}
        if score < Config.FALLBACK_MIN_SCORE or self._missing_field_count(structured) > Config.FALLBACK_MAX_MISSING:
            return True

        conf = self._normalize_confidence_scores(confidence_scores)
        for field in Config.COMPLETENESS_FIELDS:
            if str(structured.get(field, "") or "").strip() and conf.get(field, 0) < Config.MANUAL_REVIEW_MIN_CONFIDENCE:
                return True

        for field in Config.COMPLETENESS_FIELDS:
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
        extra_reason_codes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        structured = structured_data if isinstance(structured_data, dict) else {}
        conf = self._normalize_confidence_scores(confidence_scores)
        reason_codes: List[str] = list(extra_reason_codes or [])

        if score < Config.MANUAL_REVIEW_MIN_SCORE:
            reason_codes.append("low_completeness")

        for field in Config.COMPLETENESS_FIELDS:
            field_code = re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")
            if not str(structured.get(field, "") or "").strip():
                reason_codes.append(f"missing_{field_code}")
                continue
            if conf.get(field, 0) < Config.MANUAL_REVIEW_MIN_CONFIDENCE:
                reason_codes.append(f"low_confidence_{field_code}")
            if "?" in str(structured.get(field, "")):
                reason_codes.append(f"uncertain_character_{field_code}")

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

    def process_single_asset(self, qr: str, info: Dict[str, Any]) -> str:
        """Processes one asset: Builds prompt, calls LLM, saves JSON."""
        images = info["images"] # Dict {seq: path}
        building = info["building"]

        # Skip-if-exists guard: avoid duplicate billing for already-processed assets.
        existing_json_path = os.path.join(Config.OUTPUT_FOLDER, f"{qr}_BF_{building}.json")
        if os.path.exists(existing_json_path) and not Config.OVERWRITE_EXISTING_JSON:
            logging.info(f"[{qr}] Final status: {STATUS_SKIPPED_EXISTS} (existing JSON found, overwrite disabled).")
            return None

        # Hybrid OCR context is used immediately when enabled, and as an assisted retry before hard fallback.
        ocr_context = self._extract_ocr_context(images) if Config.HYBRID_OCR_AGENT_ENABLED else ""
        ocr_assisted_retry = bool(ocr_context)

        def build_content_block(active_ocr_context: str = "") -> List[Dict[str, Any]]:
            content_block: List[Dict[str, Any]] = []
            ocr_prompt_addendum = ""
            if active_ocr_context:
                ocr_prompt_addendum = (
                    "\n\nOCR Reference Text (Use this to resolve ambiguous characters, especially for SERIAL NUMBER):\n"
                    f"{active_ocr_context}"
                )

            prompt_text = f"""
You are an expert industrial asset extraction agent.
You will receive images of a Backflow (BF) device.
Extract the following fields strictly:
- Manufacturer: Identify the brand (Watts, Wilkins, Conbraco, Apollo).
- Model: The exact model string (e.g., 009M2, 975XL, 375 XL).
- Serial Number: The alpha-numeric serial. Check the "OCR Reference Text" below if the image is blurry.
- Diameter: The size in inches (e.g., 1/2", 3/4", 1", 1-1/2", 2").

Rules:
1. Examine ALL images provided.
2. If text is unclear, use your best reasoning based on standard nameplate formats.
3. For Diameter:
    - Look for **cast markings on the brass/bronze body** (e.g., "1/2", "3/4", "1"). The size is often NOT on the stainless steel nameplate.
    - **Watts Tag Rule**: If you see a **prefix number** before "LF" or the Model ID (e.g., "2LF 009M1QT" or "2 009"), that number is the Diameter. Extract it as `2"`.
    - **CRITICAL**: Do NOT extract "1" from "1.2MPa" or "175 PSIG" or "180 °F". These are pressure/temperature ratings, NOT the size.
    - **Prioritize fractional sizes** (1/2, 3/4) if ambiguous for these small devices.
    - Ensure it ends with a double quote ("). If the tag says "2LF", infer the quote and return `2"`.
4. **CRITICAL**: Do NOT confuse the model suffix "XL" (or similar) with the dimension. "375 XL 1/2" means Model is "375 XL" and Diameter is "1/2"". It is NOT "1-1/2"".
5. The photo may be rotated 90 degrees or upside-down. Mentally rotate the plate upright before reading any field; never transcribe characters in the rotated orientation.
6. **CRITICAL**: Never output a date as the Serial Number. Values shaped like MM/YY, MM/YYYY, or YYYY/MM (e.g., 05/2018) are manufacturing dates; an upside-down date can look like '8102/50' or '8102/90' - these are misreads, not serials. If the true serial is unreadable, return "".
7. If a field is not visible, return empty string.{ocr_prompt_addendum}
"""
            content_block.append({"type": "text", "text": prompt_text.strip()})

            # Add images (0 and 1)
            # Sort by sequence to maintain order
            for seq in sorted(images.keys()):
                path = images[seq]
                b64_url = self.encode_image(path)
                content_block.append({
                    "type": "image_url",
                    "image_url": {"url": b64_url}
                })
            return content_block

        best_data: Dict[str, Any] = {}
        best_confidence: Dict[str, int] = {}
        best_score = -1.0
        plan = get_llm_model_plan(Config)
        attempted_models: List[str] = []
        stop_extraction = False
        serial_date_misread_suspected = False

        try:
            for n, model_name in enumerate(plan, start=1):
                role = role_for_position(n)
                logging.info(f"[{qr}] LLM attempt {n}/{len(plan)} using {role} model {model_name}")
                attempted_models.append(model_name)

                # Gate: skip second/third model unless quality of best-so-far is below threshold.
                if n > 1 and not self._should_fallback_to_heavier_model(
                    best_score, best_data, best_confidence,
                ):
                    logging.info(
                        f"[{qr}] Fallback not needed (best_score={best_score:.0f}%, "
                        f"missing={self._missing_field_count(best_data)}). Accepting result."
                    )
                    break

                # On fallback escalation, surface OCR context if not already in use.
                if n > 1:
                    if not ocr_context and Config.OCR_MODE != "off":
                        ocr_context = self._extract_ocr_context(images)
                        ocr_assisted_retry = bool(ocr_context)
                    logging.info(
                        "[%s] Fallback enabled. Retrying with %s (best_score=%.0f%%, missing=%s, ocr_assisted_retry=%s).",
                        qr, model_name, best_score, self._missing_field_count(best_data), ocr_assisted_retry,
                    )

                content_block = build_content_block(ocr_context)
                model_succeeded = False
                for retry in range(Config.MAX_LLM_ATTEMPTS_PER_MODEL):
                    try:
                        kwargs: Dict[str, Any] = {
                            "model": model_name,
                            "messages": [{"role": "user", "content": content_block}],
                            "response_format": BFStructuredExtraction,
                            "max_completion_tokens": self._max_completion_tokens_for_model(
                                800, model_name, hard=n > 1,
                            ),
                        }
                        effort = self._reasoning_effort_for_model(model_name, hard=n > 1)
                        if effort:
                            kwargs["reasoning_effort"] = effort

                        completion = self.client.beta.chat.completions.parse(**kwargs)
                        parsed = completion.choices[0].message.parsed
                        if not parsed:
                            logging.warning(f"[{qr}] Failed to parse structured output from {model_name}.")
                            continue

                        parsed_payload = parsed.model_dump(by_alias=True)
                        candidate = {
                            field: str(parsed_payload.get(field, "") or "").strip()
                            for field in Config.COMPLETENESS_FIELDS
                        }
                        candidate_confidence = self._normalize_confidence_scores(
                            parsed_payload.get("Confidence Scores", {})
                        )
                        if looks_like_date_misread_serial(candidate.get("Serial Number", "")):
                            logging.warning(
                                f"[{qr}] Discarding Serial Number '{candidate.get('Serial Number', '')}' from "
                                f"{model_name}: reads as a manufacturing date (possibly rotated/upside-down)."
                            )
                            candidate["Serial Number"] = ""
                            candidate_confidence.pop("Serial Number", None)
                            serial_date_misread_suspected = True
                        candidate_score = completeness_score(candidate, Config.COMPLETENESS_FIELDS)
                        logging.info(
                            "[%s] Extraction completed: completeness=%.0f%%, missing_fields=%s (model=%s, role=%s).",
                            qr, candidate_score, self._missing_field_count(candidate), model_name, role,
                        )
                        model_succeeded = True

                        if candidate_score > best_score:
                            best_data = candidate
                            best_confidence = candidate_confidence
                            best_score = candidate_score

                        if not self._should_fallback_to_heavier_model(
                            candidate_score, candidate, candidate_confidence,
                        ):
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
                        logging.warning(
                            f"[{qr}] {model_name} attempt {retry + 1}/{Config.MAX_LLM_ATTEMPTS_PER_MODEL} failed: {e}"
                        )
                        time.sleep(Config.API_RETRY_DELAY * (retry + 1))

                if stop_extraction:
                    break
                if n == 1 and not Config.ENABLE_LLM_FALLBACK and not model_succeeded:
                    break

        except QuotaExceeded:
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="BF",
                image_paths=list(images.values()),
                failure_reason="llm_quota_exceeded",
                missing_fields=[f for f in Config.COMPLETENESS_FIELDS if not str(best_data.get(f, "") or "").strip()],
                attempted_models=attempted_models, status=STATUS_QUOTA,
            )
            logging.info(f"[{qr}] Final status: {STATUS_QUOTA}")
            return None
        except AuthFailed:
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="BF",
                image_paths=list(images.values()),
                failure_reason="llm_auth_failed",
                missing_fields=[f for f in Config.COMPLETENESS_FIELDS if not str(best_data.get(f, "") or "").strip()],
                attempted_models=attempted_models, status=STATUS_AUTH,
            )
            logging.info(f"[{qr}] Final status: {STATUS_AUTH}")
            return None

        extracted_data = best_data if best_data else {field: "" for field in Config.COMPLETENESS_FIELDS}
        extracted_confidence = best_confidence if best_confidence else {}
        
        # Completeness
        score = completeness_score(extracted_data, Config.COMPLETENESS_FIELDS)
        
        # Completeness Guard: Check existing
        existing = self._load_existing_json(qr, building)
        if existing:
            existing_score = existing.get("completeness_score", 0)
            # ME Standard: Allow update if new score is >= existing (i.e. only skip if existing is strictly better)
            if existing_score > score:
                logging.info(f"[{qr}] Existing completeness ({existing_score}%) > New ({score}%). Skipping save.")
                self._update_ai_status(qr)
                # Return None to match ME standard of only listing saved items
                return None

        # Build Output
        conf_scores = self._normalize_confidence_scores(extracted_confidence)
        if serial_date_misread_suspected and str(extracted_data.get("Serial Number", "") or "").strip():
            # A rescue attempt replaced a date-shaped misread; keep the value but
            # force it below the manual-review confidence threshold.
            conf_scores["Serial Number"] = min(conf_scores.get("Serial Number", 0), 65)
        avg_conf = self._avg_confidence(conf_scores)
        manual_review = self._build_manual_review_metadata(
            extracted_data,
            conf_scores,
            score,
            ocr_assisted_retry=ocr_assisted_retry,
            extra_reason_codes=(
                ["serial_date_misread_suspected"] if serial_date_misread_suspected else None
            ),
        )
        flagged_for_review = manual_review.get("flag_for_review") == 1
        if flagged_for_review:
            extracted_data["Flagged"] = "true"
            extracted_data["Approved"] = ""
            logging.warning(
                "[%s] Marked BF extraction for manual review: %s",
                qr,
                ", ".join(manual_review.get("reason_codes", [])),
            )
            append_manual_review(
                Config.MANUAL_REVIEW_QUEUE_FILE,
                qr=qr, building=building, asset_type="BF",
                image_paths=list(images.values()),
                failure_reason=",".join(manual_review.get("reason_codes", [])) or "low_quality_extraction",
                missing_fields=[f for f in Config.COMPLETENESS_FIELDS if not str(extracted_data.get(f, "") or "").strip()],
                attempted_models=attempted_models,
                status=STATUS_LOW_QUALITY,
            )

        output_payload = {
            "qr_code": qr,
            "building_number": building,
            "asset_type": "- BF",
            "structured_data": extracted_data,
            "completeness_score": score,
            "confidence_scores": conf_scores,
            "Avg_ai_conf": avg_conf,
            "manual_review": manual_review,
        }
        
        # Save JSON
        filename = f"{qr}_BF_{building}.json"
        
        # 1. Atomic File Write via Temp File
        fd, temp_path = tempfile.mkstemp(dir=Config.OUTPUT_FOLDER, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(output_payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())  # Force OS buffers to write to the physical disk layer
                
            # 2. Validation: Verify non-empty
            if os.path.getsize(temp_path) == 0:
                raise ValueError(f"Generated JSON is 0 bytes for QR: {qr}")
                
            # 3. Atomic Rename
            final_path = os.path.join(Config.OUTPUT_FOLDER, filename)
            os.replace(temp_path, final_path)
            
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logging.error(f"Failed to write JSON for {qr}: {e}")
            return None  # Abort DB update entirely
            
        try:
            # Update Status & Log
            self._update_ai_status(qr, final_path)
            conf_scores = output_payload.get("confidence_scores", {})
            avg_conf = sum(conf_scores.values()) / max(1, len(conf_scores)) if conf_scores else 0
            final_status = STATUS_LOW_QUALITY if flagged_for_review else STATUS_SUCCESS
            logging.info(f"Successfully processed and saved asset QR: {qr} (Completeness: {score:.0f}% | Avg Conf: {avg_conf:.0f}%)")
            logging.info(f"[{qr}] Final status: {final_status}")
        except Exception as e:
            print(f"Warning: Failed to update status/log for {qr}: {e}")

        # Return payload key info for summary (Standardized with ME)
        return output_payload

    def run(self):
        """Main execution loop with concurrency."""
        assets = self.discover_assets()
        if not assets:
            msg = "No BF assets found for AI processing. Skipping BF run."
            logging.info(msg)
            print(msg)
            return

        print(f"Starting processing of {len(assets)} assets with {Config.MAX_WORKERS} workers...")
        
        summary_lines = []
        success_count = 0
        
        with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
            future_to_qr = {
                executor.submit(self.process_single_asset, qr, info): qr 
                for qr, info in assets.items()
            }
            
            for future in as_completed(future_to_qr):
                qr = future_to_qr[future]
                try:
                    payload = future.result()
                    if payload:
                        # Success - Generate summary line here (Standardized)
                        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                        bldg = payload.get("building_number", "Unknown")
                        conf_scores = payload.get("confidence_scores", {})
                        avg_conf = sum(conf_scores.values()) / max(1, len(conf_scores)) if conf_scores else 0
                        line = f"- QR: {qr} | BF | {timestamp} | Building: {bldg} | Avg Conf: {avg_conf:.0f}%"
                        
                        summary_lines.append(line)
                        success_count += 1
                except Exception as e:
                    logging.error(f"Failed to process {qr}: {e}")
                    print(f"CRITICAL ERROR processing {qr}: {e}")

        # Final Summary
        print(f"\n--- SUMMARY ---\nTotal assets processed: {len(assets)}\nSuccessfully saved: {success_count}")
        if summary_lines:
            print("")
            for line in sorted(summary_lines):
                print(line)


# --- [5] Entry Point ---
def main():
    setup_environment()
    processor = AssetProcessor()
    processor.run()

if __name__ == "__main__":
    main()
