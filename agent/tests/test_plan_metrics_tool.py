import json
import unittest

import pandas as pd

from services.plan_metrics_tool import PlanMetricsTool


class PlanMetricsToolTests(unittest.TestCase):
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
                },
            ]
        )
        self.feedback = pd.DataFrame(
            [
                {
                    "lead_date": pd.Timestamp("2026-09-01"),
                    "unit_id": "u1",
                    "is_valid": True,
                    "is_contacted": True,
                },
                {
                    "lead_date": pd.Timestamp("2026-09-02"),
                    "unit_id": "u1",
                    "is_valid": True,
                    "is_contacted": False,
                },
            ]
        )

    def test_returns_one_plan_window_summary_and_daily_metrics(self):
        result = PlanMetricsTool(self.units, self.feedback).get_plan_metrics(
            "u1", "2026-09-03", "3d"
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["found"])
        self.assertEqual(result["plan_name"], "计划一")
        self.assertEqual(result["plan_type"], "shallow")
        self.assertEqual(result["cost"], 300.0)
        self.assertEqual(result["metrics"]["impressions"], 3000)
        self.assertEqual(result["metrics"]["clicks"], 200)
        self.assertEqual(result["metrics"]["leads"], 20)
        self.assertEqual(result["metrics"]["valid_leads"], 2)
        self.assertEqual(result["metrics"]["valid_lead_rate"], 0.1)
        self.assertEqual(result["metrics"]["valid_lead_cost"], 150.0)
        self.assertEqual(result["time_window"]["start_date"], "2026-09-01")
        self.assertEqual(result["time_window"]["end_date"], "2026-09-03")
        self.assertEqual(result["data_completeness"]["status"], "partial_plan_days")
        self.assertEqual(result["data_completeness"]["missing_dates"], [])
        self.assertEqual(result["data_completeness"]["missing_plan_dates"], ["2026-09-03"])
        self.assertEqual(len(result["daily_metrics"]), 2)
        json.dumps(result, ensure_ascii=False)

    def test_nonexistent_plan_has_explicit_not_found_result(self):
        result = PlanMetricsTool(self.units, self.feedback).get_plan_metrics(
            "missing", "2026-09-03", "day"
        )

        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["found"])
        self.assertEqual(result["data_completeness"]["status"], "not_found")
        self.assertEqual(result["daily_metrics"], [])
        self.assertIsNone(result["metrics"]["spend"])
        self.assertTrue(result["warnings"])
        self.assertEqual(result["time_window"]["name"], "1d")
        json.dumps(result, ensure_ascii=False)

    def test_known_plan_without_rows_in_window_is_not_reported_as_zero(self):
        result = PlanMetricsTool(self.units, self.feedback).get_plan_metrics(
            "u1", "2026-09-04", "day"
        )

        self.assertEqual(result["status"], "no_data")
        self.assertTrue(result["found"])
        self.assertEqual(result["data_completeness"]["status"], "no_data_in_window")
        self.assertIsNone(result["cost"])
        self.assertEqual(result["daily_metrics"], [])
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
