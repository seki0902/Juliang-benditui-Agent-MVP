import json
import unittest

from schemas.common import AgentName, ConfidenceLevel, EvidenceItem
from schemas.data_analysis import (
    AnalysisFinding,
    DataAnalysisResult,
    DiagnosisTarget,
    HandoffSuggestion,
    MissingDataItem,
)


class DataAnalysisSchemaTests(unittest.TestCase):
    def test_can_express_no_anomaly(self):
        result = DataAnalysisResult(
            summary="本次数据未发现需要升级的异常。",
            confidence=ConfidenceLevel.HIGH,
        )

        self.assertEqual(result.anomalies, [])
        self.assertIsNone(result.handoff_suggestion)
        self.assertIsInstance(json.loads(result.model_dump_json()), dict)

    def test_can_express_conflict_and_data_shortage(self):
        evidence = EvidenceItem(
            evidence_type="data",
            source="account_daily_metrics.csv",
            object_type="account",
            object_id="acct-1",
            metric_or_content="lead_count",
            value=12,
            time_range="2026-09-15",
        )
        result = DataAnalysisResult(
            summary="发现报表与回传数据存在冲突，暂不足以完成完整判断。",
            conflicts=[
                AnalysisFinding(
                    description="投放报表留资数与回传有效留资数不一致。",
                    object_type="account",
                    object_id="acct-1",
                    evidence=[evidence],
                    confidence=ConfidenceLevel.MEDIUM,
                )
            ],
            missing_data=[
                MissingDataItem(
                    field="lead_quality_feedback",
                    reason="当前日期尚未收到完整反馈表。",
                    impact="无法判断有效留资成本。",
                )
            ],
            evidence=[evidence],
            confidence=ConfidenceLevel.MEDIUM,
        )

        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.missing_data[0].field, "lead_quality_feedback")

    def test_can_suggest_further_diagnosis_without_forcing_route(self):
        result = DataAnalysisResult(
            summary="发现一个值得进一步诊断的计划。",
            targets_for_diagnosis=[
                DiagnosisTarget(
                    question="该计划的转化下降主要来自流量还是素材？",
                    object_type="plan",
                    object_id="plan-1",
                    evidence_ids=["evidence-1"],
                )
            ],
            handoff_suggestion=HandoffSuggestion(
                suggested_agent=AgentName.ADS_DIAGNOSIS,
                task="分析计划转化下降的可能原因。",
                reason="当前数据已足以提出诊断问题，但不足以直接下结论。",
                confidence=ConfidenceLevel.MEDIUM,
            ),
        )

        self.assertEqual(result.handoff_suggestion.suggested_agent, AgentName.ADS_DIAGNOSIS)
        self.assertEqual(result.targets_for_diagnosis[0].object_id, "plan-1")


if __name__ == "__main__":
    unittest.main()
