import json
import tempfile
import unittest
from pathlib import Path

from services.historical_e2e_runner import HistoricalE2ERunner


INPUTS_ROOT = Path(__file__).resolve().parents[1] / "inputs"
REAL_INPUT_ROOT = next(
    (
        path
        for path in (INPUTS_ROOT.iterdir() if INPUTS_ROOT.exists() else ())
        if path.is_dir() and any(path.iterdir())
    ),
    None,
)


@unittest.skipUnless(
    REAL_INPUT_ROOT is not None,
    "real historical account exports are not available in this checkout",
)
class HistoricalE2ERunnerTests(unittest.TestCase):
    def test_real_exports_run_through_replay_tools_graph_without_push(self):
        assert REAL_INPUT_ROOT is not None
        with tempfile.TemporaryDirectory() as output_dir:
            report = HistoricalE2ERunner(
                REAL_INPUT_ROOT,
                output_dir,
            ).run()

            self.assertEqual(report["account_count"], 3)
            self.assertEqual(report["dynamic_route_count"], 2)
            self.assertTrue(report["push_disabled"])
            self.assertEqual(
                [
                    item["replay"]["reconciliation_pass"]
                    for item in report["accounts"]
                ],
                [True, False, True],
            )
            self.assertTrue(
                all(item["graph"]["status"] == "success" for item in report["accounts"])
            )
            self.assertTrue(
                all(
                    item["graph"]["push_result"]["status"] == "disabled"
                    for item in report["accounts"]
                )
            )
            self.assertTrue(
                any(
                    trace["tool_name"] == "get_account_metrics"
                    for item in report["accounts"]
                    for trace in item["graph"]["tool_trace"]
                )
            )
            account_two_tools = {
                trace["tool_name"]: trace["status"]
                for trace in report["accounts"][1]["graph"]["tool_trace"]
            }
            self.assertIn(
                account_two_tools.get("get_plan_metrics"),
                {"ok", "partial"},
            )
            self.assertEqual(account_two_tools.get("get_material_metrics"), "ok")

            report_path = Path(output_dir, "historical_e2e_report.json")
            markdown_path = Path(output_dir, "historical_e2e_report.md")
            self.assertTrue(report_path.is_file())
            self.assertTrue(markdown_path.is_file())
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["account_count"], 3)
            self.assertEqual(saved["dynamic_route_count"], 2)


if __name__ == "__main__":
    unittest.main()
