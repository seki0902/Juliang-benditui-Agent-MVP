import json
import unittest

from pydantic import ValidationError

from schemas.common import (
    AgentName,
    ConfidenceLevel,
    EvidenceItem,
    HandoffSuggestion,
    OpenQuestion,
    RouteRecord,
    RouteTraceRecord,
    ToolCallRecord,
)


class CommonSchemaTests(unittest.TestCase):
    def test_agent_name_is_a_finite_serializable_set(self):
        self.assertEqual(
            {item.value for item in AgentName},
            {"data_analysis", "ads_diagnosis", "content_review", "strategy", "finish"},
        )
        route = RouteRecord(
            from_agent=AgentName.DATA_ANALYSIS,
            to_agent=AgentName.ADS_DIAGNOSIS,
            reason="diagnostic handoff",
            confidence=ConfidenceLevel.MEDIUM,
        )
        self.assertEqual(json.loads(route.model_dump_json())["to_agent"], "ads_diagnosis")

    def test_evidence_supports_data_and_content(self):
        data_evidence = EvidenceItem(
            evidence_type="data",
            source="account_daily_metrics.csv",
            object_type="account",
            object_id="acct-1",
            metric_or_content="spend",
            value=123.45,
            time_range="2026-09-15",
        )
        content_evidence = EvidenceItem(
            evidence_type="content",
            source="material_review",
            object_type="material",
            object_id="mat-1",
            metric_or_content="opening_claim",
            value="示例口播片段",
            note="待人工确认",
        )

        for item in (data_evidence, content_evidence):
            self.assertIsInstance(json.loads(item.model_dump_json()), dict)

    def test_common_records_are_serializable_and_validate_required_fields(self):
        question = OpenQuestion(question="是否存在回传延迟？", blocking=True)
        tool_call = ToolCallRecord(
            tool_name="load_report",
            status="succeeded",
            arguments={"date": "2026-09-15"},
            result={"rows": 10},
        )
        payload = {"question": question.model_dump(mode="json"), "tool": tool_call.model_dump(mode="json")}
        self.assertEqual(payload["question"]["blocking"], True)
        self.assertEqual(payload["tool"]["result"]["rows"], 10)

        with self.assertRaises(ValidationError):
            RouteRecord(from_agent="not_an_agent", to_agent=AgentName.FINISH, reason="invalid")

    def test_route_trace_record_contains_supervisor_decision_details(self):
        trace = RouteTraceRecord(
            step=3,
            from_agent="supervisor",
            to_agent=AgentName.CONTENT_REVIEW,
            reason="已有诊断结果仍缺少素材解释。",
            task="检查素材表现与内容风险。",
            timestamp="2026-09-17T10:00:00+00:00",
        )
        payload = json.loads(trace.model_dump_json(by_alias=True))

        self.assertEqual(
            payload,
            {
                "step": 3,
                "from": "supervisor",
                "to": "content_review",
                "reason": "已有诊断结果仍缺少素材解释。",
                "task": "检查素材表现与内容风险。",
                "timestamp": "2026-09-17T10:00:00+00:00",
            },
        )
        restored = RouteTraceRecord.model_validate(payload)
        self.assertEqual(restored.to_agent, AgentName.CONTENT_REVIEW)

    def test_handoff_suggestion_is_shared_by_agent_schemas(self):
        from schemas.ads_diagnosis import DiagnosisHandoffSuggestion
        from schemas.content_review import ContentHandoffSuggestion
        from schemas.data_analysis import HandoffSuggestion as DataAnalysisHandoffSuggestion

        self.assertIs(DataAnalysisHandoffSuggestion, HandoffSuggestion)
        self.assertIs(DiagnosisHandoffSuggestion, HandoffSuggestion)
        self.assertIs(ContentHandoffSuggestion, HandoffSuggestion)


if __name__ == "__main__":
    unittest.main()
