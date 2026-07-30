"""Centralized LLM cost-control strategy for ME / EL / BF extraction pipelines.

Provides:
- Model plan ordering with attempt cap and premium gate
- Quota / auth error classification (fast-fail on 429 / insufficient_quota)
- Thread-safe manual review queue writer
- Status constants shared across all three pipelines
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, List, Optional

from openai import APIConnectionError, APIStatusError, BadRequestError, RateLimitError

# Substrings that indicate a billing / quota failure regardless of exception class.
QUOTA_KEYWORDS = (
    "insufficient_quota",
    "exceeded your current quota",
    "billing",
    "billing_hard_limit_reached",
)

# Final status constants — single source of truth used by ME / EL / BF.
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial_success"
STATUS_LOW_QUALITY = "low_quality_extraction"
STATUS_QUOTA = "llm_quota_exceeded"
STATUS_AUTH = "llm_auth_failed"
STATUS_API_ERROR = "llm_api_error"
STATUS_NO_JSON = "no_json_generated"
STATUS_MANUAL_REVIEW = "manual_review_required"
STATUS_SKIPPED_EXISTS = "skipped_existing_json"


class QuotaExceeded(Exception):
    """Raised when an OpenAI 429 / insufficient_quota response is detected.

    Halts all further model attempts for the affected asset.
    """


class AuthFailed(Exception):
    """Raised when OpenAI authentication (401/403/invalid_api_key) fails."""


def get_llm_model_plan(cfg) -> List[str]:
    """Return ordered list of model names to try for one asset.

    Honors ENABLE_LLM_FALLBACK / ENABLE_PREMIUM_FALLBACK flags and caps the
    sequence at MAX_LLM_ATTEMPTS_PER_ASSET. Deduplicates while preserving order.
    """
    raw: List[Optional[str]] = [getattr(cfg, "PRIMARY_LLM_MODEL", "")]
    if getattr(cfg, "ENABLE_LLM_FALLBACK", False):
        raw.append(getattr(cfg, "FALLBACK_LLM_MODEL", ""))
    if getattr(cfg, "ENABLE_PREMIUM_FALLBACK", False):
        raw.append(getattr(cfg, "PREMIUM_LLM_MODEL", ""))

    seen: set = set()
    out: List[str] = []
    for name in raw:
        m = (name or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)

    cap = max(1, int(getattr(cfg, "MAX_LLM_ATTEMPTS_PER_ASSET", len(out)) or len(out)))
    return out[:cap]


def role_for_position(n: int) -> str:
    """Return human-readable role label for the n-th (1-indexed) attempt."""
    if n == 1:
        return "primary"
    if n == 2:
        return "fallback"
    return "premium"


def is_quota_error(exc: BaseException) -> bool:
    """True for 429 / insufficient_quota / billing-related failures."""
    if isinstance(exc, RateLimitError):
        return True
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if isinstance(exc, APIStatusError) and status_code == 429:
        return True
    msg = str(exc).lower()
    return any(k in msg for k in QUOTA_KEYWORDS)


def is_auth_error(exc: BaseException) -> bool:
    """True for 401 / 403 / invalid_api_key failures."""
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if isinstance(exc, APIStatusError) and status_code in (401, 403):
        return True
    msg = str(exc).lower()
    return (
        "authentication" in msg
        or "invalid_api_key" in msg
        or "incorrect api key" in msg
        or "invalid api key" in msg
    )


_review_lock = Lock()


def append_manual_review(
    queue_path: str,
    *,
    qr: str,
    building: str,
    asset_type: str,
    image_paths: Iterable[str],
    failure_reason: str,
    missing_fields: Iterable[str],
    attempted_models: Iterable[str],
    status: str,
) -> None:
    """Append one JSON line to the manual review queue. Thread-safe."""
    entry = {
        "qr_code": qr,
        "building_code": building,
        "asset_type": asset_type,
        "image_paths": list(image_paths),
        "failure_reason": failure_reason,
        "missing_fields": list(missing_fields),
        "attempted_models": list(attempted_models),
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    parent = os.path.dirname(queue_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _review_lock:
        with open(queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def warn_legacy_env_vars(prefix: str, legacy_names: Iterable[str]) -> None:
    """Log a single warning at startup if deprecated env vars are still set."""
    found = [name for name in legacy_names if os.getenv(name)]
    if found:
        logging.warning(
            "[%s] Deprecated env vars detected and ignored: %s. "
            "Use %sPRIMARY_LLM_MODEL / %sFALLBACK_LLM_MODEL / %sPREMIUM_LLM_MODEL instead.",
            prefix,
            ", ".join(found),
            prefix,
            prefix,
            prefix,
        )


__all__ = [
    "STATUS_SUCCESS",
    "STATUS_PARTIAL",
    "STATUS_LOW_QUALITY",
    "STATUS_QUOTA",
    "STATUS_AUTH",
    "STATUS_API_ERROR",
    "STATUS_NO_JSON",
    "STATUS_MANUAL_REVIEW",
    "STATUS_SKIPPED_EXISTS",
    "QuotaExceeded",
    "AuthFailed",
    "get_llm_model_plan",
    "role_for_position",
    "is_quota_error",
    "is_auth_error",
    "append_manual_review",
    "warn_legacy_env_vars",
    "APIConnectionError",
    "APIStatusError",
    "BadRequestError",
    "RateLimitError",
]
