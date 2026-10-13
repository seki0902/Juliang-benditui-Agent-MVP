import unittest
from dataclasses import dataclass
from typing import Any

from agents.supervisor_agent import SupervisorAgent
from schemas.ads_diagnosis import (
    AdsDiagnosisResult,
    ContentTarget,
    EvidenceRequest,
)
from schemas.common import AgentName, ConfidenceLevel, HandoffSuggestion, OpenQuestion
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.strategy import StrategyEvidenceRequest, StrategyResult
from schemas.supervisor import SupervisorDecision


@dataclass(frozen=True)
class _SupervisorCase:
    name: str
    state: dict[str, Any]
    decision: SupervisorDecision


class _CaseStructuredRunnable:
    def __init__(self, model: "_CaseModel") -> None:
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        return self.model.case.decision.model_dump(mode="json")


class _CaseModel:
    def __init__(self, case: _SupervisorCase) -> None:
        self.case = case
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _CaseStructuredRunnable(self)


def _base_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": "supervisor-case-run",
        "report_date": "2026-09-17",
        "task": "根据当前 State 协调本轮分析",
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": None,
        "ads_diagnosis_result": None,
        "content_review_result": None,
        "strategy_result": None,
        "open_questions": [],
        "evidence": [],
        "agent_call_counts": {},
        "total_agent_calls": 0,
        "route_trace": [],
        "tool_trace": [],
        "errors": [],
    }
    state.update(overrides)
    return state


def _decision(
    target: AgentName,
    *,
    task: str,
    reason: str,
    question: OpenQuestion | None = None,
    can_finish: bool = False,
) -> SupervisorDecision:
    return SupervisorDecision(
        next_agent=target,
        task=task,
        reason=reason,
        open_question_to_resolve=question,
        can_finish=can_finish,
    )


def _case_fixtures() -> tuple[_SupervisorCase, ...]:
    data_handoff = HandoffSuggestion(
        suggested_agent=AgentName.ADS_DIAGNOSIS,
        task="解释当前数据层已发现的现象",
        reason="数据结果包含需要进一步解释的对象。",
        confidence=ConfidenceLevel.MEDIUM,
    )
    data_result = DataAnalysisResult(
        summary="数据层已经整理出可供后续协作的事实。",
        handoff_suggestion=data_handoff,
        confidence=ConfidenceLevel.MEDIUM,
    )

    diagnosis_result = AdsDiagnosisResult(
        summary="广告诊断已完成，结果提出了需要进一步核验的内容对象。",
        need_content_review=True,
        content_targets=[
            ContentTarget(
                object_type="material",
                object_id="material-1",
                reason="需要核验该素材的内容证据。",
            )
        ],
        evidence_needed=[
            EvidenceRequest(
                request="补充 material-1 的内容资产",
                reason="当前诊断仍需内容侧证据。",
                object_type="material",
                object_id="material-1",
            )
        ],
        confidence=ConfidenceLevel.LOW,
    )
    strategy_gap = StrategyResult(
        overall_judgement="当前综合判断存在需要补充的证据。",
        need_more_evidence=True,
        evidence_request=StrategyEvidenceRequest(
            request="补充有效线索反馈",
            reason="当前 State 的数据和诊断结果都无法验证质量关系。",
            source_hint="feedback_export",
            target_type="account",
            target_id="acct-1",
        ),
        cannot_conclude=["当前无法确认是否需要继续调整计划。"],
        confidence=ConfidenceLevel.LOW,
    )

    return (
        _SupervisorCase(
            name="no_analysis",
            state=_base_state(),
            decision=_decision(
                AgentName.DATA_ANALYSIS,
                task="整理当前任务所需的数据事实、覆盖范围和缺失信息。",
                reason="State 尚未包含任何专业 Agent 结果，先交给专业 Agent 建立可复核上下文。",
            ),
        ),
        _SupervisorCase(
            name="data_done_diagnosis_missing",
            state=_base_state(data_analysis_result=data_result),
            decision=_decision(
                AgentName.ADS_DIAGNOSIS,
                task="基于 Data Analysis Result 解释待核验的数据现象，并记录原因不确定性。",
                reason="Data Analysis Result 已存在，当前缺少 Ads Diagnosis Result。",
            ),
        ),
        _SupervisorCase(
            name="diagnosis_requests_content",
            state=_base_state(
                data_analysis_result=data_result,
                ads_diagnosis_result=diagnosis_result,
            ),
            decision=_decision(
                AgentName.CONTENT_REVIEW,
                task="核验 Ads Diagnosis Result 指出的 material-1 内容证据，不扩展到未提供的素材。",
                reason="Ads Diagnosis Result 明确请求对具体素材进行内容核验，且 State 已提供素材对象范围。",
                question=OpenQuestion(
                    question="核验 material-1 的内容证据。",
                    reason="响应 Ads Diagnosis 的内容核验请求。",
                    owner="content_review",
                ),
            ),
        ),
        _SupervisorCase(
            name="strategy_requests_more_evidence",
            state=_base_state(
                data_analysis_result=data_result,
                ads_diagnosis_result=AdsDiagnosisResult(
                    summary="广告诊断已完成，但保留需要补证的原因假设。",
                    confidence=ConfidenceLevel.LOW,
                ),
                strategy_result=strategy_gap,
                open_questions=[
                    OpenQuestion(
                        question="补充有效线索反馈",
                        reason="Strategy Agent 标记了证据缺口。",
                        owner="data_analysis",
                    )
                ],
            ),
            decision=_decision(
                AgentName.DATA_ANALYSIS,
                task="补充 Strategy Result 请求的有效线索反馈，并返回可追踪的数据状态。",
                reason="Strategy Result 将证据缺口标记为当前策略判断的阻塞事项。",
                question=OpenQuestion(
                    question="补充有效线索反馈",
                    reason="响应 Strategy Agent 的 evidence_request。",
                    owner="data_analysis",
                ),
            ),
        ),
        _SupervisorCase(
            name="strategy_done_no_open_question",
            state=_base_state(
                data_analysis_result=data_result,
                ads_diagnosis_result=AdsDiagnosisResult(
                    summary="广告诊断已完成，当前没有新增未决问题。",
                    confidence=ConfidenceLevel.MEDIUM,
                ),
                content_review_result=ContentReviewResult(
                    summary="内容复核已完成，当前没有新增未决问题。",
                    confidence=ConfidenceLevel.MEDIUM,
                ),
                strategy_result=StrategyResult(
                    overall_judgement="已有专业结果已完成综合，当前 State 可以交由报告层处理。",
                    confidence=ConfidenceLevel.MEDIUM,
                ),
                open_questions=[],
            ),
            decision=_decision(
                AgentName.FINISH,
                task="结束本轮 Agent 协作，并由报告层汇总已有 State。",
                reason="Strategy Result 已存在，且当前没有未解决的开放问题。",
                can_finish=True,
            ),
        ),
    )


class SupervisorAgentCaseTests(unittest.TestCase):
    def test_five_state_cases_produce_valid_orchestration_decisions(self):
        professional_agents = {
            AgentName.DATA_ANALYSIS,
            AgentName.ADS_DIAGNOSIS,
            AgentName.CONTENT_REVIEW,
            AgentName.STRATEGY,
        }
        forbidden_business_fields = {
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
            "final_strategy",
            "material_judgement",
        }

        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                update = SupervisorAgent(
                    model=model,
                    allowed_agents=(
                        AgentName.DATA_ANALYSIS,
                        AgentName.ADS_DIAGNOSIS,
                        AgentName.CONTENT_REVIEW,
                        AgentName.STRATEGY,
                        AgentName.FINISH,
                    ),
                ).run(case.state)
                decision = update["supervisor_decision"]

                self.assertIsInstance(decision, SupervisorDecision)
                self.assertEqual(model.structured_schema, SupervisorDecision)
                self.assertFalse(model.structured_include_raw)
                self.assertEqual(len(model.structured_inputs), 1)
                self.assertEqual(set(decision.model_dump()), set(SupervisorDecision.model_fields))
                self.assertTrue(forbidden_business_fields.isdisjoint(decision.model_dump()))
                self.assertNotIn("errors", update)
                self.assertIn("data_analysis_result", model.structured_inputs[0][1].content)
                self.assertIn("open_questions", model.structured_inputs[0][1].content)

                if case.name == "no_analysis":
                    self.assertIn(decision.next_agent, professional_agents)
                    self.assertNotEqual(decision.next_agent, AgentName.FINISH)
                    self.assertNotIn("素材不好", decision.task + decision.reason)
                elif case.name == "data_done_diagnosis_missing":
                    self.assertEqual(decision.next_agent, AgentName.ADS_DIAGNOSIS)
                    self.assertFalse(decision.can_finish)
                elif case.name == "diagnosis_requests_content":
                    self.assertEqual(decision.next_agent, AgentName.CONTENT_REVIEW)
                    self.assertIsNotNone(decision.open_question_to_resolve)
                    self.assertIn("material-1", decision.open_question_to_resolve.question)
                elif case.name == "strategy_requests_more_evidence":
                    self.assertIn(decision.next_agent, professional_agents)
                    self.assertNotEqual(decision.next_agent, AgentName.FINISH)
                    self.assertIsNotNone(decision.open_question_to_resolve)
                    self.assertIn("有效线索反馈", decision.open_question_to_resolve.question)
                elif case.name == "strategy_done_no_open_question":
                    self.assertEqual(decision.next_agent, AgentName.FINISH)
                    self.assertTrue(decision.can_finish)

    def test_handoff_and_result_details_are_passed_to_the_supervisor_model(self):
        cases = _case_fixtures()
        diagnosis_case = next(case for case in cases if case.name == "diagnosis_requests_content")
        model = _CaseModel(diagnosis_case)
        SupervisorAgent(model=model).run(diagnosis_case.state)

        state_message = model.structured_inputs[0][1].content
        self.assertIn("handoff_suggestion", state_message)
        self.assertIn("need_content_review", state_message)
        self.assertIn("content_targets", state_message)

        evidence_case = next(case for case in cases if case.name == "strategy_requests_more_evidence")
        evidence_model = _CaseModel(evidence_case)
        SupervisorAgent(model=evidence_model).run(evidence_case.state)
        evidence_message = evidence_model.structured_inputs[0][1].content
        self.assertIn("need_more_evidence", evidence_message)
        self.assertIn("evidence_request", evidence_message)
        self.assertIn("agent_call_counts", evidence_message)


if __name__ == "__main__":
    unittest.main()
