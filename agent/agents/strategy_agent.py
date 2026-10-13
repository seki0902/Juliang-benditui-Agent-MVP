"""Strategy Agent Node.

The Strategy Agent combines structured results already produced by the
specialist Agents. It does not re-query raw advertising data or perform any
backend mutation. The final answer is validated through the shared Pydantic
structured-output boundary and returned as a partial MultiAgentState update.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from time import perf_counter_ns

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from schemas.common import AgentName, EvidenceItem, OpenQuestion
from schemas.strategy import StrategyResult
from services.principle_loader import PrincipleLoader
from services.structured_output import invoke_structured
from services.tool_trace import make_tool_call_record
from state import MultiAgentState


_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"


class StrategyAgentError(RuntimeError):
    """Raised when the Strategy Node cannot complete its technical flow."""


def _json_safe(value: Any) -> Any:
    """Convert State values into JSON-safe values for model messages."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _unique_evidence(items: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    result: list[EvidenceItem] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(
            _json_safe(item),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _state_questions(result: StrategyResult) -> list[OpenQuestion]:
    """Expose strategy uncertainty and evidence requests in shared State."""
    questions = [
        OpenQuestion(
            question=statement,
            reason="Strategy Agent 当前无法安全下结论。",
        )
        for statement in result.cannot_conclude
    ]
    if result.evidence_request is not None:
        questions.append(
            OpenQuestion(
                question=result.evidence_request.request,
                reason=result.evidence_request.reason,
            )
        )
    return questions


@dataclass
class StrategyAgent:
    """Run one synthesis turn and return a partial State update."""

    model: Any
    principles: str | None = None
    prompt_path: Path | str | None = None
    structured_retries: int = 0
    tools: Sequence[Any] | None = None
    max_tool_calls: int = 4

    def __post_init__(self) -> None:
        if isinstance(self.structured_retries, bool) or not isinstance(
            self.structured_retries, int
        ):
            raise TypeError("structured_retries must be a non-negative integer")
        if self.structured_retries < 0:
            raise ValueError("structured_retries must be a non-negative integer")
        if isinstance(self.max_tool_calls, bool) or not isinstance(self.max_tool_calls, int) or self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a non-negative integer")

    def _proposal_facts(self, messages):
        """Only a deterministic suggestion calculator is exposed to Strategy."""
        lookup = {tool.name: tool for tool in self.tools or []}
        if set(lookup) - {"get_adjustment_options"}:
            raise StrategyAgentError("Strategy only accepts the read-only adjustment calculator")
        trace, options = [], []
        if not lookup or not self.max_tool_calls:
            return trace, options
        selector = self.model.bind_tools(list(lookup.values()))
        used = 0
        while used < self.max_tool_calls:
            response = selector.invoke(messages)
            messages.append(response)
            calls = getattr(response, "tool_calls", [])
            if not calls:
                break
            for call in calls:
                started = perf_counter_ns()
                name, arguments = call["name"], call["args"]
                result = {"status": "unavailable", "data": {}, "warnings": ["unknown tool or tool limit"]}
                if name in lookup and used < self.max_tool_calls:
                    try:
                        result = lookup[name].invoke(arguments)
                    except Exception as exc:
                        result = {"status": "error", "data": {}, "warnings": [type(exc).__name__]}
                    used += 1
                else:
                    used = self.max_tool_calls
                if result.get("status") == "ok":
                    for option in result.get("data", {}).get("options", []):
                        options.append((str(result["data"]["plan_id"]), option))
                trace.append(make_tool_call_record(agent=AgentName.STRATEGY,
                    tool_name=name, call_id=call["id"], arguments=arguments,
                    status=result["status"], result=result,
                    duration_ms=(perf_counter_ns() - started) / 1_000_000))
                messages.append(ToolMessage(content=json.dumps(result, ensure_ascii=False),
                                            tool_call_id=call["id"], name=name))
        return trace, options

    def _prompt_text(self) -> str:
        path = Path(self.prompt_path) if self.prompt_path is not None else _DEFAULT_PROMPT_PATH
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StrategyAgentError(f"Strategy Prompt cannot be read: {path}") from exc

    def _principles_text(self) -> str:
        if self.principles is not None:
            return self.principles
        try:
            return PrincipleLoader().load_text(AgentName.STRATEGY)
        except (OSError, ValueError) as exc:
            raise StrategyAgentError("Strategy Principles cannot be loaded") from exc

    def _system_message(self) -> SystemMessage:
        content = (
            f"{self._prompt_text()}\n\n"
            "## 当前运行时注入的 Strategy Principles\n\n"
            f"{self._principles_text()}"
        )
        return SystemMessage(content=content)

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
                "请基于以下当前 State 开始 Strategy。只综合 State 中已有的 Agent 结果和 "
                "Evidence；不要重新从头分析原始数据，也不要执行真实投放修改。请处理冲突，"
                "区分建议、OBSERVE/保持观察和无法下结论的事项；证据不足时使用 "
                "need_more_evidence 与 evidence_request。\n\n"
                f"{payload}"
            )
        )

    def run(self, state: MultiAgentState) -> dict[str, Any]:
        """Run the Node and return a LangGraph-compatible partial update."""
        messages: list[Any] = [self._system_message(), self._state_message(state)]
        tool_trace, options = self._proposal_facts(messages)
        result = invoke_structured(
            self.model,
            StrategyResult,
            messages,
            max_retries=self.structured_retries,
        )

        for action in result.priority_actions:
            if action.proposal is not None:
                if not any(action.target_id == plan_id and action.proposal.model_dump() == option
                           for plan_id, option in options):
                    raise StrategyAgentError("numeric proposal must match a returned option for its target plan")

        update: dict[str, Any] = {
            "strategy_result": result,
            "evidence": _unique_evidence(result.supporting_evidence),
            "open_questions": _state_questions(result),
            "tool_trace": tool_trace,
        }
        return update

    __call__ = run


def strategy_node(
    state: MultiAgentState,
    *,
    model: Any,
    principles: str | None = None,
    prompt_path: Path | str | None = None,
    structured_retries: int = 0,
    tools: Sequence[Any] | None = None,
    max_tool_calls: int = 4,
) -> dict[str, Any]:
    """Functional entry point for registering the Strategy Node."""
    return StrategyAgent(
        model=model,
        principles=principles,
        prompt_path=prompt_path,
        structured_retries=structured_retries,
        tools=tools,
        max_tool_calls=max_tool_calls,
    ).run(state)


__all__ = [
    "StrategyAgent",
    "StrategyAgentError",
    "strategy_node",
]
