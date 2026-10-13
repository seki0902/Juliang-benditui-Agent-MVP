from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from baseline_engine import build_baselines
from browser_downloader import resolve_date_range
from recommendation_engine import build_recommendations
from state_store import StateStore


class DailyComponentsTests(unittest.TestCase):
    def test_baselines_have_3_7_30_day_windows(self) -> None:
        dates = pd.date_range("2026-08-20", "2026-09-14", freq="D")
        frame = pd.DataFrame({
            "data_date": dates,
            "unit_id": ["u1"] * len(dates),
            "spend": [100.0] * len(dates),
            "impressions": [1000] * len(dates),
            "clicks": [50] * len(dates),
            "leads": [5] * len(dates),
        })
        result = build_baselines(frame, "unit_id", pd.Timestamp("2026-09-14"))
        self.assertEqual(set(result[0]["windows"]), {"3d", "7d", "30d", "previous_7d"})
        self.assertEqual(result[0]["windows"]["3d"]["spend"], 300.0)
        self.assertEqual(result[0]["stage"], "observing")

    def test_mtd_date_range_ends_yesterday(self) -> None:
        now = datetime(2026, 9, 15, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        start, end = resolve_date_range("mtd_to_yesterday", now, "Asia/Shanghai")
        self.assertEqual(start.isoformat(), "2026-09-01")
        self.assertEqual(end.isoformat(), "2026-09-14")

    def test_recommendation_requires_human_approval(self) -> None:
        report = {
            "anomalies": [{
                "object_type": "unit",
                "object_id": "u1",
                "object_name": "测试计划",
                "action": "control_risk",
                "evidence": ["消耗 300 元但留资为 0"],
                "candidate_causes": ["delivery_or_funnel_issue"],
                "missing_evidence": ["actual_status"],
            }]
        }
        result = build_recommendations(report, "account:2026-09-14")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["action"], "CONTROL_RISK")
        self.assertTrue(result[0]["requires_human_approval"])
        self.assertEqual(result[0]["status"], "generated")

    def test_state_store_is_idempotent_for_same_run_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.sqlite3"
            with StateStore(db_path) as store:
                first_id, first_done, first_status = store.begin_run("account:2026-09-14", "2026-09-14", "account", "v3")
                store.finish_run(first_id, "success")
                second_id, second_done, second_status = store.begin_run("account:2026-09-14", "2026-09-14", "account", "v3")
            self.assertEqual(first_id, second_id)
            self.assertFalse(first_done)
            self.assertEqual(first_status, "running")
            self.assertTrue(second_done)
            self.assertEqual(second_status, "success")
