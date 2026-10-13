"""LangGraph orchestration flow for the Multi-Agent technical foundation.

This module wires node boundaries only.  It deliberately keeps business
routing out of Python: the Supervisor writes ``SupervisorDecision`` and this
Graph maps its finite ``next_agent`` value to a node.  The bootstrap boundary
is implemented locally; the runtime can inject later node callables, while
technical call limits and Supervisor route tracing stay in the orchestrator.
Default no-op implementations exist only for compile-time smoke tests before
the remaining runtime boundaries are connected; the deterministic report
renderer is enabled by default.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from agents.supervisor_agent import (
    DEFAULT_AGENT_CALL_LIMITS,
    DEFAULT_MAX_AGENT_CALLS,
    DEFAULT_MAX_TOTAL_CALLS,
)
from schemas.common import AgentName, OpenQuestion, RouteTraceRecord
from schemas.supervisor import SupervisorDecision
from services.agent_trace import (
    extract_retry_count,
    extract_token_usage,
    make_agent_trace_record,
    retry_count_from_error,
)
from services.context_builder import bootstrap_context
from services.report_renderer import report_renderer_node
from state import MultiAgentState, apply_state_update


GRAPH_NODE_NAMES = (
    "bootstrap_context",
    "supervisor",
    "data_analysis_agent",
    "ads_diagnosis_agent",
    "content_review_agent",
    "strategy_agent",
    "report_renderer",
    "push",
)

PROFESSIONAL_NODE_NAMES = (
    "data_analysis_agent",
    "ads_diagnosis_agent",
    "content_review_agent",
    "strategy_agent",
)

SUPERVISOR_ROUTE_MAP = {
    AgentName.DATA_ANALYSIS.value: "data_analysis_agent",
    AgentName.ADS_DIAGNOSIS.value: "ads_diagnosis_agent",
    AgentName.CONTENT_REVIEW.value: "content_review_agent",
    AgentName.STRATEGY.value: "strategy_agent",
    AgentName.FINISH.value: "report_renderer",
}
# ``route_from_supervisor`` returns a node name.  LangGraph's ``path_map``
# therefore needs node names as keys, rather than the enum values accepted by
# ``SupervisorDecision.next_agent``.
SUPERVISOR_PATH_MAP = {
    destination: destination for destination in SUPERVISOR_ROUTE_MAP.values()
}

GraphNode = Callable[[MultiAgentState], Mapping[str, Any]]


class GraphBuildError(ValueError):
    """Raised when Graph node injection is invalid."""


class GraphRoutingError(RuntimeError):
    """Raised when State cannot be mapped to a safe Graph destination."""


_ORCHESTRATOR_SAFETY_QUESTION = "调用限制或运行保护已触发，无法继续调度专业 Agent。"


@dataclass(frozen=True)
class GraphCallLimitConfig:
    """Technical call limits enforced by the Graph orchestrator."""

    agent_call_limits: dict[str, int]
    max_total_calls: int | None


_PROFESSIONAL_TARGETS = frozenset(DEFAULT_AGENT_CALL_LIMITS)


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphBuildError(f"{field_name} must be a non-negative integer")
    return value


def _normalise_call_limits(
    agent_call_limits: Mapping[AgentName | str, int] | None,
    max_total_calls: int | None,
) -> GraphCallLimitConfig:
    limits = dict(DEFAULT_AGENT_CALL_LIMITS)
    if agent_call_limits is not None:
        if not isinstance(agent_call_limits, Mapping):
            raise GraphBuildError("agent_call_limits must be a mapping")
        for raw_name, raw_limit in agent_call_limits.items():
            try:
                name = raw_name if isinstance(raw_name, AgentName) else AgentName(raw_name)
            except (TypeError, ValueError) as exc:
                raise GraphBuildError(
                    f"agent_call_limits contains an invalid AgentName: {raw_name}"
                ) from exc
            if name.value not in _PROFESSIONAL_TARGETS:
                raise GraphBuildError(
                    "agent_call_limits may only configure professional Agent targets"
                )
            limits[name.value] = _require_non_negative_int(
                raw_limit,
                field_name=f"agent_call_limits[{name.value}]",
            )

    if max_total_calls is not None:
        max_total_calls = _require_non_negative_int(
            max_total_calls,
            field_name="max_total_calls",
        )
    return GraphCallLimitConfig(
        agent_call_limits=limits,
        max_total_calls=max_total_calls,
    )


def _noop_node(_state: MultiAgentState) -> dict[str, Any]:
    """Compile-only placeholder; later phases replace it with real nodes."""
    return {}


def _read_call_state(state: MultiAgentState) -> tuple[dict[str, int], int]:
    if not isinstance(state, Mapping):
        raise GraphRoutingError("Graph State must be a mapping")

    raw_counts = state.get("agent_call_counts", {})
    if raw_counts is None:
        raw_counts = {}
    if not isinstance(raw_counts, Mapping):
        raise GraphRoutingError("State agent_call_counts must be a mapping")

    counts: dict[str, int] = {}
    for raw_name, raw_count in raw_counts.items():
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
            raise GraphRoutingError(
                f"State agent_call_counts[{raw_name}] must be a non-negative integer"
            )
        counts[str(raw_name.value if isinstance(raw_name, AgentName) else raw_name)] = raw_count

    total = state.get("total_agent_calls", 0)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GraphRoutingError("State total_agent_calls must be a non-negative integer")
    return counts, total


def _limit_warning(
    target: AgentName,
    *,
    counts: Mapping[str, int],
    total: int,
    limits: GraphCallLimitConfig,
) -> str | None:
    if target not in {
        AgentName.DATA_ANALYSIS,
        AgentName.ADS_DIAGNOSIS,
        AgentName.CONTENT_REVIEW,
        AgentName.STRATEGY,
    }:
        return None

    total_warning = _total_limit_warning(total, limits)
    if total_warning is not None:
        return total_warning

    current = counts.get(target.value, 0)
    limit = limits.agent_call_limits[target.value]
    if current >= limit:
        return (
            f"调用限制已达到：{target.value} 已使用 {current}/{limit} 次，"
            "无法继续调度该专业 Agent。"
        )
    return None


def _total_limit_warning(
    total: int,
    limits: GraphCallLimitConfig,
) -> str | None:
    if limits.max_total_calls is not None and total >= limits.max_total_calls:
        return (
            f"调用限制已达到：total_agent_calls={total}，"
            f"系统总专业 Agent 调用上限为 {limits.max_total_calls}。"
        )
    return None


def _normalise_node_update(
    state: MultiAgentState,
    raw_update: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw_update, Mapping):
        raise GraphRoutingError("Graph node must return a mapping State update")
    try:
        merged = apply_state_update(state, raw_update)
    except (KeyError, TypeError) as exc:
        raise GraphRoutingError(f"Graph node returned an invalid State update: {exc}") from exc
    return {field: merged[field] for field in raw_update}


def _safe_finalize_update(
    state: MultiAgentState,
    warning: str,
    *,
    counts: Mapping[str, int] | None = None,
    total: int | None = None,
    prior_update: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current_counts, current_total = _read_call_state(state)
    final_counts = dict(current_counts if counts is None else counts)
    final_total = current_total if total is None else total
    update = (
        _normalise_node_update(state, prior_update)
        if prior_update is not None
        else {}
    )

    existing_questions = list(update.get("open_questions", state.get("open_questions", [])) or [])
    existing_errors = list(update.get("errors", state.get("errors", [])) or [])
    question = OpenQuestion(
        question=_ORCHESTRATOR_SAFETY_QUESTION,
        reason=(
            f"{warning} 当前 State 中可能仍有未解决问题，应在报告中保留说明。"
        ),
        blocking=True,
        owner="orchestrator",
    )
    if question not in existing_questions:
        existing_questions.append(question)
    if warning not in existing_errors:
        existing_errors.append(warning)

    update.update(
        {
            "supervisor_decision": SupervisorDecision(
                next_agent=AgentName.FINISH,
                task="安全结束当前 Agent 协作，并交由报告层汇总已有 State。",
                reason=f"{warning} 已进入安全结束流程，不再继续调度专业 Agent。",
                open_question_to_resolve=question,
                can_finish=True,
            ),
            "open_questions": existing_questions,
            "errors": existing_errors,
            "agent_call_counts": final_counts,
            "total_agent_calls": final_total,
        }
    )
    return update


def _route_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_supervisor_route_trace(
    state: MultiAgentState,
    update: dict[str, Any],
    decision: SupervisorDecision,
) -> dict[str, Any]:
    raw_trace = state.get("route_trace", [])
    if raw_trace is None:
        raw_trace = []
    if not isinstance(raw_trace, list):
        raise GraphRoutingError("State route_trace must be a list")

    trace = list(raw_trace)
    step = len(trace) + 1
    for item in trace:
        raw_step = (
            item.step
            if isinstance(item, RouteTraceRecord)
            else item.get("step")
            if isinstance(item, Mapping)
            else None
        )
        if isinstance(raw_step, int) and not isinstance(raw_step, bool):
            step = max(step, raw_step + 1)
    trace.append(
        RouteTraceRecord(
            step=step,
            from_agent="supervisor",
            to_agent=decision.next_agent,
            reason=decision.reason,
            task=decision.task,
            timestamp=_route_timestamp(),
        )
    )
    update["route_trace"] = trace
    return update


def _strip_route_trace(raw_update: Any) -> Any:
    if not isinstance(raw_update, Mapping):
        return raw_update
    return {
        field: value
        for field, value in raw_update.items()
        if field != "route_trace"
    }


def _strip_agent_trace(raw_update: Any) -> Any:
    """Keep Agent Trace creation in the Graph boundary, not in node output."""
    if not isinstance(raw_update, Mapping):
        return raw_update
    return {
        field: value
        for field, value in raw_update.items()
        if field != "agent_trace"
    }


def _append_agent_trace(
    state: MultiAgentState,
    update: Mapping[str, Any],
    *,
    agent: str,
    node: GraphNode,
    started_ns: int,
    status: str,
    retry_count: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Append one Graph-owned, bounded Agent trace to a node update."""

    raw_trace = state.get("agent_trace", [])
    if raw_trace is None:
        raw_trace = []
    if not isinstance(raw_trace, list):
        raise GraphRoutingError("State agent_trace must be a list")

    clean_update = _strip_agent_trace(update)
    if not isinstance(clean_update, Mapping):
        raise GraphRoutingError("Agent trace update must be a mapping")
    if retry_count is None:
        retry_count = extract_retry_count(node)
    record = make_agent_trace_record(
        agent=agent,
        state=state,
        output=clean_update,
        status=status,
        duration_ms=(perf_counter_ns() - started_ns) / 1_000_000,
        retry_count=retry_count,
        token_usage=extract_token_usage(node),
        error=error,
    )
    traced_update = dict(clean_update)
    traced_update["agent_trace"] = [*raw_trace, record]
    return traced_update


def _is_orchestrator_safety_finish(state: MultiAgentState) -> bool:
    """Keep a safety finish across the fixed professional-to-Supervisor edge."""
    try:
        decision = _decision_from_state(state)
    except GraphRoutingError:
        return False
    if decision.next_agent != AgentName.FINISH or not decision.can_finish:
        return False

    raw_questions = state.get("open_questions", [])
    if not isinstance(raw_questions, list):
        return False
    for raw_question in raw_questions:
        try:
            question = (
                raw_question
                if isinstance(raw_question, OpenQuestion)
                else OpenQuestion.model_validate(raw_question)
            )
        except (TypeError, ValidationError):
            continue
        if (
            question.question == _ORCHESTRATOR_SAFETY_QUESTION
            and question.blocking
            and question.owner == "orchestrator"
        ):
            return True
    return False


def _protected_supervisor_node(
    node: GraphNode,
    limits: GraphCallLimitConfig,
) -> GraphNode:
    """Protect a Supervisor result before the conditional route executes."""

    def protected(state: MultiAgentState) -> Mapping[str, Any]:
        started_ns = perf_counter_ns()
        counts, total = _read_call_state(state)
        if _is_orchestrator_safety_finish(state):
            # A professional node always returns to Supervisor in the graph
            # topology.  Preserve an orchestrator-created safety finish here
            # instead of allowing a fresh model decision to restart the loop.
            update = {
                "supervisor_decision": _decision_from_state(state),
                "agent_call_counts": counts,
                "total_agent_calls": total,
            }
            update = _append_supervisor_route_trace(
                state,
                update,
                update["supervisor_decision"],
            )
            return _append_agent_trace(
                state,
                update,
                agent="supervisor",
                node=node,
                started_ns=started_ns,
                status="skipped",
                retry_count=0,
            )
        total_warning = _total_limit_warning(total, limits)
        if total_warning is not None:
            update = _safe_finalize_update(
                state,
                total_warning,
                counts=counts,
                total=total,
            )
            update = _append_supervisor_route_trace(
                state,
                update,
                update["supervisor_decision"],
            )
            return _append_agent_trace(
                state,
                update,
                agent="supervisor",
                node=node,
                started_ns=started_ns,
                status="skipped",
                retry_count=0,
                error=total_warning,
            )

        try:
            raw_update = node(state)
            decision_update = _strip_agent_trace(_strip_route_trace(raw_update))
            update = _normalise_node_update(state, decision_update)
        except Exception as exc:
            warning = (
                f"Supervisor Agent 调用失败（{type(exc).__name__}），"
                "已停止继续调度并进入安全结束流程。"
            )
            update = _safe_finalize_update(
                state,
                warning,
                counts=counts,
                total=total,
            )
            update = _append_supervisor_route_trace(
                state,
                update,
                update["supervisor_decision"],
            )
            return _append_agent_trace(
                state,
                update,
                agent="supervisor",
                node=node,
                started_ns=started_ns,
                status="failed",
                retry_count=retry_count_from_error(exc),
                error=f"{type(exc).__name__}: {exc}",
            )

        # Counters belong to the orchestrator.  A Supervisor result cannot
        # rewrite them and thereby bypass the Graph guard.
        update["agent_call_counts"] = counts
        update["total_agent_calls"] = total
        merged_state = dict(state)
        merged_state.update(update)

        try:
            decision = _decision_from_state(merged_state)
        except GraphRoutingError as exc:
            # The normal route function will emit the precise invalid-decision
            # error.  Do not replace it with a call-limit message here.
            return _append_agent_trace(
                state,
                update,
                agent="supervisor",
                node=node,
                started_ns=started_ns,
                status="failed",
                error=str(exc),
            )

        warning = _limit_warning(
            decision.next_agent,
            counts=counts,
            total=total,
            limits=limits,
        )
        if warning is None:
            update = _append_supervisor_route_trace(state, update, decision)
            return _append_agent_trace(
                state,
                update,
                agent="supervisor",
                node=node,
                started_ns=started_ns,
                status="succeeded",
            )
        update = _safe_finalize_update(
            state,
            warning,
            counts=counts,
            total=total,
            prior_update=decision_update,
        )
        update = _append_supervisor_route_trace(
            state,
            update,
            update["supervisor_decision"],
        )
        return _append_agent_trace(
            state,
            update,
            agent="supervisor",
            node=node,
            started_ns=started_ns,
            status="guarded",
            error=warning,
        )

    return protected


def _counted_professional_node(
    name: str,
    node: GraphNode,
    limits: GraphCallLimitConfig,
) -> GraphNode:
    """Count one professional Agent invocation and stop failed/limited loops."""
    target = AgentName(name.removesuffix("_agent"))

    def counted(state: MultiAgentState) -> Mapping[str, Any]:
        started_ns = perf_counter_ns()
        counts, total = _read_call_state(state)
        warning = _limit_warning(target, counts=counts, total=total, limits=limits)
        if warning is not None:
            update = _safe_finalize_update(state, warning)
            return _append_agent_trace(
                state,
                update,
                agent=target.value,
                node=node,
                started_ns=started_ns,
                status="skipped",
                retry_count=0,
                error=warning,
            )

        next_counts = dict(counts)
        next_counts[target.value] = next_counts.get(target.value, 0) + 1
        next_total = total + 1
        try:
            raw_update = node(state)
            update = _normalise_node_update(
                state,
                _strip_agent_trace(_strip_route_trace(raw_update)),
            )
        except Exception as exc:
            warning = (
                f"{target.value} Agent 调用失败（{type(exc).__name__}），"
                "已停止继续调查并进入安全结束流程。"
            )
            update = _safe_finalize_update(
                state,
                warning,
                counts=next_counts,
                total=next_total,
            )
            return _append_agent_trace(
                state,
                update,
                agent=target.value,
                node=node,
                started_ns=started_ns,
                status="failed",
                retry_count=retry_count_from_error(exc),
                error=f"{type(exc).__name__}: {exc}",
            )

        update["agent_call_counts"] = next_counts
        update["total_agent_calls"] = next_total
        return _append_agent_trace(
            state,
            update,
            agent=target.value,
            node=node,
            started_ns=started_ns,
            status="succeeded",
        )

    return counted


def _node_registry(
    node_overrides: Mapping[str, GraphNode] | None,
) -> dict[str, GraphNode]:
    registry: dict[str, GraphNode] = {
        name: _noop_node
        for name in GRAPH_NODE_NAMES
    }
    # Bootstrap is the first real boundary: it prepares metadata and empty
    # orchestration containers.  The renderer is deterministic and can be
    # enabled by default because it only formats structured State; the
    # remaining runtime boundaries stay injectable until their integration
    # steps are complete.
    registry["bootstrap_context"] = bootstrap_context
    registry["report_renderer"] = report_renderer_node
    if node_overrides is None:
        return registry
    if not isinstance(node_overrides, Mapping):
        raise TypeError("node_overrides must be a mapping of Graph node names to callables")

    unknown = set(node_overrides) - set(GRAPH_NODE_NAMES)
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise GraphBuildError(f"unknown Graph node override(s): {names}")
    for name, node in node_overrides.items():
        if not callable(node):
            raise GraphBuildError(f"Graph node '{name}' must be callable")
        registry[name] = node
    return registry


def _decision_from_state(state: MultiAgentState) -> SupervisorDecision:
    if not isinstance(state, Mapping):
        raise GraphRoutingError(
            "Graph State must be a mapping before Supervisor routing can run"
        )
    raw_decision = state.get("supervisor_decision")
    if raw_decision is None:
        raise GraphRoutingError(
            "Supervisor decision is missing; Graph cannot determine the next node"
        )
    try:
        if isinstance(raw_decision, SupervisorDecision):
            return raw_decision
        return SupervisorDecision.model_validate(raw_decision)
    except (TypeError, ValidationError) as exc:
        raise GraphRoutingError("Supervisor decision is not a valid SupervisorDecision") from exc


def route_from_supervisor(state: MultiAgentState) -> str:
    """Map the Supervisor's finite target to a Graph node.

    This function intentionally contains no metric, content, or business
    condition.  Invalid or missing decisions fail closed instead of guessing
    a route.
    """
    decision = _decision_from_state(state)
    target = decision.next_agent.value
    destination = SUPERVISOR_ROUTE_MAP.get(target)
    if destination is None:
        raise GraphRoutingError(f"Supervisor target is not routable: {target}")

    if decision.next_agent == AgentName.FINISH and not decision.can_finish:
        raise GraphRoutingError("finish route requires SupervisorDecision.can_finish=true")
    if decision.next_agent != AgentName.FINISH and decision.can_finish:
        raise GraphRoutingError(
            "professional Agent route cannot set SupervisorDecision.can_finish=true"
        )
    return destination


# Public descriptive alias for callers that refer to this node as a
# conditional router.  Keep ``route_from_supervisor`` as the stable name used
# by the Phase 12.1 skeleton and existing integrations.
conditional_route = route_from_supervisor


def build_graph(
    *,
    node_overrides: Mapping[str, GraphNode] | None = None,
    checkpointer: Any | None = None,
    agent_call_limits: Mapping[AgentName | str, int] | None = None,
    max_total_calls: int | None = DEFAULT_MAX_TOTAL_CALLS,
):
    """Build and compile the Supervisor-centred Graph.

    ``node_overrides`` is the runtime seam for the actual bootstrap,
    Supervisor, specialist, renderer, and push implementations.  The default
    registry uses the real bootstrap and keeps the remaining placeholders
    inert until their integration steps are complete.

    ``agent_call_limits`` and ``max_total_calls`` are technical orchestration
    limits.  They are applied around the Supervisor and professional Agent
    nodes so injected implementations cannot bypass the loop guard.
    """
    limits = _normalise_call_limits(agent_call_limits, max_total_calls)
    registry = _node_registry(node_overrides)
    registry["supervisor"] = _protected_supervisor_node(registry["supervisor"], limits)
    for name in PROFESSIONAL_NODE_NAMES:
        registry[name] = _counted_professional_node(name, registry[name], limits)
    builder = StateGraph(MultiAgentState)

    for name in GRAPH_NODE_NAMES:
        builder.add_node(name, registry[name])

    builder.add_edge(START, "bootstrap_context")
    builder.add_edge("bootstrap_context", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        path_map=SUPERVISOR_PATH_MAP,
    )

    for name in PROFESSIONAL_NODE_NAMES:
        builder.add_edge(name, "supervisor")

    builder.add_edge("report_renderer", "push")
    builder.add_edge("push", END)

    if checkpointer is None:
        return builder.compile()
    return builder.compile(checkpointer=checkpointer)


create_graph = build_graph


__all__ = [
    "DEFAULT_AGENT_CALL_LIMITS",
    "DEFAULT_MAX_AGENT_CALLS",
    "DEFAULT_MAX_TOTAL_CALLS",
    "GRAPH_NODE_NAMES",
    "PROFESSIONAL_NODE_NAMES",
    "SUPERVISOR_PATH_MAP",
    "SUPERVISOR_ROUTE_MAP",
    "GraphBuildError",
    "GraphCallLimitConfig",
    "GraphRoutingError",
    "build_graph",
    "conditional_route",
    "create_graph",
    "route_from_supervisor",
]
