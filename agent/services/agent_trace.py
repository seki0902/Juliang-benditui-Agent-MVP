"""Safe, local Agent-node tracing.

The Graph owns the lifecycle boundary, while this module owns the shape of
the audit record.  Agent input and output are deliberately summarized rather
than copied into State: a trace should show what kind of context was present
and which schema was produced without becoming a second prompt or report
store.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from typing import Any

from pydantic import BaseModel

from schemas.common import AgentTraceRecord
from services.tool_trace import is_sensitive_key, sanitize_trace_value


_RESULT_FIELDS = (
    "data_analysis_result",
    "ads_diagnosis_result",
    "content_review_result",
    "strategy_result",
    "supervisor_decision",
)
_COLLECTION_FIELDS = (
    "open_questions",
    "evidence",
    "route_trace",
    "tool_trace",
    "agent_trace",
    "errors",
)
_TOKEN_USAGE_KEYS = frozenset(
    {
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "total_tokens",
    }
)


def _field_names(value: Any) -> list[str]:
    if isinstance(value, BaseModel):
        fields = getattr(type(value), "model_fields", {})
        return sorted(str(name) for name in fields)
    if isinstance(value, Mapping):
        return sorted(
            str(key)
            for key in value
            if not is_sensitive_key(key)
        )[:50]
    return []


def _schema_descriptor(value: Any) -> dict[str, Any]:
    if value is None:
        return {"present": False}
    if isinstance(value, BaseModel):
        return {
            "present": True,
            "schema": type(value).__name__,
            "fields": _field_names(value),
        }
    if isinstance(value, Mapping):
        return {
            "present": True,
            "schema": "mapping",
            "fields": _field_names(value),
        }
    return {
        "present": True,
        "schema": type(value).__name__,
    }


def summarize_agent_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded summary of State suitable for an audit record."""

    if not isinstance(state, Mapping):
        return {"state_type": type(state).__name__, "fields": []}

    account_context = state.get("account_context")
    account_keys = (
        sorted(
            str(key)
            for key in account_context
            if not is_sensitive_key(key)
        )[:50]
        if isinstance(account_context, Mapping)
        else []
    )
    collections: dict[str, int] = {}
    for field in _COLLECTION_FIELDS:
        value = state.get(field)
        if isinstance(value, (list, tuple, set)):
            collections[field] = len(value)

    result_slots = {
        field: _schema_descriptor(state.get(field))
        for field in _RESULT_FIELDS
    }
    counters = {
        "agent_call_counts": sanitize_trace_value(state.get("agent_call_counts", {})),
        "total_agent_calls": state.get("total_agent_calls", 0),
    }
    return {
        "fields": sorted(str(field) for field in state),
        "run_id": sanitize_trace_value(state.get("run_id")),
        "report_date": sanitize_trace_value(state.get("report_date")),
        "task": sanitize_trace_value(state.get("task")),
        "account_context_keys": account_keys,
        "result_slots": result_slots,
        "collection_counts": collections,
        "counters": counters,
    }


def summarize_agent_output(update: Any) -> dict[str, Any]:
    """Describe an Agent update without copying result values."""

    if not isinstance(update, Mapping):
        return {
            "update_type": type(update).__name__,
            "fields": [],
        }

    result_fields = [field for field in _RESULT_FIELDS if field in update]
    return {
        "fields": sorted(str(field) for field in update),
        "result_schemas": {
            field: _schema_descriptor(update.get(field))
            for field in result_fields
        },
    }


def _normalise_token_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    selected: dict[str, Any] = {}
    for key, raw_value in value.items():
        name = str(key)
        if name in _TOKEN_USAGE_KEYS and isinstance(raw_value, (int, float)):
            selected[name] = raw_value
    return selected or None


def extract_token_usage(source: Any) -> dict[str, Any] | None:
    """Read token counts only when a provider/runtime exposes them."""

    candidates = [source, getattr(source, "model", None)]
    bound_self = getattr(source, "__self__", None)
    if bound_self is not None:
        candidates.append(bound_self)
        candidates.append(getattr(bound_self, "model", None))

    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, Mapping):
            for key in ("token_usage", "usage_metadata", "response_metadata"):
                usage = _normalise_token_usage(candidate.get(key))
                if usage:
                    return usage
            continue
        for attribute in (
            "token_usage",
            "last_token_usage",
            "usage_metadata",
            "response_metadata",
        ):
            usage = _normalise_token_usage(getattr(candidate, attribute, None))
            if usage:
                return usage
    return None


def retry_count_from_error(error: BaseException | None) -> int:
    """Convert StructuredOutputError-style attempt metadata to retries."""

    attempts = getattr(error, "attempts", None)
    if isinstance(attempts, int) and not isinstance(attempts, bool):
        return max(0, attempts - 1)
    return 0


def extract_retry_count(source: Any) -> int:
    """Read successful structured-output retry metadata when available."""

    candidates = [source, getattr(source, "model", None)]
    bound_self = getattr(source, "__self__", None)
    if bound_self is not None:
        candidates.extend([bound_self, getattr(bound_self, "model", None)])

    for candidate in candidates:
        retries = getattr(candidate, "_agent_last_structured_retries", None)
        if isinstance(retries, int) and not isinstance(retries, bool):
            return max(0, retries)
    return 0


def make_agent_trace_record(
    *,
    agent: str,
    state: Mapping[str, Any],
    output: Any,
    status: str,
    duration_ms: float,
    retry_count: int = 0,
    token_usage: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> AgentTraceRecord:
    """Create the canonical bounded Agent trace record."""

    safe_error = sanitize_trace_value(error) if error else None
    safe_usage = _normalise_token_usage(token_usage)
    return AgentTraceRecord(
        agent=str(agent),
        run_id=(
            str(sanitize_trace_value(state.get("run_id")))
            if state.get("run_id") is not None
            else None
        ),
        input_state_summary=summarize_agent_state(state),
        output_schema=summarize_agent_output(output),
        status=str(status),
        duration_ms=max(0.0, float(duration_ms)),
        retry_count=max(0, int(retry_count)),
        token_usage=safe_usage,
        timestamp=datetime.now(timezone.utc).isoformat(),
        error=safe_error,
    )


def json_agent_trace_payload(record: AgentTraceRecord) -> str:
    """Serialize an Agent trace for a local JSON/JSONL sink."""

    return json.dumps(
        sanitize_trace_value(record.model_dump(mode="json")),
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "extract_retry_count",
    "extract_token_usage",
    "json_agent_trace_payload",
    "make_agent_trace_record",
    "retry_count_from_error",
    "summarize_agent_output",
    "summarize_agent_state",
]
