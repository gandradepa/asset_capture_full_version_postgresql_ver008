"""Deterministic parser for the ai_check wrapper log.

The parser intentionally has no Flask, database, or OpenAI dependencies so the
presentation policy can be tested without network or application state.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TIMESTAMP_RE = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
    r"(?:\s+(?P<level>[A-Z]+):)?\s*(?P<message>.*)$"
)
RUN_START_RE = re.compile(
    r"ai_check version=(?P<version>\S+)\s+path=(?P<path>\S+)"
    r"\s+pid=(?P<pid>\d+)\s+host=(?P<host>\S+)"
)
STAGE_START_RE = re.compile(r"--- START Output for (EL|BF|ME) ---")
STAGE_END_RE = re.compile(
    r"--- END Output for (EL|BF|ME) ---\s*\(exit=(?P<exit>-?\d+),"
    r"\s*duration=(?P<duration>\d+)s\)"
)
MODEL_RE = re.compile(r"\b(?:EL|BF|ME) model plan:\s*\[\s*['\"]([^'\"]+)['\"]")
PENDING_RE = re.compile(r"Found\s+(\d+)\s+pending items?", re.IGNORECASE)
DISCOVERED_RE = re.compile(r"Found\s+(\d+)\s+new assets?", re.IGNORECASE)
PROCESSED_TOTAL_RE = re.compile(r"Total assets processed:\s*(\d+)", re.IGNORECASE)
SAVED_TOTAL_RE = re.compile(r"Successfully saved:\s*(\d+)", re.IGNORECASE)
ASSET_RE = re.compile(
    r"Successfully processed and saved asset QR:\s*(?P<qr>[^\s(]+)"
    r"(?:\s*\(Completeness:\s*(?P<completeness>\d+)%"
    r"(?:\s*\|\s*Avg Conf:\s*(?P<confidence>\d+)%\s*)?\))?",
    re.IGNORECASE,
)

STATUS_META = {
    "success": ("Success", "success"),
    "attention": ("Needs attention", "warning"),
    "failed": ("Failed", "danger"),
    "no_work": ("No work", "secondary"),
    "running": ("Running", "primary"),
    "incomplete": ("Incomplete", "dark"),
    "not_run": ("Not run", "secondary"),
}

WRAPPER_FAILURE_MARKERS = (
    "Missing script:",
    "Internal wrapper error:",
    "Script not found for label",
)


def _parse_timestamp(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except (TypeError, ValueError):
        return None


def _event(raw_line: str) -> Dict[str, Any]:
    raw = raw_line.rstrip("\r\n")
    match = TIMESTAMP_RE.match(raw)
    if not match:
        return {"raw": raw, "timestamp": None, "level": "", "message": raw.strip()}
    return {
        "raw": raw,
        "timestamp": _parse_timestamp(match.group("timestamp")),
        "level": (match.group("level") or "").upper(),
        "message": match.group("message").strip(),
    }


def _status_fields(target: Dict[str, Any], status: str) -> None:
    label, badge = STATUS_META[status]
    target["status"] = status
    target["status_label"] = label
    target["status_badge"] = badge


def _new_stage(name: str) -> Dict[str, Any]:
    stage: Dict[str, Any] = {
        "name": name,
        "started_at_dt": None,
        "ended_at_dt": None,
        "started_at": "",
        "ended_at": "",
        "duration_seconds": None,
        "exit_code": None,
        "model": "",
        "discovered_count": None,
        "processed_count": None,
        "saved_count": None,
        "assets": [],
        "warnings": [],
        "errors": [],
        "diagnostics": [],
        "raw_lines": [],
        "has_activity": False,
    }
    _status_fields(stage, "not_run")
    return stage


def _new_run(start_event: Optional[Dict[str, Any]] = None, partial: bool = False) -> Dict[str, Any]:
    started_at = start_event.get("timestamp") if start_event else None
    run: Dict[str, Any] = {
        "started_at_dt": started_at,
        "ended_at_dt": None,
        "started_at": started_at.strftime(TIMESTAMP_FORMAT) if started_at else "Window boundary",
        "ended_at": "",
        "duration_seconds": None,
        "version": "",
        "path": "",
        "pid": "",
        "host": "",
        "pending_count": None,
        "no_work": False,
        "terminal": False,
        "terminal_failed": False,
        "partial": partial,
        "warnings": [],
        "errors": [],
        "notices": [],
        "events": [],
        "raw_lines": [],
        "stages_by_name": {name: _new_stage(name) for name in ("EL", "BF", "ME")},
        "active_stage": None,
    }
    _status_fields(run, "incomplete" if partial else "running")
    return run


def _append_unique(items: List[str], value: str) -> None:
    normalized = value.strip()
    if normalized and normalized not in items:
        items.append(normalized)


def _is_duplicate_event(run: Dict[str, Any], event: Dict[str, Any]) -> bool:
    if not event["message"] or event["timestamp"] is not None:
        return False
    for previous in reversed(run["events"]):
        if not previous["message"]:
            continue
        return previous["message"].strip() == event["message"].strip()
    return False


def _record_event(run: Dict[str, Any], event: Dict[str, Any]) -> None:
    run["raw_lines"].append(event["raw"])
    stage_name = run.get("active_stage")
    if stage_name:
        run["stages_by_name"][stage_name]["raw_lines"].append(event["raw"])

    if _is_duplicate_event(run, event):
        return
    run["events"].append(event)

    message = event["message"]
    level = event["level"]
    if level == "WARNING" or "MISSING IMAGES" in message.upper():
        _append_unique(run["warnings"], message)
    elif level == "ERROR":
        _append_unique(run["errors"], message)

    if not stage_name:
        return
    stage = run["stages_by_name"][stage_name]
    if message and not STAGE_START_RE.search(message) and not STAGE_END_RE.search(message):
        stage["diagnostics"].append(message)

    model_match = MODEL_RE.search(message)
    if model_match:
        stage["model"] = model_match.group(1)
    discovered_match = DISCOVERED_RE.search(message)
    if discovered_match:
        stage["discovered_count"] = int(discovered_match.group(1))
    processed_match = PROCESSED_TOTAL_RE.search(message)
    if processed_match:
        stage["processed_count"] = int(processed_match.group(1))
    saved_match = SAVED_TOTAL_RE.search(message)
    if saved_match:
        stage["saved_count"] = int(saved_match.group(1))

    asset_match = ASSET_RE.search(message)
    if asset_match:
        asset = {
            "qr": asset_match.group("qr"),
            "completeness": int(asset_match.group("completeness"))
            if asset_match.group("completeness")
            else None,
            "confidence": int(asset_match.group("confidence"))
            if asset_match.group("confidence")
            else None,
            "stage": stage_name,
        }
        if not any(item["qr"] == asset["qr"] for item in stage["assets"]):
            stage["assets"].append(asset)

    if level == "WARNING" or "MISSING IMAGES" in message.upper():
        _append_unique(stage["warnings"], message)
    elif level == "ERROR":
        _append_unique(stage["errors"], message)


def _finalize_stage(stage: Dict[str, Any], fallback_status: str = "incomplete") -> None:
    if not stage["has_activity"]:
        _status_fields(stage, "not_run")
        return
    if stage["exit_code"] not in (None, 0):
        _status_fields(stage, "failed")
    elif stage["ended_at_dt"] is None:
        _status_fields(stage, fallback_status)
    elif stage["warnings"] or stage["errors"]:
        _status_fields(stage, "attention")
    elif stage["discovered_count"] == 0 or any(
        "no " in message.lower() and "assets found" in message.lower()
        for message in stage["diagnostics"]
    ):
        _status_fields(stage, "no_work")
    else:
        _status_fields(stage, "success")


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "-"
    minutes, remainder = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def _finalize_run(run: Dict[str, Any], reason: str) -> Dict[str, Any]:
    fallback_stage_status = "running" if reason == "eof" and not run["terminal"] else "incomplete"
    for stage in run["stages_by_name"].values():
        _finalize_stage(stage, fallback_stage_status)
        stage["duration_display"] = _format_duration(stage["duration_seconds"])

    if run["terminal_failed"] or any(
        stage["status"] == "failed" for stage in run["stages_by_name"].values()
    ):
        _status_fields(run, "failed")
    elif reason == "eof" and not run["terminal"]:
        _status_fields(run, "running")
    elif not run["terminal"]:
        _status_fields(run, "incomplete")
    elif run["no_work"]:
        _status_fields(run, "no_work")
    elif run["warnings"] or run["errors"]:
        _status_fields(run, "attention")
    else:
        _status_fields(run, "success")

    if run["started_at_dt"] and run["ended_at_dt"]:
        run["duration_seconds"] = max(
            0, int((run["ended_at_dt"] - run["started_at_dt"]).total_seconds())
        )
    run["duration_display"] = _format_duration(run["duration_seconds"])
    run["ended_at"] = (
        run["ended_at_dt"].strftime(TIMESTAMP_FORMAT) if run["ended_at_dt"] else ""
    )

    run["stages"] = [run["stages_by_name"][name] for name in ("EL", "BF", "ME")]
    run["assets"] = [asset for stage in run["stages"] for asset in stage["assets"]]
    run["processed_count"] = len(run["assets"])
    if not run["processed_count"]:
        run["processed_count"] = sum(stage["processed_count"] or 0 for stage in run["stages"])
    run["saved_count"] = sum(stage["saved_count"] or 0 for stage in run["stages"])
    run["warning_count"] = len(run["warnings"])
    run["failure_count"] = sum(stage["status"] == "failed" for stage in run["stages"])
    run["raw_text"] = "\n".join(run["raw_lines"]).rstrip()
    start_key = run["started_at_dt"].strftime("%Y%m%dT%H%M%S") if run["started_at_dt"] else "partial"
    run["id"] = f"run-{start_key}-{run['pid'] or 'unknown'}"
    run["search_text"] = " ".join(
        [
            run["raw_text"],
            run["host"],
            run["pid"],
            run["version"],
            *(stage["model"] for stage in run["stages"]),
            *(asset["qr"] for asset in run["assets"]),
        ]
    ).lower()
    run.pop("active_stage", None)
    return run


def _parse_lines(lines: Iterable[str]) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for raw_line in lines:
        event = _event(raw_line)
        message = event["message"]
        run_start = RUN_START_RE.search(message)
        if run_start:
            if current is not None:
                runs.append(_finalize_run(current, "next_run"))
            current = _new_run(event)
            current.update(run_start.groupdict())
            _record_event(current, event)
            continue

        if "Previous ai_check run still active" in message and "Skipping this cycle" in message:
            if current is not None:
                _record_event(current, event)
                _append_unique(current["notices"], message)
            continue

        stage_start = STAGE_START_RE.search(message)
        if stage_start:
            if current is None:
                current = _new_run(event, partial=True)
            stage_name = stage_start.group(1)
            current["active_stage"] = stage_name
            stage = current["stages_by_name"][stage_name]
            stage["has_activity"] = True
            stage["started_at_dt"] = event["timestamp"]
            stage["started_at"] = (
                event["timestamp"].strftime(TIMESTAMP_FORMAT) if event["timestamp"] else ""
            )
            _record_event(current, event)
            continue

        if current is None:
            continue

        _record_event(current, event)
        if any(marker in message for marker in WRAPPER_FAILURE_MARKERS):
            current["terminal_failed"] = True
        if current["started_at_dt"] is None and event["timestamp"]:
            current["started_at_dt"] = event["timestamp"]
            current["started_at"] = event["timestamp"].strftime(TIMESTAMP_FORMAT)

        pending_match = PENDING_RE.search(message)
        if pending_match:
            current["pending_count"] = int(pending_match.group(1))

        stage_end = STAGE_END_RE.search(message)
        if stage_end:
            stage_name = stage_end.group(1)
            stage = current["stages_by_name"][stage_name]
            stage["has_activity"] = True
            stage["exit_code"] = int(stage_end.group("exit"))
            stage["duration_seconds"] = int(stage_end.group("duration"))
            stage["ended_at_dt"] = event["timestamp"]
            stage["ended_at"] = (
                event["timestamp"].strftime(TIMESTAMP_FORMAT) if event["timestamp"] else ""
            )
            current["active_stage"] = None

        lowered = message.lower()
        if "no pending ai_status=0 items" in lowered:
            current["no_work"] = True
            current["terminal"] = True
            current["ended_at_dt"] = event["timestamp"]
            runs.append(_finalize_run(current, "terminal"))
            current = None
        elif "routine completed with one or more failed stages" in lowered:
            current["terminal"] = True
            current["terminal_failed"] = True
            current["ended_at_dt"] = event["timestamp"]
            runs.append(_finalize_run(current, "terminal"))
            current = None
        elif message.strip() == "Routine completed.":
            current["terminal"] = True
            current["ended_at_dt"] = event["timestamp"]
            runs.append(_finalize_run(current, "terminal"))
            current = None

    if current is not None:
        runs.append(_finalize_run(current, "eof"))
    return runs


def parse_ai_check_log(
    path: Path | str,
    *,
    hours: int = 72,
    now: Optional[datetime] = None,
    status_filter: str = "",
    query: str = "",
    page: int = 1,
    per_page: int = 20,
) -> Dict[str, Any]:
    """Parse and filter the AI Check log for presentation."""

    generated_at = now or datetime.now()
    cutoff = generated_at - timedelta(hours=max(1, hours))
    log_path = Path(path)
    def recent_lines(lines: Iterable[str]) -> Iterable[str]:
        inside_window = False
        for line in lines:
            timestamp = _event(line)["timestamp"]
            if timestamp is not None:
                inside_window = timestamp >= cutoff
            if inside_window:
                yield line

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        parsed_runs = _parse_lines(recent_lines(handle))

    window_runs = [
        run
        for run in parsed_runs
        if (run["ended_at_dt"] or run["started_at_dt"] or generated_at) >= cutoff
    ]
    window_runs.sort(
        key=lambda run: run["started_at_dt"] or datetime.min,
        reverse=True,
    )

    summary = {
        "routine_count": len(window_runs),
        "processed_count": sum(run["processed_count"] for run in window_runs),
        "warning_count": sum(run["warning_count"] for run in window_runs),
        "failure_count": sum(run["status"] == "failed" for run in window_runs),
    }
    status_counts = {key: 0 for key in STATUS_META if key != "not_run"}
    for run in window_runs:
        status_counts[run["status"]] += 1
    summary["status_counts"] = status_counts

    normalized_status = status_filter if status_filter in status_counts else ""
    normalized_query = query.strip().lower()
    filtered = [
        run
        for run in window_runs
        if (not normalized_status or run["status"] == normalized_status)
        and (not normalized_query or normalized_query in run["search_text"])
    ]

    page_size = max(1, min(100, per_page))
    pages = max(1, math.ceil(len(filtered) / page_size))
    current_page = max(1, min(page, pages))
    start = (current_page - 1) * page_size
    visible = filtered[start : start + page_size]

    return {
        "generated_at": generated_at,
        "cutoff": cutoff,
        "window_hours": hours,
        "summary": summary,
        "runs": visible,
        "total_filtered": len(filtered),
        "page": current_page,
        "pages": pages,
        "per_page": page_size,
        "has_previous": current_page > 1,
        "has_next": current_page < pages,
        "status_filter": normalized_status,
        "query": query.strip(),
        "status_options": [
            {"value": key, "label": STATUS_META[key][0], "count": status_counts[key]}
            for key in ("attention", "failed", "success", "no_work", "running", "incomplete")
        ],
    }
