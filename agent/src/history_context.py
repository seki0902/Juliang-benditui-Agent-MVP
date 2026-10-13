"""Assemble production history for daily decisions without importing old files."""
from __future__ import annotations

from typing import Any

import pandas as pd

from baseline_engine import build_baselines
from replay_core import ReplayConfig, metric_row


def _merge(old: list[dict], new: list[dict], key: str | None = None) -> list[dict]:
    rows = {}
    for item in old + new:
        identity = (str(item["data_date"]), str(item.get(key, "account")))
        rows[identity] = dict(item)
    return sorted(rows.values(), key=lambda row: (str(row["data_date"]), str(row.get(key, ""))))


def _totals(rows: list[dict]) -> dict:
    def total(field: str) -> float | None:
        values = [row.get(field) for row in rows]
        return sum(values) if values and all(value is not None for value in values) else None

    return metric_row(*(total(key) for key in (
        "spend", "impressions", "clicks", "leads", "valid_leads", "private_messages", "contacted_leads")))


def _diagnose(baselines: list[dict], names: dict, config: ReplayConfig) -> list[dict]:
    result = []
    for item in baselines:
        recent = item["windows"]["3d"]
        previous = item["windows"]["previous_7d"]
        evidence, causes = [], []
        signals = {}
        # Only compare complete recent calendar days with a separate earlier window.
        comparable = recent["observed_days"] == 3 and previous["observed_days"] >= 3
        if comparable:
            for metric, threshold, direction, minimum, cause in (
                ("ctr", config.ctr_decline_threshold, -1, "impressions", "creative_or_audience"),
                ("lead_conversion_rate", config.conversion_decline_threshold, -1, "clicks", "funnel_or_tracking"),
                ("lead_cost", config.cost_increase_threshold, 1, "clicks", "delivery_or_funnel"),
            ):
                floor = config.min_impressions if minimum == "impressions" else config.min_clicks
                current, baseline = recent.get(metric), previous.get(metric)
                if min(recent[minimum], previous[minimum]) < floor or current is None or not baseline:
                    continue
                change = current / baseline - 1
                signals[metric + "_change"] = round(change, 6)
                if change * direction >= threshold:
                    evidence.append(f"近3个自然日 {metric} 相对之前7日窗口变化 {change:+.1%}")
                    causes.append(cause)
        if recent["observed_days"] == 3 and recent["spend"] >= config.min_spend and recent["clicks"] >= config.min_clicks and recent["leads"] == 0:
            evidence.append(f"连续3个自然日消耗 {recent['spend']:.2f} 元、点击 {recent['clicks']} 次，平台留资为0")
            causes.append("funnel_or_tracking")
        if item["consecutive_no_spend_days"] >= 2:
            evidence.append(f"连续 {item['consecutive_no_spend_days']} 天有记录但无消耗；不能据此认定后台暂停")
            causes.append("status_or_delivery")
        if not evidence:
            continue
        result.append({
            "object_type": item["object_type"], "object_id": item["object_id"],
            "object_name": names.get(item["object_id"]) or item["object_id"],
            "anomaly": "recent_window_change", "diagnosis_status": "possible",
            "evidence": evidence, "signals": signals, "candidate_causes": list(dict.fromkeys(causes)),
            "missing_evidence": ["budget_changes", "bid_changes", "schedule_changes", "platform_status", "frequency", "valid_leads", "contacted_leads"],
            "action": "check_data", "observation_window": "24小时核查，2–3天复盘",
            "requires_human_approval": True,
        })
    return result


def enrich_with_history(report: dict[str, Any], history: dict[str, list[dict]]) -> dict[str, Any]:
    """Enrich diagnostics, leaving current-day save payloads and reconciliation intact."""
    result = dict(report)
    as_of = pd.Timestamp(report["data_date"])
    current_account = [{**report["yesterday"], "data_date": report["data_date"]}] if report.get("yesterday") else []
    accounts = _merge(history["account"], current_account)
    complete_dates = {row["data_date"] for row in accounts}
    result["history_context"] = {
        "source": "successful_account_runs", "observed_account_days": len(complete_dates),
        "first_observed_date": min(complete_dates) if complete_dates else None,
        "as_of_date": report["data_date"], "creation_dates_known": False,
    }
    result["account_baselines"] = build_baselines(
        pd.DataFrame([{**row, "account_id": "account"} for row in accounts]), "account_id", as_of)
    month_dates = {day.date().isoformat() for day in pd.date_range(as_of.replace(day=1), as_of)}
    missing = sorted(month_dates - complete_dates)
    month_rows = [row for row in accounts if row["data_date"] in month_dates]
    result["mtd"] = {
        "mtd_start_date": as_of.replace(day=1).date().isoformat(), "mtd_end_date": report["data_date"],
        **_totals(month_rows if not missing else []), "missing_dates": missing,
        "data_status": "missing_platform_days" if missing else (
            "missing_manual_feedback" if any(row.get("valid_leads") is None or row.get("contacted_leads") is None for row in month_rows) else "available"),
    }
    anomalies = []
    plan_rows = []
    for kind, id_key, name_key, daily_key, summary_key, baseline_key in (
        ("plan", "unit_id", "unit_name", "daily_plan_metrics", "plan_summary", "plan_baselines"),
        ("material", "material_id", "material_name", "daily_material_metrics", "material_summary", "material_baselines"),
    ):
        names = {str(row[id_key]): row[name_key] for row in report[summary_key]}
        rows = _merge(history[kind], report[daily_key], id_key)
        if kind == "plan":
            plan_rows = rows
        for row in rows:
            if row.get(name_key):
                names.setdefault(str(row[id_key]), row[name_key])
        if not rows:
            result[baseline_key] = []
            continue
        frame = pd.DataFrame(rows)
        baselines = build_baselines(frame, id_key, as_of)
        result[baseline_key] = baselines
        summaries = []
        for entity_id, group in frame.groupby(id_key):
            summaries.append({
                **_totals(group.to_dict("records")), id_key: str(entity_id),
                name_key: names.get(str(entity_id)) or str(entity_id),
                "active_days": int(group.loc[group["spend"] > 0, "data_date"].nunique()),
            })
        result[summary_key] = sorted(summaries, key=lambda row: row["spend"] or 0, reverse=True)
        anomalies.extend(_diagnose(baselines, names, ReplayConfig()))
    plan_names = {row["unit_id"]: row["unit_name"] for row in result["plan_summary"]}
    result["plan_timeline"] = [
        {"data_date": day, "active_units": [plan_names.get(str(row["unit_id"]), str(row["unit_id"]))
                                              for row in plan_rows if row["data_date"] == day and row["spend"] > 0]}
        for day in sorted(complete_dates)
    ]
    if not report["reconciliation"]["all_pass"]:
        anomalies = [{"object_type": "account", "object_id": "account", "object_name": "账户",
                      "anomaly": "reconciliation_failed", "diagnosis_status": "insufficient_data",
                      "evidence": ["当前单元与素材报表总量不一致，暂停效果归因"],
                      "candidate_causes": ["data_scope_mismatch"], "missing_evidence": ["reconciled_exports"],
                      "action": "check_data", "observation_window": "补齐数据后"}]
    result["anomalies"] = anomalies
    result["execution_plan_evidence"] = plan_rows
    result["notes"] = [*report.get("notes", []),
                       "历史仅来自当前账户成功的正式运行，不自动读取旧文件或回放数据。",
                       "阶段描述已观察到的轨迹，不等于计划创建时间；缺日不补零，未出现不代表后台暂停。"]
    return result
