"""Supervisor Agent Node.

The Supervisor chooses the next orchestration target from the complete State
or safely finishes the run.  It does not perform advertising analysis, call
business data Tools, or mutate backend state.  Runtime call limits are
technical guards and can be overridden by the orchestrator.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from schemas.common import AgentName, OpenQuestion
from schemas.supervisor import SupervisorDecision
from services.structured_output import invoke_structured
from state import MultiAgentState


DEFAULT_MAX_AGENT_CALLS = 2
DEFAULT_MAX_TOTAL_CALLS = 8
DEFAULT_MAX_REDECISION_ATTEMPTS = 1
DEFAULT_AGENT_CALL_LIMITS = {
    AgentName.DATA_ANALYSIS.value: DEFAULT_MAX_AGENT_CALLS,
    AgentName.ADS_DIAGNOSIS.value: DEFAULT_MAX_AGENT_CALLS,
    AgentName.CONTENT_REVIEW.value: DEFAULT_MAX_AGENT_CALLS,
    AgentName.STRATEGY.value: DEFAULT_MAX_AGENT_CALLS,
}

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "supervisor.md"
_SPECIALIST_AGENTS = frozenset(DEFAULT_AGENT_CALL_LIMITS)
_ALL_TARGETS = frozenset(
    {
        AgentName.DATA_ANALYSIS,
        AgentName.ADS_DIAGNOSIS,
        AgentName.CONTENT_REVIEW,
        AgentName.STRATEGY,
        AgentName.FINISH,
    }
)


class SupervisorAgentError(RuntimeError):
    """Raised when the Supervisor Node cannot complete its technical flow."""


def _json_safe(value: Any) -> Any:
    """Convert State values into JSON-safe values for model messages."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _normalise_agent_name(value: AgentName | str, *, field_name: str) -> AgentName:
    if isinstance(value, AgentName):
        return value
    try:
        return AgentName(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} contains an invalid AgentName: {value}") from exc


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _normalise_allowed_agents(
    allowed_agents: Iterable[AgentName | str] | None,
) -> frozenset[AgentName]:
    if allowed_agents is None:
        return _ALL_TARGETS
    if isinstance(allowed_agents, (str, bytes)):
        raise TypeError("allowed_agents must be an iterable of AgentName values")

    normalised = frozenset(
        _normalise_agent_name(item, field_name="allowed_agents")
        for item in allowed_agents
    )
    if not normalised:
        raise ValueError("allowed_agents cannot be empty")
    if AgentName.FINISH not in normalised:
        raise ValueError("allowed_agents must include finish for safe finalization")
    return normalised


def _normalise_agent_call_limits(
    limits: Mapping[AgentName | str, int] | None,
) -> dict[str, int]:
    normalised = dict(DEFAULT_AGENT_CALL_LIMITS)
    if limits is None:
        return normalised
    if not isinstance(limits, Mapping):
        raise TypeError("agent_call_limits must be a mapping")

    for raw_name, raw_limit in limits.items():
        name = _normalise_agent_name(raw_name, field_name="agent_call_limits")
        if name not in _SPECIALIST_AGENTS:
            raise ValueError(
                "agent_call_limits may only configure professional Agent targets"
            )
        normalised[name.value] = _require_non_negative_int(
            raw_limit,
            field_name=f"agent_call_limits[{name.value}]",
        )
    return normalised


def _state_call_counts(state: MultiAgentState) -> tuple[dict[str, int], int]:
    raw_counts = state.get("agent_call_counts", {})
    if raw_counts is None:
        raw_counts = {}
    if not isinstance(raw_counts, Mapping):
        raise SupervisorAgentError("State agent_call_counts must be a mapping")

    counts: dict[str, int] = {}
    for raw_name, raw_count in raw_counts.items():
        count = _require_non_negative_int(
            raw_count,
            field_name=f"agent_call_counts[{raw_name}]",
        )
        name = raw_name.value if isinstance(raw_name, AgentName) else str(raw_name)
        counts[name] = count

    raw_total = state.get("total_agent_calls", 0)
    total = _require_non_negative_int(
        raw_total,
        field_name="State total_agent_calls",
    )
    return counts, total


@dataclass
class SupervisorAgent:
    """Run one Supervisor decision and return a partial State update."""

    model: Any
    prompt_path: Path | str | None = None
    allowed_agents: Iterable[AgentName | str] | None = None
    agent_call_limits: Mapping[AgentName | str, int] | None = None
    max_total_calls: int | None = DEFAULT_MAX_TOTAL_CALLS
    structured_retries: int = 0

    def __post_init__(self) -> None:
        self.allowed_agents = _normalise_allowed_agents(self.allowed_agents)
        self.agent_call_limits = _normalise_agent_call_limits(self.agent_call_limits)

        if self.max_total_calls is not None:
            self.max_total_calls = _require_non_negative_int(
                self.max_total_calls,
                field_name="max_total_calls",
            )
        self.structured_retries = _require_non_negative_int(
            self.structured_retries,
            field_name="structured_retries",
        )

    def _prompt_text(self) -> str:
        path = Path(self.prompt_path) if self.prompt_path is not None else _DEFAULT_PROMPT_PATH
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SupervisorAgentError(f"Supervisor Prompt cannot be read: {path}") from exc

    def _system_message(self) -> SystemMessage:
        return SystemMessage(content=self._prompt_text())

    @staticmethod
    def _state_message(state: MultiAgentState) -> HumanMessage:
        payload = json.dumps(
            _json_safe(dict(state)),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return HumanMessage(
            content=(
                "请基于以下完整 MultiAgentState 做一次 Supervisor orchestration 决定。"
                "只选择下一步 Agent 或 finish；不要自行完成任何专业业务分析，"
                "也不要执行真实投放修改。请检查已有结果、open_questions、调用状态"
                "和最近的 handoff_suggestion，但不要把 handoff 当作命令。\n\n"
                f"{payload}"
            )
        )

    @staticmethod
    def _guard_message(
        *,
        issue: str,
        counts: Mapping[str, int],
        total: int,
        max_total_calls: int | None,
    ) -> HumanMessage:
        guard_state = {
            "guard_type": "supervisor_redecision",
            "warning": issue,
            "agent_call_counts": dict(counts),
            "total_agent_calls": total,
            "max_total_calls": max_total_calls,
            "instruction": "这是技术保护状态，不是业务结论；请重新选择允许且未达到上限的目标，或 finish。",
        }
        return HumanMessage(
            content=(
                "上一次 Supervisor 决定触发了技术保护，请重新决策一次。\n"
                + json.dumps(
                    _json_safe(guard_state),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        )

    def _decision_issue(
        self,
        decision: SupervisorDecision,
        *,
        counts: Mapping[str, int],
    ) -> str | None:
        target = decision.next_agent
        if target not in self.allowed_agents:
            return (
                f"Supervisor 路由目标 {target.value} 不在运行时允许集合，"
                "已拒绝本次路由。"
            )

        if target is AgentName.FINISH:
            if not decision.can_finish:
                return "Supervisor 的 finish 决定缺少 can_finish=true，已要求重新决策。"
            return None

        if decision.can_finish:
            return "非 finish 路由不能设置 can_finish=true，已要求重新决策。"

        current_count = counts.get(target.value, 0)
        limit = self.agent_call_limits[target.value]
        if current_count >= limit:
            return (
                f"调用限制已达到：{target.value} 已使用 {current_count}/{limit} 次，"
                "需要重新决策。"
            )
        return None

    @staticmethod
    def _limit_question() -> OpenQuestion:
        return OpenQuestion(
            question="调用限制已达到，无法继续调度专业 Agent。",
            reason="技术保护触发；当前 State 中可能仍有未解决问题，应在报告中保留说明。",
            blocking=True,
            owner="supervisor",
        )

    def _safe_finalize(
        self,
        warnings: list[str],
    ) -> dict[str, Any]:
        question = self._limit_question()
        decision = SupervisorDecision(
            next_agent=AgentName.FINISH,
            task="安全结束当前 Agent 协作，并交由报告层汇总已有 State。",
            reason=(
                "；".join(warnings)
                + " 已进入安全结束流程，不再继续调度专业 Agent。"
            ),
            open_question_to_resolve=question,
            can_finish=True,
        )
        return {
            "supervisor_decision": decision,
            "open_questions": [question],
            "errors": list(warnings),
        }

    def _invoke_decision(self, messages: list[Any]) -> SupervisorDecision:
        return invoke_structured(
            self.model,
            SupervisorDecision,
            messages,
            max_retries=self.structured_retries,
        )

    def run(self, state: MultiAgentState) -> dict[str, Any]:
        """Run the Node and return a LangGraph-compatible partial update."""
        counts, total = _state_call_counts(state)

        if self.max_total_calls is not None and total >= self.max_total_calls:
            warning = (
                f"调用限制已达到：total_agent_calls={total}，"
                f"系统总调用上限为 {self.max_total_calls}。"
            )
            return self._safe_finalize([warning])

        messages: list[Any] = [self._system_message(), self._state_message(state)]
        decision = self._invoke_decision(messages)
        issue = self._decision_issue(decision, counts=counts)
        warnings: list[str] = []

        if issue is not None:
            warnings.append(issue)
            messages.append(
                self._guard_message(
                    issue=issue,
                    counts=counts,
                    total=total,
                    max_total_calls=self.max_total_calls,
                )
            )
            # The checklist deliberately allows exactly one re-decision after
            # a technical guard.  A second invalid/limited result is never
            # routed and falls back to safe finalization.
            decision = self._invoke_decision(messages)
            second_issue = self._decision_issue(decision, counts=counts)
            if second_issue is not None:
                warnings.append(second_issue)
                return self._safe_finalize(warnings)

        update: dict[str, Any] = {"supervisor_decision": decision}
        if warnings:
            update["errors"] = warnings
        return update

    __call__ = run


def supervisor_node(
    state: MultiAgentState,
    *,
    model: Any,
    prompt_path: Path | str | None = None,
    allowed_agents: Iterable[AgentName | str] | None = None,
    agent_call_limits: Mapping[AgentName | str, int] | None = None,
    max_total_calls: int | None = DEFAULT_MAX_TOTAL_CALLS,
    structured_retries: int = 0,
) -> dict[str, Any]:
    """Functional entry point for registering the Supervisor Node."""
    return SupervisorAgent(
        model=model,
        prompt_path=prompt_path,
        allowed_agents=allowed_agents,
        agent_call_limits=agent_call_limits,
        max_total_calls=max_total_calls,
        structured_retries=structured_retries,
    ).run(state)


__all__ = [
    "DEFAULT_AGENT_CALL_LIMITS",
    "DEFAULT_MAX_AGENT_CALLS",
    "DEFAULT_MAX_REDECISION_ATTEMPTS",
    "DEFAULT_MAX_TOTAL_CALLS",
    "SupervisorAgent",
    "SupervisorAgentError",
    "supervisor_node",
]
