"""Shared Agent-facing return protocol for all seven data Tools."""

from __future__ import annotations

import json

from collections.abc import Mapping
from functools import wraps
from inspect import Signature, signature
from typing import Any, Callable, ParamSpec, TypeVar


from langchain_core.messages import AIMessage
from pydantic import BaseModel


REQUIRED_TOOL_KEYS = frozenset(
    {"status", "source", "time_window", "data", "warnings"}
)
ALLOWED_STATUSES = frozenset({"ok", "partial", "unavailable", "not_found", "error"})

_METRIC_DATA_FIELDS = {
    "cost",
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "conversions",
    "leads",
    "cvr",
    "lead_conversion_rate",
    "cpl",
    "lead_cost",
    "valid_leads",
    "valid_lead_rate",
    "valid_cpl",
    "valid_lead_cost",
    "contacted_leads",
    "contact_cost",
}
_CONTENT_DATA_FIELDS = {
    "title",
    "script",
    "transcript",
    "caption",
    "hook",
    "selling_points",
    "cta",
    "tags",
}

P = ParamSpec("P")
R = TypeVar("R")


def json_safe(value: Any) -> Any:
    """Convert state and Tool payloads into values safe for model messages."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def as_ai_message(response: Any) -> Any:
    """Keep LangChain messages intact and adapt simple fake/provider mappings."""
    if isinstance(response, Mapping):
        return AIMessage(
            content=response.get("content", ""),
            tool_calls=list(response.get("tool_calls") or []),
        )
    return response


def raw_tool_calls(response: Any) -> list[Any]:
    calls = getattr(response, "tool_calls", None)
    if calls is None and isinstance(response, Mapping):
        calls = response.get("tool_calls")
    if calls is None:
        additional_kwargs = getattr(response, "additional_kwargs", {})
        if isinstance(additional_kwargs, Mapping):
            calls = additional_kwargs.get("tool_calls")
    return list(calls or [])


def invoke_tool(tool: Any, arguments: dict[str, Any]) -> Any:
    if hasattr(tool, "invoke") and callable(tool.invoke):
        return tool.invoke(arguments)
    if callable(tool):
        return tool(**arguments)
    raise TypeError("runtime Tool is neither an invokable Runnable nor a callable")


def _status_from_completeness(result: Mapping[str, Any], default: str) -> str:
    completeness = result.get("data_completeness")
    if not isinstance(completeness, Mapping):
        return default
    value = str(completeness.get("status") or "").strip().lower()
    if value in {"available", "complete", "ok"}:
        return "ok"
    if value == "not_found":
        return "not_found"
    if value.startswith("partial") or value.startswith("missing"):
        return "partial"
    if value in {"unavailable", "no_data", "no_data_in_window"}:
        return "unavailable"
    return default


def canonical_status(status: Any, default: str = "ok") -> str:
    """Map legacy completeness/status labels to the shared status vocabulary."""
    value = str(status or "").strip().lower()
    if value in ALLOWED_STATUSES:
        return value
    if value in {"no_data", "no_data_in_window"}:
        return "unavailable"
    if value == "not_observed":
        return "unavailable"
    if value.startswith("partial") or value.startswith("missing"):
        return "partial"
    return default


def _derive_data(result: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(result.get("metrics"), Mapping):
        return dict(result["metrics"])
    history_fields = ("current_window", "history_window", "trend")
    if any(field in result for field in history_fields):
        return {field: result[field] for field in history_fields if field in result}
    content = {
        field: result[field]
        for field in _CONTENT_DATA_FIELDS
        if field in result
    }
    if content:
        return content
    return {
        field: result[field]
        for field in _METRIC_DATA_FIELDS
        if field in result
    }


def normalise_tool_result(
    result: Any,
    *,
    default_status: str = "ok",
    default_source: str | None = None,
    default_time_window: Any = None,
) -> dict[str, Any]:
    """Add the common protocol while preserving tool-specific result fields."""
    if isinstance(result, Mapping):
        normalised = dict(result)
    else:
        normalised = {"data": {"raw_result": result}}

    if "status" not in normalised or normalised["status"] in (None, ""):
        normalised["status"] = _status_from_completeness(normalised, default_status)
    if "source" not in normalised:
        normalised["source"] = default_source
    if "time_window" not in normalised:
        normalised["time_window"] = default_time_window
    if "data" not in normalised or normalised["data"] is None:
        normalised["data"] = _derive_data(normalised)
    elif not isinstance(normalised["data"], Mapping):
        normalised["data"] = {"value": normalised["data"]}

    warnings = normalised.get("warnings")
    if warnings is None:
        normalised["warnings"] = []
    elif isinstance(warnings, str):
        normalised["warnings"] = [warnings]
    else:
        normalised["warnings"] = list(warnings)

    status = normalised["status"]
    if status not in ALLOWED_STATUSES:
        normalised.setdefault("status_category", canonical_status(status))
    return normalised


def _bound_arguments(call_signature: Signature, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        return dict(call_signature.bind_partial(*args, **kwargs).arguments)
    except TypeError:
        return {}


def unexecuted_tool_result() -> dict[str, Any]:
    """Reply explicitly to requests skipped because the Agent exhausted its budget."""
    return {
        "status": "unavailable",
        "source": "runtime_tool_budget",
        "time_window": None,
        "data": {},
        "warnings": ["Tool request was not executed because the node reached its Tool limit."],
    }


def error_tool_result(
    tool_name: str,
    exc: Exception,
    *,
    source: str | None = None,
    time_window: Any = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "source": source,
        "time_window": time_window,
        "data": {},
        "warnings": [f"{tool_name} 调用失败：{type(exc).__name__}: {exc}"],
        "error_type": type(exc).__name__,
    }


def tool_protocol(
    *,
    tool_name: str | None = None,
    default_source: str | None = None,
    time_window_arg: str | None = "time_window",
    default_time_window: Any = None,
) -> Callable[[Callable[P, R]], Callable[P, dict[str, Any]]]:
    """Decorate a Tool entry point with stable fields and exception handling."""

    def decorate(function: Callable[P, R]) -> Callable[P, dict[str, Any]]:
        call_signature = signature(function)
        name = tool_name or function.__name__

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
            arguments = _bound_arguments(call_signature, args, kwargs)
            call_window = (
                arguments.get(time_window_arg)
                if time_window_arg is not None
                else None
            )
            if call_window is None:
                call_window = default_time_window
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                return error_tool_result(
                    name,
                    exc,
                    source=default_source,
                    time_window=call_window,
                )
            return normalise_tool_result(
                result,
                default_source=default_source,
                default_time_window=call_window,
            )

        return wrapped

    return decorate
