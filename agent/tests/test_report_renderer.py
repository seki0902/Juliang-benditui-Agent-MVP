import unittest

from graph import build_graph
from schemas.ads_diagnosis import (
    AdsDiagnosisResult,
    DiagnosisCertainty,
    DiagnosisItem,
    EvidenceRequest,
    SuspectedCause,
    UncertaintyItem,
)
from schemas.common import AgentName, OpenQuestion, RouteTraceRecord
from schemas.content_review import (
    ContentFinding,
    ContentReviewResult,
    ContentUncertainty,
    PerformanceExplanation,
    TestDirection,
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
from schemas.supervisor import SupervisorDecision
from services.report_renderer import (
    ReportRenderer,
    render_daily_report,
    render_report,
    report_renderer_node,
)


def _complete_state():
    return {
        "run_id": "renderer-run-1",
        "report_date": "2026-09-18",
        "task": "生成投放诊断日报",
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": DataAnalysisResult(
            summary="数据侧发现 CTR 从 2.1% 降至 1.4%。",
            observations=[
                AnalysisFinding(
                    description="账户层 CTR 持续下降",
                    object_type="account",
                    object_id="acct-1",
                )
            ],
            anomalies=[
                AnalysisFinding(
                    description="计划 plan-1 的消耗集中度异常",
                    object_type="plan",
                    object_id="plan-1",
                )
            ],
            missing_data=[
                MissingDataItem(
                    field="lead_quality",
                    reason="当前批次尚未回传销售结果",
                )
            ],
        ),
        "ads_diagnosis_result": AdsDiagnosisResult(
            summary="投放侧存在一个待验证的流量匹配假设。",
            diagnoses=[
                DiagnosisItem(
                    statement="计划定向与当前素材受众可能不完全匹配",
                    certainty=DiagnosisCertainty.HYPOTHESIS,
                    object_type="plan",
                    object_id="plan-1",
                )
            ],
            suspected_causes=[
                SuspectedCause(
                    cause="近期流量结构发生变化",
                    certainty=DiagnosisCertainty.HYPOTHESIS,
                )
            ],
            uncertainties=[
                UncertaintyItem(
                    question="是否存在回传延迟？",
                    reason="需要对齐转化时间窗",
                )
            ],
            evidence_needed=[
                EvidenceRequest(
                    request="补充近 7 日分时转化数据",
                    reason="用于复核流量结构假设",
                )
            ],
        ),
        "content_review_result": ContentReviewResult(
            summary="素材侧发现开头信息密度偏高。",
            content_findings=[
                ContentFinding(
                    material_id="mat-1",
                    category="opening",
                    finding="前 3 秒同时出现多个卖点",
                )
            ],
            performance_explanations=[
                PerformanceExplanation(
                    material_id="mat-1",
                    performance_signal="CTR",
                    content_relationship="信息密度可能影响首屏理解",
                )
            ],
            test_directions=[
                TestDirection(
                    direction="保留核心卖点并简化首屏文案",
                    reason="便于观察点击变化",
                )
            ],
            uncertainties=[
                ContentUncertainty(
                    question="是否有完整口播稿？",
                    reason="当前只拿到素材摘要",
                    needed_input="补充完整口播稿",
                )
            ],
        ),
        "strategy_result": StrategyResult(
            overall_judgement="建议先补齐转化回传，再复核计划与素材的共同变化。",
            priority_actions=[
                PriorityAction(
                    priority=1,
                    target_type="plan",
                    target_id="plan-1",
                    action_type="观察",
                    reason="当前证据不足以支持直接调整",
                    expected_signal="转化数据完整",
                    recheck_condition="下一周期复核",
                )
            ],
            observation_items=[
                ObservationItem(
                    target_type="material",
                    target_id="mat-1",
                    observation="继续观察简化首屏后的 CTR 变化",
                    recheck_condition="累计足够曝光后复核",
                )
            ],
            evidence_request=StrategyEvidenceRequest(
                request="补充销售回传与分时转化数据",
                reason="避免把相关性当作因果结论",
            ),
            cannot_conclude=["暂不能确认单一素材是 CTR 下滑的唯一原因"],
        ),
        "supervisor_decision": SupervisorDecision(
            next_agent=AgentName.FINISH,
            task="生成日报",
            reason="结构化结果已汇总",
            can_finish=True,
        ),
        "open_questions": [
            OpenQuestion(
                question="是否存在归因窗口变化？",
                reason="平台侧配置尚未核对",
            )
        ],
        "route_trace": [
            RouteTraceRecord(
                step=1,
                from_agent="supervisor",
                to_agent=AgentName.DATA_ANALYSIS,
                task="检查数据异常",
                reason="先确认事实层变化",
                timestamp="2026-09-18T00:00:00+00:00",
            ),
            RouteTraceRecord(
                step=2,
                from_agent="supervisor",
                to_agent=AgentName.FINISH,
                task="生成日报",
                reason="结果已汇总",
                timestamp="2026-09-18T00:01:00+00:00",
            ),
        ],
    }


class ReportRendererTests(unittest.TestCase):
    def test_complete_structured_state_renders_all_sections_and_existing_facts(self):
        state = _complete_state()
        report = ReportRenderer(compact=False, include_path=True).render(state)

        for title in (
            "【今日重点】",
            "【数据发现】",
            "【投放诊断】",
            "【内容审核】",
            "【策略建议】",
            "【仍需观察】",
            "【证据不足项】",
            "【分析路径】",
        ):
            with self.subTest(title=title):
                self.assertIn(title, report)

        for existing_text in (
            "建议先补齐转化回传",
            "账户层 CTR 持续下降",
            "计划定向与当前素材受众可能不完全匹配",
            "前 3 秒同时出现多个卖点",
            "当前证据不足以支持直接调整",
            "是否存在归因窗口变化？",
            "Step 1: supervisor → data_analysis",
            "Step 2: supervisor → finish",
        ):
            with self.subTest(existing_text=existing_text):
                self.assertIn(existing_text, report)

        self.assertNotIn("自动暂停计划", report)
        self.assertIs(render_report, render_daily_report)
        self.assertEqual(report_renderer_node(state), {"final_report": render_daily_report(state)})

    def test_missing_results_render_explicit_status_without_crashing(self):
        state = {
            "run_id": "missing-run",
            "report_date": "2026-09-18",
            "open_questions": [],
            "route_trace": [],
        }

        report = ReportRenderer(compact=False, include_path=True).render(state)

        self.assertIn("【数据发现】", report)
        self.assertIn("Data Analysis Agent 未调用", report)
        self.assertIn("Ads Diagnosis Agent 未调用", report)
        self.assertIn("不适用 / 本次未调用", report)
        self.assertIn("Strategy Agent 未调用", report)
        self.assertIn("【证据不足项】", report)
        self.assertIn("【分析路径】", report)

    def test_serialized_result_mappings_are_supported_and_path_can_be_hidden(self):
        state = _complete_state()
        serialized = dict(state)
        for field in (
            "data_analysis_result",
            "ads_diagnosis_result",
            "content_review_result",
            "strategy_result",
        ):
            serialized[field] = serialized[field].model_dump(mode="json")
        serialized["route_trace"] = [
            record.model_dump(mode="json", by_alias=True)
            for record in serialized["route_trace"]
        ]
        serialized["open_questions"] = [
            question.model_dump(mode="json")
            for question in serialized["open_questions"]
        ]

        report = ReportRenderer(include_path=False, compact=False).render(serialized)

        self.assertIn("数据侧发现 CTR 从 2.1% 降至 1.4%", report)
        self.assertIn("是否存在归因窗口变化？", report)
        self.assertNotIn("【分析路径】", report)

    def test_default_graph_uses_renderer_after_finish(self):
        def supervisor(_state):
            return {
                "supervisor_decision": SupervisorDecision(
                    next_agent=AgentName.FINISH,
                    task="生成日报",
                    reason="结构化结果已汇总",
                    can_finish=True,
                )
            }

        result = build_graph(node_overrides={"supervisor": supervisor}).invoke(
            {
                "run_id": "graph-renderer-run",
                "report_date": "2026-09-18",
                "task": "测试默认 Renderer",
            }
        )

        self.assertIn("【账户总体】", result["final_report"])
        self.assertNotIn("【分析路径】", result["final_report"])
        self.assertIn("数据尚未完成分析", result["final_report"])


    def test_default_five_sections_preserve_upstream_gaps_and_uncertainties(self):
        state = _complete_state()
        expected = [
            state["data_analysis_result"].missing_data[0].reason,
            state["ads_diagnosis_result"].evidence_needed[0].request,
            state["ads_diagnosis_result"].uncertainties[0].question,
            state["content_review_result"].uncertainties[0].needed_input,
            state["open_questions"][0].question,
        ]
        # Render the serialized State used by saved real-model runs.
        for field in ("data_analysis_result", "ads_diagnosis_result",
                      "content_review_result", "strategy_result"):
            state[field] = state[field].model_dump(mode="json")
        state["open_questions"] = [q.model_dump(mode="json") for q in state["open_questions"]]
        report = render_daily_report(state)
        self.assertEqual(
            [line for line in report.splitlines() if line.startswith("【")],
            ["【账户总体】", "【待处理计划】", "【问题更可能在哪】",
             "【今天具体做什么】", "【下一次看什么】"],
        )
        for text in expected:
            self.assertIn(text, report)
        self.assertIn("当前证据不足以支持直接调整", report)
        self.assertIn("下一周期复核", report)


if __name__ == "__main__":
    unittest.main()
