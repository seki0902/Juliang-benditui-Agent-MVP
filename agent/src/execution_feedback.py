"""Turn human recommendation feedback into conservative diagnostic context."""
from __future__ import annotations

from typing import Any


def _assessment(status: str, result_1d: str, result_3d: str) -> tuple[str, str]:
    text = " ".join([status, result_1d, result_3d]).lower()
    if not status:
        return "pending", "未填写执行状态，不能评价建议。"
    if "未执行" in text or "未操作" in text:
        return "not_executed", "建议未执行，不能用后续数据评价建议效果。"
    if "无改善" in text or "未改善" in text or "变差" in text or "恶化" in text:
        return "not_improved", "人工反馈显示执行后未改善，不重复同类动作，应先复核根因。"
    if "改善" in text or "好转" in text or "有效" in text:
        return "improved", "人工反馈显示执行后有改善，仍需结合后续数据观察。"
    if "已执行" in text or "执行" in text:
        return "awaiting_result", "已执行，但尚无明确效果结论，继续观察窗口。"
    return "unclassified", "执行状态未能标准化识别，保留人工原文，不据此自动下结论。"


def build_execution_feedback_context(feedback_rows: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    """Return display-ready feedback context without inferring missing outcomes."""
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in feedback_rows[:limit]:
        observed = row.get("observed_result") or {}
        status = str(row.get("execution_status") or "").strip()
        result_1d = str(observed.get("result_after_1d") or "").strip()
        result_3d = str(observed.get("result_after_3d") or "").strip()
        assessment, conclusion = _assessment(status, result_1d, result_3d)
        counts[assessment] = counts.get(assessment, 0) + 1
        items.append({
            "recommendation_id": row.get("recommendation_id"),
            "feedback_date": observed.get("data_date"),
            "execution_status": status,
            "actual_action": row.get("actual_action"),
            "result_after_1d": result_1d,
            "result_after_3d": result_3d,
            "notes": observed.get("notes"),
            "assessment": assessment,
            "conclusion": conclusion,
        })
    return {"feedback_count": len(items), "assessment_counts": counts, "items": items}
