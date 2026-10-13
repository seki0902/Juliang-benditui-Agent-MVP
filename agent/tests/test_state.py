import json
import unittest
from typing import get_type_hints

from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.common import (
    AgentName,
    AgentTraceRecord,
    EvidenceItem,
    OpenQuestion,
    RouteRecord,
    ToolCallRecord,
)
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.strategy import StrategyResult
from schemas.supervisor import SupervisorDecision
from state import MultiAgentState, apply_state_update


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


class MultiAgentStateTests(unittest.TestCase):
    def test_state_has_all_agent_result_positions_and_shared_context(self):
        fields = get_type_hints(MultiAgentState)
        expected = {
            "data_analysis_result",
            "ads_diagnosis_result",
            "content_review_result",
            "strategy_result",
            "supervisor_decision",
            "open_questions",
            "evidence",
            "agent_call_counts",
            "total_agent_calls",
            "route_trace",
            "tool_trace",
            "agent_trace",
            "errors",
        }

        self.assertTrue(expected.issubset(fields))

    def test_state_can_hold_structured_results_questions_evidence_and_trace(self):
        evidence = EvidenceItem(
            evidence_type="data",
            source="account_daily_metrics.csv",
            object_type="account",
            object_id="acct-1",
            metric_or_content="spend",
            value=100,
            time_range="2026-09-15",
        )
        state: MultiAgentState = {
            "run_id": "run-1",
            "report_date": "2026-09-15",
            "task": "生成日报诊断",
            "account_context": {"account_id": "acct-1"},
            "data_analysis_result": DataAnalysisResult(summary="发现一个待诊断问题。"),
            "ads_diagnosis_result": AdsDiagnosisResult(summary="原因仍需验证。"),
            "content_review_result": ContentReviewResult(summary="尚未请求内容审核。"),
            "strategy_result": StrategyResult(overall_judgement="等待证据。"),
            "supervisor_decision": SupervisorDecision(
                next_agent=AgentName.ADS_DIAGNOSIS,
                task="验证原因",
                reason="数据分析已提出诊断目标。",
            ),
            "open_questions": [OpenQuestion(question="是否存在回传延迟？")],
            "evidence": [evidence],
            "agent_call_counts": {"data_analysis": 1, "ads_diagnosis": 1},
            "total_agent_calls": 2,
            "route_trace": [
                RouteRecord(
                    from_agent=AgentName.DATA_ANALYSIS,
                    to_agent=AgentName.ADS_DIAGNOSIS,
                    reason="需要原因诊断",
                )
            ],
            "tool_trace": [ToolCallRecord(tool_name="load_report", status="succeeded")],
            "agent_trace": [
                AgentTraceRecord(
                    agent="data_analysis",
                    status="succeeded",
                    duration_ms=1,
                    timestamp="2026-09-18T00:00:00+00:00",
                )
            ],
            "errors": [],
            "final_report": None,
            "push_result": None,
        }

        serialized = json.dumps(_jsonable(state), ensure_ascii=False)
        restored = json.loads(serialized)
        self.assertEqual(restored["run_id"], "run-1")
        self.assertEqual(restored["evidence"][0]["metric_or_content"], "spend")
        self.assertEqual(restored["supervisor_decision"]["next_agent"], "ads_diagnosis")

    def test_state_does_not_define_real_ad_execution_fields(self):
        fields = set(get_type_hints(MultiAgentState))
        forbidden = {
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }

        self.assertTrue(forbidden.isdisjoint(fields))

    def test_result_slots_are_overwritten_but_history_lists_are_appended(self):
        first_route = RouteRecord(
            from_agent=AgentName.DATA_ANALYSIS,
            to_agent=AgentName.ADS_DIAGNOSIS,
            reason="first handoff",
        )
        second_route = RouteRecord(
            from_agent=AgentName.ADS_DIAGNOSIS,
            to_agent=AgentName.CONTENT_REVIEW,
            reason="content follow-up",
        )
        initial: MultiAgentState = {
            "data_analysis_result": DataAnalysisResult(summary="old result"),
            "route_trace": [first_route],
            "tool_trace": [],
            "errors": [],
        }

        updated = apply_state_update(
            initial,
            {
                "data_analysis_result": DataAnalysisResult(summary="new result"),
                "route_trace": [second_route],
                "tool_trace": [ToolCallRecord(tool_name="load_report", status="succeeded")],
                "agent_trace": [
                    AgentTraceRecord(
                        agent="data_analysis",
                        status="succeeded",
                        duration_ms=1,
                        timestamp="2026-09-18T00:00:00+00:00",
                    )
                ],
                "errors": ["temporary warning"],
            },
        )

        self.assertEqual(updated["data_analysis_result"].summary, "new result")
        self.assertEqual(updated["route_trace"], [first_route, second_route])
        self.assertEqual(len(updated["tool_trace"]), 1)
        self.assertEqual(len(updated["agent_trace"]), 1)
        self.assertEqual(updated["errors"], ["temporary warning"])
        self.assertEqual(initial["data_analysis_result"].summary, "old result")

    def test_second_write_does_not_duplicate_exact_history_items(self):
        route = RouteRecord(
            from_agent=AgentName.DATA_ANALYSIS,
            to_agent=AgentName.ADS_DIAGNOSIS,
            reason="same handoff",
        )
        state: MultiAgentState = {"route_trace": [route]}

        updated = apply_state_update(state, {"route_trace": [route]})

        self.assertEqual(updated["route_trace"], [route])

    def test_unknown_fields_and_non_list_append_updates_fail(self):
        with self.assertRaises(KeyError):
            apply_state_update({}, {"budget_change": "-20%"})

        with self.assertRaises(TypeError):
            apply_state_update({}, {"route_trace": "not-a-list"})


if __name__ == "__main__":
    unittest.main()
