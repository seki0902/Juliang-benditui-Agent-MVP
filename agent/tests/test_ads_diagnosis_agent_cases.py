import unittest
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agents.ads_diagnosis_agent import AdsDiagnosisAgent
from schemas.ads_diagnosis import (
    AdsDiagnosisResult,
    ContentTarget,
    DiagnosisCertainty,
    DiagnosisItem,
    EvidenceRequest,
    SuspectedCause,
    UncertaintyItem,
)
from schemas.common import ConfidenceLevel, EvidenceItem
from schemas.data_analysis import (
    AnalysisFinding,
    DataAnalysisResult,
    DiagnosisTarget,
    MissingDataItem,
)


def _evidence(
    metric: str,
    value: Any,
    *,
    object_type: str = "plan",
    object_id: str = "plan-1",
    note: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_type="metric",
        source="fixture.ads_diagnosis",
        object_type=object_type,
        object_id=object_id,
        metric_or_content=metric,
        value=value,
        time_range="2026-09-17",
        note=note,
    )


@dataclass(frozen=True)
class _AdsDiagnosisCase:
    name: str
    state: dict[str, Any]
    result: AdsDiagnosisResult
    tool_calls: tuple[dict[str, Any], ...] = ()
    tool_names: tuple[str, ...] = ()
    expects_content_review: bool = False


class _CaseStructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        # Return a JSON-shaped object so the Node exercises the shared
        # AdsDiagnosisResult validation boundary for every case.
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
        return AIMessage(content="当前上下文和已查询证据已足够形成结构化诊断")

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _CaseStructuredRunnable(self)


def _tool_fixture(name: str):
    def tool(**arguments):
        return {
            "status": "ok",
            "source": f"fixture.{name}",
            "time_window": {
                "name": arguments.get("time_window", "day"),
                "start_date": "2026-09-17",
                "end_date": "2026-09-17",
            },
            "data": {
                "object_id": (
                    arguments.get("plan_id")
                    or arguments.get("material_id")
                    or arguments.get("object_id")
                    or "acct-1"
                ),
                "fixture": name,
            },
            "warnings": [],
        }

    return tool


def _base_state(task: str, data_result: DataAnalysisResult, evidence=()):
    return {
        "run_id": "ads-diagnosis-mock-run",
        "report_date": "2026-09-17",
        "task": task,
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": data_result,
        "content_review_result": None,
        "evidence": list(evidence),
    }


def _case_fixtures():
    plan_cost = _evidence("cost", 48, object_type="plan", object_id="plan-1")
    ctr = _evidence(
        "ctr",
        0.42,
        object_type="material",
        object_id="material-1",
        note="fixture is labelled high CTR",
    )
    valid_rate = _evidence(
        "valid_lead_rate",
        0.08,
        object_type="material",
        object_id="material-1",
        note="fixture is labelled low valid-lead rate",
    )
    sample = _evidence(
        "leads",
        2,
        object_type="plan",
        object_id="plan-2",
        note="insufficient sample fixture",
    )
    quality = _evidence(
        "valid_leads",
        3,
        object_type="account",
        object_id="acct-1",
    )
    spend = _evidence(
        "spend",
        100,
        object_type="account",
        object_id="acct-1",
    )

    return (
        _AdsDiagnosisCase(
            name="A_single_plan_anomaly",
            state=_base_state(
                "解释单个计划成本异常的可能原因",
                DataAnalysisResult(
                    summary="计划 plan-1 的成本出现需要进一步诊断的变化。",
                    anomalies=[
                        AnalysisFinding(
                            description="计划成本在当前数据窗口出现异常表现。",
                            object_type="plan",
                            object_id="plan-1",
                            evidence=[plan_cost],
                        )
                    ],
                    targets_for_diagnosis=[
                        DiagnosisTarget(
                            question="计划 plan-1 成本变化的原因是什么？",
                            object_type="plan",
                            object_id="plan-1",
                        )
                    ],
                    evidence=[plan_cost],
                ),
                evidence=[plan_cost],
            ),
            result=AdsDiagnosisResult(
                summary="计划层成本异常可以确认，但具体根因仍需历史窗口验证。",
                diagnoses=[
                    DiagnosisItem(
                        statement="计划 plan-1 存在需要解释的成本变化。",
                        certainty=DiagnosisCertainty.LIKELY,
                        object_type="plan",
                        object_id="plan-1",
                        supporting_evidence=[plan_cost],
                        confidence=ConfidenceLevel.MEDIUM,
                    )
                ],
                suspected_causes=[
                    SuspectedCause(
                        cause="计划历史表现或当前数据窗口可能影响成本变化解释。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                supporting_evidence=[plan_cost],
                uncertainties=[
                    UncertaintyItem(
                        question="成本变化是否在可比历史窗口中持续？",
                        reason="当前 State 只有当前窗口的计划证据。",
                        evidence_needed=["计划历史窗口"],
                    )
                ],
                evidence_needed=[
                    EvidenceRequest(
                        request="补充 plan-1 的历史成本和线索表现",
                        reason="用于区分单次波动与持续性变化。",
                        source_hint="runtime data Tool",
                        object_type="plan",
                        object_id="plan-1",
                    )
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            tool_calls=(
                {
                    "name": "get_plan_metrics",
                    "args": {
                        "plan_id": "plan-1",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-a-plan",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_plan_metrics",),
        ),
        _AdsDiagnosisCase(
            name="B_high_ctr_low_valid_lead",
            state=_base_state(
                "解释高 CTR 与低有效线索率同时出现的可能原因",
                DataAnalysisResult(
                    summary="material-1 的点击表现与有效线索质量方向不一致。",
                    conflicts=[
                        AnalysisFinding(
                            description="素材前端点击率较高，但有效线索率较低。",
                            object_type="material",
                            object_id="material-1",
                            evidence=[ctr, valid_rate],
                        )
                    ],
                    targets_for_diagnosis=[
                        DiagnosisTarget(
                            question="material-1 的点击和线索质量为何不一致？",
                            object_type="material",
                            object_id="material-1",
                        )
                    ],
                    evidence=[ctr, valid_rate],
                ),
                evidence=[ctr, valid_rate],
            ),
            result=AdsDiagnosisResult(
                summary="高 CTR 与低有效线索率的冲突已确认，但原因可能来自内容衔接或线索质量链路。",
                diagnoses=[
                    DiagnosisItem(
                        statement="material-1 存在前端点击与后端有效线索质量不一致。",
                        certainty=DiagnosisCertainty.LIKELY,
                        object_type="material",
                        object_id="material-1",
                        supporting_evidence=[ctr, valid_rate],
                        confidence=ConfidenceLevel.HIGH,
                    )
                ],
                suspected_causes=[
                    SuspectedCause(
                        cause="素材吸引的点击人群与有效线索目标可能不完全匹配。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.MEDIUM,
                    ),
                    SuspectedCause(
                        cause="线索质量回传或归因链路可能存在不完整。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.LOW,
                    ),
                ],
                supporting_evidence=[ctr, valid_rate],
                uncertainties=[
                    UncertaintyItem(
                        question="低有效线索率主要来自人群匹配还是反馈链路？",
                        reason="当前前端和后端指标只能说明冲突，不能单独确认根因。",
                        evidence_needed=["线索质量明细", "素材内容核验"],
                    )
                ],
                need_content_review=True,
                content_targets=[
                    ContentTarget(
                        object_id="material-1",
                        reason="需要核验素材表达与目标线索筛选之间是否存在解释因素。",
                    )
                ],
                evidence_needed=[
                    EvidenceRequest(
                        request="补充 material-1 的线索质量明细和归因信息",
                        reason="区分内容衔接问题与质量回传问题。",
                        source_hint="runtime data Tool",
                        object_type="material",
                        object_id="material-1",
                    )
                ],
                confidence=ConfidenceLevel.MEDIUM,
            ),
            tool_calls=(
                {
                    "name": "get_material_metrics",
                    "args": {
                        "material_id": "material-1",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-b-material",
                    "type": "tool_call",
                },
                {
                    "name": "get_lead_quality",
                    "args": {
                        "report_date": "2026-09-17",
                        "time_window": "day",
                        "object_type": "material",
                        "object_id": "material-1",
                    },
                    "id": "case-b-quality",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_material_metrics", "get_lead_quality"),
            expects_content_review=True,
        ),
        _AdsDiagnosisCase(
            name="C_insufficient_sample",
            state=_base_state(
                "判断样本不足时能否确认计划原因",
                DataAnalysisResult(
                    summary="plan-2 当前只有少量线索，暂不足以稳定解释表现变化。",
                    missing_data=[
                        MissingDataItem(
                            field="sample_size",
                            reason="当前窗口样本量不足。",
                            impact="无法稳定比较计划表现或确认原因。",
                            requested_from="plan metrics",
                        )
                    ],
                    targets_for_diagnosis=[
                        DiagnosisTarget(
                            question="样本是否足以支持计划原因判断？",
                            object_type="plan",
                            object_id="plan-2",
                        )
                    ],
                    evidence=[sample],
                ),
                evidence=[sample],
            ),
            result=AdsDiagnosisResult(
                summary="当前样本和观察范围不足，无法确认 plan-2 的根因。",
                diagnoses=[],
                suspected_causes=[],
                supporting_evidence=[sample],
                uncertainties=[
                    UncertaintyItem(
                        question="plan-2 的样本和观察周期何时足以支持原因判断？",
                        reason="当前样本不足，且缺少可比观察窗口。",
                        evidence_needed=["更多观察周期", "可比样本"],
                    )
                ],
                evidence_needed=[
                    EvidenceRequest(
                        request="补充 plan-2 的后续观察窗口和可比样本",
                        reason="当前不能把偶然波动解释为稳定根因。",
                        source_hint="runtime data Tool",
                        object_type="plan",
                        object_id="plan-2",
                    )
                ],
                confidence=ConfidenceLevel.UNKNOWN,
            ),
        ),
        _AdsDiagnosisCase(
            name="D_lead_quality_anomaly",
            state=_base_state(
                "解释线索质量异常的可能原因",
                DataAnalysisResult(
                    summary="账户有效线索数量与平台线索量之间存在质量差异。",
                    anomalies=[
                        AnalysisFinding(
                            description="线索质量指标出现需要核验的异常。",
                            object_type="account",
                            object_id="acct-1",
                            evidence=[quality],
                        )
                    ],
                    targets_for_diagnosis=[
                        DiagnosisTarget(
                            question="账户线索质量异常的来源是什么？",
                            object_type="account",
                            object_id="acct-1",
                        )
                    ],
                    evidence=[quality],
                ),
                evidence=[quality],
            ),
            result=AdsDiagnosisResult(
                summary="线索质量层存在可观察异常，但仍需反馈明细确认具体原因。",
                diagnoses=[
                    DiagnosisItem(
                        statement="账户线索质量指标出现需要解释的变化。",
                        certainty=DiagnosisCertainty.LIKELY,
                        object_type="account",
                        object_id="acct-1",
                        supporting_evidence=[quality],
                        confidence=ConfidenceLevel.MEDIUM,
                    )
                ],
                suspected_causes=[
                    SuspectedCause(
                        cause="线索筛选结果与平台回传口径可能存在差异。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.MEDIUM,
                    )
                ],
                supporting_evidence=[quality],
                uncertainties=[
                    UncertaintyItem(
                        question="质量异常来自线索来源差异还是反馈口径差异？",
                        reason="当前只有聚合质量证据，没有逐条反馈明细。",
                        evidence_needed=["逐条线索质量反馈"],
                    )
                ],
                evidence_needed=[
                    EvidenceRequest(
                        request="补充账户线索质量反馈明细",
                        reason="核对平台线索、有效线索和反馈状态的对应关系。",
                        source_hint="runtime data Tool",
                        object_type="account",
                        object_id="acct-1",
                    )
                ],
                confidence=ConfidenceLevel.MEDIUM,
            ),
            tool_calls=(
                {
                    "name": "get_lead_quality",
                    "args": {
                        "report_date": "2026-09-17",
                        "time_window": "day",
                        "object_type": "account",
                        "object_id": "acct-1",
                    },
                    "id": "case-d-quality",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_lead_quality",),
        ),
        _AdsDiagnosisCase(
            name="E_multiple_possible_causes",
            state=_base_state(
                "分析数据结论存在多种可能原因时的诊断边界",
                DataAnalysisResult(
                    summary="账户、计划和线索质量证据方向不完全一致。",
                    conflicts=[
                        AnalysisFinding(
                            description="不同层级的表现变化无法由单一现象直接解释。",
                            object_type="account",
                            object_id="acct-1",
                            evidence=[spend],
                        )
                    ],
                    targets_for_diagnosis=[
                        DiagnosisTarget(
                            question="当前冲突更可能来自账户、计划还是线索层？",
                            object_type="account",
                            object_id="acct-1",
                        )
                    ],
                    evidence=[spend],
                ),
                evidence=[spend],
            ),
            result=AdsDiagnosisResult(
                summary="当前证据支持多个方向，暂不能确认唯一根因。",
                diagnoses=[
                    DiagnosisItem(
                        statement="账户层和计划层的变化关系尚未被充分拆分。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        object_type="account",
                        object_id="acct-1",
                        supporting_evidence=[spend],
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                suspected_causes=[
                    SuspectedCause(
                        cause="账户级流量结构变化可能是候选原因。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.LOW,
                    ),
                    SuspectedCause(
                        cause="计划分布或计划间差异可能是候选原因。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.LOW,
                    ),
                    SuspectedCause(
                        cause="线索质量反馈时延或口径差异可能是候选原因。",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.LOW,
                    ),
                ],
                supporting_evidence=[spend],
                uncertainties=[
                    UncertaintyItem(
                        question="哪一个层级的变化能解释全部观察结果？",
                        reason="当前证据来自不同层级，尚缺少同窗口的可比拆分。",
                        evidence_needed=["计划拆分", "流量结构", "线索质量时间对齐"],
                    )
                ],
                evidence_needed=[
                    EvidenceRequest(
                        request="补充账户、计划和线索质量的对齐窗口",
                        reason="比较候选原因的时间关系和覆盖范围。",
                        source_hint="runtime data Tool",
                        object_type="account",
                        object_id="acct-1",
                    )
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            tool_calls=(
                {
                    "name": "get_history_context",
                    "args": {
                        "report_date": "2026-09-17",
                        "time_window": "previous_7d",
                    },
                    "id": "case-e-history",
                    "type": "tool_call",
                },
                {
                    "name": "get_account_metrics",
                    "args": {
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-e-account",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_history_context", "get_account_metrics"),
        ),
    )


class AdsDiagnosisAgentCaseTests(unittest.TestCase):
    def test_five_mock_cases_pass_schema_and_core_behavior_checks(self):
        content_review_cases = []
        cases_with_hypotheses = []
        cases_with_uncertainty = []

        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                tools = {
                    name: _tool_fixture(name)
                    for name in case.tool_names
                }

                update = AdsDiagnosisAgent(
                    model=model,
                    tools=tools or None,
                    principles="fixture ads-diagnosis principles; no business thresholds",
                    max_tool_calls=4,
                ).run(case.state)
                result = update["ads_diagnosis_result"]

                self.assertIsInstance(result, AdsDiagnosisResult)
                self.assertEqual(model.structured_schema, AdsDiagnosisResult)
                self.assertIn("ads_diagnosis_result", update)
                self.assertIn("evidence", update)
                self.assertIn("open_questions", update)
                self.assertIn("tool_trace", update)
                self.assertEqual(len(update["tool_trace"]), len(case.tool_calls))
                self.assertEqual(len(update["open_questions"]), len(result.uncertainties))
                self.assertEqual(result.need_content_review, case.expects_content_review)

                if result.need_content_review:
                    content_review_cases.append(case.name)
                    self.assertTrue(result.content_targets)
                    self.assertTrue(all(target.object_id for target in result.content_targets))
                else:
                    self.assertFalse(result.content_targets)

                if any(
                    cause.certainty is DiagnosisCertainty.HYPOTHESIS
                    for cause in result.suspected_causes
                ):
                    cases_with_hypotheses.append(case.name)
                if result.uncertainties:
                    cases_with_uncertainty.append(case.name)

                forbidden = {
                    "budget_change",
                    "bid_change",
                    "pause_plan",
                    "scale_percentage",
                    "execution_status",
                    "backend_action",
                }
                self.assertTrue(forbidden.isdisjoint(result.model_dump()))

                if case.name == "A_single_plan_anomaly":
                    self.assertTrue(result.diagnoses)
                    self.assertEqual(result.diagnoses[0].object_id, "plan-1")
                    self.assertEqual(update["tool_trace"][0].tool_name, "get_plan_metrics")
                elif case.name == "B_high_ctr_low_valid_lead":
                    self.assertEqual(len(result.suspected_causes), 2)
                    self.assertEqual(result.content_targets[0].object_id, "material-1")
                    self.assertEqual(len(update["tool_trace"]), 2)
                elif case.name == "C_insufficient_sample":
                    self.assertFalse(result.suspected_causes)
                    self.assertTrue(result.uncertainties)
                    self.assertTrue(result.evidence_needed)
                    self.assertFalse(result.need_content_review)
                elif case.name == "D_lead_quality_anomaly":
                    self.assertTrue(result.diagnoses)
                    self.assertTrue(result.evidence_needed)
                    self.assertEqual(update["tool_trace"][0].tool_name, "get_lead_quality")
                elif case.name == "E_multiple_possible_causes":
                    self.assertGreaterEqual(len(result.suspected_causes), 3)
                    self.assertTrue(
                        all(
                            cause.certainty is DiagnosisCertainty.HYPOTHESIS
                            for cause in result.suspected_causes
                        )
                    )
                    self.assertEqual(len(update["tool_trace"]), 2)

        # Content Review is requested only for the case with a concrete
        # material-level question, not as a mandatory route for every case.
        self.assertEqual(content_review_cases, ["B_high_ctr_low_valid_lead"])
        self.assertLess(len(content_review_cases), len(_case_fixtures()))
        self.assertGreaterEqual(len(cases_with_hypotheses), 3)
        self.assertGreaterEqual(len(cases_with_uncertainty), 4)

    def test_tool_messages_exist_only_for_case_selected_tools(self):
        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                tools = {
                    name: _tool_fixture(name)
                    for name in case.tool_names
                }
                AdsDiagnosisAgent(
                    model=model,
                    tools=tools or None,
                    principles="fixture principles",
                ).run(case.state)
                tool_messages = [
                    message
                    for message in model.structured_inputs[0]
                    if isinstance(message, ToolMessage)
                ]
                self.assertEqual(len(tool_messages), len(case.tool_calls))


if __name__ == "__main__":
    unittest.main()
