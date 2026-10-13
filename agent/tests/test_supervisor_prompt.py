import re
import unittest
from pathlib import Path


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "supervisor.md"


class SupervisorPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def test_prompt_exists_and_defines_supervisor_sections(self):
        required_sections = [
            "# Supervisor Agent Prompt",
            "## 身份",
            "## 运行时输入",
            "## 可调度的目标",
            "## 工作目标",
            "## 可以做",
            "## 不可以做",
            "## 路由与结束判断",
            "## 输出契约",
            "## 输出前自检",
            "SupervisorDecision",
        ]

        for section in required_sections:
            with self.subTest(section=section):
                self.assertIn(section, self.prompt)

    def test_prompt_covers_global_state_and_handoff_inputs(self):
        expected_inputs = [
            "当前任务",
            "当前完整 `MultiAgentState`",
            "Data Analysis Result",
            "Ads Diagnosis Result",
            "Content Review Result",
            "Strategy Result",
            "open_questions",
            "agent_call_counts",
            "最近一次专业 Agent 的 `handoff_suggestion`",
        ]

        for expected in expected_inputs:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.prompt)

    def test_prompt_only_allows_orchestration_targets_and_finish(self):
        for target in (
            "`data_analysis`",
            "`ads_diagnosis`",
            "`content_review`",
            "`strategy`",
            "`finish`",
            "next_agent",
            "task",
            "reason",
            "can_finish",
        ):
            with self.subTest(target=target):
                self.assertIn(target, self.prompt)

        for boundary in (
            "handoff_suggestion` 只能作为参考",
            "不做专业业务分析",
            "不要自己回答“素材不好”",
            "不要自己回答“应该停计划”",
            "不要自己写 Data Analysis、Ads Diagnosis、Content Review 或 Strategy 的专业结论",
            "不要输出预算、出价、定向、暂停、关停、扩量、缩量",
            "只做了调度或结束判断",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, self.prompt)

    def test_prompt_does_not_encode_business_thresholds_or_fixed_route(self):
        self.assertIsNone(re.search(r"\d+\s*%", self.prompt))
        self.assertIsNone(re.search(r"必须调用\s+`?get_[a-z_]+", self.prompt))
        self.assertIsNone(re.search(r"固定顺序|必须先调用", self.prompt))
        self.assertIn("调用次数是技术保护，不是业务路由规则", self.prompt)
        self.assertIn("不要在 Prompt 中自行写死具体上限", self.prompt)


if __name__ == "__main__":
    unittest.main()
