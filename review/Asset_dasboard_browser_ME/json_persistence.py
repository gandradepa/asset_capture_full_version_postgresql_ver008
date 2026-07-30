"""Import-safe JSON persistence helpers shared by EL review + Swift Over.

This module intentionally depends only on the standard library so tests can
import it without bootstrapping the full Flask app.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode


DEFAULT_STATE_ONLY_KEYS = frozenset({"Approved", "Flagged"})
# Keys dropped from the dashboard back-navigation query. ``archive`` is
# intentionally NOT transient: the "Show Archive" toggle the user set on the
# dashboard must survive a round-trip through the review page (back-link,
# Save & Next/Prev, and reload all keep it).
TRANSIENT_DASHBOARD_QUERY_KEYS = frozenset()
SWIFT_REVISION_FIELDS = (
    "Equipment ID",
    "Supply From",
    "Room",
    "Voltage Rating",
    "Voltage Rating (UoM)",
    "Amperage Rating",
    "Amperage Rating (UoM)",
    "Power Rating",
    "Power Rating (UoM)",
    "QR Code",
)


class RevisionConflictError(Exception):
    """Raised when a submitted revision token is missing or stale."""

    def __init__(self, message: str, current_revision: str | None = None):
        super().__init__(message)
        self.current_revision = current_revision


def _canonical_json(value) -> str:
    return json.dumps(
        {} if value is None else value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash_payload(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_text(value) -> str:
    return "" if value is None else str(value).strip()


def compute_review_revision(json_payload) -> str:
    """Hash the canonical on-disk JSON payload for stale-review detection."""
    return _hash_payload(json_payload)


def compute_swift_revision(asset_snapshot) -> str:
    """Hash the editable Swift Over row snapshot for stale-row detection."""
    snapshot = asset_snapshot or {}
    canonical = {}
    for field in SWIFT_REVISION_FIELDS:
        if field == "QR Code":
            canonical[field] = _normalize_text(
                snapshot.get("QR Code") or snapshot.get("matched_qr")
            )
            continue
        canonical[field] = _normalize_text(snapshot.get(field))
    return _hash_payload(canonical)


def atomic_write_json(path: str, payload, *, indent: int = 4) -> None:
    """Atomically replace ``path`` with ``payload`` encoded as JSON."""
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{os.path.basename(path)}.",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _load_processed_log(processed_log_path: str) -> dict:
    if not os.path.exists(processed_log_path):
        return {}
    try:
        with open(processed_log_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_json_processed(
    processed_log_path: str,
    *,
    file_paths: list[str] | tuple[str, ...] | None = None,
    filename_mtimes: Mapping[str, float] | None = None,
) -> dict:
    """Update the processed JSON watcher log with current file mtimes."""
    updates = {}
    for file_path in file_paths or ():
        if not file_path or not os.path.isfile(file_path):
            continue
        updates[os.path.basename(file_path)] = os.path.getmtime(file_path)
    for filename, mtime in (filename_mtimes or {}).items():
        if not filename:
            continue
        updates[os.path.basename(str(filename))] = float(mtime)

    if not updates and os.path.exists(processed_log_path):
        return _load_processed_log(processed_log_path)

    log_data = _load_processed_log(processed_log_path)
    log_data.update(updates)
    atomic_write_json(processed_log_path, log_data, indent=2)
    return log_data


def _has_meaningful_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    if isinstance(value, (int, float, bool)):
        return True
    return bool(str(value).strip())


def has_meaningful_structured_data(
    structured_data,
    state_only_keys: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Return True only when ``structured_data`` contains real asset content."""
    if not isinstance(structured_data, Mapping) or not structured_data:
        return False
    ignored_keys = DEFAULT_STATE_ONLY_KEYS if state_only_keys is None else state_only_keys
    ignored = {str(key).strip() for key in ignored_keys}
    for key, value in structured_data.items():
        if str(key).strip() in ignored:
            continue
        if _has_meaningful_value(value):
            return True
    return False


def normalize_dashboard_query(
    query_string,
    *,
    transient_keys: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Drop transient dashboard params and return a normalized query plus saved params."""
    raw_query = _normalize_text(query_string)
    if raw_query.startswith("?"):
        raw_query = raw_query[1:]
    if not raw_query:
        return "", {}

    excluded = (
        TRANSIENT_DASHBOARD_QUERY_KEYS
        if transient_keys is None
        else {str(key).strip() for key in transient_keys}
    )
    filtered_pairs = []
    saved_params = {}

    for key, value in parse_qsl(raw_query, keep_blank_values=True):
        if key in excluded:
            continue
        filtered_pairs.append((key, value))
        if value:
            saved_params[key] = value

    normalized_query = urlencode(filtered_pairs)
    return normalized_query, saved_params


def merge_form_into_structured(
    structured: dict,
    form_data: Mapping[str, str],
    *,
    skip_fields: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
    hidden_passthrough_fields: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
    ignored_form_fields: set[str] | frozenset[str] | tuple[str, ...] | list[str] | None = None,
) -> bool:
    """Merge form fields without clearing structured keys missing from the POST."""
    if not isinstance(structured, dict):
        raise TypeError("structured must be a dict")

    skip = set(skip_fields or ())
    passthrough = set(hidden_passthrough_fields or ())
    ignored = set(ignored_form_fields or ())
    changed = False

    for field in list(structured.keys()):
        if field in skip:
            continue
        if field in passthrough and field not in form_data:
            continue
        if field not in form_data:
            continue
        form_value = form_data[field]
        if structured.get(field, "") != form_value:
            changed = True
        structured[field] = form_value

    for field, form_value in form_data.items():
        if field in skip or field in ignored:
            continue
        if field not in structured:
            structured[field] = form_value
            changed = True

    return changed


def ensure_revision_matches(
    current_payload,
    submitted_revision: str | None,
    *,
    revision_fn=compute_review_revision,
    missing_message: str = "Missing revision token. Reload and retry.",
    conflict_message: str = "This record changed since you loaded it. Reload and try again.",
) -> str:
    """Return the current revision or raise ``RevisionConflictError``."""
    current_revision = revision_fn(current_payload)
    submitted = _normalize_text(submitted_revision)
    if not submitted:
        raise RevisionConflictError(missing_message, current_revision=current_revision)
    if submitted != current_revision:
        raise RevisionConflictError(conflict_message, current_revision=current_revision)
    return current_revision


__all__ = [
    "RevisionConflictError",
    "atomic_write_json",
    "compute_review_revision",
    "compute_swift_revision",
    "ensure_revision_matches",
    "has_meaningful_structured_data",
    "mark_json_processed",
    "merge_form_into_structured",
    "normalize_dashboard_query",
]
