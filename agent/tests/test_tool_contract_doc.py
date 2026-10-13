from pathlib import Path
import unittest


class ToolContractDocTests(unittest.TestCase):
    def test_tool_inventory_lists_all_required_tools_and_known_gaps(self):
        doc_path = Path(__file__).resolve().parents[1] / "docs" / "agent_tool_contracts.md"
        text = doc_path.read_text(encoding="utf-8")

        tools = (
            "get_account_metrics",
            "get_plan_metrics",
            "get_material_metrics",
            "get_lead_quality",
            "get_history_context",
            "get_content_asset",
            "get_sales_feedback",
        )
        for tool in tools:
            with self.subTest(tool=tool):
                self.assertIn(f"`{tool}`", text)

        self.assertIn("get_content_asset` 在实现前需要项目负责人指定", text)
        self.assertIn("get_sales_feedback` 在实现前需要项目负责人指定", text)
        self.assertIn("StateStore.list_feedback()", text)


if __name__ == "__main__":
    unittest.main()
