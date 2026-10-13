"""Narrow account-metrics Tool built on the existing replay functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from replay_core import aggregate_account, load_feedback, load_platform_reports, mtd_summary
from .metrics_tool_utils import resolve_window as _resolve_window
from .metrics_tool_utils import valid_lead_rate as _valid_lead_rate
from .tool_protocol import tool_protocol


@dataclass
class AccountMetricsTool:
    """Account metrics context; the Agent-facing call only needs date/window."""

    units: pd.DataFrame
    feedback: pd.DataFrame | None = None

    @classmethod
    def from_report_files(
        cls,
        unit_report_path: Path | str,
        material_report_path: Path | str,
        feedback_path: Path | str | None = None,
    ) -> "AccountMetricsTool":
        units, _materials, _meta = load_platform_reports(
            Path(unit_report_path), Path(material_report_path)
        )
        feedback = load_feedback(Path(feedback_path) if feedback_path else None)
        return cls(units=units, feedback=feedback)

    @tool_protocol(
        tool_name="get_account_metrics",
        default_source="replay_core.unit_report",
    )
    def get_account_metrics(self, report_date: str, time_window: str) -> dict[str, Any]:
        start, end, normalized_window = _resolve_window(report_date, time_window)
        feedback = self.feedback if self.feedback is not None else load_feedback(None)
        account_daily = aggregate_account(self.units, feedback)
        summary = mtd_summary(account_daily, feedback, start, end)
        warnings = []
        if summary.get("missing_dates"):
            warnings.append("选定时间窗口缺少平台报表日期，窗口汇总未将缺失日期补为 0")
        if feedback.empty:
            warnings.append("没有可用的反馈数据，质量字段保持缺失")

        result = {
            "report_date": end.date().isoformat(),
            "time_window": {
                "name": normalized_window,
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
            },
            # Keep both the Tool contract names and existing replay names.
            "cost": summary.get("spend"),
            "spend": summary.get("spend"),
            "impressions": summary.get("impressions"),
            "clicks": summary.get("clicks"),
            "ctr": summary.get("ctr"),
            "conversions": summary.get("leads"),
            "leads": summary.get("leads"),
            "cvr": summary.get("lead_conversion_rate"),
            "lead_conversion_rate": summary.get("lead_conversion_rate"),
            "cpl": summary.get("lead_cost"),
            "lead_cost": summary.get("lead_cost"),
            "valid_leads": summary.get("valid_leads"),
            "valid_lead_rate": _valid_lead_rate(summary.get("valid_leads"), summary.get("leads")),
            "valid_cpl": summary.get("valid_lead_cost"),
            "valid_lead_cost": summary.get("valid_lead_cost"),
            "contacted_leads": summary.get("contacted_leads"),
            "contact_cost": summary.get("contact_cost"),
            "data_completeness": {
                "status": summary.get("data_status"),
                "missing_dates": summary.get("missing_dates", []),
                "feedback_available": not feedback.empty,
                "platform_source": "unit_report",
                "quality_source": "feedback_export" if not feedback.empty else None,
            },
            "warnings": warnings,
        }
        return result


@tool_protocol(
    tool_name="get_account_metrics",
    default_source="replay_core.unit_report",
)
def get_account_metrics(
    report_date: str,
    time_window: str,
    unit_report_path: Path | str,
    material_report_path: Path | str,
    feedback_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load the existing report sources and return JSON-shaped account metrics."""
    tool = AccountMetricsTool.from_report_files(
        unit_report_path=unit_report_path,
        material_report_path=material_report_path,
        feedback_path=feedback_path,
    )
    return tool.get_account_metrics(report_date, time_window)
