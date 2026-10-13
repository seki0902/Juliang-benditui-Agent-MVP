"""Deterministic human-readable report rendering from structured State.

This module is intentionally not an analysis Agent.  It only formats fields
already produced by the specialist Agents and preserves explicit uncertainty
or missing-result markers instead of inventing a conclusion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
import re

from pydantic import BaseModel, ValidationError

from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.strategy import StrategyResult
from state import MultiAgentState


ModelT = TypeVar("ModelT", bound=BaseModel)


def _model_or_none(value: Any, model: type[ModelT]) -> ModelT | None:
    if value is None:
        return None
    if isinstance(value, model):
        return value
    if isinstance(value, Mapping):
        try:
            return model.model_validate(value)
        except (TypeError, ValidationError):
            return None
    return None


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _items(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _object_label(value: Any) -> str:
    object_type = _text(getattr(value, "object_type", None))
    object_id = _text(getattr(value, "object_id", None))
    if object_type and object_id:
        return f"{object_type}={object_id}"
    return object_id or object_type


def _append(lines: list[str], value: Any, *, prefix: str = "- ") -> None:
    text = _text(value)
    if text:
        lines.append(f"{prefix}{text}")


def _append_labeled(lines: list[str], label: str, value: Any) -> None:
    text = _text(value)
    if text:
        lines.append(f"- {label}：{text}")


def _section(lines: list[str], title: str, body: Sequence[str]) -> None:
    lines.append(title)
    lines.extend(body or ["（无结构化记录）"])
    lines.append("")


def _render_today_focus(result: StrategyResult | None) -> list[str]:
    if result is None:
        return ["Strategy Agent 未调用，暂无综合判断。"]
    return [result.overall_judgement or "Strategy Result 未提供综合判断。"]


def _render_account_overview(state, strategy, data):
    """Keep coverage grounded in account-bound tool facts, including real zero."""
    overview = _render_today_focus(strategy) if strategy else [data.summary if data else "数据尚未完成分析。"]
    context = state.get("account_context") or {}
    if hasattr(context, "model_dump"):
        context = context.model_dump()
    status = context.get("data_status") or {}
    coverage = status.get("deep_conversion_coverage") or {}
    has_records = any(item.get("available_days") for item in coverage.values())
    if has_records:
        # Correct only the known unscoped record-absence claim. Field-specific
        # and date/window-specific uncertainty remains the model's statement.
        pattern = r"(?:深转(?:反馈)?(?:（石墨）)?|石墨深转)全窗口(?:缺失|无记录)"
        overview = ["".join(
            sentence if re.search(r"今日|当天|报告日|\d+[d天日]|\d{4}-\d{2}-\d{2}", sentence)
            else re.sub(pattern, "深转存在部分记录（具体覆盖见下方）", sentence)
            for sentence in re.findall(r"[^。！？]+[。！？]?", text)) for text in overview]
    if status.get("all_pass") is False:
        differences = status.get("differences_unit_minus_material") or {}
        overview.append("单元与素材对账未通过（截至报告日的导出明细，单元减素材）：" +
                        "、".join(f"{key}={value}" for key, value in differences.items() if value) +
                        "；两层数据不能直接视为同一统计范围，差异保留待核对。")
    for window, item in coverage.items():
        days = item.get("available_days") or []
        missing = item.get("missing_dates") or []
        blank = item.get("blank_value_dates") or {}
        parts = [f"石墨账户级 {window}：" + ("有记录日期 " + "、".join(days) if days else "窗口无记录")]
        if missing:
            parts.append("无记录日期 " + "、".join(missing))
        if blank:
            parts.append("有记录但值为空：" + "；".join(f"{field}（{'、'.join(dates)}）" for field, dates in blank.items()))
        valid = item.get("valid_leads")
        parts.append(f"有效深转合计={valid}" if valid is not None else "有效深转无法完整汇总，不补零")
        overview.append("；".join(parts) + "。")
    return [line for line in overview if line]


def _render_data(result: DataAnalysisResult | None) -> list[str]:
    if result is None:
        return ["Data Analysis Agent 未调用，暂无数据发现。"]

    lines: list[str] = []
    _append(lines, result.summary, prefix="")
    for label, field in (
        ("观察", "observations"),
        ("异常", "anomalies"),
        ("冲突", "conflicts"),
    ):
        for finding in _items(getattr(result, field, None)):
            description = _text(getattr(finding, "description", None))
            location = _object_label(finding)
            if location:
                description = f"{description}（{location}）" if description else location
            _append_labeled(lines, label, description)
    return lines or ["Data Analysis Result 未提供可展示内容。"]


def _render_diagnosis(result: AdsDiagnosisResult | None) -> list[str]:
    if result is None:
        return ["Ads Diagnosis Agent 未调用，暂无投放诊断。"]

    lines: list[str] = []
    _append(lines, result.summary, prefix="")
    for diagnosis in _items(result.diagnoses):
        certainty = _text(getattr(diagnosis, "certainty", None))
        statement = _text(getattr(diagnosis, "statement", None))
        location = _object_label(diagnosis)
        suffix = f"（{location}）" if location else ""
        _append_labeled(lines, certainty or "诊断", f"{statement}{suffix}")
    for cause in _items(result.suspected_causes):
        certainty = _text(getattr(cause, "certainty", None), "hypothesis")
        _append_labeled(lines, certainty, getattr(cause, "cause", None))
    return lines or ["Ads Diagnosis Result 未提供可展示内容。"]


def _render_content(result: ContentReviewResult | None) -> list[str]:
    if result is None:
        return ["不适用 / 本次未调用"]

    lines: list[str] = []
    _append(lines, result.summary, prefix="")
    for finding in _items(result.content_findings):
        material_id = _text(getattr(finding, "material_id", None))
        category = _text(getattr(finding, "category", None))
        finding_text = _text(getattr(finding, "finding", None))
        label = "内容发现"
        if category:
            label = f"内容发现/{category}"
        if material_id:
            finding_text = f"{finding_text}（material={material_id}）"
        _append_labeled(lines, label, finding_text)
    for explanation in _items(result.performance_explanations):
        material_id = _text(getattr(explanation, "material_id", None))
        relationship = _text(getattr(explanation, "content_relationship", None))
        if material_id:
            relationship = f"{relationship}（material={material_id}）"
        _append_labeled(lines, "表现解释", relationship)
    for direction in _items(result.test_directions):
        _append_labeled(lines, "测试方向", getattr(direction, "direction", None))
    return lines or ["Content Review Result 未提供可展示内容。"]


def _render_strategy(result: StrategyResult | None) -> list[str]:
    if result is None:
        return ["Strategy Agent 未调用，暂无策略建议。"]

    lines: list[str] = []
    _append(lines, result.overall_judgement, prefix="")
    for action in _items(result.priority_actions):
        priority = _text(getattr(action, "priority", None))
        action_type = _text(getattr(action, "action_type", None))
        target = _text(getattr(action, "target_id", None))
        reason = _text(getattr(action, "reason", None))
        detail = "｜".join(part for part in (action_type, target, reason) if part)
        _append_labeled(lines, f"优先级 {priority}" if priority else "建议", detail)
    return lines or ["Strategy Result 未提供可展示内容。"]


def _render_observations(
    state: Mapping[str, Any],
    diagnosis: AdsDiagnosisResult | None,
    content: ContentReviewResult | None,
    strategy: StrategyResult | None,
) -> list[str]:
    lines: list[str] = []
    if strategy is not None:
        for item in _items(strategy.observation_items):
            observation = _text(_mapping_value(item, "observation"))
            recheck = _text(_mapping_value(item, "recheck_condition"))
            detail = f"{observation}；复核条件：{recheck}" if recheck else observation
            _append(lines, detail)
    if diagnosis is not None:
        for item in _items(diagnosis.uncertainties):
            question = _text(_mapping_value(item, "question"))
            reason = _text(_mapping_value(item, "reason"))
            _append(lines, f"{question}；原因：{reason}" if reason else question)
    if content is not None:
        for item in _items(content.uncertainties):
            question = _text(_mapping_value(item, "question"))
            reason = _text(_mapping_value(item, "reason"))
            _append(lines, f"{question}；原因：{reason}" if reason else question)
    for item in _items(state.get("open_questions")):
        question = _text(_mapping_value(item, "question"))
        reason = _text(_mapping_value(item, "reason"))
        _append(lines, f"{question}；原因：{reason}" if reason else question)
    return lines


def _render_evidence_gaps(
    data: DataAnalysisResult | None,
    diagnosis: AdsDiagnosisResult | None,
    content: ContentReviewResult | None,
    strategy: StrategyResult | None,
) -> list[str]:
    lines: list[str] = []
    if data is not None:
        for item in _items(data.missing_data):
            field = _text(getattr(item, "field", None))
            reason = _text(getattr(item, "reason", None))
            detail = f"{field}：{reason}" if field else reason
            _append(lines, detail)
    if diagnosis is not None:
        for item in _items(diagnosis.evidence_needed):
            request = _text(getattr(item, "request", None))
            reason = _text(getattr(item, "reason", None))
            detail = f"{request}；原因：{reason}" if reason else request
            _append(lines, detail)
    if content is not None:
        for item in _items(content.uncertainties):
            needed = _text(getattr(item, "needed_input", None))
            if needed:
                _append(lines, needed)
    if strategy is not None:
        request = strategy.evidence_request
        if request is not None:
            text = _text(request.request)
            reason = _text(request.reason)
            _append(lines, f"{text}；原因：{reason}" if reason else text)
        for item in _items(strategy.cannot_conclude):
            _append(lines, item)
    return lines


def _mapping_value(value: Any, *keys: str) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
    for key in keys:
        if hasattr(value, key):
            return getattr(value, key)
    return None


def _render_path(state: Mapping[str, Any]) -> list[str]:
    trace = _items(state.get("route_trace"))
    lines: list[str] = []
    for item in trace:
        step = _mapping_value(item, "step")
        source = _mapping_value(item, "from", "from_agent")
        target = _mapping_value(item, "to", "to_agent")
        task = _text(_mapping_value(item, "task"))
        reason = _text(_mapping_value(item, "reason"))
        route = f"{source} → {target}" if source or target else "未命名路由"
        detail = "；".join(part for part in (task, reason) if part)
        prefix = f"Step {step}: " if step is not None else ""
        _append(lines, f"{prefix}{route}{('｜' + detail) if detail else ''}")
    return lines or ["（暂无 route trace）"]


@dataclass(frozen=True)
class ReportRenderer:
    """Render the current structured State without calling an LLM."""

    include_path: bool = False
    compact: bool = True

    def render(self, state: MultiAgentState | Mapping[str, Any]) -> str:
        if not isinstance(state, Mapping):
            raise TypeError("ReportRenderer state must be a mapping")

        data = _model_or_none(state.get("data_analysis_result"), DataAnalysisResult)
        diagnosis = _model_or_none(state.get("ads_diagnosis_result"), AdsDiagnosisResult)
        content = _model_or_none(state.get("content_review_result"), ContentReviewResult)
        strategy = _model_or_none(state.get("strategy_result"), StrategyResult)

        report_date = _text(state.get("report_date"), "未指定日期")
        run_id = _text(state.get("run_id"), "未指定 run_id")
        lines = [
            f"# 投放诊断日报｜{report_date}",
            f"run_id：{run_id}",
            "",
        ]
        if self.compact:
            _section(lines, "【账户总体】", _render_account_overview(state, strategy, data))
            plans = []
            if data:
                for item in data.anomalies:
                    _append(plans, f"{_object_label(item)}：{item.description}")
            if strategy:
                for action in sorted(strategy.priority_actions, key=lambda x: x.priority):
                    _append(plans, f"{action.target_id or action.target_type}：{action.problem_layer or action.action_type}")
            _section(lines, "【待处理计划】", list(dict.fromkeys(plans)) or ["暂无已确认的待处理计划。"])
            causes = []
            if diagnosis:
                for item in diagnosis.diagnoses:
                    _append(causes, f"{_object_label(item)}：{item.statement}（{item.certainty.value}）")
            if content:
                for item in content.material_assessments:
                    _append(causes, f"素材 {item.material_id}：{item.performance_summary}")
                for item in content.content_findings:
                    _append(causes, f"素材 {item.material_id}正文：{item.finding}")
                for item in content.performance_explanations:
                    _append(causes, f"素材 {item.material_id}内容与表现：{item.content_relationship}")
            _section(lines, "【问题更可能在哪】", causes or ["证据不足，暂不能归因。"])
            actions, checks = [], []
            checks.extend(_render_evidence_gaps(data, diagnosis, content, None))
            checks.extend(_render_observations(state, diagnosis, None, None))
            if content:
                if content.content_analysis_status != "complete":
                    _append(checks, "素材正文分析未完成：" + content.content_analysis_status)
                for item in content.uncertainties:
                    _append(checks, f"素材判断限制：{item.question}；{item.reason}")
            if strategy:
                for item in sorted(strategy.priority_actions, key=lambda x: x.priority):
                    target = item.target_id or item.target_type
                    detail = f"{target}：{item.action_type}；{item.reason}"
                    if item.proposal:
                        p = item.proposal
                        label = "预算" if p.parameter == "budget" else "出价"
                        detail += f"；{label} {p.current_value:g} → {p.proposed_value:g} 元（{p.change_percent:+g}%），待人工执行"
                    if item.why_not_other_actions:
                        detail += f"；其他变量暂不动：{item.why_not_other_actions}"
                    _append(actions, detail)
                    check = f"{target}：{item.recheck_condition}；有效信号：{item.expected_signal}"
                    if item.invalidation_condition:
                        check += f"；反证：{item.invalidation_condition}"
                    _append(checks, check)
                for item in strategy.observation_items:
                    _append(checks, f"{item.target_id or item.target_type}：{item.observation}；{item.recheck_condition}")
                for gap in strategy.cannot_conclude:
                    _append(checks, f"判断限制：{gap}")
                if strategy.evidence_request:
                    _append(checks, f"需补：{strategy.evidence_request.request}；{strategy.evidence_request.reason}")
            for error in state.get("errors") or []:
                _append(checks, f"运行限制：{error}")
            if not strategy:
                checks.append("- 策略结果缺失，不能把未生成建议解释为无需调整。")
            _section(lines, "【今天具体做什么】", actions or ["暂无已形成的调整建议。"])
            _section(lines, "【下一次看什么】", list(dict.fromkeys(checks)))
            return "\n".join(lines).rstrip() + "\n"
        _section(lines, "【今日重点】", _render_today_focus(strategy))
        _section(lines, "【数据发现】", _render_data(data))
        _section(lines, "【投放诊断】", _render_diagnosis(diagnosis))
        _section(lines, "【内容审核】", _render_content(content))
        _section(lines, "【策略建议】", _render_strategy(strategy))
        _section(lines, "【仍需观察】", _render_observations(state, diagnosis, content, strategy) or ["（无结构化观察事项）"])
        _section(lines, "【证据不足项】", _render_evidence_gaps(data, diagnosis, content, strategy) or ["（无结构化证据不足项）"])
        if self.include_path:
            _section(lines, "【分析路径】", _render_path(state))
        return "\n".join(lines).rstrip() + "\n"

    __call__ = render


def render_daily_report(state: MultiAgentState | Mapping[str, Any]) -> str:
    """Functional entry point for deterministic daily report rendering."""

    return ReportRenderer().render(state)


render_report = render_daily_report


def report_renderer_node(state: MultiAgentState) -> dict[str, Any]:
    """LangGraph node boundary for the human-facing report."""

    return {"final_report": render_daily_report(state)}


__all__ = [
    "ReportRenderer",
    "render_daily_report",
    "render_report",
    "report_renderer_node",
]
