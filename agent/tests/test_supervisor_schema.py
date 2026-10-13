import json
import unittest

from pydantic import ValidationError

from schemas.common import AgentName, OpenQuestion
from schemas.supervisor import SupervisorDecision


class SupervisorSchemaTests(unittest.TestCase):
    def test_can_route_to_agent_or_finish(self):
        decision = SupervisorDecision(
            next_agent=AgentName.DATA_ANALYSIS,
            task="先确认当前日期的账户和计划数据是否完整。",
            reason="当前 State 尚缺少基础数据分析结果。",
            open_question_to_resolve=OpenQuestion(
                question="当前报表是否覆盖完整观察窗口？",
                blocking=True,
            ),
        )
        finish = SupervisorDecision(
            next_agent=AgentName.FINISH,
            task="汇总已有结果。",
            reason="所有必要 Agent 已完成且没有未解决问题。",
            can_finish=True,
        )

        self.assertEqual(decision.next_agent, AgentName.DATA_ANALYSIS)
        self.assertTrue(finish.can_finish)
        self.assertIsInstance(json.loads(decision.model_dump_json()), dict)

    def test_rejects_invalid_agent_name(self):
        with self.assertRaises(ValidationError):
            SupervisorDecision(
                next_agent="budget_optimizer",
                task="执行预算优化",
                reason="invalid route",
            )

    def test_rejects_business_conclusion_or_execution_fields(self):
        with self.assertRaises(ValidationError):
            SupervisorDecision(
                next_agent=AgentName.STRATEGY,
                task="汇总分析",
                reason="继续处理",
                budget_change="-20%",
            )

        with self.assertRaises(ValidationError):
            SupervisorDecision(
                next_agent=AgentName.CONTENT_REVIEW,
                task="检查素材",
                reason="内容审核建议",
                material_judgement="bad_material",
            )


if __name__ == "__main__":
    unittest.main()
