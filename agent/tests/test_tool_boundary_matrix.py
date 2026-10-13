import json
import unittest
from unittest.mock import patch

import pandas as pd

from services.account_metrics_tool import AccountMetricsTool
from services.content_asset_tool import ContentAssetTool
from services.history_context_tool import HistoryContextTool
from services.lead_quality_tool import LeadQualityTool
from services.material_metrics_tool import MaterialMetricsTool
from services.plan_metrics_tool import PlanMetricsTool
from services.sales_feedback_tool import SalesFeedbackTool
from services.tool_protocol import ALLOWED_STATUSES, REQUIRED_TOOL_KEYS


class _HistoryFixtureStore:
    def __init__(self, rows):
        self.rows = rows

    def load_history(self, _account_id, _before_date):
        return {
            object_type: [dict(row) for row in object_rows]
            for object_type, object_rows in self.rows.items()
        }


class _SalesFixtureAdapter:
    source = "crm_fixture"

    def get_sales_feedback(self, *_args):
        return {"won_deals": 1, "revenue": 3200.0}


class _EmptySalesAdapter:
    source = "crm_fixture"

    def get_sales_feedback(self, *_args):
        return None


class _ExplodingSalesAdapter:
    source = "crm_fixture"

    def get_sales_feedback(self, *_args):
        raise RuntimeError("sales source unavailable")


class _ExplodingContentAdapter:
    source = "content_fixture"

    def get(self, _material_id):
        raise RuntimeError("content source unavailable")


class ToolBoundaryMatrixTests(unittest.TestCase):
    def setUp(self):
        self.units = pd.DataFrame(
            [
                {
                    "data_date": pd.Timestamp("2026-09-01"),
                    "unit_id": "u1",
                    "unit_name": "计划一",
                    "plan_type": "shallow",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 2,
                },
                {
                    "data_date": pd.Timestamp("2026-09-02"),
                    "unit_id": "u1",
                    "unit_name": "计划一",
                    "plan_type": "shallow",
                    "spend": 200,
                    "impressions": 2000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 3,
                },
            ]
        )
        self.units_with_other_plan = pd.concat(
            [
                self.units,
                pd.DataFrame(
                    [
                        {
                            "data_date": pd.Timestamp("2026-09-03"),
                            "unit_id": "u2",
                            "unit_name": "计划二",
                            "plan_type": "deep",
                            "spend": 50,
                            "impressions": 500,
                            "clicks": 50,
                            "leads": 5,
                            "private_messages": 1,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        self.materials = pd.DataFrame(
            [
                {
                    "data_date": pd.Timestamp("2026-09-01"),
                    "material_id": "m1",
                    "material_name": "素材一",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 2,
                },
                {
                    "data_date": pd.Timestamp("2026-09-02"),
                    "material_id": "m1",
                    "material_name": "素材一",
                    "spend": 200,
                    "impressions": 2000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 3,
                },
            ]
        )
        self.materials_with_other_material = pd.concat(
            [
                self.materials,
                pd.DataFrame(
                    [
                        {
                            "data_date": pd.Timestamp("2026-09-03"),
                            "material_id": "m2",
                            "material_name": "素材二",
                            "spend": 50,
                            "impressions": 500,
                            "clicks": 50,
                            "leads": 5,
                            "private_messages": 1,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        self.account_feedback = pd.DataFrame(
            [
                {
                    "lead_date": pd.Timestamp("2026-09-01"),
                    "is_valid": True,
                    "is_contacted": True,
                },
                {
                    "lead_date": pd.Timestamp("2026-09-02"),
                    "is_valid": False,
                    "is_contacted": False,
                },
            ]
        )
        self.scoped_feedback = pd.DataFrame(
            [
                {
                    "lead_date": pd.Timestamp("2026-09-01"),
                    "unit_id": "u1",
                    "material_id": "m1",
                    "is_valid": True,
                    "is_contacted": True,
                    "invalid_reason": None,
                },
                {
                    "lead_date": pd.Timestamp("2026-09-02"),
                    "unit_id": "u1",
                    "material_id": "m1",
                    "is_valid": False,
                    "is_contacted": False,
                    "invalid_reason": "无效线索",
                },
            ]
        )

    @staticmethod
    def _history_row(data_date, object_id, id_column, spend):
        return {
            "data_date": data_date,
            id_column: object_id,
            "spend": spend,
            "impressions": spend * 10,
            "clicks": spend,
            "leads": spend / 10,
            "private_messages": 0,
        }

    def _history_store(self, missing_date=None):
        rows = {"account": [], "plan": [], "material": []}
        for day_number in range(1, 11):
            data_date = f"2026-09-{day_number:02d}"
            if data_date == missing_date:
                continue
            rows["account"].append(
                self._history_row(data_date, "account", "account_id", 100)
            )
            rows["plan"].append(self._history_row(data_date, "u1", "unit_id", 10))
            rows["material"].append(
                self._history_row(data_date, "m1", "material_id", 5)
            )
        return _HistoryFixtureStore(rows)

    def _assert_contract(self, result, expected_status=None):
        self.assertTrue(REQUIRED_TOOL_KEYS <= result.keys())
        canonical_status = result.get("status_category", result["status"])
        self.assertIn(canonical_status, ALLOWED_STATUSES)
        self.assertIsInstance(result["data"], dict)
        self.assertIsInstance(result["warnings"], list)
        if expected_status is not None:
            self.assertEqual(result["status"], expected_status)
        json.dumps(result, ensure_ascii=False)

    def _assert_structured_error(self, result):
        self._assert_contract(result, expected_status="error")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["data"], {})

    def test_normal_input_is_json_safe_for_every_tool(self):
        history = HistoryContextTool(self._history_store(), "acct-1")
        results = {
            "account": AccountMetricsTool(
                self.units, self.account_feedback
            ).get_account_metrics("2026-09-02", "mtd"),
            "plan": PlanMetricsTool(
                self.units, self.scoped_feedback
            ).get_plan_metrics("u1", "2026-09-02", "mtd"),
            "material": MaterialMetricsTool(
                self.materials, self.scoped_feedback
            ).get_material_metrics("m1", "2026-09-02", "mtd"),
            "lead_quality": LeadQualityTool(
                feedback=self.scoped_feedback, units=self.units
            ).get_lead_quality(
                "2026-09-02", "mtd", object_type="plan", object_id="u1"
            ),
            "history": history.get_history_context(
                "account",
                None,
                "2026-09-11",
                "3d",
                comparison_window="previous_7d",
                metrics=["spend"],
            ),
            "content": ContentAssetTool.from_mapping(
                {"m1": {"title": "素材标题", "script": "素材脚本"}}
            ).get_content_asset("m1"),
            "sales": SalesFeedbackTool(_SalesFixtureAdapter()).get_sales_feedback(
                "2026-09-17", "3d", object_type="plan", object_id="u1"
            ),
        }

        for name, result in results.items():
            with self.subTest(tool=name):
                self._assert_contract(result, expected_status="ok")

    def test_nonexistent_ids_are_explicit_and_no_data_is_not_zero(self):
        cases = {
            "missing_plan": PlanMetricsTool(
                self.units, self.scoped_feedback
            ).get_plan_metrics("missing", "2026-09-02", "day"),
            "missing_material": MaterialMetricsTool(
                self.materials, self.scoped_feedback
            ).get_material_metrics("missing", "2026-09-02", "day"),
            "missing_plan_quality": LeadQualityTool(
                feedback=self.scoped_feedback, units=self.units
            ).get_lead_quality(
                "2026-09-02", "day", object_type="plan", object_id="missing"
            ),
            "missing_history_plan": HistoryContextTool(
                self._history_store(), "acct-1"
            ).get_history_context("plan", "missing", "2026-09-11", "3d"),
            "missing_content": ContentAssetTool.from_mapping(
                {"m1": {"title": "已存在"}}
            ).get_content_asset("missing"),
            "missing_sales_window": SalesFeedbackTool(
                _EmptySalesAdapter()
            ).get_sales_feedback("2026-09-17", "day"),
        }

        for name, result in cases.items():
            with self.subTest(case=name):
                self._assert_contract(result)
                self.assertNotEqual(result["status"], "ok")

        self.assertEqual(cases["missing_plan"]["status"], "not_found")
        self.assertEqual(cases["missing_material"]["status"], "not_found")
        self.assertEqual(
            cases["missing_content"]["asset_status"], "not_found"
        )
        self.assertEqual(
            cases["missing_sales_window"]["data_completeness"]["sales_status"],
            "no_records_in_window",
        )

        no_data_cases = {
            "account": AccountMetricsTool(
                self.units, self.account_feedback
            ).get_account_metrics("2026-09-03", "day"),
            "plan": PlanMetricsTool(
                self.units, self.scoped_feedback
            ).get_plan_metrics("u1", "2026-09-04", "day"),
            "material": MaterialMetricsTool(
                self.materials, self.scoped_feedback
            ).get_material_metrics("m1", "2026-09-04", "day"),
            "lead_quality": LeadQualityTool(
                feedback=self.scoped_feedback, units=self.units
            ).get_lead_quality("2026-09-03", "day"),
            "history": HistoryContextTool(None, "acct-1").get_history_context(
                "account", None, "2026-09-11", "3d"
            ),
            "content": ContentAssetTool.from_mapping(
                {"m1": {"material_name": "仅有名称"}}
            ).get_content_asset("m1"),
            "sales": SalesFeedbackTool(_EmptySalesAdapter()).get_sales_feedback(
                "2026-09-17", "day"
            ),
        }
        for name, result in no_data_cases.items():
            with self.subTest(no_data=name):
                self._assert_contract(result)
                self.assertNotEqual(result["status"], "ok")
        self.assertIsNone(no_data_cases["account"]["cost"])
        self.assertIsNone(no_data_cases["plan"]["cost"])
        self.assertIsNone(no_data_cases["material"]["cost"])
        self.assertIsNone(no_data_cases["lead_quality"]["valid_leads"])

    def test_partial_data_is_reported_without_zero_fill(self):
        cases = {
            "account": AccountMetricsTool(
                self.units, self.account_feedback
            ).get_account_metrics("2026-09-03", "3d"),
            "plan": PlanMetricsTool(
                self.units_with_other_plan, self.scoped_feedback
            ).get_plan_metrics("u1", "2026-09-03", "3d"),
            "material": MaterialMetricsTool(
                self.materials_with_other_material, self.scoped_feedback
            ).get_material_metrics("m1", "2026-09-03", "3d"),
            "lead_quality": LeadQualityTool(
                feedback=self.scoped_feedback, units=self.units
            ).get_lead_quality("2026-09-03", "3d"),
            "history": HistoryContextTool(
                self._history_store(missing_date="2026-09-09"), "acct-1"
            ).get_history_context(
                "account", None, "2026-09-11", "3d", "previous_7d"
            ),
        }

        for name, result in cases.items():
            with self.subTest(partial=name):
                self._assert_contract(result, expected_status="partial")
                self.assertTrue(result["warnings"])

        self.assertEqual(
            cases["account"]["data_completeness"]["status"],
            "missing_platform_days",
        )
        self.assertEqual(
            cases["plan"]["data_completeness"]["status"],
            "partial_plan_days",
        )
        self.assertEqual(
            cases["material"]["data_completeness"]["status"],
            "partial_material_days",
        )
        self.assertEqual(
            cases["history"]["current_window"]["missing_dates"],
            ["2026-09-09"],
        )
        self.assertIsNone(cases["account"]["cost"])
        self.assertIsNone(cases["lead_quality"]["platform_spend"])

    def test_invalid_time_window_is_structured_for_every_window_tool(self):
        cases = {
            "account": lambda: AccountMetricsTool(
                self.units, self.account_feedback
            ).get_account_metrics("2026-09-02", "not-a-window"),
            "plan": lambda: PlanMetricsTool(
                self.units, self.scoped_feedback
            ).get_plan_metrics("u1", "2026-09-02", "not-a-window"),
            "material": lambda: MaterialMetricsTool(
                self.materials, self.scoped_feedback
            ).get_material_metrics("m1", "2026-09-02", "not-a-window"),
            "lead_quality": lambda: LeadQualityTool(
                feedback=self.scoped_feedback, units=self.units
            ).get_lead_quality("2026-09-02", "not-a-window"),
            "history": lambda: HistoryContextTool(
                self._history_store(), "acct-1"
            ).get_history_context(
                "account", None, "2026-09-11", "not-a-window"
            ),
            "sales": lambda: SalesFeedbackTool().get_sales_feedback(
                "2026-09-17", "not-a-window"
            ),
        }

        for name, invoke in cases.items():
            with self.subTest(tool=name):
                result = invoke()
                self._assert_structured_error(result)
                self.assertEqual(result["error_type"], "ValueError")

    def test_underlying_failures_do_not_escape_the_tool_boundary(self):
        cases = [
            (
                "account",
                "services.account_metrics_tool.aggregate_account",
                lambda: AccountMetricsTool(
                    self.units, self.account_feedback
                ).get_account_metrics("2026-09-02", "day"),
            ),
            (
                "plan",
                "services.plan_metrics_tool.summarize_entities",
                lambda: PlanMetricsTool(
                    self.units, self.scoped_feedback
                ).get_plan_metrics("u1", "2026-09-02", "day"),
            ),
            (
                "material",
                "services.material_metrics_tool.summarize_entities",
                lambda: MaterialMetricsTool(
                    self.materials, self.scoped_feedback
                ).get_material_metrics("m1", "2026-09-02", "day"),
            ),
            (
                "lead_quality",
                "services.lead_quality_tool.aggregate_account",
                lambda: LeadQualityTool(
                    feedback=self.scoped_feedback, units=self.units
                ).get_lead_quality("2026-09-02", "day"),
            ),
        ]

        for name, target, invoke in cases:
            with self.subTest(tool=name):
                with patch(target, side_effect=RuntimeError(f"{name} boom")):
                    result = invoke()
                self._assert_structured_error(result)
                self.assertEqual(result["error_type"], "RuntimeError")

        history = self._history_store()
        with patch.object(
            history, "load_history", side_effect=RuntimeError("history boom")
        ):
            history_result = HistoryContextTool(history, "acct-1").get_history_context(
                "account", None, "2026-09-11", "3d"
            )
        self._assert_structured_error(history_result)
        self.assertEqual(history_result["error_type"], "RuntimeError")

        content_result = ContentAssetTool(_ExplodingContentAdapter()).get_content_asset(
            "m1"
        )
        self._assert_contract(content_result)
        self.assertEqual(content_result["status"], "unavailable")
        self.assertEqual(content_result["asset_status"], "adapter_error")

        sales_result = SalesFeedbackTool(
            _ExplodingSalesAdapter()
        ).get_sales_feedback("2026-09-17", "day")
        self._assert_contract(sales_result, expected_status="error")
        self.assertEqual(
            sales_result["data_completeness"]["sales_status"], "adapter_error"
        )


if __name__ == "__main__":
    unittest.main()
