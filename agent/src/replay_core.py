"""Deterministic local replay core for the Douyin Local Push Growth Agent.

The replay is intentionally read-only. It loads the exported unit/material
reports, normalizes platform metrics, optionally joins a Shimo lead-feedback
export, calculates yesterday and MTD metrics, and emits rule evidence.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from baseline_engine import build_baselines


UNIT_COLUMNS = {
    "id": "单元ID",
    "name": "单元名称",
    "date": "日期",
    "campaign_type": "投放类型",
    "spend": "消耗(元)",
    "impressions": "展示次数",
    "clicks": "点击次数",
    "leads": "线索留资数(计费时间)",
    "private_messages": "私信意向数(计费时间)",
}

MATERIAL_COLUMNS = {
    "id": "素材ID",
    "name": "素材名称",
    "date": "日期",
    "spend": "消耗(元)",
    "impressions": "展示次数",
    "clicks": "点击次数",
    "leads": "线索留资数(计费时间)",
    "private_messages": "私信意向数(计费时间)",
}


@dataclass
class ReplayConfig:
    exclude_latest_date: bool = True
    min_impressions: int = 1000
    min_clicks: int = 30
    min_spend: float = 100.0
    ctr_decline_threshold: float = 0.20
    conversion_decline_threshold: float = 0.15
    cost_increase_threshold: float = 0.30


def _clean_number(value: Any, percent: bool = False) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return np.nan
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "—", "None", "nan", "NaN"}:
        return np.nan
    try:
        number = float(text.rstrip("%"))
    except ValueError:
        return np.nan
    return number / 100 if percent or text.endswith("%") else number


def _clean_id(value: Any) -> str | None:
    if value is None or (isinstance(value, (float, np.floating)) and math.isnan(float(value))):
        return None
    if isinstance(value, (float, np.floating)):
        # Excel often exposes long numeric IDs as scientific-notation floats.
        # Keep a stable decimal representation for joins and report output.
        return str(int(value))
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text or None


def _clean_date(value: Any) -> pd.Timestamp | pd.NaT:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return pd.NaT
    return pd.to_datetime(str(value).strip(), errors="coerce")


def _require_columns(df: pd.DataFrame, required: Iterable[str], source: Path) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{source.name} 缺少字段: {missing}")


def _read_data_rows(path: Path, columns: dict[str, str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = pd.read_excel(path, sheet_name=0)
    _require_columns(raw, columns.values(), path)

    # 导出表第一行通常是总计行，ID 为空；只把有对象 ID 的行作为明细。
    id_column = columns["id"]
    total_row = raw[raw[id_column].isna()].head(1)
    data = raw[raw[id_column].notna()].copy()

    if data.empty:
        raise ValueError(f"{path.name} 没有可用明细行")

    normalized = pd.DataFrame()
    normalized["entity_id"] = data[id_column].map(_clean_id)
    normalized["entity_name"] = data[columns["name"]].astype(str).str.strip()
    normalized["data_date"] = data[columns["date"]].map(_clean_date).dt.normalize()
    normalized["spend"] = data[columns["spend"]].map(_clean_number)
    normalized["impressions"] = data[columns["impressions"]].map(_clean_number)
    normalized["clicks"] = data[columns["clicks"]].map(_clean_number)
    normalized["leads"] = data[columns["leads"]].map(_clean_number)
    normalized["private_messages"] = data[columns["private_messages"]].map(_clean_number)

    if "campaign_type" in columns:
        normalized["campaign_type"] = data[columns["campaign_type"]].astype(str).str.strip()

    normalized = normalized.dropna(subset=["entity_id", "data_date"])
    normalized["spend"] = normalized["spend"].fillna(0.0)
    for column in ["impressions", "clicks", "leads", "private_messages"]:
        normalized[column] = normalized[column].fillna(0.0)

    total = {}
    if not total_row.empty:
        row = total_row.iloc[0]
        for key in ["spend", "impressions", "clicks", "leads", "private_messages"]:
            total[key] = _clean_number(row[columns[key]])

    return normalized, {"source_file": str(path), "raw_rows": len(raw), "detail_rows": len(normalized), "export_total": total}


def load_platform_reports(unit_path: Path, material_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    units, unit_meta = _read_data_rows(unit_path, UNIT_COLUMNS)
    materials, material_meta = _read_data_rows(material_path, MATERIAL_COLUMNS)
    units = units.rename(columns={"entity_id": "unit_id", "entity_name": "unit_name"})
    materials = materials.rename(columns={"entity_id": "material_id", "entity_name": "material_name"})

    units["plan_type"] = np.select(
        [units["unit_name"].str.contains("浅层", na=False), units["unit_name"].str.contains("深转", na=False)],
        ["shallow", "deep"],
        default="unknown",
    )
    units["observed_status"] = np.where(units["spend"] > 0, "spending", "no_spend")
    units["data_completeness"] = "complete"
    materials["data_completeness"] = "complete"

    meta = {"unit": unit_meta, "material": material_meta}
    return units, materials, meta


def load_feedback(path: Path | None) -> pd.DataFrame:
    """Load an optional Shimo export.

    Supported fields are intentionally permissive. At minimum the export must
    contain a lead date and either an effective or contacted flag.
    """
    if path is None:
        return pd.DataFrame(
            columns=["lead_id", "lead_date", "is_valid", "is_contacted", "invalid_reason"]
        )

    if path.suffix.lower() in {".xlsx", ".xls"}:
        raw = pd.read_excel(path)
    else:
        raw = pd.read_csv(path)

    def find(*names: str) -> str | None:
        for name in names:
            if name in raw.columns:
                return name
        return None

    date_col = find("lead_date", "线索日期", "日期")
    if date_col is None:
        raise ValueError("石墨反馈表缺少线索日期字段")

    result = pd.DataFrame()
    id_col = find("lead_id", "线索ID", "线索id")
    unit_col = find("unit_id", "单元ID", "计划ID", "计划id")
    material_col = find("material_id", "素材ID", "素材id")
    valid_col = find("is_valid", "是否有效", "有效留资")
    contacted_col = find("is_contacted", "是否对接")
    invalid_reason_col = find("invalid_reason", "无效原因", "无效理由", "原因")
    result["lead_id"] = raw[id_col].map(_clean_id) if id_col else np.arange(len(raw)).astype(str)
    result["lead_date"] = raw[date_col].map(_clean_date).dt.normalize()
    if unit_col:
        result["unit_id"] = raw[unit_col].map(_clean_id)
    if material_col:
        result["material_id"] = raw[material_col].map(_clean_id)
    result["is_valid"] = raw[valid_col].map(_to_bool) if valid_col else pd.NA
    result["is_contacted"] = raw[contacted_col].map(_to_bool) if contacted_col else pd.NA
    result["invalid_reason"] = (
        raw[invalid_reason_col].map(_clean_feedback_text)
        if invalid_reason_col
        else pd.NA
    )
    return result.dropna(subset=["lead_date"])


def _clean_feedback_text(value: Any) -> str | pd.NA:
    if value is None or pd.isna(value):
        return pd.NA
    text = str(value).strip()
    return text or pd.NA


def _to_bool(value: Any) -> bool | pd.NA:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return pd.NA
    text = str(value).strip().lower()
    if text in {"是", "已对接", "y", "yes", "true", "1", "有效"}:
        return True
    if text in {"否", "未对接", "n", "no", "false", "0", "无效"}:
        return False
    return pd.NA


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator is None or pd.isna(denominator) or denominator == 0:
        return None
    if numerator is None or pd.isna(numerator):
        return None
    return float(numerator) / float(denominator)


def metric_row(spend: float, impressions: float, clicks: float, leads: float,
               valid_leads: float | None, private_messages: float,
               contacted_leads: float | None) -> dict[str, Any]:
    return {
        "spend": _round(spend),
        "leads": _int_or_none(leads),
        "lead_cost": _round(_safe_ratio(spend, leads)),
        "valid_leads": _int_or_none(valid_leads),
        "valid_lead_cost": _round(_safe_ratio(spend, valid_leads)),
        "impressions": _int_or_none(impressions),
        "clicks": _int_or_none(clicks),
        "ctr": _round(_safe_ratio(clicks, impressions), 6),
        "cpc": _round(_safe_ratio(spend, clicks)),
        "cpm": _round(_safe_ratio(spend * 1000 if spend is not None else None, impressions)),
        "private_messages": _int_or_none(private_messages),
        "lead_conversion_rate": _round(_safe_ratio(leads, clicks), 6),
        "contacted_leads": _int_or_none(contacted_leads),
        "contact_cost": _round(_safe_ratio(spend, contacted_leads)),
    }


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _int_or_none(value: float | None) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(round(float(value)))


def aggregate_metrics(df: pd.DataFrame, key: str, feedback: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby(["data_date", key], as_index=False)[
        ["spend", "impressions", "clicks", "leads", "private_messages"]
    ].sum()

    feedback_key = key if key in feedback.columns else None
    if feedback.empty or feedback_key is None:
        grouped["valid_leads"] = np.nan
        grouped["contacted_leads"] = np.nan
    else:
        feedback_daily = feedback.groupby(["lead_date", feedback_key], as_index=False).agg(
            valid_leads=("is_valid", lambda series: int(series.dropna().eq(True).sum()) if series.notna().any() else np.nan),
            contacted_leads=("is_contacted", lambda series: int(series.dropna().eq(True).sum()) if series.notna().any() else np.nan),
        )
        grouped = grouped.merge(feedback_daily, left_on=["data_date", key], right_on=["lead_date", feedback_key], how="left").drop(columns=["lead_date"])

    rows = []
    for _, row in grouped.iterrows():
        values = metric_row(
            row["spend"], row["impressions"], row["clicks"], row["leads"],
            row["valid_leads"], row["private_messages"], row["contacted_leads"],
        )
        values.update({"data_date": row["data_date"].date().isoformat(), key: str(row[key])})
        rows.append(values)
    return pd.DataFrame(rows)


def aggregate_account(df: pd.DataFrame, feedback: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby("data_date", as_index=False)[
        ["spend", "impressions", "clicks", "leads", "private_messages"]
    ].sum()
    if feedback.empty:
        grouped["valid_leads"] = np.nan
        grouped["contacted_leads"] = np.nan
    else:
        feedback_daily = feedback.groupby("lead_date", as_index=False).agg(
            valid_leads=("is_valid", lambda series: int(series.dropna().eq(True).sum()) if series.notna().any() else np.nan),
            contacted_leads=("is_contacted", lambda series: int(series.dropna().eq(True).sum()) if series.notna().any() else np.nan),
        )
        grouped = grouped.merge(feedback_daily, left_on="data_date", right_on="lead_date", how="left").drop(columns=["lead_date"])

    rows = []
    for _, row in grouped.iterrows():
        values = metric_row(
            row["spend"], row["impressions"], row["clicks"], row["leads"],
            row["valid_leads"], row["private_messages"], row["contacted_leads"],
        )
        values["data_date"] = row["data_date"].date().isoformat()
        rows.append(values)
    return pd.DataFrame(rows)


def mtd_summary(account_daily: pd.DataFrame, feedback: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    current = account_daily[
        (pd.to_datetime(account_daily["data_date"]) >= start)
        & (pd.to_datetime(account_daily["data_date"]) <= end)
    ]
    spend = float(current["spend"].sum()) if not current.empty else 0.0
    leads = float(current["leads"].sum()) if not current.empty else 0.0
    private_messages = float(current["private_messages"].sum()) if not current.empty else 0.0
    impressions = float(current["impressions"].sum()) if "impressions" in current.columns and not current.empty else None
    clicks = float(current["clicks"].sum()) if "clicks" in current.columns and not current.empty else None

    if feedback.empty:
        valid_leads = None
        contacted_leads = None
    else:
        feedback_month = feedback[(feedback["lead_date"] >= start) & (feedback["lead_date"] <= end)]
        valid_leads = int(feedback_month["is_valid"].dropna().eq(True).sum()) if feedback_month["is_valid"].notna().any() else None
        contacted_leads = int(feedback_month["is_contacted"].dropna().eq(True).sum()) if feedback_month["is_contacted"].notna().any() else None

    observed_dates = set(pd.to_datetime(current["data_date"]).dt.normalize())
    missing_dates = [day.date().isoformat() for day in pd.date_range(start, end) if day not in observed_dates]
    if missing_dates:
        return {
            "mtd_start_date": start.date().isoformat(), "mtd_end_date": end.date().isoformat(),
            "data_status": "missing_platform_days", "missing_dates": missing_dates,
            **{key: None for key in ("spend", "impressions", "clicks", "leads", "lead_cost",
                                    "ctr", "cpc", "cpm", "lead_conversion_rate", "valid_leads",
                                    "valid_lead_cost", "private_messages", "contacted_leads", "contact_cost")},
        }
    return {
        "mtd_start_date": start.date().isoformat(),
        "mtd_end_date": end.date().isoformat(),
        "spend": _round(spend),
        "impressions": _int_or_none(impressions),
        "clicks": _int_or_none(clicks),
        "leads": _int_or_none(leads),
        "lead_cost": _round(_safe_ratio(spend, leads)),
        "ctr": _round(_safe_ratio(clicks, impressions)),
        "cpc": _round(_safe_ratio(spend, clicks)),
        "cpm": _round(_safe_ratio(spend * 1000, impressions)),
        "lead_conversion_rate": _round(_safe_ratio(leads, clicks)),
        "valid_leads": valid_leads,
        "valid_lead_cost": _round(_safe_ratio(spend, valid_leads)),
        "private_messages": _int_or_none(private_messages),
        "contacted_leads": contacted_leads,
        "contact_cost": _round(_safe_ratio(spend, contacted_leads)),
        "data_status": "missing_manual_feedback" if feedback.empty else "available",
    }


def summarize_entities(df: pd.DataFrame, id_column: str, name_column: str,
                       feedback: pd.DataFrame) -> list[dict[str, Any]]:
    """Return period summaries without copying account-level feedback downward."""
    feedback_key = id_column if id_column in feedback.columns else None
    summaries: list[dict[str, Any]] = []
    for entity_id, frame in df.groupby(id_column):
        spend = float(frame["spend"].sum())
        impressions = float(frame["impressions"].sum())
        clicks = float(frame["clicks"].sum())
        leads = float(frame["leads"].sum())
        private_messages = float(frame["private_messages"].sum())
        if feedback_key is None or feedback.empty:
            valid_leads = None
            contacted_leads = None
        else:
            entity_feedback = feedback[feedback[feedback_key].astype(str) == str(entity_id)]
            valid_leads = int(entity_feedback["is_valid"].dropna().eq(True).sum()) if entity_feedback["is_valid"].notna().any() else None
            contacted_leads = int(entity_feedback["is_contacted"].dropna().eq(True).sum()) if entity_feedback["is_contacted"].notna().any() else None
        row = metric_row(spend, impressions, clicks, leads, valid_leads, private_messages, contacted_leads)
        row.update({id_column: str(entity_id), name_column: str(frame[name_column].iloc[0]), "active_days": int((frame["spend"] > 0).sum())})
        summaries.append(row)
    return sorted(summaries, key=lambda item: item["spend"] or 0, reverse=True)


def _weighted_ratio(frame: pd.DataFrame, numerator: str, denominator: str) -> float | None:
    if frame.empty:
        return None
    return _safe_ratio(frame[numerator].sum(), frame[denominator].sum())


def _ratio_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return current / baseline - 1


def _aggregate_window(frame: pd.DataFrame) -> dict[str, Any]:
    spend = frame["spend"].sum()
    impressions = frame["impressions"].sum()
    clicks = frame["clicks"].sum()
    leads = frame["leads"].sum()
    return {
        "spend": float(spend),
        "impressions": int(impressions),
        "clicks": int(clicks),
        "leads": int(leads),
        "ctr": _weighted_ratio(frame, "clicks", "impressions"),
        "lead_conversion_rate": _weighted_ratio(frame, "leads", "clicks"),
        "lead_cost": _safe_ratio(spend, leads),
    }


def diagnose_units(units: pd.DataFrame, config: ReplayConfig) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    as_of_date = units["data_date"].max()
    for unit_id, frame in units.groupby("unit_id"):
        frame = frame.sort_values("data_date").copy()
        active = frame[frame["spend"] > 0]
        if len(active) < 4:
            continue

        # Use the first/last three active dates to detect lifecycle change.
        first = _aggregate_window(active.head(3))
        recent = _aggregate_window(active.tail(3))
        evidence: list[str] = []
        causes: list[str] = []
        signals: dict[str, Any] = {}

        ctr_change = _ratio_change(recent["ctr"], first["ctr"])
        cvr_change = _ratio_change(recent["lead_conversion_rate"], first["lead_conversion_rate"])
        cost_change = _ratio_change(recent["lead_cost"], first["lead_cost"])
        last_active_date = active["data_date"].max()
        inactive_days = int((as_of_date - last_active_date).days)
        signals.update({"ctr_change": _round(ctr_change, 4), "lead_conversion_rate_change": _round(cvr_change, 4), "lead_cost_change": _round(cost_change, 4)})
        signals["inactive_days_after_last_spend"] = inactive_days

        if ctr_change is not None and ctr_change <= -config.ctr_decline_threshold:
            evidence.append(f"最近3个投放日 CTR 较前3个投放日下降 {abs(ctr_change):.1%}")
            causes.extend(["creative_issue", "audience_issue"])
        if cvr_change is not None and cvr_change <= -config.conversion_decline_threshold:
            evidence.append(f"最近3个投放日留资转化率下降 {abs(cvr_change):.1%}")
            causes.append("landing_or_handling_issue")
        if cost_change is not None and cost_change >= config.cost_increase_threshold:
            evidence.append(f"最近3个投放日单个留资成本上涨 {cost_change:.1%}")
            causes.append("delivery_or_funnel_issue")
        if recent["leads"] == 0 and recent["spend"] >= config.min_spend:
            evidence.append(f"最近3个投放日消耗 {_round(recent['spend'])} 元但平台留资为 0")
            causes.append("creative_or_delivery_issue")
        if inactive_days >= 2:
            evidence.append(f"最近一次有消耗日期为 {last_active_date.date().isoformat()}，之后连续 {inactive_days} 天未观察到消耗")
            causes.append("plan_status_or_delivery_change")

        if not evidence:
            continue

        unit_name = str(frame["unit_name"].iloc[0])
        missing = ["valid_leads", "contacted_leads", "frequency", "audience_overlap", "plan_material_relation"]
        unique_causes = list(dict.fromkeys(causes))
        diagnosis_status = "possible" if len(unique_causes) > 1 else "probable"
        action = "observe"
        if recent["leads"] == 0 and recent["spend"] >= config.min_spend:
            action = "control_risk"
        elif inactive_days >= 2:
            action = "check_status_or_control"
        elif cvr_change is not None and cvr_change <= -config.conversion_decline_threshold:
            action = "inspect_handling"
        elif ctr_change is not None and ctr_change <= -config.ctr_decline_threshold:
            action = "replace_or_test_material"

        anomalies.append({
            "object_type": "unit",
            "object_id": str(unit_id),
            "object_name": unit_name,
            "anomaly": "lifecycle_or_efficiency_decline",
            "diagnosis_status": diagnosis_status,
            "candidate_causes": unique_causes,
            "evidence": evidence,
            "missing_evidence": missing,
            "signals": signals,
            "action": action,
            "observation_window": "48h",
            "requires_human_approval": True,
        })
    return anomalies


def build_plan_timeline(units: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[dict[str, Any]]:
    all_dates = pd.date_range(start, end, freq="D")
    result = []
    for date in all_dates:
        current = units[units["data_date"] == date]
        active = current[current["spend"] > 0]["unit_name"].tolist()
        observed = current["unit_name"].tolist()
        result.append({
            "data_date": date.date().isoformat(),
            "observed_units": observed,
            "active_units": active,
            "active_unit_count": len(active),
        })
    return result


def reconcile(units: pd.DataFrame, materials: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    unit_totals = {column: float(units[column].sum()) for column in ["spend", "impressions", "clicks", "leads", "private_messages"]}
    material_totals = {column: float(materials[column].sum()) for column in ["spend", "impressions", "clicks", "leads", "private_messages"]}
    differences = {key: _round(unit_totals[key] - material_totals[key], 4) for key in unit_totals}
    checks = {key: abs(value) <= (0.01 if key == "spend" else 0.0) for key, value in differences.items()}
    return {
        "unit_totals": unit_totals,
        "material_totals": material_totals,
        "differences_unit_minus_material": differences,
        "checks": checks,
        "all_pass": all(checks.values()),
        "export_totals": meta,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if pd.isna(value) if not isinstance(value, (list, dict, tuple)) else False:
        return None
    return value


def run_replay(unit_path: Path, material_path: Path, output_dir: Path,
               feedback_path: Path | None = None, config: ReplayConfig | None = None) -> dict[str, Any]:
    config = config or ReplayConfig()
    units, materials, meta = load_platform_reports(unit_path, material_path)
    feedback = load_feedback(feedback_path)

    max_date = max(units["data_date"].max(), materials["data_date"].max())
    as_of_date = max_date - pd.Timedelta(days=1) if config.exclude_latest_date else max_date
    min_date = min(units["data_date"].min(), materials["data_date"].min())
    units = units[units["data_date"] <= as_of_date].copy()
    materials = materials[materials["data_date"] <= as_of_date].copy()
    feedback = feedback[feedback["lead_date"] <= as_of_date].copy()

    account_daily = aggregate_account(units, feedback)
    unit_daily = aggregate_metrics(units, "unit_id", feedback)
    material_daily = aggregate_metrics(materials, "material_id", feedback)
    mtd = mtd_summary(account_daily, feedback, as_of_date.replace(day=1), as_of_date)
    anomalies = diagnose_units(units, config)
    reconciliation = reconcile(units, materials, meta)
    timeline = build_plan_timeline(units, min_date, as_of_date)
    plan_summary = summarize_entities(units, "unit_id", "unit_name", feedback)
    material_summary = summarize_entities(materials, "material_id", "material_name", feedback)
    plan_baselines = build_baselines(units, "unit_id", as_of_date)
    material_baselines = build_baselines(materials, "material_id", as_of_date)

    output_dir.mkdir(parents=True, exist_ok=True)
    units.to_csv(output_dir / "normalized_unit_daily.csv", index=False, encoding="utf-8-sig")
    materials.to_csv(output_dir / "normalized_material_daily.csv", index=False, encoding="utf-8-sig")
    account_daily.to_csv(output_dir / "account_daily_metrics.csv", index=False, encoding="utf-8-sig")

    report = {
        "report_date": (as_of_date + pd.Timedelta(days=1)).date().isoformat(),
        "data_date": as_of_date.date().isoformat(),
        "source_snapshot_max_date": max_date.date().isoformat(),
        "data_completeness": "partial_manual_feedback_missing" if feedback_path is None else "platform_and_feedback_loaded",
        "missing_fields": [] if feedback_path else ["valid_leads", "contacted_leads", "valid_lead_cost", "contact_cost"],
        "yesterday": next((row for row in account_daily.to_dict("records") if row["data_date"] == as_of_date.date().isoformat()), None),
        "daily_plan_metrics": unit_daily.to_dict("records"),
        "daily_material_metrics": material_daily.to_dict("records"),
        "plan_baselines": plan_baselines,
        "material_baselines": material_baselines,
        "mtd": mtd,
        "plan_summary": plan_summary,
        "material_summary": material_summary,
        "reconciliation": reconciliation,
        "plan_timeline": timeline,
        "anomalies": anomalies,
        "notes": [
            "已排除导出最大日期。" if config.exclude_latest_date else "统计日期取自导出文件，未排除最大日期。",
            "当前没有石墨人工反馈文件，不能把确认意向自动等同于有效留资。",
            "计划-素材历史关联不在两份导出明细中，素材诊断不能反推具体所属计划。",
        ],
    }
    report = _jsonable(report)
    (output_dir / "replay_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "replay_report.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "缺失"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    yesterday = report.get("yesterday") or {}
    mtd = report["mtd"]
    lines = [
        f"# 本地推投放日报｜{report['report_date']}",
        "",
        "## 1. 数据状态",
        f"- 昨日统计日期：{report['data_date']}",
        f"- 原始快照最大日期：{report['source_snapshot_max_date']}",
        f"- 数据完整度：{report['data_completeness']}",
        f"- 缺失字段：{', '.join(report['missing_fields']) if report['missing_fields'] else '无'}",
        "",
        "## 2. 昨日数据",
        "| 指标 | 数值 |",
        "|---|---:|",
    ]
    for label, key in [
        ("消耗", "spend"), ("留资量", "leads"), ("单个留资成本", "lead_cost"),
        ("有效留资量", "valid_leads"), ("有效留资成本", "valid_lead_cost"),
        ("曝光量", "impressions"), ("点击量", "clicks"), ("CTR", "ctr"),
        ("CPC", "cpc"), ("私信咨询数", "private_messages"), ("CPM", "cpm"),
        ("留资转化率", "lead_conversion_rate"), ("对接数量", "contacted_leads"),
        ("对接成本", "contact_cost"),
    ]:
        value = yesterday.get(key)
        if key in {"ctr", "lead_conversion_rate"} and value is not None:
            value = f"{value:.2%}"
        lines.append(f"| {label} | {_fmt(value)} |")

    lines.extend([
        "",
        "## 3. 本月累计（当月1日至昨日）",
        f"- 累计数据状态：{mtd['data_status']}；缺少 {len(mtd.get('missing_dates', []))} 个统计日，缺失时不以单日数据代替月累计。",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 留资量 | {_fmt(mtd['leads'])} |",
        f"| 留资成本 | {_fmt(mtd['lead_cost'])} |",
        f"| 对接数量 | {_fmt(mtd['contacted_leads'])} |",
        f"| 对接成本 | {_fmt(mtd['contact_cost'])} |",
        "",
        "## 4. 计划活跃时间线",
        "| 日期 | 当天有消耗的计划 |",
        "|---|---|",
    ])
    for row in report["plan_timeline"]:
        lines.append(f"| {row['data_date']} | {'、'.join(row['active_units']) if row['active_units'] else '无'} |")

    lines.extend([
        "",
        "## 5. 计划汇总（截至昨日）",
        "| 计划 | 阶段 | 活跃天数 | 消耗 | 留资量 | 单个留资成本 | CTR | 留资转化率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    plan_stage = {item["object_id"]: item["stage"] for item in report.get("plan_baselines", [])}
    for row in report["plan_summary"]:
        ctr = f"{row['ctr']:.2%}" if row.get("ctr") is not None else "缺失"
        cvr = f"{row['lead_conversion_rate']:.2%}" if row.get("lead_conversion_rate") is not None else "缺失"
        lines.append(
            f"| {row['unit_name']} | {plan_stage.get(row['unit_id'], '缺失')} | {row['active_days']} | {_fmt(row['spend'])} | "
            f"{_fmt(row['leads'])} | {_fmt(row['lead_cost'])} | {ctr} | {cvr} |"
        )

    lines.extend([
        "",
        "## 6. 素材汇总（截至昨日，按消耗排序）",
        "| 素材 | 阶段 | 消耗 | 留资量 | 单个留资成本 | CTR | 留资转化率 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    material_stage = {item["object_id"]: item["stage"] for item in report.get("material_baselines", [])}
    for row in report["material_summary"][:20]:
        ctr = f"{row['ctr']:.2%}" if row.get("ctr") is not None else "缺失"
        cvr = f"{row['lead_conversion_rate']:.2%}" if row.get("lead_conversion_rate") is not None else "缺失"
        lines.append(
            f"| {row['material_name']} | {material_stage.get(row['material_id'], '缺失')} | {_fmt(row['spend'])} | {_fmt(row['leads'])} | "
            f"{_fmt(row['lead_cost'])} | {ctr} | {cvr} |"
        )

    lines.extend(["", "## 7. 异常和诊断"])
    if not report["anomalies"]:
        lines.append("- 当前回放规则未发现达到门槛的计划级异常。")
    else:
        for item in report["anomalies"]:
            lines.extend([
                f"- **{item['object_name']}**：{item['anomaly']}，诊断状态：{item['diagnosis_status']}，建议：{item['action']}。",
                f"  - 候选根因：{'、'.join(item['candidate_causes'])}",
                f"  - 证据：{'；'.join(item['evidence'])}",
                f"  - 缺失证据：{'、'.join(item['missing_evidence'])}",
            ])

    lines.extend(["", "## 8. 数据限制", *[f"- {note}" for note in report["notes"]]])
    if report.get("history_context"):
        history = report["history_context"]
        lines.extend(["", "## 9. 历史覆盖与近期状态", "",
                      f"- 首次采集：{history['first_observed_date']}；累计有数据：{history['observed_account_days']} 天。",
                      "- 未满连续3日或未达到曝光/点击门槛时，仅观察，不据此判断计划生死。", "",
                      "| 对象 | 当日观察状态 | 近3日有记录天数 | 此前7日有记录天数 | 最后消耗日期 |",
                      "|---|---|---:|---:|---|"])
        names = {str(row["unit_id"]): row["unit_name"] for row in report["plan_summary"]}
        names.update({str(row["material_id"]): row["material_name"] for row in report["material_summary"]})
        labels = {"spending": "有消耗", "no_spend": "有记录无消耗", "not_observed": "当天未出现", "unknown": "数据不完整"}
        for item in report.get("plan_baselines", []) + report.get("material_baselines", []):
            windows = item["windows"]
            lines.append(f"| {names.get(item['object_id'], item['object_id'])} | {labels.get(item['observed_status'], item['observed_status'])} | {windows['3d']['observed_days']}/3 | {windows['previous_7d']['observed_days']}/7 | {item['last_spend_date'] or '未观察到'} |")
    feedback_context = report.get("execution_feedback_context") or {}
    if feedback_context.get("items"):
        lines.extend(["", "## 10. 昨日建议执行反馈"])
        for item in feedback_context["items"]:
            result_parts = [part for part in [item.get("result_after_1d"), item.get("result_after_3d")] if part]
            result = "；".join(result_parts) if result_parts else "未填写结果"
            lines.append(
                f"- {item.get('recommendation_id')}：{item.get('conclusion')} "
                f"执行状态={item.get('execution_status') or '未填'}；结果={result}"
            )
    pending_actions = report.get("pending_execution_actions") or []
    if pending_actions:
        lines.extend(["", "## 11. 待确认投放动作"])
        for item in pending_actions:
            lines.append(
                f"- **{item['object_name']}**：{item['operation']}；状态：待你确认。"
                f" {item['live_value_requirement']}"
            )
    return "\n".join(lines) + "\n"
