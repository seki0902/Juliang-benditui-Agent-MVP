import json
import unittest

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


class AdsDiagnosisSchemaTests(unittest.TestCase):
    def test_can_express_clear_cause_and_hypothesis(self):
        result = AdsDiagnosisResult(
            summary="当前有一个相对明确的原因，同时保留一个待验证假设。",
            diagnoses=[
                DiagnosisItem(
                    statement="计划转化下降与后端有效留资下降同时出现。",
                    certainty=DiagnosisCertainty.LIKELY,
                    object_type="plan",
                    object_id="plan-1",
                    confidence=ConfidenceLevel.MEDIUM,
                )
            ],
            suspected_causes=[
                SuspectedCause(
                    cause="素材可能吸引了较多非目标人群。",
                    certainty=DiagnosisCertainty.HYPOTHESIS,
                    evidence_ids=["evidence-1"],
                )
            ],
        )

        self.assertEqual(result.diagnoses[0].certainty, DiagnosisCertainty.LIKELY)
        self.assertEqual(result.suspected_causes[0].certainty, DiagnosisCertainty.HYPOTHESIS)

    def test_can_request_content_review_for_multiple_targets(self):
        evidence = EvidenceItem(
            evidence_type="data",
            source="material_daily_metrics.csv",
            object_type="material",
            object_id="mat-1",
            metric_or_content="valid_lead_rate",
            value=0.02,
            time_range="2026-09-15",
        )
        result = AdsDiagnosisResult(
            summary="需要检查多个素材是否共同造成流量筛选偏差。",
            supporting_evidence=[evidence],
            need_content_review=True,
            content_targets=[
                ContentTarget(object_id="mat-1", reason="有效线索率明显偏低。"),
                ContentTarget(object_id="mat-2", reason="点击表现与后端结果不一致。"),
            ],
        )

        self.assertTrue(result.need_content_review)
        self.assertEqual(len(result.content_targets), 2)
        self.assertEqual(result.content_targets[0].object_id, "mat-1")

    def test_can_request_additional_evidence_and_has_no_execution_fields(self):
        result = AdsDiagnosisResult(
            summary="现有证据不足，只能提出假设。",
            uncertainties=[
                UncertaintyItem(
                    question="是否存在回传延迟？",
                    reason="当前反馈表尚未覆盖完整观察窗口。",
                    evidence_needed=["lead_quality_feedback"],
                )
            ],
            evidence_needed=[
                EvidenceRequest(
                    request="补充最近 7 天有效留资反馈。",
                    reason="验证前端留资与后端质量是否一致。",
                    source_hint="feedback_export",
                )
            ],
        )

        self.assertEqual(len(result.evidence_needed), 1)
        self.assertEqual(len(result.uncertainties), 1)
        execution_fields = {"budget_change", "bid_change", "pause_plan", "scale_percentage"}
        self.assertTrue(execution_fields.isdisjoint(AdsDiagnosisResult.model_fields))
        self.assertIsInstance(json.loads(result.model_dump_json()), dict)


if __name__ == "__main__":
    unittest.main()
