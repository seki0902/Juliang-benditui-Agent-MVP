import unittest

from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.common import ConfidenceLevel, EvidenceItem, OpenQuestion
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult, MissingDataItem
from schemas.strategy import (
    ObservationItem,
    PriorityAction,
    StrategyEvidenceRequest,
    StrategyResult,
)
from agents.strategy_agent import StrategyAgent, strategy_node
from state import apply_state_update


class _FakeStructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        return self.model.final_result.model_dump(mode="json")


class _FakeModel:
    def __init__(self, final_result):
        self.final_result = final_result
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _FakeStructuredRunnable(self)


class StrategyAgentTests(unittest.TestCase):
    def _evidence(self, metric="spend", value=100, object_type="plan", object_id="plan-1"):
        return EvidenceItem(
            evidence_type="metric",
            source="fixture.strategy",
            object_type=object_type,
            object_id=object_id,
            metric_or_content=metric,
            value=value,
            time_range="2026-09-17",
        )

    def _state(self, *, content=None, evidence=()):
        return {
            "run_id": "strategy-run-1",
            "report_date": "2026-09-17",
            "task": "综合当前分析并给出下一步建议",
            "account_context": {"account_id": "acct-1"},
            "data_analysis_result": DataAnalysisResult(
                summary="当前有可供综合的账户和计划观察。"
            ),
            "ads_diagnosis_result": AdsDiagnosisResult(
                summary="计划原因仍需结合整体证据判断。"
            ),
            "content_review_result": content,
            "evidence": list(evidence),
            "open_questions": [],
        }

    def _run(self, result, *, state=None):
        model = _FakeModel(result)
        update = strategy_node(
            state or self._state(),
            model=model,
            principles="fixture strategy principles",
        )
        return model, update

    def test_node_synthesises_complete_evidence_into_a_prioritised_recommendation(self):
        evidence = self._evidence()
        result = StrategyResult(
            overall_judgement="现有证据支持先复核计划层问题。",
            priority_actions=[
                PriorityAction(
                    priority=1,
                    target_type="plan",
                    target_id="plan-1",
                    action_type="review_plan_evidence",
                    reason="计划证据覆盖完整，适合先做针对性复核。",
                    expected_signal="计划表现和原因证据更清晰",
                    recheck_condition="补充复核证据后再次评估",
                )
            ],
            supporting_evidence=[evidence],
            confidence=ConfidenceLevel.HIGH,
        )
        model, update = self._run(result, state=self._state(evidence=[evidence]))

        self.assertIsInstance(update["strategy_result"], StrategyResult)
        self.assertEqual(update["strategy_result"].priority_actions[0].priority, 1)
        self.assertEqual(update["evidence"], [evidence])
        self.assertEqual(update["open_questions"], [])
        self.assertEqual(update["tool_trace"], [])
        self.assertIs(model.structured_schema, StrategyResult)
        self.assertFalse(model.structured_include_raw)
        self.assertIn("fixture strategy principles", model.structured_inputs[0][0].content)

    def test_node_loads_filled_project_principles_when_not_overridden(self):
        model = _FakeModel(StrategyResult(overall_judgement="fixture strategy result"))

        StrategyAgent(model=model).run(self._state())

        system_text = model.structured_inputs[0][0].content
        self.assertIn("Strategy Principles", system_text)
        principle_text = system_text[system_text.rfind("# Strategy Principles") :]
        self.assertIn("P41", principle_text)
        self.assertIn("P42", principle_text)
        self.assertIn("10%–30%", principle_text)
        self.assertIn("深转", principle_text)
        self.assertNotIn("TODO", principle_text)

    def test_node_handles_missing_content_result_without_faking_content_agent(self):
        result = StrategyResult(
            overall_judgement="没有 Content Review Result，当前只保持数据层观察。",
            observation_items=[
                ObservationItem(
                    target_type="plan",
                    target_id="plan-1",
                    observation="OBSERVE / 保持观察，暂不把缺失内容证据解释成内容问题。",
                    recheck_condition="获得内容证据后复核",
                )
            ],
            cannot_conclude=["当前无法判断内容是否影响表现。"],
            confidence=ConfidenceLevel.LOW,
        )
        model, update = self._run(result, state=self._state(content=None))

        output = update["strategy_result"]
        self.assertFalse(output.priority_actions)
        self.assertTrue(output.observation_items)
        self.assertIn("内容", output.cannot_conclude[0])
        self.assertEqual(len(update["open_questions"]), 1)
        self.assertIsInstance(update["open_questions"][0], OpenQuestion)
        self.assertNotIn("ContentReviewResult", output.model_dump())
        state_message = model.structured_inputs[0][1].content
        self.assertIn('"content_review_result": null', state_message)

    def test_node_preserves_conflicting_evidence_and_requests_more_evidence(self):
        result = StrategyResult(
            overall_judgement="不同 Agent 结论存在冲突，暂不形成强动作建议。",
            supporting_evidence=[self._evidence("spend", 100, "account", "acct-1")],
            need_more_evidence=True,
            evidence_request=StrategyEvidenceRequest(
                request="补充账户、计划和内容表现的对齐窗口",
                reason="需要区分层级变化与内容关系的候选解释。",
                source_hint="runtime evidence",
                target_type="account",
                target_id="acct-1",
            ),
            cannot_conclude=["当前无法确认唯一的优先原因。"],
            confidence=ConfidenceLevel.LOW,
        )
        model, update = self._run(
            result,
            state=self._state(content=ContentReviewResult(summary="内容侧观察与数据侧不完全一致。")),
        )

        output = update["strategy_result"]
        self.assertTrue(output.need_more_evidence)
        self.assertIsNotNone(output.evidence_request)
        self.assertEqual(len(output.cannot_conclude), 1)
        self.assertEqual(len(update["open_questions"]), 2)
        self.assertEqual(
            [question.question for question in update["open_questions"]],
            ["当前无法确认唯一的优先原因。", "补充账户、计划和内容表现的对齐窗口"],
        )
        self.assertIn("ads_diagnosis_result", model.structured_inputs[0][1].content)

    def test_node_expresses_evidence_gap_without_inventing_a_strategy_action(self):
        result = StrategyResult(
            overall_judgement="证据不足，暂不能形成可靠策略建议。",
            need_more_evidence=True,
            evidence_request=StrategyEvidenceRequest(
                request="补充有效线索质量反馈",
                reason="当前数据分析标记了缺失字段，无法验证质量影响。",
                source_hint="lead quality feedback",
                target_type="account",
                target_id="acct-1",
            ),
            cannot_conclude=["当前无法判断是否需要调整计划。"],
            confidence=ConfidenceLevel.UNKNOWN,
        )
        insufficient_state = self._state()
        insufficient_state["data_analysis_result"] = DataAnalysisResult(
            summary="缺少线索质量反馈。",
            missing_data=[
                MissingDataItem(
                    field="valid_leads",
                    reason="当前窗口没有有效线索反馈。",
                )
            ],
        )
        _model, update = self._run(result, state=insufficient_state)

        output = update["strategy_result"]
        self.assertTrue(output.need_more_evidence)
        self.assertFalse(output.priority_actions)
        self.assertTrue(output.cannot_conclude)
        self.assertEqual(update["open_questions"][1].question, "补充有效线索质量反馈")

    def test_node_supports_observe_without_an_immediate_action_and_writes_state(self):
        evidence = self._evidence("impressions", 1000)
        result = StrategyResult(
            overall_judgement="当前账户保持观察即可。",
            priority_actions=[],
            observation_items=[
                ObservationItem(
                    target_type="account",
                    target_id="acct-1",
                    observation="OBSERVE / 保持观察当前账户表现。",
                    recheck_condition="下一次可比窗口复核同一组信号",
                    evidence_ids=[],
                )
            ],
            supporting_evidence=[evidence],
            need_more_evidence=False,
            cannot_conclude=[],
            confidence=ConfidenceLevel.MEDIUM,
        )
        state = self._state(evidence=[evidence])
        model, update = self._run(result, state=state)
        next_state = apply_state_update(state, update)

        self.assertFalse(next_state["strategy_result"].priority_actions)
        self.assertEqual(next_state["strategy_result"].observation_items[0].target_type, "account")
        self.assertEqual(next_state["evidence"], [evidence])
        self.assertEqual(next_state["open_questions"], [])
        forbidden = {
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }
        self.assertTrue(forbidden.isdisjoint(model.final_result.model_dump()))


if __name__ == "__main__":
    unittest.main()
