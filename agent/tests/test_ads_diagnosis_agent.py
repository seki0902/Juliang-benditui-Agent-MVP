import unittest

from langchain_core.messages import AIMessage, ToolMessage

from agents.ads_diagnosis_agent import AdsDiagnosisAgent, ads_diagnosis_node
from schemas.ads_diagnosis import (
    AdsDiagnosisResult,
    ContentTarget,
    DiagnosisCertainty,
    DiagnosisItem,
    EvidenceRequest,
    SuspectedCause,
    UncertaintyItem,
)
from schemas.common import ConfidenceLevel, EvidenceItem, OpenQuestion, ToolCallRecord
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
        return AIMessage(content="done")

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _FakeStructuredRunnable(self)


class AdsDiagnosisAgentTests(unittest.TestCase):
    def _state(self):
        return {
            "run_id": "run-1",
            "report_date": "2026-09-17",
            "task": "解释计划成本和有效线索表现变化的可能原因",
            "account_context": {"account_id": "acct-1", "account_name": "fixture"},
            "data_analysis_result": DataAnalysisResult(
                summary="计划成本上升，原因尚未确定。"
            ),
            "content_review_result": None,
            "evidence": [],
        }

    def _evidence(self, metric="cost"):
        return EvidenceItem(
            evidence_type="metric",
            source="fixture.plan",
            object_type="plan",
            object_id="plan-1",
            metric_or_content=metric,
            value=42,
            time_range="2026-09-17",
        )

    def _result(
        self,
        summary="diagnosis is a hypothesis",
        *,
        need_content_review=False,
        include_requests=False,
    ):
        evidence = self._evidence()
        return AdsDiagnosisResult(
            summary=summary,
            diagnoses=[
                DiagnosisItem(
                    statement="计划层可能存在成本变化，但当前证据不足以确认根因。",
                    certainty=DiagnosisCertainty.HYPOTHESIS,
                    object_type="plan",
                    object_id="plan-1",
                    supporting_evidence=[evidence],
                    confidence=ConfidenceLevel.LOW,
                )
            ],
            suspected_causes=[
                SuspectedCause(
                    cause="需要继续核对计划历史和线索质量",
                    certainty=DiagnosisCertainty.HYPOTHESIS,
                    confidence=ConfidenceLevel.LOW,
                )
            ],
            supporting_evidence=[evidence],
            uncertainties=[
                UncertaintyItem(
                    question="变化是否持续超过当前观察周期？",
                    reason="当前 State 没有足够历史窗口。",
                    evidence_needed=["计划历史窗口"],
                )
            ],
            need_content_review=need_content_review,
            content_targets=(
                [
                    ContentTarget(
                        object_id="material-1",
                        reason="需要核验该素材是否存在内容层面的解释因素。",
                    )
                ]
                if need_content_review
                else []
            ),
            evidence_needed=(
                [
                    EvidenceRequest(
                        request="补充计划历史表现和有效线索质量",
                        reason="用来区分计划趋势与线索质量变化。",
                        source_hint="runtime data Tool",
                        object_type="plan",
                        object_id="plan-1",
                    )
                ]
                if include_requests
                else []
            ),
            confidence=ConfidenceLevel.LOW,
        )

    def test_node_uses_context_without_tools_and_writes_uncertainty_to_state(self):
        model = _FakeToolModel([], self._result())

        update = ads_diagnosis_node(
            self._state(),
            model=model,
            principles="fixture ads-diagnosis principles",
            tools=None,
        )

        self.assertIsInstance(update["ads_diagnosis_result"], AdsDiagnosisResult)
        self.assertEqual(update["ads_diagnosis_result"].summary, "diagnosis is a hypothesis")
        self.assertEqual(update["tool_trace"], [])
        self.assertEqual(len(update["evidence"]), 2)
        self.assertEqual(len(update["open_questions"]), 1)
        self.assertIsInstance(update["open_questions"][0], OpenQuestion)
        self.assertIsNone(model.bound_tools)
        self.assertIs(model.structured_schema, AdsDiagnosisResult)
        self.assertFalse(model.structured_include_raw)
        system_text = model.structured_inputs[0][0].content
        self.assertIn("你是投放诊断 Agent", system_text)
        self.assertIn("fixture ads-diagnosis principles", system_text)
        state_text = model.structured_inputs[0][1].content
        self.assertIn("data_analysis_result", state_text)
        self.assertIn("account_context", state_text)

    def test_node_loads_filled_project_principles_when_not_overridden(self):
        model = _FakeToolModel([], self._result("project principles loaded"))

        ads_diagnosis_node(
            self._state(),
            model=model,
            tools=None,
        )

        system_text = model.structured_inputs[0][0].content
        self.assertIn("Ads Diagnosis Principles", system_text)
        principle_text = system_text[system_text.rfind("# Ads Diagnosis Principles") :]
        self.assertIn("P39", principle_text)
        self.assertIn("P48", principle_text)
        self.assertIn("可比", principle_text)
        self.assertNotIn("TODO", principle_text)

    def test_node_lets_model_select_and_execute_runtime_data_tool(self):
        calls = []

        def get_plan_metrics(report_date, plan_id, time_window):
            calls.append((report_date, plan_id, time_window))
            return {
                "status": "ok",
                "source": "fixture.plan",
                "time_window": {"name": time_window},
                "data": {"cost": 42, "leads": 3},
                "warnings": [],
            }

        def get_history_context(**kwargs):
            raise AssertionError("the model selected plan metrics, not history")

        model = _FakeToolModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_plan_metrics",
                            "args": {
                                "report_date": "2026-09-17",
                                "plan_id": "plan-1",
                                "time_window": "day",
                            },
                            "id": "call-plan-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="plan evidence collected"),
            ],
            self._result("plan evidence collected"),
        )

        update = AdsDiagnosisAgent(
            model=model,
            tools={
                "get_plan_metrics": get_plan_metrics,
                "get_history_context": get_history_context,
            },
            principles="fixture principles",
            max_tool_calls=2,
        ).run(self._state())

        self.assertEqual(calls, [("2026-09-17", "plan-1", "day")])
        self.assertEqual(len(model.bound_tools), 2)
        self.assertEqual(len(update["tool_trace"]), 1)
        self.assertIsInstance(update["tool_trace"][0], ToolCallRecord)
        self.assertEqual(update["tool_trace"][0].tool_name, "get_plan_metrics")
        self.assertEqual(update["tool_trace"][0].status, "ok")
        tool_messages = [
            message
            for message in model.structured_inputs[0]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn('"cost": 42', tool_messages[0].content)

    def test_node_preserves_optional_content_and_additional_evidence_requests(self):
        model = _FakeToolModel(
            [],
            self._result(
                "content review is conditionally requested",
                need_content_review=True,
                include_requests=True,
            ),
        )

        update = AdsDiagnosisAgent(
            model=model,
            principles="fixture principles",
        ).run(self._state())
        result = update["ads_diagnosis_result"]

        self.assertTrue(result.need_content_review)
        self.assertEqual(result.content_targets[0].object_id, "material-1")
        self.assertEqual(result.evidence_needed[0].object_type, "plan")
        self.assertEqual(result.evidence_needed[0].object_id, "plan-1")
        self.assertEqual(result.uncertainties[0].evidence_needed, ["计划历史窗口"])

    def test_state_update_keeps_result_contract_and_no_execution_fields(self):
        model = _FakeToolModel([], self._result("schema boundary"))
        state = self._state()

        update = AdsDiagnosisAgent(
            model=model,
            principles="fixture principles",
        ).run(state)
        next_state = apply_state_update(state, update)
        result = next_state["ads_diagnosis_result"]

        self.assertEqual(set(result.model_dump()), set(AdsDiagnosisResult.model_fields))
        self.assertEqual(next_state["open_questions"][0].question, "变化是否持续超过当前观察周期？")
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
