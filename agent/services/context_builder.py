"""Build the small, non-analytical context used at Graph entry.

The bootstrap node is deliberately a boundary between a run caller and the
Multi-Agent State.  It records run metadata, carries account metadata that is
already available to the caller, and creates empty orchestration containers.
It does not call a metrics Tool or infer a diagnosis.
"""

from __future__ import annotations

import copy
import inspect
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from state import MultiAgentState


DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_TASK = "完成当前账户的投放诊断并形成可审计的分析上下文"

_ACCOUNT_CONTEXT_SOURCE_KEYS = (
    "account_id",
    "account_name",
    "project_id",
    "project_name",
    "account_goal",
    "business_goal",
    "constraints",
    "data_source",
    "data_sources",
    "source_files",
    "timezone",
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "private_key",
    "credential",
    "cookie",
    "login",
)
_NON_BOOTSTRAP_KEY_PARTS = (
    "metric",
    "anomal",
    "finding",
    "diagnos",
    "recommend",
    "strategy",
    "evidence",
    "result",
    "summary",
    "trace",
)

_RESULT_FIELDS = (
    "data_analysis_result",
    "ads_diagnosis_result",
    "content_review_result",
    "strategy_result",
    "supervisor_decision",
    "final_report",
    "push_result",
)
_LIST_FIELDS = (
    "open_questions",
    "evidence",
    "route_trace",
    "tool_trace",
    "agent_trace",
    "errors",
)


class BootstrapContextError(ValueError):
    """Raised when the initial run context cannot satisfy the State contract."""


AccountContextLoader = Callable[[MultiAgentState], Mapping[str, Any]]
RunIdFactory = Callable[[], str]
Clock = Callable[[], date | datetime]


def _non_empty_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BootstrapContextError(f"{field_name} cannot be empty")
    return text


def _new_run_id() -> str:
    return "run_" + uuid.uuid4().hex


def _normalise_report_date(
    value: Any,
    *,
    timezone: str,
    clock: Clock | None,
) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if clock is not None:
            current = clock()
        else:
            try:
                current = datetime.now(ZoneInfo(timezone))
            except Exception as exc:
                raise BootstrapContextError(
                    f"invalid bootstrap timezone: {timezone}"
                ) from exc
        if isinstance(current, datetime) and current.tzinfo is not None:
            current = current.astimezone(ZoneInfo(timezone))
        value = current - timedelta(days=1)

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        # Accept a timestamp from an existing run manifest, but keep the State
        # contract canonical at the date level.
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError as exc:
            raise BootstrapContextError(
                "report_date must be an ISO date or ISO timestamp"
            ) from exc


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _is_non_bootstrap_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _NON_BOOTSTRAP_KEY_PARTS)


def _copy_lightweight_value(value: Any, *, path: str) -> Any:
    """Copy JSON-like metadata and reject opaque heavyweight objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key) or _is_non_bootstrap_key(key):
                continue
            copied[str(key)] = _copy_lightweight_value(
                item,
                path=f"{path}.{key}",
            )
        return copied
    if isinstance(value, (list, tuple)):
        return [
            _copy_lightweight_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise BootstrapContextError(
        f"{path} must contain lightweight JSON-like metadata; "
        f"got {type(value).__name__}"
    )


def _context_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BootstrapContextError(f"{field_name} must be a mapping")
    copied = _copy_lightweight_value(value, path=field_name)
    assert isinstance(copied, dict)
    return copied


def _invoke_context_loader(
    loader: AccountContextLoader | Callable[[], Mapping[str, Any]],
    state: MultiAgentState,
) -> Mapping[str, Any]:
    """Support both state-aware and zero-argument test/runtime loaders."""
    if isinstance(loader, Mapping):
        return loader
    if not callable(loader):
        raise BootstrapContextError("account_context_loader must be callable")

    try:
        parameters = inspect.signature(loader).parameters.values()
        accepts_positional = any(
            parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.VAR_POSITIONAL)
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_positional = True

    loaded = loader(state) if accepts_positional else loader()
    if not isinstance(loaded, Mapping):
        raise BootstrapContextError("account_context_loader must return a mapping")
    return loaded


def _existing_list(state: Mapping[str, Any], field_name: str) -> list[Any]:
    value = state.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise BootstrapContextError(f"State field '{field_name}' must be a list")
    return copy.deepcopy(value)


def _existing_call_counts(state: Mapping[str, Any]) -> dict[str, int]:
    value = state.get("agent_call_counts")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BootstrapContextError("State field 'agent_call_counts' must be a mapping")

    counts: dict[str, int] = {}
    for name, count in value.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise BootstrapContextError(
                f"State field 'agent_call_counts[{name}]' must be a non-negative integer"
            )
        counts[str(name)] = count
    return counts


def _existing_total_calls(state: Mapping[str, Any]) -> int:
    value = state.get("total_agent_calls", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BootstrapContextError(
            "State field 'total_agent_calls' must be a non-negative integer"
        )
    return value


@dataclass
class ContextBuilder:
    """Create a LangGraph-compatible bootstrap update.

    Explicit builder values override the same fields in the incoming State.
    Existing State values are retained so the node remains safe for a
    checkpointer resume; a fresh State receives empty result, question,
    evidence, counter, and trace slots.
    """

    run_id: str | None = None
    report_date: str | date | datetime | None = None
    task: str | None = None
    account_context: Mapping[str, Any] | None = None
    account_context_loader: AccountContextLoader | Callable[[], Mapping[str, Any]] | None = None
    timezone: str = DEFAULT_TIMEZONE
    run_id_factory: RunIdFactory = _new_run_id
    clock: Clock | None = None
    default_task: str = DEFAULT_TASK

    def _account_context(self, state: MultiAgentState) -> dict[str, Any]:
        context = _context_mapping(state.get("account_context"), field_name="account_context")

        # A loader is a metadata seam only.  The bootstrap node never calls a
        # data Tool; callers can use it to supply account identity or source
        # descriptors already known by the run entry point.
        if self.account_context_loader is not None:
            loaded = _invoke_context_loader(self.account_context_loader, state)
            context.update(_context_mapping(loaded, field_name="loaded account_context"))

        if self.account_context is not None:
            context.update(
                _context_mapping(self.account_context, field_name="account_context override")
            )

        # Accommodate the common run-entry shape where account identity is a
        # top-level input, while keeping it inside the State contract.
        for key in _ACCOUNT_CONTEXT_SOURCE_KEYS:
            if key in state and key not in context and state[key] is not None:
                context[key] = _copy_lightweight_value(
                    state[key],
                    path=f"state.{key}",
                )
        return context

    def build(self, state: MultiAgentState) -> dict[str, Any]:
        """Return the initial State update without mutating ``state``."""
        if not isinstance(state, Mapping):
            raise TypeError("bootstrap_context state must be a mapping")

        raw_run_id = self.run_id if self.run_id is not None else state.get("run_id")
        run_id = (
            _non_empty_text(raw_run_id, field_name="run_id")
            if raw_run_id is not None and str(raw_run_id).strip()
            else _non_empty_text(self.run_id_factory(), field_name="run_id")
        )

        raw_report_date = self.report_date
        if raw_report_date is None:
            raw_report_date = state.get("report_date")
        if raw_report_date is None:
            raw_report_date = state.get("data_date")
        report_date = _normalise_report_date(
            raw_report_date,
            timezone=self.timezone,
            clock=self.clock,
        )

        raw_task = self.task if self.task is not None else state.get("task")
        if raw_task is None or not str(raw_task).strip():
            raw_task = self.default_task
        task = _non_empty_text(raw_task, field_name="task")

        update: dict[str, Any] = {
            "run_id": run_id,
            "report_date": report_date,
            "task": task,
            "account_context": self._account_context(state),
            "agent_call_counts": _existing_call_counts(state),
            "total_agent_calls": _existing_total_calls(state),
        }

        for field_name in _RESULT_FIELDS:
            update[field_name] = copy.deepcopy(state.get(field_name))
        for field_name in _LIST_FIELDS:
            update[field_name] = _existing_list(state, field_name)
        return update

    __call__ = build


def bootstrap_context(
    state: MultiAgentState,
    *,
    run_id: str | None = None,
    report_date: str | date | datetime | None = None,
    task: str | None = None,
    account_context: Mapping[str, Any] | None = None,
    account_context_loader: AccountContextLoader
    | Callable[[], Mapping[str, Any]]
    | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    run_id_factory: RunIdFactory = _new_run_id,
    clock: Clock | None = None,
    default_task: str = DEFAULT_TASK,
) -> dict[str, Any]:
    """Functional bootstrap node used directly by the Graph or tests."""
    return ContextBuilder(
        run_id=run_id,
        report_date=report_date,
        task=task,
        account_context=account_context,
        account_context_loader=account_context_loader,
        timezone=timezone,
        run_id_factory=run_id_factory,
        clock=clock,
        default_task=default_task,
    ).build(state)


def make_bootstrap_context(**kwargs: Any) -> ContextBuilder:
    """Create a configured callable node for a runtime Graph."""
    return ContextBuilder(**kwargs)


build_context = bootstrap_context
create_bootstrap_context = make_bootstrap_context


__all__ = [
    "AccountContextLoader",
    "BootstrapContextError",
    "Clock",
    "ContextBuilder",
    "DEFAULT_TASK",
    "DEFAULT_TIMEZONE",
    "RunIdFactory",
    "bootstrap_context",
    "build_context",
    "create_bootstrap_context",
    "make_bootstrap_context",
]
