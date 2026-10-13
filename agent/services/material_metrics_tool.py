"""Narrow material-metrics Tool built on the existing replay functions."""

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
    empty_metrics,
    metric_aliases,
    metric_payload,
    normalise_id,
    optional_text,
    resolve_window,
)
from .tool_protocol import tool_protocol


def _relation_payload(materials: pd.DataFrame) -> dict[str, Any]:
    """Describe plan association without inferring it from material metrics."""
    if "unit_id" not in materials.columns:
        return {
            "status": "unavailable",
            "source": None,
            "plan_ids": None,
            "reason": "素材报表没有计划ID，当前系统也没有可读取的有效期关联映射",
        }
    plan_ids = sorted(
        {
            normalise_id(value)
            for value in materials["unit_id"].tolist()
            if normalise_id(value)
        }
    )
    if not plan_ids:
        return {
            "status": "unavailable",
            "source": "material_report.unit_id",
            "plan_ids": None,
            "reason": "素材记录没有可用的计划ID归因",
        }
    return {
        "status": "available",
        "source": "material_report.unit_id",
        "plan_ids": plan_ids,
        "reason": None,
    }


@dataclass
class MaterialMetricsTool:
    """Read-only single-material query over normalized material report rows."""

    materials: pd.DataFrame
    feedback: pd.DataFrame | None = None

    @classmethod
    def from_report_files(
        cls,
        unit_report_path: Path | str,
        material_report_path: Path | str,
        feedback_path: Path | str | None = None,
    ) -> "MaterialMetricsTool":
        _units, materials, _meta = load_platform_reports(
            Path(unit_report_path), Path(material_report_path)
        )
        feedback = load_feedback(Path(feedback_path) if feedback_path else None)
        return cls(materials=materials, feedback=feedback)

    @tool_protocol(
        tool_name="get_material_metrics",
        default_source="replay_core.material_report",
    )
    def get_material_metrics(
        self, material_id: str, report_date: str, time_window: str
    ) -> dict[str, Any]:
        start, end, normalized_window = resolve_window(report_date, time_window)
        requested_dates = pd.date_range(start, end)
        query_id = normalise_id(material_id)

        materials = self.materials.copy()
        required = {
            "data_date",
            "material_id",
            "spend",
            "impressions",
            "clicks",
            "leads",
            "private_messages",
        }
        missing = sorted(required - set(materials.columns))
        if missing:
            raise ValueError(
                f"normalized material report is missing columns: {', '.join(missing)}"
            )
        materials["material_id"] = materials["material_id"].map(normalise_id)
        materials["data_date"] = pd.to_datetime(
            materials["data_date"], errors="coerce"
        ).dt.normalize()
        materials = materials[
            materials["material_id"].ne("") & materials["data_date"].notna()
        ].copy()
        if "material_name" not in materials.columns:
            materials["material_name"] = None

        feedback = self.feedback.copy() if self.feedback is not None else load_feedback(None)
        if "lead_date" in feedback.columns:
            feedback["lead_date"] = pd.to_datetime(
                feedback["lead_date"], errors="coerce"
            ).dt.normalize()
        if "material_id" in feedback.columns:
            feedback["material_id"] = feedback["material_id"].map(normalise_id)
        feedback_window = (
            feedback[
                (feedback["lead_date"] >= start) & (feedback["lead_date"] <= end)
            ].copy()
            if "lead_date" in feedback.columns
            else feedback.iloc[0:0].copy()
        )

        time_window_info = {
            "name": normalized_window,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
        all_material_rows = materials[materials["material_id"] == query_id]
        relation = _relation_payload(all_material_rows)

        if all_material_rows.empty:
            metrics = empty_metrics()
            return {
                "status": "not_found",
                "found": False,
                "source": "replay_core.material_report",
                "object_type": "material",
                "material_id": query_id,
                "material_name": None,
                "observed_status": "not_observed",
                "report_date": end.date().isoformat(),
                "time_window": time_window_info,
                "metrics": metrics,
                "summary": dict(metrics),
                **metric_aliases(metrics),
                "daily_metrics": [],
                "runtime": {
                    "first_observed_date": None,
                    "last_observed_date": None,
                    "observed_days": 0,
                    "active_days": 0,
                },
                "related_plan_ids": relation["plan_ids"],
                "related_plans": (
                    [{"plan_id": plan_id} for plan_id in relation["plan_ids"]]
                    if relation["plan_ids"] is not None
                    else None
                ),
                "relation": relation,
                "data_completeness": {
                    "status": "not_found",
                    "missing_dates": [],
                    "observed_dates": [],
                    "platform_observed_dates": [],
                    "feedback_available": not feedback_window.empty,
                    "quality_attribution_available": "material_id" in feedback_window.columns,
                    "platform_source": "material_report",
                    "quality_source": (
                        "feedback_export" if not feedback_window.empty else None
                    ),
                },
                "warnings": [f"未找到 material_id={query_id!r} 的标准化素材记录"],
            }

        material_name = next(
            (
                optional_text(value)
                for value in all_material_rows["material_name"].tolist()
                if optional_text(value) is not None
            ),
            None,
        )
        window_rows = materials[
            (materials["data_date"] >= start)
            & (materials["data_date"] <= end)
            & (materials["material_id"] == query_id)
        ].copy()
        platform_window_rows = materials[
            (materials["data_date"] >= start) & (materials["data_date"] <= end)
        ]
        platform_observed_dates = set(platform_window_rows["data_date"].tolist())
        observed_dates = set(window_rows["data_date"].tolist())
        missing_platform_dates = [
            day.date().isoformat()
            for day in requested_dates
            if day not in platform_observed_dates
        ]
        missing_material_dates = [
            day.date().isoformat() for day in requested_dates if day not in observed_dates
        ]

        if window_rows.empty:
            metrics = empty_metrics()
            status = "no_data"
            completeness_status = "no_data_in_window"
            daily_metrics: list[dict[str, Any]] = []
            observed_status = "not_observed"
            warnings = [f"素材 {query_id!r} 在选定时间窗口内没有日明细记录"]
            runtime = {
                "first_observed_date": None,
                "last_observed_date": None,
                "observed_days": 0,
                "active_days": 0,
            }
        else:
            summary_rows = summarize_entities(
                window_rows, "material_id", "material_name", feedback_window
            )
            summary = summary_rows[0] if summary_rows else None
            metrics = metric_payload(summary)
            daily_metrics = aggregate_metrics(
                window_rows, "material_id", feedback_window
            ).to_dict("records")
            observed_status = (
                "spending" if float(window_rows["spend"].sum()) > 0 else "no_spend"
            )
            observed_dates_sorted = sorted(
                day.date().isoformat() for day in observed_dates
            )
            active_days = sum(
                1 for row in daily_metrics if float(row.get("spend") or 0) > 0
            )
            runtime = {
                "first_observed_date": observed_dates_sorted[0],
                "last_observed_date": observed_dates_sorted[-1],
                "observed_days": len(observed_dates_sorted),
                "active_days": active_days,
            }
            if missing_platform_dates:
                status = "partial"
                completeness_status = "missing_platform_days"
                warnings = [
                    "选定时间窗口缺少平台报表日期，窗口汇总未将缺失日期补为 0",
                    *(
                        [
                            f"素材自身未观察到日期：{', '.join(missing_material_dates)}"
                        ]
                        if missing_material_dates
                        else []
                    ),
                ]
            elif missing_material_dates:
                status = "partial"
                completeness_status = "partial_material_days"
                warnings = [
                    "选定时间窗口内并非每天都有该素材的明细记录；缺失日不能解释为素材下线",
                    f"素材自身未观察到日期：{', '.join(missing_material_dates)}",
                ]
            else:
                status = "ok"
                completeness_status = "available"
                warnings = []

        observed_dates_sorted = sorted(
            day.date().isoformat() for day in observed_dates
        )
        return {
            "status": status,
            "found": True,
            "source": "replay_core.material_report",
            "object_type": "material",
            "material_id": query_id,
            "material_name": material_name,
            "observed_status": observed_status,
            "report_date": end.date().isoformat(),
            "time_window": time_window_info,
            "metrics": metrics,
            "summary": dict(metrics),
            **metric_aliases(metrics),
            "daily_metrics": daily_metrics,
            "runtime": runtime,
            "active_days": runtime["active_days"],
            "related_plan_ids": relation["plan_ids"],
            "related_plans": (
                [{"plan_id": plan_id} for plan_id in relation["plan_ids"]]
                if relation["plan_ids"] is not None
                else None
            ),
            "relation": relation,
            "data_completeness": {
                "status": completeness_status,
                "missing_dates": missing_platform_dates,
                "missing_material_dates": missing_material_dates,
                "observed_dates": observed_dates_sorted,
                "platform_observed_dates": sorted(
                    day.date().isoformat() for day in platform_observed_dates
                ),
                "feedback_available": not feedback_window.empty,
                "quality_attribution_available": "material_id" in feedback_window.columns,
                "platform_source": "material_report",
                "quality_source": (
                    "feedback_export"
                    if not feedback_window.empty and "material_id" in feedback_window.columns
                    else None
                ),
            },
            "warnings": warnings
            + (
                [relation["reason"]]
                if relation["status"] == "unavailable"
                else []
            ),
        }


@tool_protocol(
    tool_name="get_material_metrics",
    default_source="replay_core.material_report",
)
def get_material_metrics(
    material_id: str,
    report_date: str,
    time_window: str,
    unit_report_path: Path | str,
    material_report_path: Path | str,
    feedback_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load report sources and return JSON-shaped metrics for one material."""
    tool = MaterialMetricsTool.from_report_files(
        unit_report_path=unit_report_path,
        material_report_path=material_report_path,
        feedback_path=feedback_path,
    )
    return tool.get_material_metrics(material_id, report_date, time_window)
