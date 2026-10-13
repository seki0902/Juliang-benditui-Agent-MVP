import json
import unittest

import pandas as pd

from services.material_metrics_tool import MaterialMetricsTool


class MaterialMetricsToolTests(unittest.TestCase):
    def setUp(self):
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
                {
                    "data_date": pd.Timestamp("2026-09-03"),
                    "material_id": "m2",
                    "material_name": "素材二",
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
                    "material_id": "m1",
                    "is_valid": True,
                    "is_contacted": True,
                },
                {
                    "lead_date": pd.Timestamp("2026-09-02"),
                    "material_id": "m1",
                    "is_valid": True,
                    "is_contacted": False,
                },
            ]
        )

    def test_returns_material_metrics_runtime_and_unavailable_relation(self):
        result = MaterialMetricsTool(self.materials, self.feedback).get_material_metrics(
            "m1", "2026-09-03", "3d"
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["found"])
        self.assertEqual(result["material_name"], "素材一")
        self.assertEqual(result["cost"], 300.0)
        self.assertEqual(result["metrics"]["impressions"], 3000)
        self.assertEqual(result["metrics"]["clicks"], 200)
        self.assertEqual(result["metrics"]["leads"], 20)
        self.assertEqual(result["metrics"]["valid_leads"], 2)
        self.assertEqual(result["metrics"]["valid_lead_rate"], 0.1)
        self.assertEqual(result["metrics"]["valid_lead_cost"], 150.0)
        self.assertEqual(result["time_window"]["start_date"], "2026-09-01")
        self.assertEqual(result["runtime"]["first_observed_date"], "2026-09-01")
        self.assertEqual(result["runtime"]["last_observed_date"], "2026-09-02")
        self.assertEqual(result["data_completeness"]["status"], "partial_material_days")
        self.assertEqual(result["related_plan_ids"], None)
        self.assertEqual(result["relation"]["status"], "unavailable")
        self.assertTrue(any("计划" in warning for warning in result["warnings"]))
        self.assertEqual(len(result["daily_metrics"]), 2)
        json.dumps(result, ensure_ascii=False)

    def test_nonexistent_material_has_explicit_result(self):
        result = MaterialMetricsTool(self.materials, self.feedback).get_material_metrics(
            "missing", "2026-09-03", "day"
        )

        self.assertEqual(result["status"], "not_found")
        self.assertFalse(result["found"])
        self.assertEqual(result["data_completeness"]["status"], "not_found")
        self.assertIsNone(result["metrics"]["spend"])
        self.assertEqual(result["daily_metrics"], [])
        self.assertTrue(result["warnings"])
        json.dumps(result, ensure_ascii=False)

    def test_known_material_without_rows_in_window_is_not_reported_as_zero(self):
        result = MaterialMetricsTool(self.materials, self.feedback).get_material_metrics(
            "m1", "2026-09-04", "day"
        )

        self.assertEqual(result["status"], "no_data")
        self.assertTrue(result["found"])
        self.assertEqual(result["data_completeness"]["status"], "no_data_in_window")
        self.assertIsNone(result["cost"])
        self.assertEqual(result["daily_metrics"], [])
        json.dumps(result, ensure_ascii=False)

    def test_explicit_unit_id_column_can_supply_relation_without_inference(self):
        materials = self.materials.copy()
        materials["unit_id"] = ["u1", "u1", "u2"]

        result = MaterialMetricsTool(materials, self.feedback).get_material_metrics(
            "m1", "2026-09-02", "mtd"
        )

        self.assertEqual(result["relation"]["status"], "available")
        self.assertEqual(result["related_plan_ids"], ["u1"])
        self.assertEqual(result["related_plans"], [{"plan_id": "u1"}])
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
