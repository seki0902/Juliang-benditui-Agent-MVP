import json
import unittest

from schemas.common import ConfidenceLevel, EvidenceItem
from schemas.strategy import (
    ObservationItem,
    PriorityAction,
    StrategyEvidenceRequest,
    StrategyResult,
)


class StrategySchemaTests(unittest.TestCase):
    def test_can_output_multiple_prioritized_recommendations(self):
        result = StrategyResult(
            overall_judgement="当前优先验证素材与线索质量关系，不直接扩大投放。",
            priority_actions=[
                PriorityAction(
                    priority=1,
                    target_type="material",
                    target_id="mat-1",
                    action_type="content_test",
                    reason="先验证目标人群筛选是否改善。",
                    expected_signal="有效线索率改善。",
                    recheck_condition="完成观察窗口后复核前后端指标。",
                ),
                PriorityAction(
                    priority=2,
                    target_type="plan",
                    target_id="plan-1",
                    action_type="recheck_data",
                    reason="确认后端反馈是否完整。",
                    expected_signal="反馈数据覆盖完整观察窗口。",
                    recheck_condition="补齐反馈后重新诊断。",
                ),
            ],
            confidence=ConfidenceLevel.MEDIUM,
        )

        self.assertEqual([item.priority for item in result.priority_actions], [1, 2])
        self.assertIsInstance(json.loads(result.model_dump_json()), dict)

    def test_can_output_continue_observing_without_an_action(self):
        result = StrategyResult(
            overall_judgement="当前样本不足，先继续观察。",
            priority_actions=[
                PriorityAction(
                    priority=1,
                    target_type="plan",
                    target_id="plan-1",
                    action_type="continue_observation",
                    reason="当前没有足够证据支持调整。",
                    expected_signal="指标保持在可观察范围内。",
                    recheck_condition="下一观察窗口复核。",
                )
            ],
            observation_items=[
                ObservationItem(
                    target_type="plan",
                    target_id="plan-1",
                    observation="保持当前状态并观察有效线索率变化。",
                    recheck_condition="累计足够样本后复核。",
                )
            ],
        )

        self.assertEqual(result.priority_actions[0].action_type, "continue_observation")
        self.assertEqual(len(result.observation_items), 1)

    def test_can_request_evidence_and_state_cannot_conclude(self):
        evidence = EvidenceItem(
            evidence_type="data",
            source="account_daily_metrics.csv",
            object_type="account",
            object_id="acct-1",
            metric_or_content="lead_count",
            value=8,
            time_range="2026-09-15",
        )
        result = StrategyResult(
            overall_judgement="暂不能判断是否适合扩量。",
            supporting_evidence=[evidence],
            need_more_evidence=True,
            evidence_request=StrategyEvidenceRequest(
                request="补充有效留资和成交反馈。",
                reason="当前只有前端留资数据。",
                source_hint="feedback_export",
                target_type="account",
                target_id="acct-1",
            ),
            cannot_conclude=["不能仅依据当前点击和前端留资判断最终业务质量。"],
        )

        self.assertTrue(result.need_more_evidence)
        self.assertEqual(len(result.cannot_conclude), 1)
        self.assertEqual(result.evidence_request.target_id, "acct-1")
        execution_fields = {"budget_change", "bid_change", "pause_plan", "scale_percentage"}
        self.assertTrue(execution_fields.isdisjoint(StrategyResult.model_fields))


if __name__ == "__main__":
    unittest.main()
