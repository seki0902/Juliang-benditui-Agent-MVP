import json
import unittest

from services.sales_feedback_tool import SalesFeedbackTool, get_sales_feedback


class _FixtureSalesAdapter:
    source = "crm_fixture"

    def __init__(self):
        self.calls = []

    def get_sales_feedback(self, object_type, object_id, start_date, end_date):
        self.calls.append((object_type, object_id, start_date, end_date))
        return {
            "won_deals": 2,
            "revenue": 6800.0,
            "follow_up": {"pending": 1, "completed": 3},
        }


class SalesFeedbackToolTests(unittest.TestCase):
    def test_without_real_source_returns_unavailable(self):
        result = get_sales_feedback("2026-09-17", "7d")

        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["source"])
        self.assertEqual(result["time_window"]["name"], "7d")
        self.assertEqual(result["data_completeness"]["sales_status"], "missing_source")
        self.assertIsNone(result["data"]["won_deals"])
        self.assertTrue(any("CRM" in warning for warning in result["warnings"]))
        json.dumps(result, ensure_ascii=False)

    def test_object_scope_without_id_is_explicitly_unavailable(self):
        result = SalesFeedbackTool().get_sales_feedback(
            "2026-09-17", "mtd", object_type="plan"
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["object_scope"]["object_type"], "plan")
        self.assertEqual(result["data_completeness"]["sales_status"], "missing_object_id")
        self.assertTrue(any("object_id" in warning for warning in result["warnings"]))
        json.dumps(result, ensure_ascii=False)

    def test_configured_adapter_is_called_with_scope_and_window(self):
        adapter = _FixtureSalesAdapter()
        result = SalesFeedbackTool(adapter).get_sales_feedback(
            "2026-09-17", "3d", object_type="unit", object_id="u1"
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "crm_fixture")
        self.assertEqual(result["object_scope"]["object_type"], "plan")
        self.assertEqual(result["data"]["won_deals"], 2)
        self.assertEqual(result["data_completeness"]["sales_status"], "available")
        self.assertEqual(adapter.calls, [("plan", "u1", "2026-09-15", "2026-09-17")])
        json.dumps(result, ensure_ascii=False)

    def test_adapter_metadata_without_sales_facts_remains_unavailable(self):
        class MetadataOnlyAdapter:
            source = "crm_fixture"

            def get_sales_feedback(self, *_args):
                return {"source": "crm_fixture", "status": "ok"}

        result = SalesFeedbackTool(MetadataOnlyAdapter()).get_sales_feedback(
            "2026-09-17", "day"
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["data_completeness"]["sales_status"], "empty_record")
        self.assertEqual(result["data"]["revenue"], None)
        self.assertTrue(result["non_sales_feedback_boundaries"])
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
