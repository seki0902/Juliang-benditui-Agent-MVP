import json
import unittest

from schemas.common import ConfidenceLevel, EvidenceItem
from schemas.content_review import (
    ContentFinding,
    ContentReviewResult,
    ContentUncertainty,
    MaterialReviewed,
    PerformanceExplanation,
    TestDirection,
)


class ContentReviewSchemaTests(unittest.TestCase):
    def test_can_review_multiple_materials(self):
        result = ContentReviewResult(
            summary="已完成两个素材的内容审核。",
            materials_reviewed=[
                MaterialReviewed(material_id="mat-1", review_scope="hook and CTA"),
                MaterialReviewed(material_id="mat-2", review_scope="audience expression"),
            ],
            content_findings=[
                ContentFinding(
                    material_id="mat-1",
                    category="hook",
                    finding="开头表达较宽，可能吸引非目标人群。",
                ),
                ContentFinding(
                    material_id="mat-2",
                    category="CTA",
                    finding="行动指引不够清晰。",
                ),
            ],
            confidence=ConfidenceLevel.MEDIUM,
        )

        self.assertEqual(len(result.materials_reviewed), 2)
        self.assertEqual(len(result.content_findings), 2)

    def test_can_quote_content_evidence_and_explain_performance(self):
        content_evidence = EvidenceItem(
            evidence_type="content",
            source="video_transcript",
            object_type="material",
            object_id="mat-1",
            metric_or_content="hook",
            value="开头直接承诺结果",
            time_range="2026-09-15",
        )
        performance_evidence = EvidenceItem(
            evidence_type="data",
            source="material_daily_metrics.csv",
            object_type="material",
            object_id="mat-1",
            metric_or_content="click_rate",
            value=0.12,
            time_range="2026-09-15",
        )
        result = ContentReviewResult(
            summary="素材内容特征与前端点击高、后端质量弱的表现存在关联。",
            content_evidence=[content_evidence],
            content_findings=[
                ContentFinding(
                    material_id="mat-1",
                    category="promise",
                    finding="承诺较强但目标人群边界不清。",
                    content_evidence=[content_evidence],
                )
            ],
            performance_explanations=[
                PerformanceExplanation(
                    material_id="mat-1",
                    performance_signal="点击率较高但有效线索率偏低。",
                    content_relationship="宽泛承诺可能扩大点击人群，但没有同步筛选目标用户。",
                    supporting_evidence=[content_evidence, performance_evidence],
                    confidence=ConfidenceLevel.MEDIUM,
                )
            ],
        )

        self.assertEqual(result.content_evidence[0].evidence_type, "content")
        self.assertEqual(result.performance_explanations[0].material_id, "mat-1")
        self.assertIsInstance(json.loads(result.model_dump_json()), dict)

    def test_can_suggest_test_directions_and_keep_uncertainties(self):
        result = ContentReviewResult(
            summary="可以提出内容测试方向，但现有素材原始文件不完整。",
            test_directions=[
                TestDirection(
                    direction="收窄目标人群表达并保留原 CTA 进行对照测试。",
                    reason="验证点击人群质量是否改善。",
                    target_material_ids=["mat-1", "mat-2"],
                    expected_signal="有效线索率提升且点击率不出现异常下滑。",
                    recheck_condition="完成足够观察窗口后复核前后端指标。",
                )
            ],
            uncertainties=[
                ContentUncertainty(
                    question="是否存在完整口播稿和视频转写？",
                    reason="当前只能看到标题，无法确认完整承诺表达。",
                    needed_input="补充原始口播稿或视频转写。",
                )
            ],
        )

        self.assertEqual(len(result.test_directions[0].target_material_ids), 2)
        self.assertEqual(result.uncertainties[0].needed_input, "补充原始口播稿或视频转写。")
        execution_fields = {"budget_change", "bid_change", "pause_plan", "scale_percentage"}
        self.assertTrue(execution_fields.isdisjoint(ContentReviewResult.model_fields))


if __name__ == "__main__":
    unittest.main()
