"""Typed state contract for the Multi-Agent graph.

State stores known facts, structured Agent results, open questions, and
execution-independent traces. It does not store ad execution status or
backend mutation parameters.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypedDict

from pydantic import BaseModel

from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.common import (
    AgentTraceRecord,
    EvidenceItem,
    OpenQuestion,
    RouteTraceRecord,
    ToolCallRecord,
)
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.strategy import StrategyResult
from schemas.supervisor import SupervisorDecision


class MultiAgentState(TypedDict, total=False):
    """Shared state passed between Supervisor and specialist Agents."""

    run_id: str
    report_date: str
    task: str
    account_context: dict[str, Any]

    # Agent results: four specialist outputs plus the Supervisor decision.
    data_analysis_result: DataAnalysisResult | None
    ads_diagnosis_result: AdsDiagnosisResult | None
    content_review_result: ContentReviewResult | None
    strategy_result: StrategyResult | None
    supervisor_decision: SupervisorDecision | None

    # Shared unresolved context and evidence.
    open_questions: list[OpenQuestion]
    evidence: list[EvidenceItem]

    # Orchestration limits and auditable traces.
    agent_call_counts: dict[str, int]
    total_agent_calls: int
    route_trace: list[RouteTraceRecord]
    tool_trace: list[ToolCallRecord]
    agent_trace: list[AgentTraceRecord]
    errors: list[str]

    # Human-facing output and notification result; no ad execution state.
    final_report: str | None
    push_result: dict[str, Any] | None


APPEND_FIELDS = frozenset(
    {
        "open_questions",
        "evidence",
        "route_trace",
        "tool_trace",
        "agent_trace",
        "errors",
    }
)

OVERWRITE_FIELDS = frozenset(
    {
        "run_id",
        "report_date",
        "task",
        "account_context",
        "data_analysis_result",
        "ads_diagnosis_result",
        "content_review_result",
        "strategy_result",
        "supervisor_decision",
        "agent_call_counts",
        "total_agent_calls",
        "final_report",
        "push_result",
    }
)

STATE_FIELDS = APPEND_FIELDS | OVERWRITE_FIELDS


def _stable_item_key(item: Any) -> str:
    """Return a stable key for exact duplicate suppression in append fields."""
    payload = item.model_dump(mode="json") if isinstance(item, BaseModel) else item
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return repr(payload)


def _append_unique(existing: list[Any], additions: list[Any]) -> list[Any]:
    merged = list(existing)
    seen = {_stable_item_key(item) for item in merged}
    for item in additions:
        key = _stable_item_key(item)
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def apply_state_update(
    state: MultiAgentState,
    update: Mapping[str, Any],
) -> MultiAgentState:
    """Apply one node update using explicit overwrite/append semantics.

    Result slots and scalar context are overwritten by the newest update.
    Shared lists are appended in order, while exact duplicate entries are
    ignored so repeated node writes do not duplicate history.
    """
    unknown_fields = set(update) - STATE_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise KeyError(f"Unknown MultiAgentState field(s): {names}")

    next_state: MultiAgentState = dict(state)

    for field in APPEND_FIELDS:
        if field not in update:
            continue
        additions = update[field]
        if not isinstance(additions, list):
            raise TypeError(f"State append field '{field}' must receive a list")
        existing = next_state.get(field, [])
        if not isinstance(existing, list):
            raise TypeError(f"State append field '{field}' must contain a list")
        next_state[field] = _append_unique(existing, additions)

    for field in OVERWRITE_FIELDS:
        if field in update:
            next_state[field] = update[field]

    return next_state
