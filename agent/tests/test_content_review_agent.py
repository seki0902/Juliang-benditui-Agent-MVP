import unittest

from langchain_core.messages import AIMessage, ToolMessage

from agents.content_review_agent import ContentReviewAgent, content_review_node
from schemas.ads_diagnosis import AdsDiagnosisResult, ContentTarget
from schemas.common import ConfidenceLevel, EvidenceItem, OpenQuestion, ToolCallRecord
from schemas.content_review import (
    ContentFinding,
    ContentReviewResult,
    ContentUncertainty,
    MaterialReviewed,
    PerformanceExplanation,
    TestDirection,
)
from schemas.data_analysis import DataAnalysisResult
from state import apply_state_update


class _FakeStructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        return self.model.final_result


class _FakeToolModel:
    def __init__(self, planning_responses, final_result):
        self.planning_responses = list(planning_responses)
        self.final_result = final_result
        self.bound_tools = None
        self.planning_inputs = []
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.planning_inputs.append(list(messages))
        if self.planning_responses:
            return self.planning_responses.pop(0)
        return AIMessage(content="内容证据已收集")

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _FakeStructuredRunnable(self)


class ContentReviewAgentTests(unittest.TestCase):
    def _state(self):
        return {
            "run_id": "run-1",
            "report_date": "2026-09-17",
            "task": "审核 material-1 的内容以及它与投放表现的可能关系",
            "account_context": {"account_id": "acct-1"},
            "data_analysis_result": DataAnalysisResult(
                summary="material-1 需要进一步核验内容与表现关系。"
            ),
            "ads_diagnosis_result": AdsDiagnosisResult(
                summary="建议按需核验 material-1。",
                need_content_review=True,
                content_targets=[
                    ContentTarget(
                        object_id="material-1",
                        reason="需要核验具体素材内容。",
                    )
                ],
            ),
            "content_review_result": None,
            "evidence": [],
        }

    def _content_evidence(self):
        return EvidenceItem(
            evidence_type="content",
            source="fixture.content_asset",
            object_type="material",
            object_id="material-1",
            metric_or_content="hook",
            value="为什么投了很多岗位仍没有反馈？",
            time_range=None,
        )

    def _performance_evidence(self):
        return EvidenceItem(
            evidence_type="metric",
            source="fixture.material",
            object_type="material",
            object_id="material-1",
            metric_or_content="clicks",
            value=42,
            time_range="2026-09-17",
        )

    def _result(self, *, missing_content=False):
        if missing_content:
            return ContentReviewResult(
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
            )

        content_evidence = self._content_evidence()
        performance_evidence = self._performance_evidence()
        return ContentReviewResult(
            summary="已基于素材内容和表现证据完成有限审核。",
            materials_reviewed=[MaterialReviewed(material_id="material-1")],
            content_findings=[
                ContentFinding(
                    material_id="material-1",
                    category="Hook",
                    finding="开头提出了明确的求职反馈场景。",
                    content_evidence=[content_evidence],
                    confidence=ConfidenceLevel.MEDIUM,
                )
            ],
            performance_explanations=[
                PerformanceExplanation(
                    material_id="material-1",
                    performance_signal="点击表现已被观察到",
                    content_relationship="该内容可能与点击信号存在关系，但不能单独确认因果。",
                    supporting_evidence=[performance_evidence],
                    confidence=ConfidenceLevel.LOW,
                )
            ],
            content_evidence=[content_evidence],
            test_directions=[
                TestDirection(
                    direction="比较不同场景开头的内容表达",
                    reason="验证开头场景与点击表现之间的可能关系。",
                    target_material_ids=["material-1"],
                    expected_signal="内容表现信号",
                    recheck_condition="补充可比窗口后复核",
                )
            ],
            uncertainties=[
                ContentUncertainty(
                    question="内容表达与线索质量之间是否存在关系？",
                    reason="当前只有点击表现证据，缺少完整质量反馈。",
                    needed_input="线索质量明细",
                )
            ],
            confidence=ConfidenceLevel.MEDIUM,
        )

    def test_node_reads_content_and_performance_with_runtime_tools(self):
        calls = []

        def get_content_asset(material_id):
            calls.append(("content", material_id))
            return {
                "status": "ok",
                "source": "fixture.content_asset",
                "time_window": None,
                "data": {
                    "material_id": material_id,
                    "hook": "为什么投了很多岗位仍没有反馈？",
                    "script": "了解准备方法",
                },
                "material_id": material_id,
                "hook": "为什么投了很多岗位仍没有反馈？",
                "script": "了解准备方法",
                "warnings": [],
            }

        def get_material_metrics(material_id, report_date, time_window):
            calls.append(("metrics", material_id, report_date, time_window))
            return {
                "status": "ok",
                "source": "fixture.material",
                "time_window": {"name": time_window},
                "data": {"clicks": 42, "leads": 3},
                "warnings": [],
            }

        def get_history_context(**kwargs):
            raise AssertionError("history is not needed for this review")

        model = _FakeToolModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_content_asset",
                            "args": {"material_id": "material-1"},
                            "id": "call-content-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "get_material_metrics",
                            "args": {
                                "material_id": "material-1",
                                "report_date": "2026-09-17",
                                "time_window": "day",
                            },
                            "id": "call-metrics-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="内容和表现证据已收集"),
            ],
            self._result(),
        )

        update = content_review_node(
            self._state(),
            model=model,
            tools={
                "get_content_asset": get_content_asset,
                "get_material_metrics": get_material_metrics,
                "get_history_context": get_history_context,
            },
            principles="fixture content-review principles",
            max_tool_calls=3,
        )

        self.assertEqual(
            calls,
            [
                ("content", "material-1"),
                ("metrics", "material-1", "2026-09-17", "day"),
            ],
        )
        self.assertEqual(len(update["tool_trace"]), 2)
        self.assertTrue(all(isinstance(item, ToolCallRecord) for item in update["tool_trace"]))
        self.assertEqual(update["tool_trace"][0].tool_name, "get_content_asset")
        self.assertEqual(update["tool_trace"][1].tool_name, "get_material_metrics")
        self.assertIsInstance(update["content_review_result"], ContentReviewResult)
        self.assertEqual(model.structured_schema, ContentReviewResult)
        self.assertFalse(model.structured_include_raw)
        self.assertEqual(len(update["evidence"]), 2)
        self.assertEqual(len(update["open_questions"]), 1)
        self.assertIsInstance(update["open_questions"][0], OpenQuestion)
        self.assertIn("fixture content-review principles", model.structured_inputs[0][0].content)
        tool_messages = [
            message
            for message in model.structured_inputs[0]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(len(tool_messages), 2)
        self.assertIn("material-1", tool_messages[0].content)

    def test_node_loads_filled_project_principles_when_not_overridden(self):
        model = _FakeToolModel([], self._result())

        ContentReviewAgent(model=model, tools=None).run(self._state())

        system_text = model.structured_inputs[0][0].content
        self.assertIn("Content Review Principles", system_text)
        principle_text = system_text[system_text.rfind("# Content Review Principles") :]
        self.assertIn("正文与投放数据一起分析", principle_text)
        self.assertIn("P40", principle_text)
        self.assertIn("正文与投放数据一起分析", principle_text)
        self.assertNotIn("TODO", principle_text)

    def test_node_keeps_missing_content_as_unavailable_and_uncertain(self):
        def get_content_asset(material_id):
            return {
                "status": "unavailable",
                "source": None,
                "time_window": None,
                "data": {},
                "material_id": material_id,
                "found": False,
                "reason": "没有可靠的素材内容来源",
                "warnings": ["素材内容不可用"],
            }

        model = _FakeToolModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_content_asset",
                            "args": {"material_id": "material-1"},
                            "id": "call-missing-content",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="素材内容不可用"),
            ],
            self._result(missing_content=True),
        )

        update = ContentReviewAgent(
            model=model,
            tools={"get_content_asset": get_content_asset},
            principles="fixture principles",
        ).run(self._state())
        result = update["content_review_result"]

        self.assertEqual(update["tool_trace"][0].status, "unavailable")
        self.assertFalse(result.materials_reviewed)
        self.assertFalse(result.content_findings)
        self.assertFalse(result.content_evidence)
        self.assertTrue(result.uncertainties)
        self.assertIn("无法完成内容本身判断", result.summary)
        self.assertNotIn("errors", update)

    def test_node_can_use_existing_context_without_tools_and_write_state(self):
        model = _FakeToolModel([], self._result())
        state = self._state()

        update = ContentReviewAgent(
            model=model,
            principles="fixture principles",
            tools=None,
        ).run(state)
        next_state = apply_state_update(state, update)

        self.assertIsInstance(next_state["content_review_result"], ContentReviewResult)
        self.assertEqual(len(next_state["evidence"]), 2)
        self.assertEqual(next_state["open_questions"][0].question, "内容表达与线索质量之间是否存在关系？")
        self.assertEqual(update["tool_trace"], [])

    def test_result_contract_has_no_ad_execution_fields(self):
        model = _FakeToolModel([], self._result())
        result = ContentReviewAgent(
            model=model,
            principles="fixture principles",
        ).run(self._state())["content_review_result"]

        self.assertEqual(set(result.model_dump()), set(ContentReviewResult.model_fields))
        forbidden = {
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }
        self.assertTrue(forbidden.isdisjoint(result.model_dump()))


if __name__ == "__main__":
    unittest.main()
