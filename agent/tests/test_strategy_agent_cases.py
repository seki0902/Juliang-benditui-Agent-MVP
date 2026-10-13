import unittest
from dataclasses import dataclass
from typing import Any

from agents.strategy_agent import StrategyAgent
from schemas.ads_diagnosis import (
    AdsDiagnosisResult,
    DiagnosisCertainty,
    DiagnosisItem,
    EvidenceRequest,
    UncertaintyItem,
)
from schemas.common import ConfidenceLevel, EvidenceItem
from schemas.content_review import (
    ContentReviewResult,
    ContentUncertainty,
    PerformanceExplanation,
)
from schemas.data_analysis import (
    AnalysisFinding,
    DataAnalysisResult,
    MissingDataItem,
)
from schemas.strategy import (
    ObservationItem,
    PriorityAction,
    StrategyEvidenceRequest,
    StrategyResult,
)


def _evidence(
    metric: str,
    value: Any,
    *,
    object_type: str = "material",
    object_id: str | None = "material-1",
    evidence_type: str = "metric",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_type=evidence_type,
        source="fixture.strategy_cases",
        object_type=object_type,
        object_id=object_id,
        metric_or_content=metric,
        value=value,
        time_range="2026-09-17",
    )


def _base_state(
    *,
    data_result: DataAnalysisResult | None,
    ads_result: AdsDiagnosisResult | None,
    content_result: ContentReviewResult | None,
    evidence: tuple[EvidenceItem, ...] = (),
) -> dict[str, Any]:
    return {
        "run_id": "strategy-case-run",
        "report_date": "2026-09-17",
        "task": "综合已有 Agent 结果并判断下一步策略",
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": data_result,
        "ads_diagnosis_result": ads_result,
        "content_review_result": content_result,
        "evidence": list(evidence),
        "open_questions": [],
    }


@dataclass(frozen=True)
class _StrategyCase:
    name: str
    state: dict[str, Any]
    result: StrategyResult


class _CaseStructuredRunnable:
    def __init__(self, model: "_CaseModel") -> None:
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        # Return JSON-shaped data so every case exercises the shared
        # Pydantic StrategyResult validation boundary.
        return self.model.case.result.model_dump(mode="json")


class _CaseModel:
    def __init__(self, case: _StrategyCase) -> None:
        self.case = case
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _CaseStructuredRunnable(self)


def _case_fixtures() -> tuple[_StrategyCase, ...]:
    aligned_signal = _evidence("signal_alignment", "consistent")
    aligned_quality = _evidence("quality_feedback", "available")
    aligned_data = DataAnalysisResult(
        summary="数据层观察到前端表现与后端质量信号方向一致。",
        observations=[
            AnalysisFinding(
                description="material-1 的已知表现信号可以与质量反馈一起复核。",
                object_type="material",
                object_id="material-1",
                evidence=[aligned_signal, aligned_quality],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        evidence=[aligned_signal, aligned_quality],
        confidence=ConfidenceLevel.MEDIUM,
    )
    aligned_ads = AdsDiagnosisResult(
        summary="广告诊断认为该素材的表现方向与质量信号一致。",
        diagnoses=[
            DiagnosisItem(
                statement="material-1 的前后端表现信号具备一致性。",
                certainty=DiagnosisCertainty.LIKELY,
                object_type="material",
                object_id="material-1",
                supporting_evidence=[aligned_signal, aligned_quality],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        supporting_evidence=[aligned_signal, aligned_quality],
        confidence=ConfidenceLevel.MEDIUM,
    )
    aligned_content = ContentReviewResult(
        summary="内容复核发现素材表达与已有表现信号相互印证。",
        performance_explanations=[
            PerformanceExplanation(
                material_id="material-1",
                performance_signal="表现信号与内容表达方向一致",
                content_relationship="现有内容证据支持继续验证该方向",
                supporting_evidence=[aligned_signal],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        content_evidence=[aligned_signal],
        confidence=ConfidenceLevel.MEDIUM,
    )

    conflict_ads_signal = _evidence("ads_hypothesis", "content_mismatch")
    conflict_content_signal = _evidence(
        "content_review",
        "no_clear_mismatch",
        evidence_type="content",
    )
    conflict_ads = AdsDiagnosisResult(
        summary="广告诊断提出内容匹配度可能是表现差异的原因。",
        diagnoses=[
            DiagnosisItem(
                statement="material-1 可能存在内容匹配问题。",
                certainty=DiagnosisCertainty.HYPOTHESIS,
                object_type="material",
                object_id="material-1",
                supporting_evidence=[conflict_ads_signal],
                confidence=ConfidenceLevel.LOW,
            )
        ],
        supporting_evidence=[conflict_ads_signal],
        evidence_needed=[
            EvidenceRequest(
                request="补充可比窗口下的素材内容与质量反馈",
                reason="用于验证广告侧的内容匹配假设",
                source_hint="content review and feedback",
                object_type="material",
                object_id="material-1",
            )
        ],
        confidence=ConfidenceLevel.LOW,
    )
    conflict_content = ContentReviewResult(
        summary="内容复核暂未发现能够确认匹配问题的内容证据。",
        performance_explanations=[
            PerformanceExplanation(
                material_id="material-1",
                performance_signal="广告侧表现差异",
                content_relationship="现有内容证据暂不支持内容匹配问题的确定结论",
                supporting_evidence=[conflict_content_signal],
                confidence=ConfidenceLevel.LOW,
            )
        ],
        content_evidence=[conflict_content_signal],
        uncertainties=[
            ContentUncertainty(
                question="内容差异是否足以解释广告侧表现差异？",
                reason="当前缺少完全可比的投放窗口和质量反馈。",
                needed_input="补充可比窗口与后端反馈",
            )
        ],
        confidence=ConfidenceLevel.LOW,
    )

    missing_data = DataAnalysisResult(
        summary="当前 State 缺少足以形成策略判断的质量反馈。",
        missing_data=[
            MissingDataItem(
                field="valid_lead_feedback",
                reason="当前窗口没有可用的有效线索反馈记录。",
                impact="无法验证前端表现与业务质量之间的关系。",
                requested_from="feedback_export",
            )
        ],
        confidence=ConfidenceLevel.LOW,
    )
    insufficient_ads = AdsDiagnosisResult(
        summary="广告诊断只能保留待验证假设。",
        suspected_causes=[],
        uncertainties=[
            UncertaintyItem(
                question="当前表现是否需要调整计划？",
                reason="缺少质量反馈，无法区分表现波动和持续性问题。",
                evidence_needed=["valid_lead_feedback"],
            )
        ],
        evidence_needed=[
            EvidenceRequest(
                request="补充有效线索反馈",
                reason="用于验证计划调整是否有依据",
                source_hint="feedback_export",
                object_type="account",
                object_id="acct-1",
            )
        ],
        confidence=ConfidenceLevel.UNKNOWN,
    )

    normal_data_signal = _evidence(
        "account_observation",
        "stable",
        object_type="account",
        object_id="acct-1",
    )
    normal_data = DataAnalysisResult(
        summary="当前窗口未发现需要升级处理的明确异常。",
        observations=[
            AnalysisFinding(
                description="账户处于可继续观察的状态。",
                object_type="account",
                object_id="acct-1",
                evidence=[normal_data_signal],
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
        evidence=[normal_data_signal],
        confidence=ConfidenceLevel.MEDIUM,
    )
    normal_ads = AdsDiagnosisResult(
        summary="广告诊断未发现需要立即升级的明确原因。",
        supporting_evidence=[normal_data_signal],
        confidence=ConfidenceLevel.MEDIUM,
    )
    normal_content = ContentReviewResult(
        summary="内容复核未发现需要升级的明确问题。",
        content_evidence=[normal_data_signal],
        confidence=ConfidenceLevel.MEDIUM,
    )

    return (
        _StrategyCase(
            name="ads_content_aligned",
            state=_base_state(
                data_result=aligned_data,
                ads_result=aligned_ads,
                content_result=aligned_content,
                evidence=(aligned_signal, aligned_quality),
            ),
            result=StrategyResult(
                overall_judgement="Ads 与 Content 的证据方向一致，可以先安排针对性复核并观察反馈。",
                priority_actions=[
                    PriorityAction(
                        priority=1,
                        target_type="material",
                        target_id="material-1",
                        action_type="review_aligned_signal",
                        reason="多个 Agent 的结果与共享证据相互支持。",
                        expected_signal="复核后能够继续验证该方向。",
                        recheck_condition="补充复核证据后重新评估。",
                    )
                ],
                supporting_evidence=[aligned_signal, aligned_quality],
                confidence=ConfidenceLevel.MEDIUM,
            ),
        ),
        _StrategyCase(
            name="ads_content_conflict",
            state=_base_state(
                data_result=DataAnalysisResult(
                    summary="数据层记录到需要解释的表现差异。",
                    conflicts=[
                        AnalysisFinding(
                            description="广告侧假设与内容侧观察方向不一致。",
                            object_type="material",
                            object_id="material-1",
                            evidence=[conflict_ads_signal, conflict_content_signal],
                            confidence=ConfidenceLevel.LOW,
                        )
                    ],
                    evidence=[conflict_ads_signal, conflict_content_signal],
                    confidence=ConfidenceLevel.LOW,
                ),
                ads_result=conflict_ads,
                content_result=conflict_content,
                evidence=(conflict_ads_signal, conflict_content_signal),
            ),
            result=StrategyResult(
                overall_judgement="Ads 与 Content 的解释存在冲突，暂不形成强行动建议。",
                need_more_evidence=True,
                evidence_request=StrategyEvidenceRequest(
                    request="补充可比窗口下的素材内容、投放表现和质量反馈",
                    reason="需要先核对两侧证据的时间窗口与对象范围。",
                    source_hint="aligned performance and feedback export",
                    target_type="material",
                    target_id="material-1",
                ),
                cannot_conclude=["当前无法确认内容匹配假设是否是唯一优先原因。"],
                confidence=ConfidenceLevel.LOW,
            ),
        ),
        _StrategyCase(
            name="no_content_result",
            state=_base_state(
                data_result=aligned_data,
                ads_result=aligned_ads,
                content_result=None,
                evidence=(aligned_signal, aligned_quality),
            ),
            result=StrategyResult(
                overall_judgement="缺少 Content Review Result，先 OBSERVE / 保持观察已有数据和广告诊断信号。",
                observation_items=[
                    ObservationItem(
                        target_type="material",
                        target_id="material-1",
                        observation="OBSERVE / 保持观察，暂不把缺失的内容结果解释成内容问题。",
                        recheck_condition="获得 Content Review Result 后重新评估。",
                        evidence_ids=[],
                    )
                ],
                cannot_conclude=["当前无法判断内容因素是否影响表现。"],
                confidence=ConfidenceLevel.LOW,
            ),
        ),
        _StrategyCase(
            name="insufficient_evidence",
            state=_base_state(
                data_result=missing_data,
                ads_result=insufficient_ads,
                content_result=None,
            ),
            result=StrategyResult(
                overall_judgement="证据不足，暂不形成调整计划的策略建议。",
                need_more_evidence=True,
                evidence_request=StrategyEvidenceRequest(
                    request="补充有效线索反馈",
                    reason="数据分析和广告诊断都标记了同一证据缺口。",
                    source_hint="feedback_export",
                    target_type="account",
                    target_id="acct-1",
                ),
                cannot_conclude=["当前无法判断是否需要调整计划。"],
                confidence=ConfidenceLevel.UNKNOWN,
            ),
        ),
        _StrategyCase(
            name="normal_account",
            state=_base_state(
                data_result=normal_data,
                ads_result=normal_ads,
                content_result=normal_content,
                evidence=(normal_data_signal,),
            ),
            result=StrategyResult(
                overall_judgement="正常账户保持观察即可，当前没有需要升级的明确行动。",
                observation_items=[
                    ObservationItem(
                        target_type="account",
                        target_id="acct-1",
                        observation="OBSERVE / 保持观察当前账户表现。",
                        recheck_condition="下一可比窗口复核同一组信号。",
                        evidence_ids=[],
                    )
                ],
                supporting_evidence=[normal_data_signal],
                need_more_evidence=False,
                confidence=ConfidenceLevel.MEDIUM,
            ),
        ),
    )


class StrategyAgentCaseTests(unittest.TestCase):
    def test_five_mock_cases_follow_strategy_schema_and_boundaries(self):
        forbidden_execution_fields = {
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }

        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                update = StrategyAgent(
                    model=model,
                    principles="fixture strategy principles; no business thresholds",
                ).run(case.state)
                result = update["strategy_result"]

                self.assertIsInstance(result, StrategyResult)
                self.assertEqual(model.structured_schema, StrategyResult)
                self.assertFalse(model.structured_include_raw)
                self.assertEqual(len(model.structured_inputs), 1)
                self.assertEqual(update["tool_trace"], [])
                self.assertTrue(forbidden_execution_fields.isdisjoint(result.model_dump()))
                self.assertIsInstance(
                    StrategyResult.model_validate(result.model_dump(mode="json")),
                    StrategyResult,
                )

        aligned = next(case for case in _case_fixtures() if case.name == "ads_content_aligned")
        aligned_model = _CaseModel(aligned)
        aligned_update = StrategyAgent(
            model=aligned_model,
            principles="fixture strategy principles",
        ).run(aligned.state)
        aligned_result = aligned_update["strategy_result"]
        self.assertEqual(aligned_result.priority_actions[0].target_id, "material-1")
        self.assertFalse(aligned_result.need_more_evidence)
        self.assertIn('"content_review_result"', aligned_model.structured_inputs[0][1].content)

        conflict = next(case for case in _case_fixtures() if case.name == "ads_content_conflict")
        conflict_update = StrategyAgent(
            model=_CaseModel(conflict),
            principles="fixture strategy principles",
        ).run(conflict.state)
        conflict_result = conflict_update["strategy_result"]
        self.assertTrue(conflict_result.need_more_evidence)
        self.assertIsNotNone(conflict_result.evidence_request)
        self.assertFalse(conflict_result.priority_actions)
        self.assertTrue(conflict_result.cannot_conclude)

    def test_no_content_and_insufficient_evidence_do_not_get_filled_with_fake_findings(self):
        cases = _case_fixtures()
        no_content = next(case for case in cases if case.name == "no_content_result")
        no_content_model = _CaseModel(no_content)
        no_content_update = StrategyAgent(
            model=no_content_model,
            principles="fixture strategy principles",
        ).run(no_content.state)
        no_content_result = no_content_update["strategy_result"]

        self.assertFalse(no_content_result.priority_actions)
        self.assertTrue(no_content_result.observation_items)
        self.assertTrue(no_content_result.cannot_conclude)
        self.assertIn("内容", no_content_result.cannot_conclude[0])
        self.assertIn('"content_review_result": null', no_content_model.structured_inputs[0][1].content)
        self.assertEqual(no_content_update["open_questions"][0].question, no_content_result.cannot_conclude[0])

        insufficient = next(case for case in cases if case.name == "insufficient_evidence")
        insufficient_update = StrategyAgent(
            model=_CaseModel(insufficient),
            principles="fixture strategy principles",
        ).run(insufficient.state)
        insufficient_result = insufficient_update["strategy_result"]

        self.assertTrue(insufficient_result.need_more_evidence)
        self.assertIsNotNone(insufficient_result.evidence_request)
        self.assertFalse(insufficient_result.priority_actions)
        self.assertTrue(insufficient_result.cannot_conclude)
        self.assertEqual(
            insufficient_update["open_questions"][1].question,
            insufficient_result.evidence_request.request,
        )

    def test_normal_account_can_observe_without_requesting_more_evidence(self):
        normal = next(case for case in _case_fixtures() if case.name == "normal_account")
        model = _CaseModel(normal)
        update = StrategyAgent(
            model=model,
            principles="fixture strategy principles",
        ).run(normal.state)
        result = update["strategy_result"]

        self.assertFalse(result.priority_actions)
        self.assertTrue(result.observation_items)
        self.assertFalse(result.need_more_evidence)
        self.assertIsNone(result.evidence_request)
        self.assertFalse(result.cannot_conclude)
        self.assertEqual(update["open_questions"], [])


if __name__ == "__main__":
    unittest.main()
