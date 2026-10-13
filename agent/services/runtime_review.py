"""Collect offline historical runs and prepare an honest Step 19.4 review packet.

The existing source repository is read-only; all artifacts go to --output.
Structural checks never certify semantic boundaries or model autonomy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


RESULT_FIELDS = {
    "supervisor": "supervisor_decision",
    "data_analysis": "data_analysis_result",
    "ads_diagnosis": "ads_diagnosis_result",
    "content_review": "content_review_result",
    "strategy": "strategy_result",
}


def json_safe(value):
    if hasattr(value, "model_dump"):
        return json_safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def inspect_state(state, models):
    """Fail closed on incomplete evidence; validate existing output contracts."""
    state = json_safe(state)
    issues = []
    if not state.get("final_report"):
        issues.append("missing final report")
    if state.get("errors"):
        issues.append("run contains errors")
    if (state.get("push_result") or {}).get("status") != "disabled":
        issues.append("push is not explicitly disabled")
    routes = state.get("route_trace") or []
    if not routes or routes[-1].get("to", routes[-1].get("to_agent")) != "finish":
        issues.append("missing terminal finish route")
    trace = state.get("agent_trace") or []
    observed = {item.get("agent", "").removesuffix("_agent") for item in trace}
    if not trace:
        issues.append("missing agent trace")
    for route in routes:
        target = route.get("to", route.get("to_agent"))
        if target != "finish" and target not in observed:
            issues.append(f"routed agent missing from trace: {target}")
    for agent, field in RESULT_FIELDS.items():
        result = state.get(field)
        if agent in observed and result is None:
            issues.append(f"{agent}: trace has no corresponding result")
        if result is not None:
            if agent not in observed:
                issues.append(f"{agent}: result has no corresponding trace")
            try:
                models[agent].model_validate(result)
            except Exception as exc:
                issues.append(f"{agent}: invalid output contract ({type(exc).__name__})")
    allowed_common = {"evidence", "open_questions", "tool_trace", "errors",
                      "agent_call_counts", "total_agent_calls", "agent_trace", "route_trace"}
    for item in trace:
        agent = item.get("agent", "").removesuffix("_agent")
        if item.get("status") not in {"ok", "success", "succeeded"} or item.get("error"):
            issues.append(f"{agent}: unsuccessful agent call")
        if agent not in RESULT_FIELDS:
            issues.append(f"unknown traced agent: {agent}")
            continue
        fields = (item.get("output_schema") or {}).get("fields")
        if not isinstance(fields, list) or not fields:
            issues.append(f"{agent}: missing output field trace")
            continue
        allowed = allowed_common | {RESULT_FIELDS[agent]}
        if agent == "supervisor":
            allowed |= {"next_agent", "supervisor_reason"}
        for field in fields:
            if field not in allowed:
                issues.append(f"{agent}: unexpected state write {field}")
    for item in state.get("tool_trace") or []:
        if item.get("status") not in {"ok", "partial", "unavailable", "not_found"} or item.get("error"):
            issues.append(f"tool error: {item.get('tool_name')}")
    return {
        "structural_status": "fail" if issues else "pass",
        "issues": sorted(set(issues)),
        "agent_coverage": {name: name in observed for name in RESULT_FIELDS},
        "semantic_review": {name: "pending" if name in observed else "not_observed"
                            for name in RESULT_FIELDS},
        "model_boundary_status": "not_evaluated",
    }

