"""Narrow plan-metrics Tool built on the existing replay functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from replay_core import (
    aggregate_metrics,
    load_feedback,
    load_platform_reports,
    summarize_entities,
)

from .metrics_tool_utils import (
    empty_metrics as _empty_metrics,
    metric_aliases as _metric_aliases,
    metric_payload as _metric_payload,
    normalise_id as _normalise_id,
    optional_text as _optional_text,
    resolve_window,
)
from .tool_protocol import tool_protocol


@dataclass
class PlanMetricsTool:
    """Read-only single-plan query over normalized unit-level report rows."""

    units: pd.DataFrame
    feedback: pd.DataFrame | None = None

    @classmethod
    def from_report_files(
        cls,
        unit_report_path: Path | str,
        material_report_path: Path | str,
        feedback_path: Path | str | None = None,
    ) -> "PlanMetricsTool":
        units, _materials, _meta = load_platform_reports(
            Path(unit_report_path), Path(material_report_path)
        )
        feedback = load_feedback(Path(feedback_path) if feedback_path else None)
        return cls(units=units, feedback=feedback)

    @tool_protocol(
        tool_name="get_plan_metrics",
        default_source="replay_core.unit_report",
    )
    def get_plan_metrics(
        self, plan_id: str, report_date: str, time_window: str
    ) -> dict[str, Any]:
        start, end, normalized_window = resolve_window(report_date, time_window)
        requested_dates = pd.date_range(start, end)
        query_id = _normalise_id(plan_id)

        units = self.units.copy()
        required = {"data_date", "unit_id", "spend", "impressions", "clicks", "leads", "private_messages"}
        missing = sorted(required - set(units.columns))
        if missing:
            raise ValueError(f"normalized unit report is missing columns: {', '.join(missing)}")
        units["unit_id"] = units["unit_id"].map(_normalise_id)
        units["data_date"] = pd.to_datetime(units["data_date"], errors="coerce").dt.normalize()
        units = units[units["unit_id"].ne("") & units["data_date"].notna()].copy()
        if "unit_name" not in units.columns:
            units["unit_name"] = None

        feedback = self.feedback.copy() if self.feedback is not None else load_feedback(None)
        if "lead_date" in feedback.columns:
            feedback["lead_date"] = pd.to_datetime(
                feedback["lead_date"], errors="coerce"
            ).dt.normalize()
        if "unit_id" in feedback.columns:
            feedback["unit_id"] = feedback["unit_id"].map(_normalise_id)
        feedback_window = feedback[
            (feedback["lead_date"] >= start) & (feedback["lead_date"] <= end)
        ].copy() if "lead_date" in feedback.columns else feedback.iloc[0:0].copy()

        all_plan_rows = units[units["unit_id"] == query_id]
        time_window_info = {
            "name": normalized_window,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }

        if all_plan_rows.empty:
            metrics = _empty_metrics()
            return {
                "status": "not_found",
                "found": False,
                "source": "replay_core.unit_report",
                "object_type": "plan",
                "plan_id": query_id,
                "plan_name": None,
                "plan_type": None,
                "observed_status": "not_observed",
                "report_date": end.date().isoformat(),
                "time_window": time_window_info,
                "metrics": metrics,
                "summary": dict(metrics),
                **_metric_aliases(metrics),
                "daily_metrics": [],
                "data_completeness": {
                    "status": "not_found",
                    "missing_dates": [],
                    "observed_dates": [],
                    "platform_observed_dates": [],
                    "feedback_available": not feedback_window.empty,
                    "quality_attribution_available": "unit_id" in feedback_window.columns,
                    "platform_source": "unit_report",
                    "quality_source": "feedback_export" if not feedback_window.empty else None,
                },
                "warnings": [f"未找到 plan_id={query_id!r} 的标准化计划记录"],
            }

        plan_name = next(
            (
                _optional_text(value)
                for value in all_plan_rows["unit_name"].tolist()
                if _optional_text(value) is not None
            ),
            None,
        )
        plan_type = next(
            (
                _optional_text(value)
                for value in all_plan_rows.get("plan_type", pd.Series(dtype=object)).tolist()
                if _optional_text(value) is not None
            ),
            None,
        )
        window_rows = units[
            (units["data_date"] >= start)
            & (units["data_date"] <= end)
            & (units["unit_id"] == query_id)
        ].copy()
        platform_window_rows = units[
            (units["data_date"] >= start) & (units["data_date"] <= end)
        ]
        platform_observed_dates = set(platform_window_rows["data_date"].tolist())
        observed_dates = set(window_rows["data_date"].tolist())
        missing_platform_dates = [
            day.date().isoformat() for day in requested_dates if day not in platform_observed_dates
        ]
        missing_plan_dates = [
            day.date().isoformat() for day in requested_dates if day not in observed_dates
        ]

        if window_rows.empty:
            metrics = _empty_metrics()
            completeness_status = "no_data_in_window"
            status = "no_data"
            daily_metrics: list[dict[str, Any]] = []
            warnings = [f"计划 {query_id!r} 在选定时间窗口内没有日明细记录"]
            observed_status = "not_observed"
        else:
            summary_rows = summarize_entities(
                window_rows, "unit_id", "unit_name", feedback_window
            )
            summary = summary_rows[0] if summary_rows else None
            metrics = _metric_payload(summary)
            daily_metrics = aggregate_metrics(
                window_rows, "unit_id", feedback_window
            ).to_dict("records")
            observed_status = "spending" if float(window_rows["spend"].sum()) > 0 else "no_spend"
            if missing_platform_dates:
                completeness_status = "missing_platform_days"
                status = "partial"
                warnings = [
                    "选定时间窗口缺少平台报表日期，窗口汇总未将缺失日期补为 0",
                    *([f"计划自身未观察到日期：{', '.join(missing_plan_dates)}"] if missing_plan_dates else []),
                ]
            elif missing_plan_dates:
                completeness_status = "partial_plan_days"
                status = "partial"
                warnings = [
                    "选定时间窗口内并非每天都有该计划的明细记录；缺失日不能解释为后台暂停",
                    f"计划自身未观察到日期：{', '.join(missing_plan_dates)}",
                ]
            else:
                completeness_status = "available"
                status = "ok"
                warnings = []

        return {
            "status": status,
            "found": True,
            "source": "replay_core.unit_report",
            "object_type": "plan",
            "plan_id": query_id,
            "plan_name": plan_name,
            "plan_type": plan_type,
            "observed_status": observed_status,
            "report_date": end.date().isoformat(),
            "time_window": time_window_info,
            "metrics": metrics,
            "summary": dict(metrics),
            **_metric_aliases(metrics),
            "daily_metrics": daily_metrics,
            "data_completeness": {
                "status": completeness_status,
                "missing_dates": missing_platform_dates,
                "missing_plan_dates": missing_plan_dates,
                "observed_dates": sorted(day.date().isoformat() for day in observed_dates),
                "platform_observed_dates": sorted(
                    day.date().isoformat() for day in platform_observed_dates
                ),
                "feedback_available": not feedback_window.empty,
                "quality_attribution_available": "unit_id" in feedback_window.columns,
                "platform_source": "unit_report",
                "quality_source": (
                    "feedback_export"
                    if not feedback_window.empty and "unit_id" in feedback_window.columns
                    else None
                ),
            },
            "active_days": (
                int(sum(1 for row in daily_metrics if float(row.get("spend") or 0) > 0))
                if daily_metrics
                else 0
            ),
            "warnings": warnings,
        }


@tool_protocol(
    tool_name="get_plan_metrics",
    default_source="replay_core.unit_report",
)
def get_plan_metrics(
    plan_id: str,
    report_date: str,
    time_window: str,
    unit_report_path: Path | str,
    material_report_path: Path | str,
    feedback_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load report sources and return JSON-shaped metrics for one plan."""
    tool = PlanMetricsTool.from_report_files(
        unit_report_path=unit_report_path,
        material_report_path=material_report_path,
        feedback_path=feedback_path,
    )
    return tool.get_plan_metrics(plan_id, report_date, time_window)
