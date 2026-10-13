"""Read-only historical E2E dry run for local exported reports.

This module connects the existing deterministic replay/import path to the
technical Multi-Agent Graph without touching a platform backend or a delivery
channel.  Its default Graph factory is deliberately a replay adapter: it
calls the real read-only metrics Tools over the exported XLSX files and
returns validated Agent results, so the E2E handoff can be tested offline.
Model-backed runtime nodes can be supplied through ``graph_factory`` later.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from graph import build_graph
from run_import import discover_exports, run_import
from schemas.ads_diagnosis import (
    AdsDiagnosisResult,
    DiagnosisCertainty,
    DiagnosisItem,
    EvidenceRequest,
    SuspectedCause,
    UncertaintyItem,
)
from schemas.common import (
    AgentName,
    ConfidenceLevel,
    EvidenceItem,
    OpenQuestion,
)
from schemas.data_analysis import (
    AnalysisFinding,
    DataAnalysisResult,
    DiagnosisTarget,
    MissingDataItem,
)
from schemas.strategy import ObservationItem, PriorityAction, StrategyResult
from schemas.supervisor import SupervisorDecision
from services.account_metrics_tool import AccountMetricsTool
from services.material_metrics_tool import MaterialMetricsTool
from services.metrics_tool_utils import normalise_id
from services.plan_metrics_tool import PlanMetricsTool
from services.tool_protocol import normalise_tool_result
from services.tool_trace import make_tool_call_record, safe_tool_error


GraphFactory = Callable[["HistoricalReplayInput"], Any]


@dataclass(frozen=True)
class HistoricalReplayInput:
    """One account's real local exports plus its deterministic replay result."""

    account_key: str
    source_label: str
    input_dir: Path
    unit_path: Path
    material_path: Path
    replay_output_dir: Path
    replay_report: dict[str, Any]


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mapping_value(value: Any, *keys: str) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
    for key in keys:
        if hasattr(value, key):
            return getattr(value, key)
    return None


def _tool_call(
    *,
    agent: AgentName,
    tool_name: str,
    tool: Callable[..., Mapping[str, Any]],
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], Any, str | None]:
    started_ns = perf_counter_ns()
    try:
        raw_result = tool(**dict(arguments))
        result = normalise_tool_result(
            raw_result,
            default_source="historical_replay",
            default_time_window=arguments.get("time_window"),
        )
    except Exception as exc:
        result = normalise_tool_result(
            {
                "status": "error",
                "source": "historical_replay",
                "time_window": arguments.get("time_window"),
                "data": {},
                "warnings": [f"{type(exc).__name__}: {exc}"],
            },
            default_source="historical_replay",
            default_time_window=arguments.get("time_window"),
        )
    error = safe_tool_error(tool_name, result)
    record = make_tool_call_record(
        agent=agent,
        tool_name=tool_name,
        call_id=f"historical-{agent.value}-{tool_name}",
        arguments=arguments,
        status=str(result.get("status") or "ok"),
        result=result,
        duration_ms=(perf_counter_ns() - started_ns) / 1_000_000,
        error=error,
    )
    return result, record, error


def _metric_evidence(
    *,
    source: str,
    object_type: str,
    object_id: str | None,
    result: Mapping[str, Any],
    time_window: str,
) -> EvidenceItem:
    metric_data = result.get("data")
    if not isinstance(metric_data, Mapping):
        metric_data = {
            key: result.get(key)
            for key in (
                "spend",
                "impressions",
                "clicks",
                "ctr",
                "leads",
                "lead_cost",
                "valid_leads",
                "valid_lead_rate",
                "valid_lead_cost",
            )
            if key in result
        }
    return EvidenceItem(
        evidence_type="historical_replay_metric",
        source=source,
        object_type=object_type,
        object_id=object_id,
        metric_or_content="3d_metrics",
        value=dict(metric_data),
        time_range=time_window,
        note="Read-only replay Tool result; quality fields remain unavailable when no feedback export exists.",
    )


def _has_id_in_window(
    items: Any,
    *,
    id_key: str,
    object_id: str | None,
    report_date: str,
    window_days: int = 3,
) -> bool:
    if not object_id or not isinstance(items, list):
        return False
    end = report_date
    try:
        end_day = date.fromisoformat(report_date)
    except ValueError:
        return False
    start = (end_day - timedelta(days=window_days - 1)).isoformat()
    target = normalise_id(object_id)
    return any(
        isinstance(item, Mapping)
        and normalise_id(item.get(id_key)) == target
        and start <= str(item.get("data_date") or "") <= end
        for item in items
    )


def _recent_id(
    items: Any,
    *,
    id_key: str,
    report_date: str,
    window_days: int = 3,
) -> str | None:
    """Choose an actually observed recent object for a follow-up Tool call."""

    if not isinstance(items, list):
        return None
    try:
        start = (
            date.fromisoformat(report_date) - timedelta(days=window_days - 1)
        ).isoformat()
    except ValueError:
        return None
    candidates: list[tuple[str, int, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        object_id = normalise_id(item.get(id_key))
        data_date = str(item.get("data_date") or "")
        if not object_id or not data_date or not start <= data_date <= report_date:
            continue
        has_spend = int(float(item.get("spend") or 0) > 0)
        candidates.append((data_date, has_spend, object_id))
    if not candidates:
        return None
    return max(candidates)[2]


def _route_targets(case: HistoricalReplayInput) -> list[str]:
    """Choose a technical dry-run route from replay integrity, not spend edits."""

    reconciliation = case.replay_report.get("reconciliation") or {}
    if reconciliation.get("all_pass") is False:
        return [
            AgentName.DATA_ANALYSIS.value,
            AgentName.ADS_DIAGNOSIS.value,
            AgentName.STRATEGY.value,
            AgentName.FINISH.value,
        ]
    return [
        AgentName.DATA_ANALYSIS.value,
        AgentName.STRATEGY.value,
        AgentName.FINISH.value,
    ]


def build_historical_replay_graph(case: HistoricalReplayInput):
    """Build the default no-network Graph adapter for one replay input."""

    report = case.replay_report
    report_date = str(report["data_date"])
    account_tool = AccountMetricsTool.from_report_files(case.unit_path, case.material_path)
    plan_tool = PlanMetricsTool.from_report_files(case.unit_path, case.material_path)
    material_tool = MaterialMetricsTool.from_report_files(
        case.unit_path,
        case.material_path,
    )
    targets = _route_targets(case)
    supervisor_calls = 0

    def supervisor(_state: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal supervisor_calls
        target = targets[min(supervisor_calls, len(targets) - 1)]
        supervisor_calls += 1
        agent = AgentName(target)
        return {
            "supervisor_decision": SupervisorDecision(
                next_agent=agent,
                task=f"historical replay handoff to {target}",
                reason=(
                    "replay reconciliation requires layered diagnosis"
                    if target == AgentName.ADS_DIAGNOSIS.value
                    else "continue the read-only historical replay flow"
                ),
                can_finish=agent is AgentName.FINISH,
            )
        }

    def data_analysis(_state: Mapping[str, Any]) -> dict[str, Any]:
        arguments = {"report_date": report_date, "time_window": "3d"}
        account_result, tool_record, tool_error = _tool_call(
            agent=AgentName.DATA_ANALYSIS,
            tool_name="get_account_metrics",
            tool=account_tool.get_account_metrics,
            arguments=arguments,
        )
        evidence = [
            _metric_evidence(
                source="historical_replay.account_metrics",
                object_type="account",
                object_id=case.account_key,
                result=account_result,
                time_window="3d",
            )
        ]
        observations: list[AnalysisFinding] = []
        for anomaly in (report.get("anomalies") or [])[:5]:
            if not isinstance(anomaly, Mapping):
                continue
            anomaly_evidence = EvidenceItem(
                evidence_type="historical_replay_anomaly",
                source="replay_core.diagnose_units",
                object_type=str(anomaly.get("object_type") or "unit"),
                object_id=normalise_id(anomaly.get("object_id")) or None,
                metric_or_content="anomaly",
                value={
                    "anomaly": anomaly.get("anomaly"),
                    "signals": anomaly.get("signals"),
                    "evidence": anomaly.get("evidence"),
                },
                time_range=str(anomaly.get("observation_window") or "historical"),
                note="Replay rule evidence; not a backend action.",
            )
            evidence.append(anomaly_evidence)
            observations.append(
                AnalysisFinding(
                    description=str(anomaly.get("anomaly") or "historical anomaly"),
                    object_type=str(anomaly.get("object_type") or "unit"),
                    object_id=normalise_id(anomaly.get("object_id")) or None,
                    evidence=[anomaly_evidence],
                    confidence=ConfidenceLevel.MEDIUM,
                )
            )

        missing_fields = list(report.get("missing_fields") or [])
        missing_data = [
            MissingDataItem(
                field=str(field),
                reason="historical export has no corresponding quality/follow-up field",
                impact="cannot conclude effective lead quality or close-stage ROI",
                requested_from="manual feedback export",
            )
            for field in missing_fields
        ]
        reconciliation = report.get("reconciliation") or {}
        if reconciliation.get("all_pass") is False:
            missing_data.append(
                MissingDataItem(
                    field="unit_material_reconciliation",
                    reason="unit and material export totals do not reconcile",
                    impact="material-level attribution and downstream diagnosis need review",
                    requested_from="export scope or plan-material relation check",
                )
            )

        first_anomaly = next(
            (item for item in (report.get("anomalies") or []) if isinstance(item, Mapping)),
            None,
        )
        targets_for_diagnosis = []
        if first_anomaly is not None:
            targets_for_diagnosis.append(
                DiagnosisTarget(
                    question="verify the replay anomaly with plan-level metrics",
                    object_type=str(first_anomaly.get("object_type") or "unit"),
                    object_id=normalise_id(first_anomaly.get("object_id")) or None,
                )
            )
        summary = (
            f"Historical replay loaded {case.account_key} for {report_date}; "
            f"{len(report.get('anomalies') or [])} replay anomalies were retained."
        )
        update: dict[str, Any] = {
            "data_analysis_result": DataAnalysisResult(
                summary=summary,
                observations=observations,
                anomalies=[],
                targets_for_diagnosis=targets_for_diagnosis,
                missing_data=missing_data,
                evidence=evidence,
                confidence=(
                    ConfidenceLevel.LOW
                    if missing_data or tool_error
                    else ConfidenceLevel.MEDIUM
                ),
            ),
            "evidence": evidence,
            "tool_trace": [tool_record],
        }
        if tool_error:
            update["errors"] = [tool_error]
        return update

    def ads_diagnosis(state: Mapping[str, Any]) -> dict[str, Any]:
        data_result = state.get("data_analysis_result")
        target = None
        if data_result is not None:
            target_items = _mapping_value(data_result, "targets_for_diagnosis") or []
            if target_items:
                target = target_items[0]
        plan_id = normalise_id(_mapping_value(target, "object_id")) if target else ""
        plan_records = report.get("daily_plan_metrics") or []
        if not _has_id_in_window(
            plan_records,
            id_key="unit_id",
            object_id=plan_id,
            report_date=report_date,
        ):
            plan_id = _recent_id(
                plan_records,
                id_key="unit_id",
                report_date=report_date,
            ) or ""
        material_id = _recent_id(
            report.get("daily_material_metrics"),
            id_key="material_id",
            report_date=report_date,
        ) or ""

        tool_trace = []
        errors: list[str] = []
        evidence: list[EvidenceItem] = []
        diagnoses: list[DiagnosisItem] = []
        if plan_id:
            plan_result, plan_record, plan_error = _tool_call(
                agent=AgentName.ADS_DIAGNOSIS,
                tool_name="get_plan_metrics",
                tool=plan_tool.get_plan_metrics,
                arguments={
                    "plan_id": plan_id,
                    "report_date": report_date,
                    "time_window": "3d",
                },
            )
            tool_trace.append(plan_record)
            if plan_error:
                errors.append(plan_error)
            plan_evidence = _metric_evidence(
                source="historical_replay.plan_metrics",
                object_type="plan",
                object_id=plan_id,
                result=plan_result,
                time_window="3d",
            )
            evidence.append(plan_evidence)
            diagnoses.append(
                DiagnosisItem(
                    statement="Replay anomaly should be checked at plan level before any action.",
                    certainty=DiagnosisCertainty.HYPOTHESIS,
                    object_type="plan",
                    object_id=plan_id,
                    supporting_evidence=[plan_evidence],
                    confidence=ConfidenceLevel.MEDIUM,
                )
            )
        if material_id:
            material_result, material_record, material_error = _tool_call(
                agent=AgentName.ADS_DIAGNOSIS,
                tool_name="get_material_metrics",
                tool=material_tool.get_material_metrics,
                arguments={
                    "material_id": material_id,
                    "report_date": report_date,
                    "time_window": "3d",
                },
            )
            tool_trace.append(material_record)
            if material_error:
                errors.append(material_error)
            evidence.append(
                _metric_evidence(
                    source="historical_replay.material_metrics",
                    object_type="material",
                    object_id=material_id,
                    result=material_result,
                    time_window="3d",
                )
            )

        uncertainties = [
            UncertaintyItem(
                question="Are the replay anomalies still present after quality and follow-up data are added?",
                reason="The supplied account exports do not include valid-lead or contacted-lead feedback.",
                evidence_needed=["valid_leads", "contacted_leads", "follow-up timing"],
            )
        ]
        update: dict[str, Any] = {
            "ads_diagnosis_result": AdsDiagnosisResult(
                summary="Historical replay diagnosis remains a hypothesis until quality feedback and reconciliation are verified.",
                diagnoses=diagnoses,
                suspected_causes=[
                    SuspectedCause(
                        cause="delivery, lifecycle, or export-scope issue may contribute",
                        certainty=DiagnosisCertainty.HYPOTHESIS,
                        confidence=ConfidenceLevel.LOW,
                    )
                ],
                supporting_evidence=evidence,
                uncertainties=uncertainties,
                need_content_review=False,
                evidence_needed=[
                    EvidenceRequest(
                        request="provide valid-lead and follow-up feedback for the same 3-day window",
                        reason="front-end replay metrics cannot establish close-stage quality",
                        source_hint="manual feedback export",
                    )
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            "evidence": evidence,
            "open_questions": [
                OpenQuestion(
                    question=item.question,
                    reason=item.reason,
                )
                for item in uncertainties
            ],
            "tool_trace": tool_trace,
        }
        if errors:
            update["errors"] = errors
        return update

    def strategy(_state: Mapping[str, Any]) -> dict[str, Any]:
        reconciliation = report.get("reconciliation") or {}
        safe_data = reconciliation.get("all_pass") is True
        action_type = "observe" if safe_data else "control_risk"
        judgement = (
            "Hold and observe the next comparable 3-day window; no backend action is executed."
            if safe_data
            else "Control risk: reconciliation is not passed; verify export scope and quality feedback before interpreting plan or material performance."
        )
        return {
            "strategy_result": StrategyResult(
                overall_judgement=judgement,
                priority_actions=[
                    PriorityAction(
                        priority=1,
                        target_type="account",
                        target_id=case.account_key,
                        action_type=action_type,
                        reason="historical replay is read-only and quality fields are incomplete",
                        expected_signal="reconciled 3-day metrics and available quality feedback",
                        recheck_condition="re-run the same account with a comparable 3-day window",
                    )
                ],
                observation_items=[
                    ObservationItem(
                        target_type="account",
                        target_id=case.account_key,
                        observation="retain the replay evidence without converting it into an executed change",
                        recheck_condition="next comparable 3-day window",
                    )
                ],
                need_more_evidence=True,
                cannot_conclude=[
                    "effective lead quality, contacted rate, and ROI are unavailable from the supplied exports"
                ],
                confidence=ConfidenceLevel.LOW,
            ),
            "open_questions": [
                OpenQuestion(
                    question="Can the missing quality and reconciliation fields be supplied for the same window?",
                    reason="No backend action is safe while these fields remain unresolved.",
                )
            ],
            "tool_trace": [],
        }

    return build_graph(
        node_overrides={
            "supervisor": supervisor,
            "data_analysis_agent": data_analysis,
            "ads_diagnosis_agent": ads_diagnosis,
            "strategy_agent": strategy,
            "push": lambda _state: {
                "push_result": {
                    "status": "disabled",
                    "reason": "historical_e2e_dry_run",
                }
            },
        },
        max_total_calls=12,
    )


def _route_from_state(state: Mapping[str, Any]) -> list[str]:
    route = ["supervisor"]
    trace = state.get("route_trace") or []
    for index, item in enumerate(trace):
        target = _mapping_value(item, "to", "to_agent")
        if target is None:
            continue
        route.append(str(target))
        if index < len(trace) - 1:
            route.append("supervisor")
    return route


def render_historical_e2e_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Historical E2E Dry Run",
        "",
        f"status: {report.get('status')}",
        f"accounts: {report.get('account_count')}",
        "push: disabled",
        "",
        "| account | data date | replay reconciliation | graph | route |",
        "|---|---|---|---|---|",
    ]
    for item in report.get("accounts", []):
        replay = item.get("replay") or {}
        graph = item.get("graph") or {}
        lines.append(
            "| {account} | {date} | {reconciliation} | {graph_status} | {route} |".format(
                account=item.get("account_key"),
                date=item.get("data_date"),
                reconciliation=replay.get("reconciliation_pass"),
                graph_status=graph.get("status"),
                route=" -> ".join(graph.get("route") or []),
            )
        )
    lines.extend(
        [
            "",
            "The run used local replay/import and read-only metrics Tools. No backend mutation or delivery call was made.",
        ]
    )
    return "\n".join(lines) + "\n"


class HistoricalE2ERunner:
    """Run historical account exports through replay and a no-push Graph."""

    def __init__(
        self,
        input_root: str | Path,
        output_root: str | Path,
        *,
        graph_factory: GraphFactory | None = None,
        exclude_latest_date: bool = True,
    ) -> None:
        self.input_root = Path(input_root)
        self.output_root = Path(output_root)
        self.graph_factory = graph_factory or build_historical_replay_graph
        self.exclude_latest_date = exclude_latest_date

    def _input_dirs(self) -> list[Path]:
        if not self.input_root.exists():
            raise FileNotFoundError(f"historical input directory does not exist: {self.input_root}")
        if not self.input_root.is_dir():
            raise NotADirectoryError(f"historical input root is not a directory: {self.input_root}")
        root_exports = [
            path
            for path in self.input_root.iterdir()
            if path.is_file() and path.suffix.lower() in {".xlsx", ".xls", ".csv"}
        ]
        if root_exports:
            return [self.input_root]
        return sorted(path for path in self.input_root.iterdir() if path.is_dir())

    def _load_replay_input(self, account_index: int, input_dir: Path) -> HistoricalReplayInput:
        exports = discover_exports(input_dir)
        unit_path = exports.get("unit")
        material_path = exports.get("material")
        if unit_path is None or material_path is None:
            raise ValueError(f"unit/material exports are required: {input_dir}")
        account_key = f"account-{account_index:03d}"
        replay_output_dir = self.output_root / account_key / "replay"
        replay_report = run_import(
            input_dir,
            replay_output_dir,
            unit=unit_path.name,
            material=material_path.name,
            exclude_latest_date=self.exclude_latest_date,
        )
        return HistoricalReplayInput(
            account_key=account_key,
            source_label=input_dir.name,
            input_dir=input_dir,
            unit_path=unit_path,
            material_path=material_path,
            replay_output_dir=replay_output_dir,
            replay_report=replay_report,
        )

    def _initial_state(self, case: HistoricalReplayInput) -> dict[str, Any]:
        replay = case.replay_report
        reconciliation = replay.get("reconciliation") or {}
        return {
            "run_id": f"historical_e2e_{case.account_key}_{replay.get('data_date')}",
            "report_date": str(replay.get("data_date")),
            "task": "historical local-push E2E dry run",
            "account_context": {
                "account_id": case.account_key,
                "source_label": case.source_label,
                "data_source": "historical_replay",
                "source_files": [case.unit_path.name, case.material_path.name],
                "replay_report": str(case.replay_output_dir / "replay_report.json"),
                "reconciliation_pass": reconciliation.get("all_pass"),
                "data_completeness": replay.get("data_completeness"),
            },
        }

    def run(self) -> dict[str, Any]:
        self.output_root.mkdir(parents=True, exist_ok=True)
        reports: list[dict[str, Any]] = []
        for index, input_dir in enumerate(self._input_dirs(), start=1):
            case = self._load_replay_input(index, input_dir)
            account_output = self.output_root / case.account_key
            started_ns = perf_counter_ns()
            state: Mapping[str, Any] = {}
            errors: list[str] = []
            try:
                graph = self.graph_factory(case)
                invoke = getattr(graph, "invoke", None)
                if not callable(invoke):
                    raise TypeError("historical graph factory must return an invokable Graph")
                state = invoke(self._initial_state(case))
                if not isinstance(state, Mapping):
                    raise TypeError("historical Graph must return a State mapping")
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

            replay = case.replay_report
            reconciliation = replay.get("reconciliation") or {}
            graph_status = "success" if state and state.get("final_report") else "error"
            graph_output = {
                "status": graph_status,
                "route": _route_from_state(state),
                "agent_outputs": {
                    field: _json_safe(state.get(field))
                    for field in (
                        "supervisor_decision",
                        "data_analysis_result",
                        "ads_diagnosis_result",
                        "content_review_result",
                        "strategy_result",
                    )
                },
                "agent_trace": _json_safe(state.get("agent_trace") or []),
                "tool_trace": _json_safe(state.get("tool_trace") or []),
                "final_report": state.get("final_report"),
                "push_result": _json_safe(state.get("push_result")),
                "errors": [*errors, *[str(item) for item in (state.get("errors") or [])]],
                "latency_ms": (perf_counter_ns() - started_ns) / 1_000_000,
            }
            _write_json(account_output / "graph_output.json", graph_output)
            account_report = {
                "account_key": case.account_key,
                "source_label": case.source_label,
                "data_date": replay.get("data_date"),
                "source_snapshot_max_date": replay.get("source_snapshot_max_date"),
                "replay": {
                    "status": "success",
                    "output_dir": str(case.replay_output_dir),
                    "reconciliation_pass": reconciliation.get("all_pass"),
                    "data_completeness": replay.get("data_completeness"),
                    "missing_fields": replay.get("missing_fields") or [],
                    "anomaly_count": len(replay.get("anomalies") or []),
                },
                "graph": graph_output,
                "push_disabled": True,
                "status": "success" if graph_status == "success" and reconciliation.get("all_pass") is True else "partial",
            }
            reports.append(account_report)

        route_types = {
            " -> ".join(item.get("graph", {}).get("route") or [])
            for item in reports
        }
        batch = {
            "status": "success" if reports and all(item["status"] == "success" for item in reports) else "partial",
            "account_count": len(reports),
            "dynamic_route_count": len(route_types),
            "push_disabled": True,
            "accounts": reports,
        }
        _write_json(self.output_root / "historical_e2e_report.json", batch)
        (self.output_root / "historical_e2e_report.md").write_text(
            render_historical_e2e_markdown(batch),
            encoding="utf-8",
        )
        return batch


__all__ = [
    "GraphFactory",
    "HistoricalE2ERunner",
    "HistoricalReplayInput",
    "build_historical_replay_graph",
    "render_historical_e2e_markdown",
]
