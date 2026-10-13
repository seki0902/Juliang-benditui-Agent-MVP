"""Safe, bounded Tool-call tracing for specialist Agents.

Tool results are needed in full by the model, but the shared State and its
checkpoints are an audit surface.  This module builds the latter from a
small, redacted summary so API credentials, login material, and large raw
report payloads do not enter ``tool_trace``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import re
from typing import Any

from pydantic import BaseModel

from schemas.common import AgentName, ToolCallRecord


REDACTED_VALUE = "[REDACTED]"
MAX_TRACE_STRING_LENGTH = 256
MAX_TRACE_ITEMS = 20
MAX_TRACE_DEPTH = 4

_SENSITIVE_KEY_PARTS = frozenset(
    {
        "access_key",
        "access_token",
        "auth",
        "auth_header",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "browser_context",
        "client_secret",
        "login_state",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "storage_state",
        "token",
    }
)
_SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|"
    r"password|client[_-]?secret|private[_-]?key|secret|credential|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")


def _normalise_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def is_sensitive_key(key: Any) -> bool:
    """Return whether a mapping key should never expose its value in trace."""

    normalised = _normalise_key(key)
    if normalised in _SENSITIVE_KEY_PARTS:
        return True
    parts = {part for part in normalised.split("_") if part}
    if parts & _SENSITIVE_KEY_PARTS:
        return True
    return any(normalised.endswith(f"_{suffix}") for suffix in _SENSITIVE_KEY_PARTS)


def _short_text(value: Any) -> str:
    text = _SENSITIVE_TEXT_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED_VALUE}",
        str(value),
    )
    text = _BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTED_VALUE}",
        text,
    )
    if len(text) <= MAX_TRACE_STRING_LENGTH:
        return text
    return f"{text[:MAX_TRACE_STRING_LENGTH - 20]}... [truncated]"


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively make a small JSON-safe value for a trace payload."""

    if depth >= MAX_TRACE_DEPTH:
        if isinstance(value, Mapping):
            return {"type": "object", "truncated": True}
        if isinstance(value, (list, tuple, set)):
            return {"type": "array", "truncated": True}
        return _short_text(value)

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _short_text(value)
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}

    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, raw_value in items[:MAX_TRACE_ITEMS]:
            key = _short_text(raw_key)
            safe[key] = (
                REDACTED_VALUE
                if is_sensitive_key(raw_key)
                else _safe_value(raw_value, depth=depth + 1)
            )
        if len(items) > MAX_TRACE_ITEMS:
            safe["_truncated_fields"] = len(items) - MAX_TRACE_ITEMS
        return safe

    if isinstance(value, (list, tuple, set)):
        values = list(value)
        safe_values = [_safe_value(item, depth=depth + 1) for item in values[:MAX_TRACE_ITEMS]]
        if len(values) > MAX_TRACE_ITEMS:
            safe_values.append({"_truncated_items": len(values) - MAX_TRACE_ITEMS})
        return safe_values

    return _short_text(value)


def sanitize_trace_value(value: Any) -> Any:
    """Return a bounded, recursively redacted value suitable for State."""

    return _safe_value(value)


def sanitize_trace_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Redact credentials and bound Tool input arguments."""

    safe = sanitize_trace_value(dict(arguments or {}))
    return safe if isinstance(safe, dict) else {"value": safe}


def _shape_summary(value: Any) -> Any:
    """Describe a Tool data payload without copying its rows or content."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if value is None:
        return None
    if isinstance(value, Mapping):
        keys = [
            _short_text(key)
            for key in list(value)[:MAX_TRACE_ITEMS]
            if not is_sensitive_key(key)
        ]
        summary: dict[str, Any] = {
            "type": "object",
            "field_count": len(value),
            "fields": keys,
        }
        if len(value) > MAX_TRACE_ITEMS:
            summary["truncated_fields"] = len(value) - MAX_TRACE_ITEMS
        return summary
    if isinstance(value, (list, tuple, set)):
        return {"type": "array", "item_count": len(value)}
    if isinstance(value, (str, bool, int, float)):
        return {"type": type(value).__name__, "value": _short_text(value)}
    return {"type": type(value).__name__}


def summarize_tool_result(result: Any) -> dict[str, Any]:
    """Keep protocol metadata and a structural summary, never full raw data."""

    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    if not isinstance(result, Mapping):
        return {
            "status": "ok",
            "result": _shape_summary(result),
        }

    summary: dict[str, Any] = {
        "status": _short_text(result.get("status", "ok")),
        "source": sanitize_trace_value(result.get("source")),
        "time_window": sanitize_trace_value(result.get("time_window")),
        "data": _shape_summary(result.get("data")),
    }
    if "warnings" in result:
        summary["warnings"] = sanitize_trace_value(result.get("warnings"))
    if "error_type" in result:
        summary["error_type"] = _short_text(result.get("error_type"))
    if "status_category" in result:
        summary["status_category"] = _short_text(result.get("status_category"))
    return summary


def safe_tool_error(tool_name: str, result: Mapping[str, Any]) -> str | None:
    """Extract a bounded error for State.errors without copying secret text."""

    if str(result.get("status") or "ok") != "error":
        return None
    warnings = result.get("warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    warning_text = "; ".join(_short_text(item) for item in list(warnings)[:3])
    if not warning_text:
        warning_text = "Tool returned status=error"
    safe_name = _short_text(tool_name)
    return f"{safe_name}: {sanitize_trace_value(warning_text)}"


def make_tool_call_record(
    *,
    agent: AgentName | str,
    tool_name: str,
    call_id: str | None,
    arguments: Mapping[str, Any] | None,
    status: str,
    result: Any,
    duration_ms: float,
    error: str | None = None,
) -> ToolCallRecord:
    """Build the canonical safe record written to ``MultiAgentState``."""

    summary = summarize_tool_result(result)
    safe_error = sanitize_trace_value(error) if error else None
    return ToolCallRecord(
        agent=agent,
        tool_name=_short_text(tool_name),
        call_id=_short_text(call_id) if call_id is not None else None,
        status=_short_text(status),
        arguments=sanitize_trace_arguments(arguments),
        duration_ms=max(0.0, float(duration_ms)),
        timestamp=datetime.now(timezone.utc).isoformat(),
        result=summary,
        result_summary=summary,
        error=safe_error,
    )


def json_trace_payload(record: ToolCallRecord) -> str:
    """Serialize a record for local JSON/JSONL sinks without unsafe defaults."""

    return json.dumps(
        sanitize_trace_value(record.model_dump(mode="json")),
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "MAX_TRACE_DEPTH",
    "MAX_TRACE_ITEMS",
    "MAX_TRACE_STRING_LENGTH",
    "REDACTED_VALUE",
    "is_sensitive_key",
    "json_trace_payload",
    "make_tool_call_record",
    "safe_tool_error",
    "sanitize_trace_arguments",
    "sanitize_trace_value",
    "summarize_tool_result",
]
