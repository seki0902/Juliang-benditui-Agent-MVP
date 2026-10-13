import unittest

from services.report_polisher import (
    ReportPolishGuardError,
    ReportPolisher,
    make_polished_report_renderer_node,
    polish_report,
    validate_polished_report,
)


BASE_REPORT = """# 投放诊断日报｜2026-09-18
run_id：run-polish-1

【今日重点】
建议先复核证据。

【数据发现】
- 发现 CTR 下降。

【投放诊断】
- 原因仍是待验证假设。

【内容审核】
不适用 / 本次未调用

【策略建议】
- 优先级 1：观察｜plan-1｜当前证据不足以支持直接调整

【仍需观察】
- 是否存在回传延迟？；原因：需要对齐转化时间窗

【证据不足项】
- 补充近 7 日分时转化数据；原因：用于复核假设

【分析路径】
- Step 1: supervisor → data_analysis｜检查数据异常
"""


class _FakeRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, payload):
        self.model.payload = payload
        if self.model.error is not None:
            raise self.model.error
        return self.model.response


class _FakeModel:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.payload = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.schema = schema
        self.include_raw = include_raw
        return _FakeRunnable(self)


class ReportPolisherTests(unittest.TestCase):
    def test_without_model_returns_the_deterministic_report_unchanged(self):
        outcome = ReportPolisher().run(BASE_REPORT)

        self.assertEqual(outcome.report, BASE_REPORT)
        self.assertFalse(outcome.applied)
        self.assertEqual(outcome.fallback_reason, "model_not_configured")

    def test_valid_candidate_only_polishes_an_allowed_prose_section(self):
        candidate = BASE_REPORT.replace(
            "- 发现 CTR 下降。",
            "- 发现 CTR 下降。数据侧观察到该变化。",
        )
        model = _FakeModel({"polished_report": candidate})

        outcome = ReportPolisher(model=model).run(BASE_REPORT)

        self.assertTrue(outcome.applied)
        self.assertEqual(outcome.report, candidate)
        self.assertEqual(model.schema.__name__, "ReportPolishOutput")
        self.assertFalse(model.include_raw)
        self.assertNotIn("run-polish-1", str(model.payload[0]))
        self.assertIn("投放诊断日报", str(model.payload[1]))

    def test_strategy_uncertainty_and_evidence_deletion_falls_back(self):
        unsafe_candidates = [
            BASE_REPORT.replace(
                "- 优先级 1：观察｜plan-1｜当前证据不足以支持直接调整",
                "- 优先级 1：暂停计划",
            ),
            BASE_REPORT.replace(
                "- 是否存在回传延迟？；原因：需要对齐转化时间窗\n",
                "",
            ),
            BASE_REPORT.replace(
                "不适用 / 本次未调用",
                "素材审核正常",
            ),
            BASE_REPORT.replace(
                "- 原因仍是待验证假设。",
                "- 已确认原因为流量定向错误。",
            ),
        ]

        for candidate in unsafe_candidates:
            with self.subTest(candidate=candidate):
                outcome = ReportPolisher(
                    model=_FakeModel({"polished_report": candidate})
                ).run(BASE_REPORT)
                self.assertFalse(outcome.applied)
                self.assertEqual(outcome.report, BASE_REPORT)
                self.assertTrue(outcome.fallback_reason.startswith("guard_rejected:"))

    def test_new_action_marker_in_a_prose_section_falls_back(self):
        candidate = BASE_REPORT.replace(
            "- 发现 CTR 下降。",
            "- 发现 CTR 下降，建议暂停计划。",
        )

        with self.assertRaises(ReportPolishGuardError):
            validate_polished_report(BASE_REPORT, candidate)

    def test_model_or_schema_failure_falls_back_without_leaking_error_text(self):
        outcome = ReportPolisher(
            model=_FakeModel(error=RuntimeError("api_key=secret-value"))
        ).run(BASE_REPORT)

        self.assertFalse(outcome.applied)
        self.assertEqual(outcome.report, BASE_REPORT)
        self.assertEqual(
            outcome.fallback_reason,
            "structured_output_failed:ReportPolishOutput",
        )
        self.assertNotIn("secret-value", outcome.fallback_reason)

    def test_function_and_graph_node_entry_points_are_opt_in(self):
        candidate = BASE_REPORT.replace(
            "- 发现 CTR 下降。",
            "- 发现 CTR 下降。数据侧观察到该变化。",
        )
        model = _FakeModel({"polished_report": candidate})
        self.assertEqual(
            polish_report(BASE_REPORT, model=model),
            candidate,
        )

        state = {
            "run_id": "node-run",
            "report_date": "2026-09-18",
            "open_questions": [],
            "route_trace": [],
        }
        node = make_polished_report_renderer_node(None)
        result = node(state)

        self.assertIn("【账户总体】", result["final_report"])
        self.assertIn("数据尚未完成分析", result["final_report"])


if __name__ == "__main__":
    unittest.main()
