"""V2 contract regressions. Fake model tests do not certify model judgement."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from agents.strategy_agent import StrategyAgent, StrategyAgentError
from agents.content_review_agent import ContentReviewAgent
from schemas.strategy import AdjustmentProposal, PriorityAction, StrategyResult
from schemas.content_review import ContentReviewResult, MaterialAssessment
from schemas.common import EvidenceItem
from services.context_builder import ContextBuilder
from services.metrics_tool_utils import resolve_window
from services.report_renderer import ReportRenderer
from services.report_polisher import validate_polished_report, ReportPolishGuardError
from services.v2_context_tools import V2ContextTools


def snapshot(tmp_path, name, rows, account="a1"):
    path = tmp_path / name
    path.write_text(json.dumps({"account_id": account, "rows": rows}), encoding="utf-8")
    return path


def context(tmp_path):
    path = snapshot(tmp_path, "plans.json", [
        {"data_date": "2026-09-17", "plan_id": "p1", "budget": 600, "bid": 150,
         "material_ids": ["m1"], "traffic_type": "recommendation", "targeting": {"region": "上海"}},
        {"data_date": "2026-09-19", "plan_id": "p1", "budget": 900, "bid": 200,
         "material_ids": ["future"]},
    ])
    return V2ContextTools("a1", "2026-09-18", plan_context_path=path)


def test_yesterday_in_shanghai_and_explicit_history():
    clock = lambda: datetime(2026, 9, 19, 17, tzinfo=timezone.utc)
    assert ContextBuilder(clock=clock).build({})["report_date"] == "2026-09-19"
    assert ContextBuilder(clock=clock).build({"report_date": "2026-08-23"})["report_date"] == "2026-08-23"
    for window, days in (("1d", 1), ("3d", 3), ("5d", 5), ("30d", 30)):
        start, end, _ = resolve_window("2026-09-19", window)
        assert (end - start).days + 1 == days


def test_shimo_source_scope_missing_and_real_zero(tmp_path):
    path = snapshot(tmp_path, "shimo.json", [
        {"data_date": "2026-09-17", "valid_leads": 0, "invalid_leads": 2, "spend": 100},
        {"data_date": "2026-09-18", "valid_leads": 2, "invalid_leads": 1, "spend": 600},
        {"data_date": "2026-09-19", "valid_leads": 999, "spend": 999},
    ])
    tool = V2ContextTools("a1", "2026-09-18", shimo_path=path)
    zero = tool.get_deep_conversion("2026-09-17", "1d")
    assert zero["data"]["deep_conversions"] == 0
    assert zero["data"]["deep_conversion_cost"] is None
    known = tool.get_deep_conversion("2026-09-18", "1d")
    assert known["data"]["deep_conversion_cost"] == 300
    assert known["data"]["feedback_delivery_status"] == "unknown"
    assert tool.get_deep_conversion("2026-09-18", "3d")["data"]["deep_conversions"] is None
    assert tool.get_deep_conversion("2026-09-18", "1d", "plan", "p1")["status"] == "unavailable"
    with pytest.raises(ValueError):
        tool.get_deep_conversion("2026-09-19", "1d")
    with pytest.raises(ValueError):
        V2ContextTools("wrong", "2026-09-18", shimo_path=path)


def test_shimo_blank_day_is_named_not_reported_as_missing(tmp_path):
    path = snapshot(tmp_path, "blank.json", [
        {"data_date": "2026-09-13", "valid_leads": 0, "invalid_leads": 0, "spend": 60},
        {"data_date": "2026-09-14", "valid_leads": 0, "invalid_leads": 0, "spend": 70},
        {"data_date": "2026-09-15", "valid_leads": None, "invalid_leads": 0, "spend": 134.25},
    ])
    result = V2ContextTools("a1", "2026-09-15", shimo_path=path).get_deep_conversion("2026-09-15", "3d")
    data = result["data"]
    assert result["status"] == "partial"
    assert data["missing_dates"] == []
    assert data["blank_value_dates"] == {"valid_leads": ["2026-09-15"]}
    assert data["valid_leads"] is None
    assert data["invalid_leads"] == 0
    assert data["spend"] == 264.25
    assert "2026-09-15" in " ".join(result["warnings"])
    complete = V2ContextTools("a1", "2026-09-14", shimo_path=path).get_deep_conversion("2026-09-14", "1d")
    assert complete["status"] == "ok"
    assert complete["data"]["blank_value_dates"] == {}
    assert complete["data"]["missing_dates"] == []
    assert complete["data"]["deep_conversions"] == 0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, 1.5])
def test_shimo_rejects_invalid_counts(tmp_path, value):
    path = snapshot(tmp_path, "bad.json", [{"data_date": "2026-09-18", "valid_leads": value}])
    with pytest.raises(ValueError):
        V2ContextTools("a1", "2026-09-18", shimo_path=path).get_deep_conversion("2026-09-18", "1d")


def test_shimo_duplicate_rows_rejected(tmp_path):
    row = {"data_date": "2026-09-18", "valid_leads": 1}
    path = snapshot(tmp_path, "duplicate.json", [row, row])
    with pytest.raises(ValueError):
        V2ContextTools("a1", "2026-09-18", shimo_path=path).get_deep_conversion("2026-09-18", "1d")


def test_plan_context_and_membership_cutoff(tmp_path):
    tool = context(tmp_path)
    assert tool.get_plan_context("p1", "2026-09-18")["data"]["latest"]["budget"] == 600
    assert len(tool.get_material_plan_comparison("m1", "2026-09-18")["data"]["plans"]) == 1
    assert tool.get_material_plan_comparison("future", "2026-09-18")["status"] == "unavailable"
    assert tool.get_plan_context("unknown", "2026-09-18")["status"] == "unavailable"


def test_content_snapshot_body_and_future_cutoff(tmp_path):
    path = snapshot(tmp_path, "content.json", [
        {"data_date": "2026-09-17", "material_id": "m1", "script": "面向毕业生的求职准备", "visual_notes": "简历画面"},
        {"data_date": "2026-09-19", "material_id": "m1", "script": "future"},
    ])
    tool = V2ContextTools("a1", "2026-09-18", content_path=path)
    result = tool.get_content_asset("m1")
    assert result["script"] == "面向毕业生的求职准备"
    assert result["visual_notes"] == "简历画面"
    assert tool.get_content_asset("missing")["status"] == "unavailable"


def test_numeric_options_are_math_only_and_do_not_mutate(tmp_path):
    tool = context(tmp_path)
    before = json.dumps(tool.context, sort_keys=True)
    result = tool.get_adjustment_options("p1", "2026-09-18")
    options = result["data"]["options"]
    assert len(options) == 12
    option = next(o for o in options if o["parameter"] == "budget" and o["change_percent"] == 20)
    assert option["proposed_value"] == 720
    AdjustmentProposal(**option)
    assert json.dumps(tool.context, sort_keys=True) == before
    with pytest.raises(ValidationError):
        AdjustmentProposal(**{**option, "proposed_value": 999})
    with pytest.raises(ValidationError):
        AdjustmentProposal(**{**option, "change_percent": 50})


class Model:
    def __init__(self, result, calls=()):
        self.result, self.calls, self.messages = result, list(calls), []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return self.calls.pop(0) if self.calls else AIMessage(content="facts collected")

    def with_structured_output(self, schema, **kwargs):
        owner = self
        class Bound:
            def invoke(self, messages):
                owner.messages = messages
                return owner.result
        return Bound()


def action(proposal=None, target="p1"):
    return PriorityAction(priority=1, target_type="plan", target_id=target,
        action_type="increase_budget", reason="持续接近花满且成本正常", expected_signal="消耗增加且深转成本保持",
        recheck_condition="明天复查，结合3天5天", why_not_other_actions="素材和出价暂无异常",
        invalidation_condition="放量后深转持续恶化", proposal=proposal)


def test_strategy_proposal_must_match_target_and_real_tool_output(tmp_path):
    tool = context(tmp_path)
    option = tool.get_adjustment_options("p1", "2026-09-18")["data"]["options"][3]
    result = StrategyResult(overall_judgement="预算可能限制放量", priority_actions=[action(option)])
    calls = [AIMessage(content="", tool_calls=[{"id": "c1", "name": "get_adjustment_options",
             "args": {"plan_id": "p1", "report_date": "2026-09-18"}}])]
    bound = StructuredTool.from_function(tool.get_adjustment_options)
    update = StrategyAgent(Model(result, calls), tools=[bound]).run({})
    assert update["tool_trace"][0].agent.value == "strategy"
    assert update["strategy_result"].priority_actions[0].recommendation_only is True
    with pytest.raises(StrategyAgentError):
        StrategyAgent(Model(result)).run({})
    wrong = StrategyResult(overall_judgement="fixture", priority_actions=[action(option, "other")])
    with pytest.raises(StrategyAgentError):
        StrategyAgent(Model(wrong, calls), tools=[bound]).run({})


def test_material_performance_evidence_survives_without_claiming_complete_content():
    evidence = EvidenceItem(evidence_type="metric", source="fixture", object_id="m1",
                            object_type="material", metric_or_content="spend", value=80)
    result = ContentReviewResult(summary="有消耗但缺正文，内容归因未完成",
        material_assessments=[MaterialAssessment(material_id="m1", performance_summary="小样本",
                                                 supporting_evidence=[evidence])])
    model = Model(result)
    update = ContentReviewAgent(model).run({})
    assert update["content_review_result"].content_analysis_status == "missing"
    assert evidence in update["evidence"]
    assert "主动读取正文" in model.messages[1].content


def test_five_section_report_preserves_actions_limits_and_recheck():
    state = {"report_date": "2026-09-18", "strategy_result": StrategyResult(
        overall_judgement="账户整体正常", priority_actions=[action()], cannot_conclude=["缺计划级石墨反馈"]),
        "errors": ["Tool limit"]}
    report = ReportRenderer().render(state)
    assert report.count("【") == 5
    for text in ("素材和出价暂无异常", "放量后深转持续恶化", "缺计划级石墨反馈", "Tool limit"):
        assert text in report
    assert "【分析路径】" not in report
    assert validate_polished_report(report, report) == report
    with pytest.raises(ReportPolishGuardError):
        validate_polished_report(report, report.replace("账户整体正常", "已执行加预算"))


def test_content_complete_claim_is_downgraded_without_verified_body():
    from schemas.content_review import MaterialReviewed
    result = ContentReviewResult(summary="model claims complete", content_analysis_status="complete",
                                 materials_reviewed=[MaterialReviewed(material_id="m1")])
    update = ContentReviewAgent(Model(result)).run({})
    assert update["content_review_result"].content_analysis_status == "missing"
    assert update["content_review_result"].uncertainties


def test_verified_body_allows_complete_content_analysis():
    from schemas.content_review import MaterialReviewed
    evidence = EvidenceItem(evidence_type="content", source="verified_snapshot", object_type="material",
                            object_id="m1", metric_or_content="script", value="实际口播正文")
    result = ContentReviewResult(summary="fixture", content_analysis_status="complete",
                                 materials_reviewed=[MaterialReviewed(material_id="m1")])
    update = ContentReviewAgent(Model(result)).run({"evidence": [evidence]})
    assert update["content_review_result"].content_analysis_status == "complete"


def test_owner_cases_are_loadable_and_answers_are_not_injected():
    from services.golden_case_runner import discover_golden_cases, load_golden_case, initial_state_for_case
    paths = discover_golden_cases(Path(__file__).resolve().parents[1] / "docs" / "golden_cases_v2")
    assert len(paths) == 5
    for path in paths:
        case = load_golden_case(path)
        state = initial_state_for_case(case)
        assert "human_analysis" not in state["account_context"]["golden_case_fixture"]
        assert case.human_analysis.strategy_direction
