from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from replay_core import aggregate_metrics, metric_row, mtd_summary  # noqa: E402


class ReplayCoreTests(unittest.TestCase):
    def test_metric_formulas(self) -> None:
        row = metric_row(100, 1000, 50, 5, 2, 10, 1)
        self.assertEqual(row["lead_cost"], 20.0)
        self.assertEqual(row["valid_lead_cost"], 50.0)
        self.assertEqual(row["ctr"], 0.05)
        self.assertEqual(row["cpc"], 2.0)
        self.assertEqual(row["cpm"], 100.0)
        self.assertEqual(row["lead_conversion_rate"], 0.1)
        self.assertEqual(row["contact_cost"], 100.0)

    def test_zero_denominator_is_null(self) -> None:
        row = metric_row(100, 0, 0, 0, None, 0, None)
        self.assertIsNone(row["lead_cost"])
        self.assertIsNone(row["cpc"])
        self.assertIsNone(row["cpm"])
        self.assertIsNone(row["valid_lead_cost"])
        self.assertIsNone(row["contact_cost"])

    def test_manual_feedback_is_not_copied_to_each_unit(self) -> None:
        units = pd.DataFrame(
            [
                {"data_date": pd.Timestamp("2026-09-01"), "unit_id": "u1", "spend": 100, "impressions": 1000, "clicks": 10, "leads": 1, "private_messages": 0},
                {"data_date": pd.Timestamp("2026-09-01"), "unit_id": "u2", "spend": 100, "impressions": 1000, "clicks": 10, "leads": 1, "private_messages": 0},
            ]
        )
        feedback = pd.DataFrame(
            [{"lead_id": "l1", "lead_date": pd.Timestamp("2026-09-01"), "unit_id": "u1", "is_valid": True, "is_contacted": True}]
        )
        result = aggregate_metrics(units, "unit_id", feedback).set_index("unit_id")
        self.assertEqual(result.loc["u1", "valid_leads"], 1)
        self.assertEqual(result.loc["u1", "contacted_leads"], 1)
        self.assertTrue(pd.isna(result.loc["u2", "valid_leads"]))
        self.assertTrue(pd.isna(result.loc["u2", "contacted_leads"]))

    def test_mtd_uses_cumulative_ratio(self) -> None:
        daily = pd.DataFrame(
            [
                {"data_date": "2026-09-01", "spend": 100, "leads": 1, "private_messages": 0},
                {"data_date": "2026-09-02", "spend": 900, "leads": 9, "private_messages": 0},
            ]
        )
        feedback = pd.DataFrame(
            [
                {"lead_date": pd.Timestamp("2026-09-01"), "is_valid": True, "is_contacted": True},
                {"lead_date": pd.Timestamp("2026-09-02"), "is_valid": True, "is_contacted": False},
            ]
        )
        result = mtd_summary(daily, feedback, pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"))
        self.assertEqual(result["leads"], 10)
        self.assertEqual(result["lead_cost"], 100.0)
        self.assertEqual(result["contacted_leads"], 1)
        self.assertEqual(result["contact_cost"], 1000.0)


if __name__ == "__main__":
    unittest.main()
