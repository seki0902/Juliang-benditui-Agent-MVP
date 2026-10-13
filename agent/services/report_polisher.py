"""Optional, guarded LLM language polishing for the deterministic daily report.

The deterministic renderer remains the source of truth.  This module is an
explicit opt-in adapter: callers provide an already-created chat model and
may inject the returned node into a Graph.  The LLM only receives the human
readable report, never the raw State or account credentials.  Any provider,
schema, or safety-check failure returns the original report unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from state import MultiAgentState

from .report_renderer import report_renderer_node
from .structured_output import StructuredOutputError, invoke_structured


CORE_SECTION_TITLES = (
    "【今日重点】",
    "【数据发现】",
    "【投放诊断】",
    "【内容审核】",
    "【策略建议】",
    "【仍需观察】",
    "【证据不足项】",
)
OPTIONAL_SECTION_TITLES = ("【分析路径】",)
ALL_SECTION_TITLES = CORE_SECTION_TITLES + OPTIONAL_SECTION_TITLES

# Strategy and uncertainty sections are locked byte-for-byte after trimming
# line whitespace.  This makes the most safety-sensitive parts independent of
# the model's ability to paraphrase faithfully.
PROTECTED_SECTION_TITLES = (
    "【今日重点】",
    "【策略建议】",
    "【仍需观察】",
    "【证据不足项】",
    "【分析路径】",
)

# These are conservative, high-signal execution words.  A candidate that
# introduces one which did not exist in the deterministic report is rejected;
# this is a guardrail against the LLM adding an action in a prose section.
ACTION_MARKERS = frozenset(
    {
        "暂停",
        "开启计划",
        "关闭计划",
        "提价",
        "降价",
        "加价",
        "降预算",
        "加预算",
        "扩量",
        "缩量",
        "放量",
        "控量",
        "改出价",
        "修改预算",
        "预算调整",
        "出价调整",
        "新建",
        "删除",
        "替换",
        "切换",
        "执行",
    }
)

_SECTION_PATTERN = re.compile(r"【[^】]+】")
_MISSING_MARKERS = ("不适用 / 本次未调用", "未调用", "暂无", "无结构化")

_POLISH_SYSTEM_PROMPT = """你是一个严格受限的投放诊断日报语言润色器。

只输出结构化字段 polished_report。你只能改善表达、断句和可读性，不能：
1. 改变任何 Strategy Result 的含义；
2. 新增、删除或改写动作；
3. 删除任何仍需观察、证据不足、无法下结论或分析路径信息；
4. 新增指标、事实、原因、阈值、计划或素材；
5. 改变章节标题、章节顺序、日期或 run_id。

【今日重点】、【策略建议】、【仍需观察】、【证据不足项】和【分析路径】是锁定章节，
必须原样保留。只可谨慎润色【数据发现】、【投放诊断】和【内容审核】中的自然语言。
如果无法同时满足约束，原样返回基础日报。
"""


class ReportPolishOutput(BaseModel):
    """Strict structured response expected from the optional polish model."""

    model_config = ConfigDict(extra="forbid")

    polished_report: str = Field(min_length=1)


class ReportPolishGuardError(ValueError):
    """Raised internally when a candidate violates the polish contract."""


@dataclass(frozen=True)
class ReportPolishOutcome:
    """Result of an optional polish attempt, including safe fallback status."""

    report: str
    applied: bool
    fallback_reason: str | None = None


def _require_report(report: Any) -> str:
    if not isinstance(report, str):
        raise TypeError("base_report must be a string")
    if not report.strip():
        raise ValueError("base_report cannot be empty")
    return report


def _normalise_block(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def _content_lines(value: str) -> list[str]:
    return [line.strip() for line in value.strip().splitlines() if line.strip()]


def _line_content(value: str) -> str:
    line = value.strip()
    return line[2:].strip() if line.startswith("- ") else line


def _section_sequence(report: str) -> list[str]:
    return _SECTION_PATTERN.findall(report)


def _sections(report: str, sequence: Sequence[str]) -> tuple[str, dict[str, str]]:
    positions = [report.find(title) for title in sequence]
    if any(position < 0 for position in positions):
        raise ReportPolishGuardError("required report section is missing")

    first_start = positions[0]
    prefix = report[:first_start].strip()
    blocks: dict[str, str] = {}
    for index, title in enumerate(sequence):
        start = positions[index] + len(title)
        end = positions[index + 1] if index + 1 < len(sequence) else len(report)
        blocks[title] = report[start:end].strip()
    return prefix, blocks


def validate_polished_report(base_report: str, candidate_report: str) -> str:
    """Validate and return a candidate report, or raise a guard error.

    Validation is intentionally conservative.  The candidate must keep the
    exact section sequence and metadata prefix, lock all uncertainty/action
    sections, retain explicit missing-result markers, and avoid introducing
    known execution verbs.  A caller should fall back to ``base_report`` on
    any raised error.
    """

    base = _require_report(base_report)
    candidate = _require_report(candidate_report)
    base_sequence = _section_sequence(base)
    candidate_sequence = _section_sequence(candidate)

    v2_titles = ["【账户总体】", "【待处理计划】", "【问题更可能在哪】", "【今天具体做什么】", "【下一次看什么】"]
    if base_sequence == v2_titles:
        # V2 is already concise. Keep actions, numbers, uncertainties and facts intact.
        if _normalise_block(candidate) != _normalise_block(base):
            raise ReportPolishGuardError("V2 concise report is protected from semantic rewriting")
        return candidate

    expected_sequence = list(CORE_SECTION_TITLES)
    if "【分析路径】" in base_sequence:
        expected_sequence.append("【分析路径】")
    if base_sequence != expected_sequence:
        raise ReportPolishGuardError("base report does not match the renderer contract")
    if candidate_sequence != base_sequence:
        raise ReportPolishGuardError("candidate changed report section order or titles")

    base_prefix, base_blocks = _sections(base, base_sequence)
    candidate_prefix, candidate_blocks = _sections(candidate, candidate_sequence)
    if _normalise_block(candidate_prefix) != _normalise_block(base_prefix):
        raise ReportPolishGuardError("candidate changed report metadata")

    for title in PROTECTED_SECTION_TITLES:
        if title not in base_blocks:
            continue
        if _normalise_block(candidate_blocks[title]) != _normalise_block(base_blocks[title]):
            raise ReportPolishGuardError(f"candidate changed protected section: {title}")

    # In the three prose sections, keep the same number and order of factual
    # lines.  A candidate may append light wording to an existing line, but it
    # cannot paraphrase away the original fact or add a fresh factual bullet.
    for title in base_sequence:
        if title in PROTECTED_SECTION_TITLES:
            continue
        base_lines = _content_lines(base_blocks[title])
        candidate_lines = _content_lines(candidate_blocks[title])
        if len(candidate_lines) != len(base_lines):
            raise ReportPolishGuardError(f"candidate changed factual line count: {title}")
        for base_line, candidate_line in zip(base_lines, candidate_lines):
            if _line_content(base_line) not in _line_content(candidate_line):
                raise ReportPolishGuardError(f"candidate changed factual line: {title}")

    for marker in _MISSING_MARKERS:
        if marker in base and marker not in candidate:
            raise ReportPolishGuardError(f"candidate removed explicit status marker: {marker}")

    new_action_markers = sorted(
        marker
        for marker in ACTION_MARKERS
        if marker in candidate and marker not in base
    )
    if new_action_markers:
        raise ReportPolishGuardError(
            "candidate introduced action marker(s): " + ", ".join(new_action_markers)
        )

    return candidate


def _polish_failure_reason(error: Exception) -> str:
    if isinstance(error, ReportPolishGuardError):
        return f"guard_rejected:{error}"
    if isinstance(error, StructuredOutputError):
        return f"structured_output_failed:{error.schema.__name__}"
    return f"llm_failed:{type(error).__name__}"


@dataclass
class ReportPolisher:
    """Opt-in language polish with deterministic fallback to the base report."""

    model: Any | None = None
    structured_retries: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.structured_retries, bool) or not isinstance(
            self.structured_retries, int
        ):
            raise TypeError("structured_retries must be a non-negative integer")
        if self.structured_retries < 0:
            raise ValueError("structured_retries must be a non-negative integer")

    @staticmethod
    def _messages(base_report: str) -> list[Any]:
        return [
            SystemMessage(content=_POLISH_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "请在上述约束下处理以下基础日报。只返回 polished_report，"
                    "不要输出解释、代码块或额外字段。\n\n"
                    f"{base_report}"
                )
            ),
        ]

    def run(self, base_report: str) -> ReportPolishOutcome:
        """Attempt polish and always return a usable report."""
        base = _require_report(base_report)
        if self.model is None:
            return ReportPolishOutcome(
                report=base,
                applied=False,
                fallback_reason="model_not_configured",
            )

        try:
            result = invoke_structured(
                self.model,
                ReportPolishOutput,
                self._messages(base),
                max_retries=self.structured_retries,
            )
            candidate = validate_polished_report(base, result.polished_report)
        except Exception as exc:
            return ReportPolishOutcome(
                report=base,
                applied=False,
                fallback_reason=_polish_failure_reason(exc),
            )

        return ReportPolishOutcome(
            report=candidate,
            applied=candidate != base,
        )

    def polish(self, base_report: str) -> str:
        """Return the polished report or the unchanged deterministic fallback."""
        return self.run(base_report).report

    __call__ = polish


def polish_report(
    base_report: str,
    *,
    model: Any | None = None,
    structured_retries: int = 0,
) -> str:
    """Functional entry point for an optional guarded polish attempt."""

    return ReportPolisher(
        model=model,
        structured_retries=structured_retries,
    ).polish(base_report)


def make_polished_report_renderer_node(
    model: Any | None,
    *,
    structured_retries: int = 0,
):
    """Create an injectable Graph node that renders first, then optionally polishes."""

    polisher = ReportPolisher(model=model, structured_retries=structured_retries)

    def node(state: MultiAgentState) -> dict[str, Any]:
        base_update = report_renderer_node(state)
        return {"final_report": polisher.polish(base_update["final_report"])}

    return node


def polished_report_renderer_node(
    state: MultiAgentState,
    *,
    model: Any | None = None,
    structured_retries: int = 0,
) -> dict[str, Any]:
    """Functional Graph boundary for callers that explicitly opt into polish."""

    return make_polished_report_renderer_node(
        model,
        structured_retries=structured_retries,
    )(state)


__all__ = [
    "ACTION_MARKERS",
    "ALL_SECTION_TITLES",
    "CORE_SECTION_TITLES",
    "OPTIONAL_SECTION_TITLES",
    "PROTECTED_SECTION_TITLES",
    "ReportPolishGuardError",
    "ReportPolishOutcome",
    "ReportPolishOutput",
    "ReportPolisher",
    "make_polished_report_renderer_node",
    "polish_report",
    "polished_report_renderer_node",
    "validate_polished_report",
]
