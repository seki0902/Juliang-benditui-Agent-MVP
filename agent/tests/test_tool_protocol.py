import json
import unittest

import pandas as pd

from services.account_metrics_tool import AccountMetricsTool
from services.content_asset_tool import ContentAssetTool
from services.history_context_tool import HistoryContextTool
from services.lead_quality_tool import LeadQualityTool
from services.material_metrics_tool import MaterialMetricsTool
from services.plan_metrics_tool import PlanMetricsTool
from services.sales_feedback_tool import SalesFeedbackTool
from services.tool_protocol import ALLOWED_STATUSES, REQUIRED_TOOL_KEYS


class ToolProtocolTests(unittest.TestCase):
    def setUp(self):
        self.units = pd.DataFrame(
            [
                {
                    "data_date": pd.Timestamp("2026-09-17"),
                    "unit_id": "u1",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 0,
                }
            ]
        )
        self.materials = pd.DataFrame(
            [
                {
                    "data_date": pd.Timestamp("2026-09-17"),
                    "material_id": "m1",
                    "material_name": "素材一",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 0,
                }
            ]
        )

    def test_all_seven_tools_expose_common_protocol_keys(self):
        results = {
            "account": AccountMetricsTool(self.units).get_account_metrics(
                "2026-09-17", "day"
            ),
            "plan": PlanMetricsTool(self.units).get_plan_metrics(
                "u1", "2026-09-17", "day"
            ),
            "material": MaterialMetricsTool(self.materials).get_material_metrics(
                "m1", "2026-09-17", "day"
            ),
            "lead_quality": LeadQualityTool(units=self.units).get_lead_quality(
                "2026-09-17", "day"
            ),
            "history": HistoryContextTool(account_id="acct-1").get_history_context(
                "account", None, "2026-09-18", "day"
            ),
            "content": ContentAssetTool().get_content_asset("m1"),
            "sales": SalesFeedbackTool().get_sales_feedback("2026-09-17", "day"),
        }

        for name, result in results.items():
            with self.subTest(tool=name):
                self.assertTrue(REQUIRED_TOOL_KEYS <= result.keys())
                self.assertIsInstance(result["warnings"], list)
                self.assertIsInstance(result["data"], dict)
                json.dumps(result, ensure_ascii=False)

    def test_legacy_no_data_status_keeps_canonical_status_category(self):
        result = PlanMetricsTool(self.units).get_plan_metrics(
            "u1", "2026-09-18", "day"
        )

        self.assertEqual(result["status"], "no_data")
        self.assertEqual(result["status_category"], "unavailable")
        self.assertIn(result["status_category"], ALLOWED_STATUSES)
        self.assertTrue(REQUIRED_TOOL_KEYS <= result.keys())
        json.dumps(result, ensure_ascii=False)

    def test_invalid_window_is_returned_as_structured_error(self):
        result = AccountMetricsTool(self.units).get_account_metrics(
            "2026-09-17", "not-a-window"
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "ValueError")
        self.assertTrue(REQUIRED_TOOL_KEYS <= result.keys())
        self.assertTrue(result["warnings"])
        json.dumps(result, ensure_ascii=False)


    def test_agents_reply_to_every_request_when_tool_budget_is_exhausted(self):
        from unittest.mock import Mock
        from langchain_core.messages import AIMessage, ToolMessage
        from agents.data_analysis_agent import DataAnalysisAgent
        from agents.ads_diagnosis_agent import AdsDiagnosisAgent
        from agents.content_review_agent import ContentReviewAgent
        from schemas.data_analysis import DataAnalysisResult
        from schemas.ads_diagnosis import AdsDiagnosisResult
        from schemas.content_review import ContentReviewResult

        for agent_class, schema in (
            (DataAnalysisAgent, DataAnalysisResult),
            (AdsDiagnosisAgent, AdsDiagnosisResult),
            (ContentReviewAgent, ContentReviewResult),
        ):
            with self.subTest(agent=agent_class.__name__):
                tool = Mock()
                tool.invoke.return_value = {"status": "ok", "data": {"spend": 42}}
                model = Mock()
                model.bind_tools.return_value.invoke.return_value = AIMessage(
                    content="", tool_calls=[
                        {"name": "metrics", "args": {}, "id": "done"},
                        {"name": "metrics", "args": {}, "id": "skipped"},
                    ],
                )
                model.with_structured_output.return_value.invoke.return_value = schema(summary="fixture")
                update = agent_class(
                    model=model, tools={"metrics": tool},
                    principles="fixture", max_tool_calls=1,
                ).run({"run_id": "budget-regression", "report_date": "2026-09-17"})
                messages = model.with_structured_output.return_value.invoke.call_args.args[0]
                replies = {m.tool_call_id: json.loads(m.content)
                           for m in messages if isinstance(m, ToolMessage)}
                self.assertEqual(set(replies), {"done", "skipped"})
                self.assertEqual(replies["done"]["data"], {"spend": 42})
                self.assertEqual(replies["skipped"]["status"], "unavailable")
                self.assertEqual(replies["skipped"]["data"], {})
                self.assertEqual(tool.invoke.call_count, 1)
                self.assertEqual([r.status for r in update["tool_trace"]], ["ok", "unavailable"])
                self.assertTrue(any("Tool call limit reached" in e for e in update["errors"]))


if __name__ == "__main__":
    unittest.main()
