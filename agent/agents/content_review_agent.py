"""Content Review Agent Node.

The node is provider-neutral and reviews material content only when reliable
content evidence is available. It accepts a runtime model, optional runtime
Tools, and the Content Review Principles text. Tool selection is delegated to
the model through ``bind_tools`` and the final answer is validated by the
shared Pydantic structured-output boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from schemas.common import AgentName, EvidenceItem, OpenQuestion, ToolCallRecord
from schemas.content_review import ContentReviewResult, ContentUncertainty
from services.principle_loader import PrincipleLoader
from services.structured_output import invoke_structured
from services.tool_protocol import (
    as_ai_message as _as_ai_message,
    error_tool_result as _error_tool_result,
    invoke_tool as _invoke_tool,
    json_safe as _json_safe,
    normalise_tool_result,
    raw_tool_calls as _raw_tool_calls,
    unexecuted_tool_result,
)
from services.tool_trace import make_tool_call_record, safe_tool_error
from state import MultiAgentState


DEFAULT_MAX_TOOL_CALLS = 8
_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "content_review.md"


class ContentReviewAgentError(RuntimeError):
    """Raised when the Content Review Node cannot complete its technical flow."""


def _tool_name(tool: Any, fallback: str | None = None) -> str:
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None) or fallback
    value = str(name or "").strip()
    if not value:
        raise ContentReviewAgentError(
            "every runtime Content Review Tool must expose a name or be supplied in a named mapping"
        )
    return value


def _tool_entries(
    tools: Mapping[str, Any] | Sequence[Any] | None,
) -> tuple[list[Any], dict[str, Any]]:
    if tools is None:
        return [], {}

    if isinstance(tools, Mapping):
        entries = list(tools.items())
        values = [tool for _, tool in entries]
        lookup: dict[str, Any] = {}
        for key, tool in entries:
            alias = _tool_name(tool, fallback=str(key))
            lookup[str(key)] = tool
            lookup[alias] = tool
    else:
        values = list(tools)
        lookup = {}
        for tool in values:
            name = _tool_name(tool)
            if name in lookup and lookup[name] is not tool:
                raise ContentReviewAgentError(f"duplicate runtime Tool name: {name}")
            lookup[name] = tool

    return values, lookup


def _normalise_tool_call(raw_call: Any, index: int) -> tuple[str, dict[str, Any], str]:
    if not isinstance(raw_call, Mapping):
        raise ContentReviewAgentError("model returned a non-object Tool call")

    function = raw_call.get("function")
    if isinstance(function, Mapping):
        name = raw_call.get("name") or function.get("name")
        raw_args = raw_call.get("args", function.get("arguments", {}))
    else:
        name = raw_call.get("name")
        raw_args = raw_call.get("args", {})

    tool_name = str(name or "").strip()
    if not tool_name:
        raise ContentReviewAgentError("model returned a Tool call without a name")

    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise ContentReviewAgentError(
                f"Tool call {tool_name} returned invalid JSON arguments"
            ) from exc
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, Mapping):
        raise ContentReviewAgentError(f"Tool call {tool_name} arguments must be an object")

    call_id = str(raw_call.get("id") or f"content-review-tool-{index}")
    return tool_name, dict(raw_args), call_id


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


def _state_evidence(result: ContentReviewResult) -> list[EvidenceItem]:
    """Collect content and performance evidence for the shared State."""
    evidence = list(result.content_evidence)
    for assessment in result.material_assessments:
        evidence.extend(assessment.supporting_evidence)
    for finding in result.content_findings:
        evidence.extend(finding.content_evidence)
    for explanation in result.performance_explanations:
        evidence.extend(explanation.supporting_evidence)
    return _unique_evidence(evidence)


def _state_questions(result: ContentReviewResult) -> list[OpenQuestion]:
    """Expose unresolved content points through the shared State contract."""
    return [
        OpenQuestion(
            question=item.question,
            reason=item.reason,
        )
        for item in result.uncertainties
    ]


def _guard_content_completeness(result, state, messages):
    """Do not let a model certify complete content analysis without source text."""
    verified_body, verified_any = set(), set()
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "get_content_asset":
            continue
        try:
            payload = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if payload.get("status") != "ok":
            continue
        content = payload.get("data", payload)
        material_id = str(content.get("material_id") or payload.get("material_id") or "")
        if not material_id:
            continue
        if any(content.get(key) for key in ("title", "script", "transcript", "caption", "visual_notes")):
            verified_any.add(material_id)
        if content.get("script") or content.get("transcript"):
            verified_body.add(material_id)
    for raw in state.get("evidence") or []:
        item = _json_safe(raw)
        if not isinstance(item, dict) or not item.get("value") or not item.get("source"):
            continue
        if item.get("object_type") == "material" and item.get("metric_or_content") in {"script", "transcript", "title", "caption", "visual_notes"}:
            verified_any.add(str(item.get("object_id")))
            if item.get("metric_or_content") in {"script", "transcript"}:
                verified_body.add(str(item.get("object_id")))
    targets = {x.material_id for x in result.materials_reviewed}
    targets.update(x.material_id for x in result.material_assessments)
    targets.update(x.material_id for x in result.content_findings)
    if result.content_analysis_status == "complete" and (not targets or not targets <= verified_body):
        result = result.model_copy(update={
            "content_analysis_status": "partial" if targets & verified_any else "missing",
            "uncertainties": [*result.uncertainties, ContentUncertainty(
                question="素材正文是否足够完成内容归因？", reason="运行时未核验全部目标的口播稿或转写，不能标为完整分析。",
                needed_input="对应 material_id 的正文及相关投放表现")],
        })
    return result


@dataclass
class ContentReviewAgent:
    """Run one Content Review turn and return a partial State update."""

    model: Any
    tools: Mapping[str, Any] | Sequence[Any] | None = None
    principles: str | None = None
    prompt_path: Path | str | None = None
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    structured_retries: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_tool_calls, bool) or not isinstance(self.max_tool_calls, int):
            raise TypeError("max_tool_calls must be a non-negative integer")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a non-negative integer")

    def _prompt_text(self) -> str:
        path = Path(self.prompt_path) if self.prompt_path is not None else _DEFAULT_PROMPT_PATH
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ContentReviewAgentError(f"Content Review Prompt cannot be read: {path}") from exc

    def _principles_text(self) -> str:
        if self.principles is not None:
            return self.principles
        try:
            return PrincipleLoader().load_text(AgentName.CONTENT_REVIEW)
        except (OSError, ValueError) as exc:
            raise ContentReviewAgentError(
                "Content Review Principles cannot be loaded"
            ) from exc

    def _system_message(self) -> SystemMessage:
        content = (
            f"{self._prompt_text()}\n\n"
            "## 当前运行时注入的 Content Review Principles\n\n"
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
                "请基于以下当前 State 开始 Content Review。只使用 State 和运行时 Tool "
                "可验证的事实；请确认 material_id、计划关联、跑量和转化表现及对照可比性。"
                "主动读取正文并结合表现分析；缺正文时仅描述表现，内容归因仍未完成，"
                "content_analysis_status=missing，并写明 uncertainty。\n\n"
                f"{payload}"
            )
        )

    def _run_tool_selection(
        self,
        messages: list[Any],
        tool_values: list[Any],
        tool_lookup: dict[str, Any],
    ) -> tuple[list[ToolCallRecord], list[str]]:
        if not tool_values or self.max_tool_calls == 0:
            return [], []
        bind_tools = getattr(self.model, "bind_tools", None)
        if not callable(bind_tools):
            raise ContentReviewAgentError(
                "runtime Content Review Tools were provided, but the model does not support bind_tools"
            )

        try:
            tool_model = bind_tools(tool_values)
        except Exception as exc:
            raise ContentReviewAgentError("Content Review Tool binding failed") from exc

        trace: list[ToolCallRecord] = []
        errors: list[str] = []
        calls_used = 0
        call_index = 0
        limit_reached = False

        while calls_used < self.max_tool_calls:
            try:
                response = _as_ai_message(tool_model.invoke(messages))
            except Exception as exc:
                raise ContentReviewAgentError(
                    "Content Review Tool-selection model call failed"
                ) from exc
            messages.append(response)
            calls = _raw_tool_calls(response)
            if not calls:
                break

            for raw_call in calls:
                tool_name, arguments, call_id = _normalise_tool_call(raw_call, call_index)
                call_index += 1
                started_ns = perf_counter_ns()
                if calls_used >= self.max_tool_calls:
                    limit_reached = True
                    result = unexecuted_tool_result()
                else:
                    calls_used += 1
                    tool = tool_lookup.get(tool_name)
                    if tool is None:
                        exc = KeyError(f"unknown runtime Tool: {tool_name}")
                        result = _error_tool_result(tool_name, exc)
                    else:
                        try:
                            result = normalise_tool_result(
                                _invoke_tool(tool, arguments),
                                default_source=None,
                                default_time_window=None,
                            )
                        except Exception as exc:
                            result = _error_tool_result(tool_name, exc)

                status = str(result.get("status") or "ok")
                error_text = safe_tool_error(tool_name, result)
                if error_text:
                    errors.append(error_text)
                duration_ms = (perf_counter_ns() - started_ns) / 1_000_000
                trace.append(
                    make_tool_call_record(
                        agent=AgentName.CONTENT_REVIEW,
                        tool_name=tool_name,
                        call_id=call_id,
                        arguments=arguments,
                        status=status,
                        result=result,
                        duration_ms=duration_ms,
                        error=error_text,
                    )
                )
                messages.append(
                    ToolMessage(
                        content=json.dumps(
                            _json_safe(result),
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                        tool_call_id=call_id,
                        name=tool_name,
                    )
                )

            if limit_reached:
                break

        if limit_reached:
            errors.append(
                f"Content Review Tool call limit reached: {self.max_tool_calls}"
            )
        return trace, errors

    def run(self, state: MultiAgentState) -> dict[str, Any]:
        """Run the Node and return a LangGraph-compatible partial update."""
        messages: list[Any] = [self._system_message(), self._state_message(state)]
        tool_values, tool_lookup = _tool_entries(self.tools)
        tool_trace, errors = self._run_tool_selection(
            messages,
            tool_values,
            tool_lookup,
        )
        result = invoke_structured(
            self.model,
            ContentReviewResult,
            messages,
            max_retries=self.structured_retries,
        )
        result = _guard_content_completeness(result, state, messages)

        update: dict[str, Any] = {
            "content_review_result": result,
            "evidence": _state_evidence(result),
            "open_questions": _state_questions(result),
            "tool_trace": tool_trace,
        }
        if errors:
            update["errors"] = errors
        return update

    __call__ = run


def content_review_node(
    state: MultiAgentState,
    *,
    model: Any,
    tools: Mapping[str, Any] | Sequence[Any] | None = None,
    principles: str | None = None,
    prompt_path: Path | str | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    structured_retries: int = 0,
) -> dict[str, Any]:
    """Functional entry point for registering the Content Review Node."""
    return ContentReviewAgent(
        model=model,
        tools=tools,
        principles=principles,
        prompt_path=prompt_path,
        max_tool_calls=max_tool_calls,
        structured_retries=structured_retries,
    ).run(state)


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS",
    "ContentReviewAgent",
    "ContentReviewAgentError",
    "content_review_node",
]
