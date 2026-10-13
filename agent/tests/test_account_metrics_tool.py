import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from services.account_metrics_tool import AccountMetricsTool


class AccountMetricsToolTests(unittest.TestCase):
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
                    "private_messages": 2,
                },
                {
                    "data_date": pd.Timestamp("2026-09-02"),
                    "unit_id": "u1",
                    "spend": 200,
                    "impressions": 2000,
                    "clicks": 100,
                    "leads": 10,
                    "private_messages": 3,
                },
            ]
        )
        self.feedback = pd.DataFrame(
            [
                {
                    "lead_date": pd.Timestamp("2026-09-01"),
                    "is_valid": True,
                    "is_contacted": True,
                },
                {
                    "lead_date": pd.Timestamp("2026-09-02"),
                    "is_valid": True,
                    "is_contacted": False,
                },
            ]
        )

    def test_uses_existing_account_aggregation_and_returns_required_json_metrics(self):
        result = AccountMetricsTool(self.units, self.feedback).get_account_metrics(
            "2026-09-02", "mtd"
        )

        self.assertEqual(result["cost"], 300.0)
        self.assertEqual(result["impressions"], 3000)
        self.assertEqual(result["clicks"], 200)
        self.assertEqual(result["conversions"], 20)
        self.assertEqual(result["valid_leads"], 2)
        self.assertEqual(result["valid_lead_rate"], 0.1)
        self.assertEqual(result["valid_cpl"], 150.0)
        self.assertEqual(result["time_window"]["start_date"], "2026-09-01")
        self.assertEqual(result["data_completeness"]["status"], "available")
        json.dumps(result, ensure_ascii=False)

    def test_missing_platform_day_is_explicit_and_metrics_are_not_fabricated(self):
        result = AccountMetricsTool(self.units, self.feedback).get_account_metrics(
            "2026-09-03", "3d"
        )

        self.assertEqual(result["data_completeness"]["status"], "missing_platform_days")
        self.assertIn("2026-09-03", result["data_completeness"]["missing_dates"])
        self.assertIsNone(result["cost"])
        self.assertIsNone(result["valid_leads"])

    def test_from_report_files_reuses_existing_report_loader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unit_path = root / "unit.xlsx"
            material_path = root / "material.xlsx"
            feedback_path = root / "feedback.csv"
            pd.DataFrame(
                [
                    {
                        "单元ID": "u1",
                        "单元名称": "测试单元",
                        "日期": "2026-09-02",
                        "投放类型": "通投",
                        "消耗(元)": 100,
                        "展示次数": 1000,
                        "点击次数": 100,
                        "线索留资数(计费时间)": 10,
                        "私信意向数(计费时间)": 0,
                    }
                ]
            ).to_excel(unit_path, index=False)
            pd.DataFrame(
                [
                    {
                        "素材ID": "m1",
                        "素材名称": "测试素材",
                        "日期": "2026-09-02",
                        "消耗(元)": 100,
                        "展示次数": 1000,
                        "点击次数": 100,
                        "线索留资数(计费时间)": 10,
                        "私信意向数(计费时间)": 0,
                    }
                ]
            ).to_excel(material_path, index=False)
            pd.DataFrame(
                [{"日期": "2026-09-02", "是否有效": "是", "是否对接": "否"}]
            ).to_csv(feedback_path, index=False, encoding="utf-8-sig")

            result = AccountMetricsTool.from_report_files(
                unit_path, material_path, feedback_path
            ).get_account_metrics("2026-09-02", "day")

        self.assertEqual(result["cost"], 100.0)
        self.assertEqual(result["data_completeness"]["status"], "available")


if __name__ == "__main__":
    unittest.main()
