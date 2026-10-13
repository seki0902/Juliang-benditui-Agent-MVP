import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from services.lead_quality_tool import LeadQualityTool


class LeadQualityToolTests(unittest.TestCase):
    def setUp(self):
        self.units = pd.DataFrame(
            [
                {
                    "data_date": pd.Timestamp("2026-09-01"),
                    "unit_id": "u1",
                    "spend": 100,
                    "impressions": 1000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 0,
                },
                {
                    "data_date": pd.Timestamp("2026-09-02"),
                    "unit_id": "u1",
                    "spend": 200,
                    "impressions": 2000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 0,
                },
            ]
        )
        self.feedback = pd.DataFrame(
            [
                {
                    "lead_id": "l1",
                    "lead_date": pd.Timestamp("2026-09-01"),
                    "unit_id": "u1",
                    "is_valid": True,
                    "is_contacted": True,
                    "invalid_reason": None,
                },
                {
                    "lead_id": "l2",
                    "lead_date": pd.Timestamp("2026-09-01"),
                    "unit_id": "u1",
                    "is_valid": False,
                    "is_contacted": False,
                    "invalid_reason": "号码无效",
                },
                {
                    "lead_id": "l3",
                    "lead_date": pd.Timestamp("2026-09-02"),
                    "unit_id": "u1",
                    "is_valid": pd.NA,
                    "is_contacted": pd.NA,
                    "invalid_reason": None,
                },
            ]
        )

    def test_account_quality_returns_rates_costs_and_invalid_reasons(self):
        result = LeadQualityTool(
            feedback=self.feedback, units=self.units
        ).get_lead_quality("2026-09-02", "mtd", "account")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["object_scope"]["object_type"], "account")
        self.assertEqual(result["feedback_leads"], 3)
        self.assertEqual(result["evaluated_leads"], 2)
        self.assertEqual(result["valid_leads"], 1)
        self.assertEqual(result["invalid_leads"], 1)
        self.assertEqual(result["valid_lead_rate"], 0.05)
        self.assertEqual(result["feedback_valid_lead_rate"], 0.5)
        self.assertEqual(result["valid_lead_rate_denominator_source"], "platform_leads")
        self.assertEqual(result["valid_lead_cost"], 300.0)
        self.assertEqual(result["contacted_leads"], 1)
        self.assertEqual(result["contact_cost"], 300.0)
        self.assertEqual(result["invalid_reasons"], {"号码无效": 1})
        self.assertEqual(result["data_completeness"]["status"], "available")
        json.dumps(result, ensure_ascii=False)

    def test_real_zero_is_distinguished_from_missing_feedback(self):
        zero_feedback = self.feedback.iloc[[1]].copy()
        zero_feedback["is_valid"] = False
        zero_feedback["is_contacted"] = False
        zero_feedback["invalid_reason"] = "不符合人群"
        zero_result = LeadQualityTool(
            feedback=zero_feedback, units=self.units
        ).get_lead_quality("2026-09-01", "day")

        self.assertEqual(zero_result["valid_leads"], 0)
        self.assertEqual(zero_result["valid_lead_rate"], 0.0)
        self.assertIsNone(zero_result["valid_lead_cost"])
        self.assertEqual(zero_result["invalid_reasons"], {"不符合人群": 1})

        missing_result = LeadQualityTool(
            feedback=None, units=self.units
        ).get_lead_quality("2026-09-01", "day")
        self.assertEqual(missing_result["status"], "unavailable")
        self.assertIsNone(missing_result["valid_leads"])
        self.assertIsNone(missing_result["valid_lead_rate"])
        self.assertEqual(
            missing_result["data_completeness"]["feedback_status"], "missing_source"
        )
        json.dumps(missing_result, ensure_ascii=False)

    def test_object_scope_without_attribution_id_is_unavailable(self):
        account_feedback = self.feedback.drop(columns=["unit_id"])
        result = LeadQualityTool(
            feedback=account_feedback, units=self.units
        ).get_lead_quality(
            "2026-09-02", "mtd", object_type="plan", object_id="u1"
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["data_completeness"]["feedback_status"], "missing_scope_id")
        self.assertIsNone(result["valid_leads"])
        self.assertTrue(any("unit_id" in warning for warning in result["warnings"]))
        json.dumps(result, ensure_ascii=False)

    def test_feedback_file_preserves_invalid_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "feedback.csv"
            pd.DataFrame(
                [
                    {
                        "日期": "2026-09-01",
                        "是否有效": "否",
                        "是否对接": "否",
                        "无效原因": "重复线索",
                    }
                ]
            ).to_csv(path, index=False, encoding="utf-8-sig")
            result = LeadQualityTool.from_feedback_file(path).get_lead_quality(
                "2026-09-01", "day"
            )

        self.assertEqual(result["invalid_reasons"], {"重复线索": 1})
        self.assertEqual(result["invalid_reason_status"], "available")
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
