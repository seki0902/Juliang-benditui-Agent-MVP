"""Read-only lead-quality Tool backed by the normalized feedback export."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from replay_core import (
    aggregate_account,
    load_feedback,
    load_platform_reports,
    metric_row,
    mtd_summary,
    _to_bool,
)

from .metrics_tool_utils import normalise_id, resolve_window, valid_lead_rate
from .tool_protocol import tool_protocol


_OBJECT_TYPE_ALIASES = {
    "account": "account",
    "账户": "account",
    "plan": "plan",
    "unit": "plan",
    "计划": "plan",
    "单元": "plan",
    "material": "material",
    "素材": "material",
}

_OBJECT_ID_COLUMNS = {"plan": "unit_id", "material": "material_id"}
_PLATFORM_METRIC_COLUMNS = {
    "data_date",
    "spend",
    "impressions",
    "clicks",
    "leads",
    "private_messages",
}


def _empty_quality_data() -> dict[str, Any]:
    return {
        "feedback_leads": None,
        "evaluated_leads": None,
        "valid_leads": None,
        "valid_lead_rate": None,
        "feedback_valid_lead_rate": None,
        "valid_lead_rate_denominator": None,
        "valid_lead_rate_denominator_source": None,
        "invalid_leads": None,
        "invalid_lead_rate": None,
        "contacted_leads": None,
        "contacted_lead_rate": None,
        "valid_lead_cost": None,
        "contact_cost": None,
        "platform_spend": None,
        "platform_leads": None,
        "invalid_reasons": None,
        "invalid_reason_status": "unavailable",
    }


def _normalise_object_type(object_type: str) -> str:
    normalized = str(object_type or "").strip().lower()
    if normalized not in _OBJECT_TYPE_ALIASES:
        allowed = "account, plan, material"
        raise ValueError(f"object_type must be one of: {allowed}")
    return _OBJECT_TYPE_ALIASES[normalized]


def _normalise_feedback(feedback: pd.DataFrame | None) -> pd.DataFrame:
    if feedback is None:
        return load_feedback(None)
    result = feedback.copy()
    if "lead_date" in result.columns:
        result["lead_date"] = pd.to_datetime(
            result["lead_date"], errors="coerce"
        ).dt.normalize()
    if "unit_id" in result.columns:
        result["unit_id"] = result["unit_id"].map(normalise_id)
    if "material_id" in result.columns:
        result["material_id"] = result["material_id"].map(normalise_id)
    for column in ("is_valid", "is_contacted"):
        if column in result.columns:
            result[column] = result[column].map(_to_bool)
    if "invalid_reason" not in result.columns:
        result["invalid_reason"] = pd.NA
    return result


def _normalise_platform(
    frame: pd.DataFrame | None, id_column: str | None = None
) -> pd.DataFrame | None:
    if frame is None:
        return None
    result = frame.copy()
    if "data_date" in result.columns:
        result["data_date"] = pd.to_datetime(
            result["data_date"], errors="coerce"
        ).dt.normalize()
        result = result[result["data_date"].notna()].copy()
    if id_column and id_column in result.columns:
        result[id_column] = result[id_column].map(normalise_id)
    return result


def _platform_summary(
    frame: pd.DataFrame | None, start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, Any]:
    empty = {
        "status": "unavailable",
        "spend": None,
        "leads": None,
        "impressions": None,
        "clicks": None,
        "missing_dates": [],
    }
    if frame is None:
        return empty
    missing_columns = sorted(_PLATFORM_METRIC_COLUMNS - set(frame.columns))
    if missing_columns:
        return {**empty, "missing_columns": missing_columns}

    window = frame[(frame["data_date"] >= start) & (frame["data_date"] <= end)]
    if window.empty:
        return {
            **empty,
            "status": "missing_platform_days",
            "missing_dates": [
                day.date().isoformat() for day in pd.date_range(start, end)
            ],
        }
    empty_feedback = load_feedback(None)
    account_daily = aggregate_account(window, empty_feedback)
    summary = mtd_summary(account_daily, empty_feedback, start, end)
    return {
        "status": summary.get("data_status", "unavailable"),
        "spend": summary.get("spend"),
        "leads": summary.get("leads"),
        "impressions": summary.get("impressions"),
        "clicks": summary.get("clicks"),
        "missing_dates": summary.get("missing_dates", []),
    }


def _flag_counts(
    frame: pd.DataFrame, column: str
) -> tuple[int | None, int | None, int | None]:
    """Return positive, negative and evaluated counts; preserve all-NA state."""
    if column not in frame.columns:
        return None, None, None
    values = frame[column].dropna()
    if values.empty:
        return None, None, 0
    return (
        int(values.eq(True).sum()),
        int(values.eq(False).sum()),
        int(values.notna().sum()),
    )


def _quality_result(
    *,
    status: str,
    source: str | None,
    time_window: dict[str, str],
    object_scope: dict[str, Any],
    data: dict[str, Any],
    warnings: list[str],
    data_completeness: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "time_window": time_window,
        "object_scope": object_scope,
        "data": data,
        **data,
        "warnings": warnings,
        "data_completeness": data_completeness,
    }


@dataclass
class LeadQualityTool:
    """Query effective/contacted lead feedback for one object scope."""

    feedback: pd.DataFrame | None = None
    units: pd.DataFrame | None = None
    materials: pd.DataFrame | None = None
    feedback_source: str | None = None

    @classmethod
    def from_report_files(
        cls,
        unit_report_path: Path | str,
        material_report_path: Path | str,
        feedback_path: Path | str | None = None,
    ) -> "LeadQualityTool":
        units, materials, _meta = load_platform_reports(
            Path(unit_report_path), Path(material_report_path)
        )
        feedback = load_feedback(Path(feedback_path) if feedback_path else None)
        return cls(
            feedback=feedback,
            units=units,
            materials=materials,
            feedback_source="feedback_export" if feedback_path else None,
        )

    @classmethod
    def from_feedback_file(cls, feedback_path: Path | str) -> "LeadQualityTool":
        return cls(
            feedback=load_feedback(Path(feedback_path)),
            feedback_source="feedback_export",
        )

    def _platform_frame(
        self, object_type: str, object_id: str | None
    ) -> tuple[pd.DataFrame | None, str | None]:
        if object_type == "material":
            frame, source = self.materials, "material_report"
            id_column = "material_id"
        else:
            frame, source = self.units, "unit_report"
            id_column = "unit_id"
        normalized = _normalise_platform(frame, id_column if object_type != "account" else None)
        if normalized is None or object_type == "account":
            return normalized, source if frame is not None else None
        if id_column not in normalized.columns or object_id is None:
            return None, source if frame is not None else None
        return normalized[normalized[id_column] == object_id].copy(), source

    @tool_protocol(tool_name="get_lead_quality")
    def get_lead_quality(
        self,
        report_date: str,
        time_window: str,
        object_type: str = "account",
        object_id: str | None = None,
    ) -> dict[str, Any]:
        canonical_type = _normalise_object_type(object_type)
        normalized_id = normalise_id(object_id) if object_id is not None else ""
        normalized_id = normalized_id or None
        start, end, normalized_window = resolve_window(report_date, time_window)
        time_window_info = {
            "name": normalized_window,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
        id_column = _OBJECT_ID_COLUMNS.get(canonical_type)
        object_scope = {
            "object_type": canonical_type,
            "object_id": normalized_id,
            "id_column": id_column,
            "feedback_granularity": "account_level" if id_column is None else "object_id",
            "status": "ready" if id_column is None or normalized_id else "missing_object_id",
        }
        data = _empty_quality_data()
        warnings: list[str] = []
        platform_frame, platform_source = self._platform_frame(canonical_type, normalized_id)
        platform = _platform_summary(platform_frame, start, end)
        source = self.feedback_source or (
            "provided_dataframe"
            if self.feedback is not None and not self.feedback.empty
            else None
        )
        feedback = _normalise_feedback(self.feedback)
        source_has_rows = self.feedback is not None and not self.feedback.empty
        completeness = {
            "status": "unavailable",
            "feedback_status": "missing_source" if not source_has_rows else "unavailable",
            "feedback_rows": None,
            "platform_status": platform.get("status"),
            "platform_source": platform_source,
            "quality_source": source,
            "missing_platform_dates": platform.get("missing_dates", []),
        }

        if id_column is not None and normalized_id is None:
            object_scope["status"] = "missing_object_id"
            return _quality_result(
                status="unavailable",
                source=source,
                time_window=time_window_info,
                object_scope=object_scope,
                data=data,
                warnings=["计划或素材范围查询必须提供 object_id"],
                data_completeness=completeness,
            )

        if not source_has_rows:
            return _quality_result(
                status="unavailable",
                source=source,
                time_window=time_window_info,
                object_scope=object_scope,
                data=data,
                warnings=["没有可用的反馈数据，不能将缺失解释为质量指标为 0"],
                data_completeness=completeness,
            )

        if "lead_date" not in feedback.columns:
            completeness["feedback_status"] = "missing_lead_date"
            return _quality_result(
                status="unavailable",
                source=source,
                time_window=time_window_info,
                object_scope=object_scope,
                data=data,
                warnings=["反馈数据缺少 lead_date，无法按时间窗口归属"],
                data_completeness=completeness,
            )

        feedback_window = feedback[
            (feedback["lead_date"] >= start) & (feedback["lead_date"] <= end)
        ].copy()
        if id_column is not None:
            if id_column not in feedback_window.columns:
                completeness["feedback_status"] = "missing_scope_id"
                return _quality_result(
                    status="unavailable",
                    source=source,
                    time_window=time_window_info,
                    object_scope=object_scope,
                    data=data,
                    warnings=[
                        f"反馈数据没有 {id_column}，不能把账户级反馈归属到 {canonical_type}"
                    ],
                    data_completeness=completeness,
                )
            feedback_window = feedback_window[
                feedback_window[id_column] == normalized_id
            ].copy()

        if feedback_window.empty:
            completeness["feedback_status"] = "no_records_in_window"
            return _quality_result(
                status="unavailable",
                source=source,
                time_window=time_window_info,
                object_scope=object_scope,
                data=data,
                warnings=["选定时间窗口内没有可归属的反馈记录，不能将其解释为质量指标为 0"],
                data_completeness=completeness,
            )

        feedback_rows = len(feedback_window)
        valid_leads, invalid_leads, evaluated_leads = _flag_counts(
            feedback_window, "is_valid"
        )
        contacted_leads, _not_contacted, evaluated_contacted = _flag_counts(
            feedback_window, "is_contacted"
        )
        platform_leads = platform.get("leads")
        platform_spend = platform.get("spend")
        rate_denominator = platform_leads if platform_leads is not None else evaluated_leads
        rate_denominator_source = (
            "platform_leads" if platform_leads is not None else "evaluated_feedback_leads"
        )

        data.update(
            {
                "feedback_leads": feedback_rows,
                "evaluated_leads": evaluated_leads,
                "valid_leads": valid_leads,
                "valid_lead_rate": valid_lead_rate(valid_leads, rate_denominator),
                "feedback_valid_lead_rate": valid_lead_rate(valid_leads, evaluated_leads),
                "valid_lead_rate_denominator": rate_denominator,
                "valid_lead_rate_denominator_source": rate_denominator_source,
                "invalid_leads": invalid_leads,
                "invalid_lead_rate": valid_lead_rate(invalid_leads, evaluated_leads),
                "contacted_leads": contacted_leads,
                "contacted_lead_rate": valid_lead_rate(contacted_leads, feedback_rows),
                "platform_spend": platform_spend,
                "platform_leads": platform_leads,
            }
        )

        if platform_spend is not None:
            cost_row = metric_row(
                platform_spend,
                0,
                0,
                platform_leads or 0,
                valid_leads,
                0,
                contacted_leads,
            )
            data["valid_lead_cost"] = cost_row["valid_lead_cost"]
            data["contact_cost"] = cost_row["contact_cost"]
        else:
            warnings.append("缺少完整的平台消耗窗口，无法计算有效 CPL 和对接成本")

        if "invalid_reason" not in feedback_window.columns:
            data["invalid_reason_status"] = "unavailable"
        elif invalid_leads is None:
            data["invalid_reason_status"] = "unavailable"
        elif invalid_leads == 0:
            data["invalid_reasons"] = {}
            data["invalid_reason_status"] = "not_applicable"
        else:
            invalid_rows = feedback_window[
                feedback_window["is_valid"].notna()
                & feedback_window["is_valid"].eq(False)
            ]
            reasons = [
                str(value).strip()
                for value in invalid_rows["invalid_reason"].tolist()
                if value is not None and not pd.isna(value) and str(value).strip()
            ]
            data["invalid_reasons"] = dict(sorted(Counter(reasons).items()))
            data["invalid_reason_status"] = (
                "available" if reasons else "missing"
            )
            if not reasons:
                warnings.append("存在无效留资，但反馈中没有填写无效原因")

        if valid_leads is None:
            warnings.append("反馈中的是否有效字段尚未完成筛选，不能将其解释为有效留资为 0")
        if evaluated_contacted == 0:
            warnings.append("反馈中的是否对接字段尚未完成填写")

        status = "ok"
        if (
            valid_leads is None
            or platform_spend is None
            or data["invalid_reason_status"] in {"unavailable", "missing"}
        ):
            status = "partial"
        completeness.update(
            {
                "status": "available" if status == "ok" else "partial",
                "feedback_status": "available",
                "feedback_rows": feedback_rows,
                "evaluated_valid_leads": evaluated_leads,
                "evaluated_contacted_leads": evaluated_contacted,
            }
        )
        object_scope["status"] = "filtered" if id_column else "account"
        object_scope["feedback_rows"] = feedback_rows
        if platform.get("status") == "missing_platform_days":
            warnings.append("平台窗口存在缺失日期，平台消耗相关结果保持缺失")
            status = "partial"
            completeness["status"] = "partial"

        return _quality_result(
            status=status,
            source=source,
            time_window=time_window_info,
            object_scope=object_scope,
            data=data,
            warnings=warnings,
            data_completeness=completeness,
        )


@tool_protocol(tool_name="get_lead_quality")
def get_lead_quality(
    report_date: str,
    time_window: str,
    object_type: str = "account",
    object_id: str | None = None,
    unit_report_path: Path | str | None = None,
    material_report_path: Path | str | None = None,
    feedback_path: Path | str | None = None,
) -> dict[str, Any]:
    """Return JSON-shaped quality facts for an account, plan or material scope."""
    if unit_report_path is not None or material_report_path is not None:
        if unit_report_path is None or material_report_path is None:
            raise ValueError("unit_report_path and material_report_path must be provided together")
        tool = LeadQualityTool.from_report_files(
            unit_report_path=unit_report_path,
            material_report_path=material_report_path,
            feedback_path=feedback_path,
        )
    elif feedback_path is not None:
        tool = LeadQualityTool.from_feedback_file(feedback_path)
    else:
        tool = LeadQualityTool()
    return tool.get_lead_quality(
        report_date=report_date,
        time_window=time_window,
        object_type=object_type,
        object_id=object_id,
    )
