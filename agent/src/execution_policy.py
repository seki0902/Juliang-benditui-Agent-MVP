"""Build confirmation-gated, bounded execution actions from daily evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from execution_controller import pause_evidence


ROOT = Path(__file__).resolve().parents[1]


def load_execution_policy(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "execution_policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("execution_mode") != "confirmation_required":
        raise ValueError("当前执行策略必须为 confirmation_required")
    if not 0 < float(policy["max_budget_change_ratio"]) <= 0.1:
        raise ValueError("单次预算调整上限必须在 0 到 10% 之间")
    return policy


def _recommendation_by_object(recommendations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("object_id")): item for item in recommendations
        if item.get("object_type") == "plan"
    }


def build_pending_execution_actions(report: dict[str, Any], recommendations: list[dict[str, Any]],
                                    policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Create actions that can only be executed after a separate confirmation.

    Budget values are intentionally left unresolved. The executor must read the
    current live value and resolve the exact target BEFORE asking the user
    to confirm this action.
    """
    if report.get('reconciliation', {}).get('all_pass') is not True:
        return []
    # The current performance import is keyed by unit ID, while the verified
    # budget and on/off controls live on the project page.  A matching name is
    # not evidence that those two IDs refer to the same mutable object.
    # Until a formal report-derived mapping is available, diagnostics may still
    # be shown but no execution candidate is allowed to escape this layer.
    project_targets = report.get('execution_project_targets')
    recommendation_by_object = _recommendation_by_object(recommendations)
    evidence_rows = report.get('execution_plan_evidence', [])
    for row in report.get('plan_summary', []):
        object_id = str(row['unit_id'])
        daily = [r for r in evidence_rows if str(r.get('unit_id')) == object_id]
        if pause_evidence(daily, report['data_date']):
            pause_recommendation = {
                'recommendation_id': f"pause_{report['data_date']}_{object_id}",
                'priority': 'P0', 'object_type': 'plan', 'object_id': object_id,
                'status': 'pending',
                'action': 'CONTROL_RISK', 'source_rule_action': 'pause_after_three_zero_lead_days',
                'object_name': row['unit_name'],
                'evidence': ['连续三个自然日每天消耗>0且每天平台留资=0'],
                'candidate_causes': ['three_consecutive_spend_days_without_platform_leads'],
                'missing_evidence': [],
                'magnitude': '暂停前需逐条人工确认',
                'purpose': '满足三日零平台留资规则，可提出暂停候选。',
                'observation_window': '连续三个自然日',
            }
            recommendation_by_object[object_id] = pause_recommendation
            # Keep the recommendation, Shimo sync, feedback and execution
            # candidate on one stable ID instead of creating an orphan action.
            if not any(item.get('recommendation_id') == pause_recommendation['recommendation_id']
                       for item in recommendations):
                recommendations.append(pause_recommendation)
    baselines = {str(item["object_id"]): item for item in report.get("plan_baselines", [])}
    plan_names = {str(item["unit_id"]): item["unit_name"] for item in report.get("plan_summary", [])}
    actions: list[dict[str, Any]] = []
    for object_id, recommendation in recommendation_by_object.items():
        if recommendation.get("action") != "CONTROL_RISK":
            continue
        target = project_targets.get(object_id) if isinstance(project_targets, dict) else None
        if not isinstance(target, dict) or not target.get('project_id') or not target.get('project_name'):
            continue
        baseline = baselines.get(object_id)
        recent = ((baseline or {}).get("windows") or {}).get("3d") or {}
        daily = [r for r in evidence_rows if str(r.get('unit_id')) == object_id]
        operation = 'pause_plan' if pause_evidence(daily, report['data_date']) else 'decrease_budget'
        actions.append({
            "action_id": f"exec_{recommendation['recommendation_id']}",
            "recommendation_id": recommendation["recommendation_id"],
            "data_date": report["data_date"],
            "account_id": report.get('account_id'),
            "reconciliation_pass": True,
            "daily_evidence": daily,
            "single_day_anomaly": operation != 'pause_plan',
            "object_type": "plan",
            "object_id": object_id,
            "object_name": plan_names.get(object_id) or recommendation.get("object_name") or object_id,
            "execution_target": {
                "object_type": "project",
                "object_id": str(target['project_id']),
                "object_name": str(target['project_name']),
                "mapping_source": target.get('source'),
            },
            "operation": operation,
            "approval_status": "awaiting_human_confirmation",
            "execution_status": "not_executed",
            "policy": {
                "max_budget_change_ratio": policy["max_budget_change_ratio"],
                "requires_human_confirmation": True,
                "page_mismatch": policy["on_page_mismatch"],
            },
            "live_value_requirement": (
                "先读实时预算并生成不超过10%的具体降幅，展示原金额与目标金额后等待确认。"
                if operation == "decrease_budget" else
                "先读实时状态和三日证据，展示具体暂停计划后等待确认。"
            ),
            "evidence": recommendation.get("evidence") or [],
            "candidate_causes": recommendation.get("candidate_causes") or [],
        })
    return actions
