"""Deterministic, human-approved action suggestions.

The rule layer supplies evidence. This module only translates that evidence
into the stable recommendation contract used by the report and state store;
it never calls an ad-platform write API.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


ACTION_MAP = {
    "check_data": ("CHECK_DATA", "P1", "先核对数据、预算、出价、时段和后台状态，再判断异常原因。"),
    "control_risk": ("CONTROL_RISK", "P0", "先核对计划状态、预算、出价和回传，再由运营决定是否暂停或降预算。"),
    "check_status_or_control": ("CONTROL_RISK", "P1", "核对计划是否被暂停、预算是否耗尽或投放是否断量；确认后再决定人工控制风险。"),
    "inspect_handling": ("CHECK_FOLLOW_UP", "P1", "检查表单、承接页、销售跟进和数据回传，不直接把点击层问题归因给素材。"),
    "replace_or_test_material": ("STOP_OR_REPLACE", "P1", "先保留原素材证据，人工确认后补充新素材测试，不直接删除或替换线上素材。"),
    "observe": ("HOLD", "P2", "保持观察，补充样本和缺失证据后再判断是否调整。"),
}


def _recommendation_id(run_key: str, item: dict[str, Any]) -> str:
    raw = json.dumps(
        [run_key, item.get("object_type"), item.get("object_id"), item.get("action")],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "rec_" + hashlib.sha1(raw).hexdigest()[:16]


def build_recommendations(report: dict[str, Any], run_key: str) -> list[dict[str, Any]]:
    """Build stable suggestions from report anomalies.

    Every result explicitly requires human approval, includes the evidence
    that led to it, and provides an observation window. Missing evidence keeps
    the result at HOLD or CHECK_DATA rather than forcing a stop.
    """
    recommendations: list[dict[str, Any]] = []
    for anomaly in report.get("anomalies", []):
        source_action = str(anomaly.get("action") or "observe")
        action, priority, purpose = ACTION_MAP.get(source_action, ACTION_MAP["observe"])
        missing = list(anomaly.get("missing_evidence") or [])
        if missing and action == "STOP_OR_REPLACE":
            action = "CHECK_DATA"
            priority = "P1"
            purpose = "关键证据缺失，先补齐数据再决定是否替换素材。"
        recommendation = {
            "recommendation_id": _recommendation_id(run_key, anomaly),
            "priority": priority,
            "object_type": anomaly.get("object_type"),
            "object_id": anomaly.get("object_id"),
            "object_name": anomaly.get("object_name"),
            "action": action,
            "source_rule_action": source_action,
            "magnitude": "不涉及预算/出价幅度" if action in {"HOLD", "CHECK_DATA", "CHECK_FOLLOW_UP"} else "待补齐证据后人工确定",
            "evidence": list(anomaly.get("evidence") or []),
            "candidate_causes": list(anomaly.get("candidate_causes") or []),
            "missing_evidence": missing,
            "purpose": purpose,
            "observation_window": anomaly.get("observation_window") or "48h",
            "requires_human_approval": True,
            "status": "generated",
        }
        recommendations.append(recommendation)
    return sorted(
        recommendations,
        key=lambda item: (item["priority"], str(item.get("object_name") or "")),
    )


def render_recommendations_markdown(recommendations: list[dict[str, Any]]) -> str:
    lines = [
        "## 今日执行建议",
        "",
        "系统只提供建议，不直接修改投放后台。每条建议都需要运营人员人工确认。",
        "",
        "| 优先级 | 对象 | 动作 | 调整幅度 | 观察窗口 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    if not recommendations:
        lines.append("| - | - | 当前没有达到规则门槛的动作建议 | - | - | 无 |")
    for item in recommendations:
        lines.append(
            f"| {item['priority']} | {item.get('object_name') or item.get('object_id')} | "
            f"{item['action']} | {item['magnitude']} | {item['observation_window']} | 待人工确认 |"
        )
        lines.append("")
        lines.append(f"- **{item.get('object_name') or item.get('object_id')}**：{item['purpose']}")
        if item["evidence"]:
            lines.append(f"  - 证据：{'；'.join(map(str, item['evidence']))}")
        if item["candidate_causes"]:
            lines.append(f"  - 候选根因：{'、'.join(map(str, item['candidate_causes']))}")
        if item["missing_evidence"]:
            lines.append(f"  - 缺失证据：{'、'.join(map(str, item['missing_evidence']))}")
    return "\n".join(lines).rstrip() + "\n"
