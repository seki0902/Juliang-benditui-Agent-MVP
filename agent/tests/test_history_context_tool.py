import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from services.history_context_tool import HistoryContextTool, get_history_context
from state_store import StateStore


class HistoryContextToolTests(unittest.TestCase):
    def _metric_row(self, data_date, object_id, spend, id_key):
        return {
            "data_date": data_date,
            id_key: object_id,
            "spend": spend,
            "impressions": spend * 10,
            "clicks": spend,
            "leads": spend / 10,
            "private_messages": 0,
        }

    def _populate_store(self, path, missing_date=None):
        store = StateStore(path)
        for day_number in range(1, 11):
            data_date = f"2026-09-{day_number:02d}"
            if data_date == missing_date:
                continue
            run_id, _done, _status = store.begin_run(
                f"account:{data_date}", data_date, "acct-1", "test"
            )
            account = self._metric_row(data_date, "account", 100, "account_id")
            plan = self._metric_row(data_date, "u1", 10, "unit_id")
            material = self._metric_row(data_date, "m1", 5, "material_id")
            store.save_report(
                run_id,
                {
                    "data_date": data_date,
                    "yesterday": account,
                    "daily_plan_metrics": [plan],
                    "daily_material_metrics": [material],
                    "plan_summary": [{"unit_id": "u1", "unit_name": "计划一"}],
                    "material_summary": [{"material_id": "m1", "material_name": "素材一"}],
                },
            )
            store.finish_run(
                run_id,
                "success",
                metadata={"reconciliation_pass": True},
            )
        return store

    def test_account_history_returns_windows_series_and_numeric_trend(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.sqlite3"
            store = self._populate_store(path)
            result = HistoryContextTool(store, "acct-1").get_history_context(
                "account",
                None,
                "2026-09-11",
                "3d",
                metrics=["spend", "ctr"],
                comparison_window="previous_7d",
            )
            store.close()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["time_window"]["start_date"], "2026-09-08")
        self.assertEqual(result["time_window"]["end_date"], "2026-09-10")
        self.assertEqual(result["comparison_window"]["start_date"], "2026-09-01")
        self.assertEqual(result["comparison_window"]["end_date"], "2026-09-07")
        self.assertEqual(result["current_window"]["spend"], 300.0)
        self.assertEqual(result["history_window"]["spend"], 700.0)
        self.assertEqual(result["trend"]["spend"]["absolute_change"], -400.0)
        self.assertEqual(result["trend"]["spend"]["relative_change"], -0.571429)
        self.assertEqual(result["requested_metrics"], ["spend", "ctr"])
        self.assertEqual(len(result["daily_series"]), 10)
        self.assertEqual(result["data_completeness"]["status"], "available")
        json.dumps(result, ensure_ascii=False)

    def test_plan_and_material_history_are_filtered_to_requested_object(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.sqlite3"
            store = self._populate_store(path)
            plan_result = HistoryContextTool(store, "acct-1").get_history_context(
                "plan", "u1", "2026-09-11", "7d", "3d"
            )
            material_result = HistoryContextTool(store, "acct-1").get_history_context(
                "material", "m1", "2026-09-11", "7d", "3d"
            )
            store.close()

        self.assertEqual(plan_result["status"], "ok")
        self.assertEqual(plan_result["object_scope"]["object_type"], "plan")
        self.assertEqual(plan_result["object_scope"]["object_id"], "u1")
        self.assertEqual(plan_result["current_window"]["spend"], 70.0)
        self.assertEqual(plan_result["history_window"]["spend"], 30.0)
        self.assertTrue(all(row["object_id"] == "u1" for row in plan_result["daily_series"]))

        self.assertEqual(material_result["status"], "ok")
        self.assertEqual(material_result["object_scope"]["object_type"], "material")
        self.assertEqual(material_result["current_window"]["spend"], 35.0)
        self.assertEqual(material_result["history_window"]["spend"], 15.0)
        self.assertTrue(
            all(row["object_id"] == "m1" for row in material_result["daily_series"])
        )

    def test_missing_history_days_are_reported_without_zero_fill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.sqlite3"
            store = self._populate_store(path, missing_date="2026-09-09")
            result = HistoryContextTool(store, "acct-1").get_history_context(
                "account", None, "2026-09-11", "3d", "previous_7d"
            )
            store.close()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["current_window"]["missing_dates"], ["2026-09-09"])
        self.assertEqual(result["current_window"]["spend"], 200.0)
        self.assertIn("缺失日期未补为 0", result["warnings"][0])
        json.dumps(result, ensure_ascii=False)

    def test_no_history_is_unavailable_and_not_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.sqlite3"
            store = StateStore(path)
            result = HistoryContextTool(store, "acct-1").get_history_context(
                "account", None, "2026-09-11", "7d"
            )
            store.close()

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["data_completeness"]["history_status"], "missing_state_store")
        self.assertIsNone(result["current_window"]["spend"])
        self.assertTrue(result["warnings"])
        json.dumps(result, ensure_ascii=False)

    def test_module_function_reads_existing_store_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "history.sqlite3"
            store = self._populate_store(path)
            store.close()
            result = get_history_context(
                object_type="account",
                object_id=None,
                before_date="2026-09-11",
                time_window="3d",
                account_id="acct-1",
                state_store_path=path,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["current_window"]["spend"], 300.0)
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
