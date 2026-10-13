"""Combine platform plan/material evidence with Shimo lead-quality evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "month_review" / "2026-09"


def money(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f} 元"


def main() -> None:
    platform = json.loads((OUT / "platform_review" / "replay_report.json").read_text(encoding="utf-8"))
    shimo = json.loads((OUT / "shimo_quality.json").read_text(encoding="utf-8"))["rows"]
    quality_days = [row for row in shimo if row["valid_leads"] is not None]
    quality_spend = sum(float(row["spend"] or 0) for row in quality_days)
    quality_platform_leads = sum(int(row["platform_leads"] or 0) for row in quality_days)
    valid_leads = sum(int(row["valid_leads"] or 0) for row in quality_days)
    total = platform["reconciliation"]["unit_totals"]
    plans = platform["plan_summary"]
    current_ids = {str(row["unit_id"]) for row in platform["daily_plan_metrics"] if row["data_date"] == platform["data_date"] and float(row["spend"] or 0) > 0}
    current_plans = [row for row in plans if str(row["unit_id"]) in current_ids]
    # Eligible pause evidence must be a current, exact 3-calendar-day sequence.
    latest_three = {"2026-09-13", "2026-09-14", "2026-09-15"}
    actions: list[dict[str, Any]] = []
    for plan in current_plans:
        daily = [row for row in platform["daily_plan_metrics"] if str(row["unit_id"]) == str(plan["unit_id"]) and row["data_date"] in latest_three]
        if {row["data_date"] for row in daily} == latest_three and all(float(row["spend"] or 0) > 0 and int(row["leads"] or 0) == 0 for row in daily):
            actions.append({"operation": "pause_plan", "unit_id": plan["unit_id"], "unit_name": plan["unit_name"], "evidence": daily})
    result: dict[str, Any] = {
        "period": "2026-09-01 to 2026-09-15",
        "sources": {
            "platform": "巨量导出的单元/素材分日明细（计划、素材、消耗、平台留资）",
            "shimo": "石墨工作表1（有效留资、已对接等人工质量字段）",
        },
        "account": {
            "platform_spend": total["spend"], "platform_leads": int(total["leads"]),
            "platform_cpl": round(total["spend"] / total["leads"], 2),
            "quality_covered_days": [row["data_date"] for row in quality_days],
            "quality_covered_spend": quality_spend, "quality_covered_platform_leads": quality_platform_leads,
            "valid_leads": valid_leads,
            "valid_rate_on_covered_platform_leads": round(valid_leads / quality_platform_leads, 4) if quality_platform_leads else None,
            "valid_cpl_on_covered_spend": round(quality_spend / valid_leads, 2) if valid_leads else None,
            "contacted_leads_status": "石墨 9 月 1–15 日均未填写，不能计算对接成本",
        },
        "current_plans": current_plans,
        "actionable_candidates": actions,
        "non_actionable_findings": [
            "9.4 深转：巨量近三个投放日转化率下降、单个留资成本上升；但 9 月15日仍有平台留资，且石墨质量无法归因到该计划，列为承接/跟进核查，不给预算或暂停候选。",
            "9.14-浅层-1-4：仅投放两日，累计平台留资1条；样本不足，保持观察。",
            "8-28深转、9.3 素材1-5 浅层、9.3 素材6-10 浅层、9.8深转：近期已无消耗，不能把历史表现转换为当前预算或暂停动作，需先读实时状态。",
        ],
    }
    (OUT / "combined_review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 2026年9月本地推跨天复盘（巨量 + 石墨）", "",
        "## 数据口径", "",
        "- 巨量：计划、素材、消耗、点击与平台留资。", "- 石墨：有效留资与已对接等人工质量字段。", "",
        "## 账户质量判断", "",
        f"- 9月1–15日巨量累计消耗 {money(total['spend'])}，平台留资 {int(total['leads'])} 条，平台留资成本 {money(total['spend'] / total['leads'])}。",
        f"- 石墨已填写有效留资的日期覆盖 {len(quality_days)} 天，对应消耗 {money(quality_spend)}、平台留资 {quality_platform_leads} 条、有效留资 {valid_leads} 条；覆盖期有效率 {valid_leads / quality_platform_leads:.1%}，有效留资成本 {money(quality_spend / valid_leads)}。",
        "- 已对接字段均未填，当前不能判断对接率、对接成本或计划级线索质量。", "",
        "## 当前计划判断", "",
    ]
    for plan in current_plans:
        lines.append(f"- {plan['unit_name']}：活跃 {plan['active_days']} 天，累计消耗 {money(plan['spend'])}，平台留资 {plan['leads']} 条，平台留资成本 {money(plan['lead_cost'])}。")
    lines.extend(["", "## 预算/暂停候选", ""])
    if actions:
        lines.extend(f"- {row['unit_name']}：满足连续三日有消耗且平台留资为0，可准备暂停动作；仍需读取实时状态并逐条确认。" for row in actions)
    else:
        lines.append("- **没有可执行候选。** 当前两个有消耗计划均不满足“连续三个自然日每天有消耗且每天平台留资为0”的暂停规则；也没有仅凭石墨质量数据即可安全绑定到某一计划的降预算候选。")
    lines.extend(["", "## 需要继续核查", ""])
    lines.extend(f"- {item}" for item in result["non_actionable_findings"])
    (OUT / "combined_review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "success", "output": str(OUT / "combined_review.md"), "actionable_candidate_count": len(actions)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
