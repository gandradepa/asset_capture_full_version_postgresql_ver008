"""Feedback corpus emitter for SLD extraction (v1 + human_correction).

Two channels are produced when feedback is enabled:

- **Channel A** (human-readable, ops triage): the wrapper in ``sld_blueprint.py``
  appends START/END envelopes plus captured stdout/stderr to a single rolling
  log file (default ``/home/developer/sld_extract.log``). This module does
  not write Channel A directly — it only provides helpers the wrapper uses.

- **Channel B** (structured JSONL, training/eval): the extractor + wrapper
  append typed event records to a per-run feedback file at
  ``<feedback_dir>/<run_id>.jsonl``. Edits to extracted SLD rows in the EL
  review app append ``human_correction`` events to a sibling
  ``<feedback_dir>/corrections.jsonl`` (append-only, joined back to a run via
  ``ai_run_id``).

Disabled when ``SLD_FEEDBACK_DISABLED`` is set or no feedback file is
provided. All write failures are swallowed so a logging issue never breaks
extraction or a user-facing save.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_FEEDBACK_DIR = "/home/developer/sld_extract_feedback"
CORRECTIONS_FILENAME = "corrections.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha1(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha1(data).hexdigest()


def _safe_default(o):
    try:
        return str(o)
    except Exception:
        return None


def _serialize_usage(usage) -> Optional[dict]:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return {k: v for k, v in usage.items()}
    out: dict = {}
    for attr in (
        "input_tokens", "output_tokens", "total_tokens",
        "prompt_tokens", "completion_tokens", "reasoning_tokens",
        "input_tokens_details", "output_tokens_details",
    ):
        if hasattr(usage, attr):
            val = getattr(usage, attr)
            if hasattr(val, "model_dump"):
                try:
                    val = val.model_dump()
                except Exception:
                    val = str(val)
            out[attr] = val
    return out or None


def feedback_dir_for(env: Optional[dict] = None) -> str:
    env = env if env is not None else os.environ
    return env.get("SLD_FEEDBACK_DIR", DEFAULT_FEEDBACK_DIR)


def make_run_id(building_code: str, pdf_sha1: str) -> str:
    """``sld_<UTC YYYYMMDD_HHMMSS>_b<building>_<pdf_sha1[:6]>``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_bld = "".join(ch for ch in (building_code or "x") if ch.isalnum() or ch in "-_")[:16] or "x"
    short = (pdf_sha1 or uuid.uuid4().hex)[:6]
    return f"sld_{stamp}_b{safe_bld}_{short}"


class FeedbackEmitter:
    """Writes JSONL events to a per-run feedback file.

    Behavior is a no-op when ``feedback_file`` is empty/None or when the
    ``SLD_FEEDBACK_DISABLED`` env var is set, so callers can wire the same
    code path under any deployment.
    """

    def __init__(self, feedback_file: Optional[str], run_id: Optional[str]):
        self.feedback_file = feedback_file
        self.run_id = run_id
        self.disabled = bool(os.getenv("SLD_FEEDBACK_DISABLED")) or not feedback_file
        self._first_image_inlined = False

    def emit(self, kind: str, **fields) -> None:
        if self.disabled:
            return
        record = {
            "event_id": "ev_" + uuid.uuid4().hex[:12],
            "ts": _now_iso(),
            "run_id": self.run_id,
            "kind": kind,
        }
        record.update(fields)
        try:
            os.makedirs(os.path.dirname(self.feedback_file), exist_ok=True)
            with open(self.feedback_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=_safe_default) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Image-aware payload redaction
    # ------------------------------------------------------------------

    def _redact_image_item(self, item: dict) -> dict:
        url = item.get("image_url")
        if isinstance(url, dict):
            url_str = url.get("url", "") or ""
        else:
            url_str = str(url or "")
        if not url_str.startswith("data:"):
            return item
        _, _, b64 = url_str.partition(",")
        digest = _sha1(b64) if b64 else ""
        try:
            byte_size = (len(b64) * 3) // 4
        except Exception:
            byte_size = None
        if not self._first_image_inlined:
            self._first_image_inlined = True
            redacted = dict(item)
            redacted["image_sha1"] = digest
            redacted["image_bytes"] = byte_size
            return redacted
        redacted = {k: v for k, v in item.items() if k != "image_url"}
        redacted["image_sha1"] = digest
        redacted["image_bytes"] = byte_size
        redacted["image_omitted"] = True
        return redacted

    def redact_input(self, payload: Any) -> Any:
        """Walks a Responses-API ``input`` and reduces image_url payloads.

        First image inlined (full data URL); every subsequent image_url is
        replaced with a sha1 + byte-size summary so the corpus stays small.
        """
        if isinstance(payload, list):
            return [self.redact_input(it) for it in payload]
        if isinstance(payload, dict):
            t = payload.get("type")
            if t == "input_image":
                return self._redact_image_item(payload)
            content = payload.get("content")
            if isinstance(content, list):
                return {**payload, "content": [self.redact_input(it) for it in content]}
            return payload
        return payload

    # ------------------------------------------------------------------
    # OpenAI Responses API tracer
    # ------------------------------------------------------------------

    def traced_responses_create(self, label: str, client, **kwargs):
        """Wraps ``client.responses.create(**kwargs)`` and emits a
        ``model_call`` event capturing the input shape, model, latency,
        and response output_text + usage. Re-raises on failure after
        emitting the event so downstream error handling is unchanged.
        """
        start = time.monotonic()
        instructions = kwargs.get("instructions", "")
        input_payload = kwargs.get("input")
        text_config = kwargs.get("text")
        model = kwargs.get("model", "")
        reasoning = {k: v for k, v in kwargs.items() if k.startswith("reasoning")}
        response = None
        ok = True
        err: Optional[str] = None
        try:
            response = client.responses.create(**kwargs)
            return response
        except Exception as exc:
            ok = False
            err = repr(exc)
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            try:
                self.emit(
                    "model_call",
                    label=label,
                    model=model,
                    instructions_sha1=_sha1(instructions)[:16] if isinstance(instructions, str) else None,
                    instructions_preview=instructions[:240] if isinstance(instructions, str) else None,
                    input=self.redact_input(input_payload),
                    text_config=text_config,
                    reasoning=reasoning or None,
                    output_text=getattr(response, "output_text", None) if ok and response is not None else None,
                    response_usage=_serialize_usage(getattr(response, "usage", None)) if ok and response is not None else None,
                    latency_ms=latency_ms,
                    ok=ok,
                    error=err,
                )
            except Exception:
                pass


# ----------------------------------------------------------------------
# CLI helpers (used by extract_electrical_schema.py)
# ----------------------------------------------------------------------

def emitter_from_argv(args) -> FeedbackEmitter:
    """Build a FeedbackEmitter from argparse Namespace fields:
    ``args.feedback_file`` and ``args.run_id``. Returns a disabled emitter
    when either is absent.
    """
    return FeedbackEmitter(
        feedback_file=getattr(args, "feedback_file", None),
        run_id=getattr(args, "run_id", None),
    )


SUMMARY_SENTINEL = "__SLD_RUN_SUMMARY__"


def emit_summary_to_stdout(summary: dict) -> None:
    """Print one machine-parseable summary line the wrapper can pick up."""
    try:
        print(SUMMARY_SENTINEL + " " + json.dumps(summary, ensure_ascii=False, default=_safe_default))
    except Exception:
        pass


def parse_summary_from_stdout(stdout: str) -> Optional[dict]:
    if not stdout:
        return None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(SUMMARY_SENTINEL):
            tail = line[len(SUMMARY_SENTINEL):].strip()
            try:
                return json.loads(tail)
            except Exception:
                return None
    return None


# ----------------------------------------------------------------------
# Human-correction writer (used by EL review save handlers)
# ----------------------------------------------------------------------

def corrections_path(env: Optional[dict] = None) -> str:
    return os.path.join(feedback_dir_for(env), CORRECTIONS_FILENAME)


def write_human_correction(
    *,
    ai_run_id: Optional[str],
    building_code: Optional[str],
    qr_code: Optional[str],
    row_id: Optional[int],
    field: str,
    ai_value: Any,
    human_value: Any,
    user: Optional[str] = None,
    reason_code: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Append one ``human_correction`` event to ``corrections.jsonl``.

    Joins back to a run via ``ai_run_id``. ``ai_value`` is the value that
    was inserted by the extractor (recovered from the row's
    ``sld_ai_extract_payload`` baseline JSON), ``human_value`` is the new
    value posted by the user. Best-effort: silently swallows IO errors.
    """
    if os.getenv("SLD_FEEDBACK_DISABLED"):
        return
    record = {
        "event_id": "ev_" + uuid.uuid4().hex[:12],
        "ts": _now_iso(),
        "kind": "human_correction",
        "ai_run_id": ai_run_id,
        "building_code": building_code,
        "qr_code": qr_code,
        "row_id": row_id,
        "field": field,
        "ai_value": ai_value,
        "human_value": human_value,
        "user": user,
        "reason_code": reason_code,
    }
    if extra:
        record.update(extra)
    try:
        path = corrections_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=_safe_default) + "\n")
    except OSError:
        pass


def write_corrections_for_changed_fields(
    *,
    ai_run_id: Optional[str],
    ai_baseline: dict,
    current_row: dict,
    submitted_updates: dict,
    fields: list[str],
    building_code: Optional[str] = None,
    qr_code: Optional[str] = None,
    row_id: Optional[int] = None,
    user: Optional[str] = None,
) -> int:
    """Diff each field in ``fields`` and emit a ``human_correction`` per
    field whose submitted value differs from the AI baseline.

    Comparison uses the *AI baseline* (the value at insert time captured
    in ``sld_ai_extract_payload``) rather than the row's *current* value,
    so a sequence of edits each contribute distinct corrections.

    Returns the number of corrections emitted.
    """
    if not fields or not isinstance(submitted_updates, dict):
        return 0
    baseline = ai_baseline if isinstance(ai_baseline, dict) else {}
    count = 0
    for field in fields:
        if field not in submitted_updates:
            continue
        new_val = submitted_updates.get(field)
        old_val = baseline.get(field, current_row.get(field) if isinstance(current_row, dict) else None)
        if _normalize(new_val) == _normalize(old_val):
            continue
        write_human_correction(
            ai_run_id=ai_run_id,
            building_code=building_code,
            qr_code=qr_code,
            row_id=row_id,
            field=field,
            ai_value=old_val,
            human_value=new_val,
            user=user,
        )
        count += 1
    return count


def _normalize(v):
    if v is None:
        return ""
    return str(v).strip()
