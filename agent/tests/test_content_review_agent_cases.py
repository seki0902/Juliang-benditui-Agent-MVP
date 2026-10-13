import unittest
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agents.content_review_agent import ContentReviewAgent
from schemas.ads_diagnosis import AdsDiagnosisResult, ContentTarget
from schemas.common import ConfidenceLevel, EvidenceItem, ToolCallRecord
from schemas.content_review import (
    ContentFinding,
    ContentReviewResult,
    ContentUncertainty,
    MaterialReviewed,
    PerformanceExplanation,
    TestDirection,
)
from schemas.data_analysis import DataAnalysisResult


def _content_evidence(material_id: str, field: str, value: Any) -> EvidenceItem:
    return EvidenceItem(
        evidence_type="content",
        source="fixture.content_asset",
        object_type="material",
        object_id=material_id,
        metric_or_content=field,
        value=value,
        time_range=None,
    )


def _metric_evidence(material_id: str, metric: str, value: Any) -> EvidenceItem:
    return EvidenceItem(
        evidence_type="metric",
        source="fixture.material_metrics",
        object_type="material",
        object_id=material_id,
        metric_or_content=metric,
        value=value,
        time_range="2026-09-17",
    )


@dataclass(frozen=True)
class _ContentReviewCase:
    name: str
    state: dict[str, Any]
    result: ContentReviewResult
    tool_calls: tuple[dict[str, Any], ...] = ()
    tool_names: tuple[str, ...] = ()
    content_available: bool = True


class _CaseStructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        # Return JSON-shaped data so every fixture exercises the shared
        # ContentReviewResult validation boundary.
        return self.model.case.result.model_dump(mode="json")


class _CaseModel:
    def __init__(self, case):
        self.case = case
        self.bound_tools = None
        self.planning_inputs = []
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None
        self._tool_calls_emitted = False

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.planning_inputs.append(list(messages))
        if self.case.tool_calls and not self._tool_calls_emitted:
            self._tool_calls_emitted = True
            return AIMessage(content="", tool_calls=list(self.case.tool_calls))
        return AIMessage(content="当前内容证据已完成技术性整理")

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _CaseStructuredRunnable(self)


def _tool_fixture(name: str, *, content_available: bool):
    def tool(**arguments):
        material_id = (
            arguments.get("material_id")
            or arguments.get("object_id")
            or "material-1"
        )
        if name == "get_content_asset" and not content_available:
            return {
                "status": "unavailable",
                "source": None,
                "time_window": None,
                "data": {},
                "material_id": material_id,
                "found": False,
                "reason": "fixture 没有可读取的素材内容",
                "warnings": ["素材内容不可用"],
            }
        return {
            "status": "ok",
            "source": f"fixture.{name}",
            "time_window": None
            if name == "get_content_asset"
            else {"name": arguments.get("time_window", "day")},
            "data": {
                "material_id": material_id,
                "object_id": material_id,
                "hook": f"{material_id} 的场景开头",
                "clicks": 42,
                "leads": 3,
            },
            "warnings": [],
        }

    return tool


def _base_state(task: str, material_ids: list[str], evidence=()):
    return {
        "run_id": "content-review-mock-run",
        "report_date": "2026-09-17",
        "task": task,
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": DataAnalysisResult(
            summary="已观察到素材表现信号，等待内容层核验。"
        ),
        "ads_diagnosis_result": AdsDiagnosisResult(
            summary="按需核验指定素材。",
            need_content_review=True,
            content_targets=[
                ContentTarget(
                    object_id=material_id,
                    reason="需要核验素材内容。",
                )
                for material_id in material_ids
            ],
        ),
        "content_review_result": None,
        "evidence": list(evidence),
    }


def _case_fixtures():
    complete_content = _content_evidence(
        "material-1", "hook", "为什么投了很多岗位仍没有反馈？"
    )
    complete_metric = _metric_evidence("material-1", "clicks", 42)
    high_click = _content_evidence("material-2", "hook", "高点击样例开头")
    high_click_metric = _metric_evidence("material-2", "valid_lead_rate", 0.08)
    low_click = _content_evidence("material-3", "cta", "点击了解准备方法")
    low_click_metric = _metric_evidence("material-3", "clicks", 2)
    compare_one = _content_evidence("material-4", "hook", "场景一开头")
    compare_two = _content_evidence("material-5", "hook", "场景二开头")
    compare_one_metric = _metric_evidence("material-4", "clicks", 20)
    compare_two_metric = _metric_evidence("material-5", "clicks", 25)

    return (
        _ContentReviewCase(
            name="A_content_complete",
            state=_base_state(
                "审核 material-1 的完整素材内容",
                ["material-1"],
                [complete_content, complete_metric],
            ),
            result=ContentReviewResult(
                summary="已读取 material-1 的内容并记录有限的表现关系。",
                materials_reviewed=[MaterialReviewed(material_id="material-1")],
                content_findings=[
                    ContentFinding(
                        material_id="material-1",
                        category="Hook",
                        finding="素材开头呈现了明确的求职反馈场景。",
                        content_evidence=[complete_content],
                        confidence=ConfidenceLevel.MEDIUM,
                    )
                ],
                performance_explanations=[
                    PerformanceExplanation(
                        material_id="material-1",
                        performance_signal="点击数据可用",
                        content_relationship="该内容与点击信号可能有关，但当前不能确认因果。",
                        supporting_evidence=[complete_metric],
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                content_evidence=[complete_content],
                uncertainties=[
                    ContentUncertainty(
                        question="素材内容与线索质量是否存在关系？",
                        reason="当前只有有限的表现证据。",
                        needed_input="线索质量明细",
                    )
                ],
                confidence=ConfidenceLevel.MEDIUM,
            ),
            tool_calls=(
                {
                    "name": "get_content_asset",
                    "args": {"material_id": "material-1"},
                    "id": "case-a-content",
                    "type": "tool_call",
                },
                {
                    "name": "get_material_metrics",
                    "args": {
                        "material_id": "material-1",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-a-metrics",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_content_asset", "get_material_metrics"),
        ),
        _ContentReviewCase(
            name="B_content_missing",
            state=_base_state("审核 material-1，但素材内容可能缺失", ["material-1"]),
            result=ContentReviewResult(
                summary="没有可靠素材内容，无法完成内容本身判断。",
                materials_reviewed=[],
                content_findings=[],
                performance_explanations=[],
                content_evidence=[],
                uncertainties=[
                    ContentUncertainty(
                        question="material-1 的真实素材内容是什么？",
                        reason="素材内容 Tool 返回 unavailable。",
                        needed_input="标题、口播稿或视频转写",
                    )
                ],
                confidence=ConfidenceLevel.UNKNOWN,
            ),
            tool_calls=(
                {
                    "name": "get_content_asset",
                    "args": {"material_id": "material-1"},
                    "id": "case-b-content",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_content_asset",),
            content_available=False,
        ),
        _ContentReviewCase(
            name="C_high_click_low_quality",
            state=_base_state(
                "审核高点击但低有效线索的 material-2",
                ["material-2"],
                [high_click, high_click_metric],
            ),
            result=ContentReviewResult(
                summary="material-2 的高点击与低有效线索率是需要进一步核验的表现冲突。",
                materials_reviewed=[MaterialReviewed(material_id="material-2")],
                content_findings=[
                    ContentFinding(
                        material_id="material-2",
                        category="Hook",
                        finding="素材内容包含较强的点击吸引场景。",
                        content_evidence=[high_click],
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                performance_explanations=[
                    PerformanceExplanation(
                        material_id="material-2",
                        performance_signal="点击较高但有效线索率较低",
                        content_relationship="内容吸引点击与线索质量之间可能存在不一致，但不能仅凭这些指标确认因果。",
                        supporting_evidence=[high_click_metric],
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                content_evidence=[high_click],
                uncertainties=[
                    ContentUncertainty(
                        question="表现冲突来自内容衔接还是线索质量链路？",
                        reason="当前内容和聚合表现证据不足以区分两种可能。",
                        needed_input="线索质量明细",
                    )
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            tool_calls=(
                {
                    "name": "get_content_asset",
                    "args": {"material_id": "material-2"},
                    "id": "case-c-content",
                    "type": "tool_call",
                },
                {
                    "name": "get_material_metrics",
                    "args": {
                        "material_id": "material-2",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-c-metrics",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_content_asset", "get_material_metrics"),
        ),
        _ContentReviewCase(
            name="D_low_click_material",
            state=_base_state(
                "审核低点击的 material-3 是否存在可观察内容因素",
                ["material-3"],
                [low_click, low_click_metric],
            ),
            result=ContentReviewResult(
                summary="material-3 的低点击表现已记录，但不能仅凭表现确定内容原因。",
                materials_reviewed=[MaterialReviewed(material_id="material-3")],
                content_findings=[
                    ContentFinding(
                        material_id="material-3",
                        category="CTA",
                        finding="素材包含行动指引表达。",
                        content_evidence=[low_click],
                        confidence=ConfidenceLevel.MEDIUM,
                    )
                ],
                performance_explanations=[
                    PerformanceExplanation(
                        material_id="material-3",
                        performance_signal="点击数据较少",
                        content_relationship="当前只能确认低点击信号，不能据此确认由 CTA 或其他内容特征造成。",
                        supporting_evidence=[low_click_metric],
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                content_evidence=[low_click],
                test_directions=[
                    TestDirection(
                        direction="比较不同 CTA 表达的内容测试方向",
                        reason="验证 CTA 表达与点击信号之间的可能关系。",
                        target_material_ids=["material-3"],
                        expected_signal="点击表现信号",
                        recheck_condition="补充可比窗口后复核",
                    )
                ],
                uncertainties=[
                    ContentUncertainty(
                        question="低点击是否与 CTA 内容有关？",
                        reason="当前缺少可比内容版本和足够表现窗口。",
                        needed_input="可比素材版本和历史表现",
                    )
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            tool_calls=(
                {
                    "name": "get_content_asset",
                    "args": {"material_id": "material-3"},
                    "id": "case-d-content",
                    "type": "tool_call",
                },
                {
                    "name": "get_material_metrics",
                    "args": {
                        "material_id": "material-3",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-d-metrics",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_content_asset", "get_material_metrics"),
        ),
        _ContentReviewCase(
            name="E_multiple_material_comparison",
            state=_base_state(
                "比较 material-4 和 material-5 的内容与表现信号",
                ["material-4", "material-5"],
                [compare_one, compare_two, compare_one_metric, compare_two_metric],
            ),
            result=ContentReviewResult(
                summary="已分别读取两个素材并保留可比范围与内容归因限制。",
                materials_reviewed=[
                    MaterialReviewed(material_id="material-4"),
                    MaterialReviewed(material_id="material-5"),
                ],
                content_findings=[
                    ContentFinding(
                        material_id="material-4",
                        category="Hook",
                        finding="material-4 使用场景一开头。",
                        content_evidence=[compare_one],
                        confidence=ConfidenceLevel.MEDIUM,
                    ),
                    ContentFinding(
                        material_id="material-5",
                        category="Hook",
                        finding="material-5 使用场景二开头。",
                        content_evidence=[compare_two],
                        confidence=ConfidenceLevel.MEDIUM,
                    ),
                ],
                performance_explanations=[
                    PerformanceExplanation(
                        material_id="material-4",
                        performance_signal="material-4 点击信号可用",
                        content_relationship="可与 material-5 做范围受限的内容表现比较。",
                        supporting_evidence=[compare_one_metric],
                        confidence=ConfidenceLevel.LOW,
                    ),
                    PerformanceExplanation(
                        material_id="material-5",
                        performance_signal="material-5 点击信号可用",
                        content_relationship="可与 material-4 做范围受限的内容表现比较。",
                        supporting_evidence=[compare_two_metric],
                        confidence=ConfidenceLevel.LOW,
                    ),
                ],
                content_evidence=[compare_one, compare_two],
                test_directions=[
                    TestDirection(
                        direction="比较两个素材的场景开头表达",
                        reason="保持可比窗口后观察内容差异与表现信号的可能关系。",
                        target_material_ids=["material-4", "material-5"],
                        expected_signal="相对表现信号",
                        recheck_condition="确认时间窗口和人群可比后复核",
                    )
                ],
                uncertainties=[
                    ContentUncertainty(
                        question="两个素材的表现差异是否主要来自内容差异？",
                        reason="当前还缺少完整的人群、时间和投放环境对齐信息。",
                        needed_input="可比投放窗口和人群信息",
                    )
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            tool_calls=(
                {
                    "name": "get_content_asset",
                    "args": {"material_id": "material-4"},
                    "id": "case-e-content-1",
                    "type": "tool_call",
                },
                {
                    "name": "get_content_asset",
                    "args": {"material_id": "material-5"},
                    "id": "case-e-content-2",
                    "type": "tool_call",
                },
                {
                    "name": "get_material_metrics",
                    "args": {
                        "material_id": "material-4",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-e-metrics-1",
                    "type": "tool_call",
                },
                {
                    "name": "get_material_metrics",
                    "args": {
                        "material_id": "material-5",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-e-metrics-2",
                    "type": "tool_call",
                },
                {
                    "name": "get_history_context",
                    "args": {
                        "object_type": "material",
                        "object_id": "material-4",
                        "report_date": "2026-09-17",
                        "time_window": "previous_7d",
                    },
                    "id": "case-e-history",
                    "type": "tool_call",
                },
            ),
            tool_names=(
                "get_content_asset",
                "get_material_metrics",
                "get_history_context",
            ),
        ),
    )


class ContentReviewAgentCaseTests(unittest.TestCase):
    def test_five_mock_cases_pass_technical_boundary_checks(self):
        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                tools = {
                    name: _tool_fixture(
                        name,
                        content_available=case.content_available,
                    )
                    for name in case.tool_names
                }

                update = ContentReviewAgent(
                    model=model,
                    tools=tools or None,
                    principles="fixture content-review principles; no business thresholds",
                    max_tool_calls=6,
                ).run(case.state)
                result = update["content_review_result"]

                self.assertIsInstance(result, ContentReviewResult)
                self.assertEqual(model.structured_schema, ContentReviewResult)
                self.assertIn("content_review_result", update)
                self.assertIn("evidence", update)
                self.assertIn("open_questions", update)
                self.assertIn("tool_trace", update)
                self.assertEqual(len(update["tool_trace"]), len(case.tool_calls))
                self.assertEqual(len(update["open_questions"]), len(result.uncertainties))
                self.assertTrue(
                    all(isinstance(item, ToolCallRecord) for item in update["tool_trace"])
                )

                forbidden = {
                    "budget_change",
                    "bid_change",
                    "pause_plan",
                    "scale_percentage",
                    "execution_status",
                    "backend_action",
                }
                self.assertTrue(forbidden.isdisjoint(result.model_dump()))

                tool_messages = [
                    message
                    for message in model.structured_inputs[0]
                    if isinstance(message, ToolMessage)
                ]
                self.assertEqual(len(tool_messages), len(case.tool_calls))

                if case.name == "A_content_complete":
                    self.assertEqual(
                        [record.tool_name for record in update["tool_trace"]],
                        ["get_content_asset", "get_material_metrics"],
                    )
                    self.assertTrue(result.materials_reviewed)
                    self.assertTrue(result.content_findings)
                    self.assertTrue(result.content_evidence)
                elif case.name == "B_content_missing":
                    self.assertEqual(update["tool_trace"][0].status, "unavailable")
                    self.assertFalse(result.materials_reviewed)
                    self.assertFalse(result.content_findings)
                    self.assertFalse(result.content_evidence)
                    self.assertTrue(result.uncertainties)
                    self.assertIn("无法完成内容本身判断", result.summary)
                elif case.name == "C_high_click_low_quality":
                    self.assertTrue(result.content_findings)
                    self.assertTrue(result.performance_explanations)
                    self.assertTrue(result.uncertainties)
                    self.assertIn("不能仅凭这些指标确认因果", result.performance_explanations[0].content_relationship)
                elif case.name == "D_low_click_material":
                    self.assertTrue(result.test_directions)
                    self.assertTrue(result.uncertainties)
                    self.assertIn("不能据此确认", result.performance_explanations[0].content_relationship)
                elif case.name == "E_multiple_material_comparison":
                    self.assertEqual(
                        {item.material_id for item in result.materials_reviewed},
                        {"material-4", "material-5"},
                    )
                    self.assertEqual(
                        {item.material_id for item in result.content_findings},
                        {"material-4", "material-5"},
                    )
                    self.assertEqual(len(result.performance_explanations), 2)
                    self.assertEqual(len(result.test_directions[0].target_material_ids), 2)

    def test_missing_content_case_does_not_create_content_messages_without_a_tool_call(self):
        case = next(case for case in _case_fixtures() if case.name == "B_content_missing")
        model = _CaseModel(case)
        update = ContentReviewAgent(
            model=model,
            tools={
                "get_content_asset": _tool_fixture(
                    "get_content_asset",
                    content_available=False,
                )
            },
            principles="fixture principles",
        ).run(case.state)

        content_messages = [
            message
            for message in model.structured_inputs[0]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(len(content_messages), 1)
        self.assertIn('"status": "unavailable"', content_messages[0].content)
        self.assertFalse(update["content_review_result"].content_findings)


if __name__ == "__main__":
    unittest.main()
