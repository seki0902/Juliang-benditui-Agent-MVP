"""Offline Golden Case Runner.

The runner owns evaluation plumbing, not business judgement.  It loads a
de-identified Case, injects the input snapshot into the initial State, invokes
an injectable Graph factory, and records route/output/technical checks.  A
real Graph with model-backed nodes can be supplied by the caller; the default
Graph is intentionally only a no-network orchestration smoke path.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import perf_counter_ns
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from graph import build_graph
from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.common import (
    AgentTraceRecord,
    AgentName,
    EvidenceItem,
    OpenQuestion,
    RouteTraceRecord,
    ToolCallRecord,
)
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.golden_case import GoldenCase
from schemas.golden_case_evaluation import (
    EvaluationMark,
    GoldenCaseBatchReport,
    GoldenCaseBusinessEvaluation,
    GoldenCaseRunReport,
    GoldenCaseStructuralChecks,
    GoldenCaseTechnicalMetrics,
)
from schemas.strategy import StrategyResult
from schemas.supervisor import SupervisorDecision


CASE_RESULT_FIELDS = {
    "supervisor": ("supervisor_decision", SupervisorDecision),
    "data_analysis": ("data_analysis_result", DataAnalysisResult),
    "ads_diagnosis": ("ads_diagnosis_result", AdsDiagnosisResult),
    "content_review": ("content_review_result", ContentReviewResult),
    "strategy": ("strategy_result", StrategyResult),
}

_TRACE_FIELDS = {
    "route_trace": (RouteTraceRecord, True),
    "agent_trace": (AgentTraceRecord, True),
    "tool_trace": (ToolCallRecord, True),
    "evidence": (EvidenceItem, True),
    "open_questions": (OpenQuestion, True),
}

_EXECUTION_FIELDS = frozenset(
    {
        "budget_change",
        "bid_change",
        "pause_plan",
        "scale_percentage",
        "execution_status",
        "backend_action",
    }
)

_BOUNDARY_FORBIDDEN_FIELDS = {
    "supervisor": frozenset(
        {
            "data_analysis_result",
            "ads_diagnosis_result",
            "content_review_result",
            "strategy_result",
            "priority_actions",
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }
    ),
    "data_analysis": frozenset(
        {
            "ads_diagnosis_result",
            "content_review_result",
            "strategy_result",
            "priority_actions",
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }
    ),
    "ads_diagnosis": frozenset(
        {
            "content_review_result",
            "materials_reviewed",
            "content_findings",
            "strategy_result",
            "priority_actions",
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }
    ),
    "content_review": frozenset(
        {
            "strategy_result",
            "priority_actions",
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
        }
    ),
    "strategy": _EXECUTION_FIELDS,
}

GraphFactory = Callable[[GoldenCase], Any]
BusinessEvaluator = Callable[[GoldenCase, Mapping[str, Any]], Mapping[str, EvaluationMark]]


def load_golden_case(path: str | Path) -> GoldenCase:
    """Load and strictly validate one YAML Golden Case."""

    case_path = Path(path)
    data = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"Golden Case must be a mapping: {case_path}")
    return GoldenCase.model_validate(data)


def discover_golden_cases(case_dir: str | Path) -> list[Path]:
    """Return deterministic Case paths; the template is not included."""

    return sorted(Path(case_dir).glob("case-*.yaml"))


def initial_state_for_case(case: GoldenCase) -> dict[str, Any]:
    """Build a safe initial State and inject only the Case input snapshot.

    Human reference analysis is deliberately not injected.  The fixture is
    available under ``account_context.golden_case_fixture`` for offline Tool
    or node adapters, while the expected answer remains evaluation-only.
    """

    account = case.input_snapshot.account or {}
    account_id = str(account.get("account_id") or case.case_id)
    return {
        "run_id": f"golden_{case.case_id}",
        "report_date": case.date.isoformat(),
        "task": f"offline Golden Case evaluation: {case.case_id}",
        "account_context": {
            "account_id": account_id,
            "data_source": "offline_golden_case",
            "golden_case_id": case.case_id,
            "golden_case_fixture": case.input_snapshot.model_dump(mode="json"),
        },
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
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


def _mapping_value(value: Any, *keys: str) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
    for key in keys:
        if hasattr(value, key):
            return getattr(value, key)
    return None


def _normalise_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.endswith("_agent"):
        text = text[: -len("_agent")]
    return text


def _normalise_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _validate_state(state: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for agent_name, (field_name, model) in CASE_RESULT_FIELDS.items():
        value = state.get(field_name)
        if value is None:
            continue
        try:
            model.model_validate(value)
        except (TypeError, ValidationError) as exc:
            errors.append(f"{agent_name}.{field_name}: {exc}")

    for field_name, (model, is_collection) in _TRACE_FIELDS.items():
        values = state.get(field_name)
        if values is None:
            continue
        if not is_collection or not isinstance(values, list):
            errors.append(f"{field_name} must be a list")
            continue
        for index, value in enumerate(values):
            try:
                model.model_validate(value)
            except (TypeError, ValidationError) as exc:
                errors.append(f"{field_name}[{index}]: {exc}")
    return errors


def _route(state: Mapping[str, Any]) -> list[str]:
    route = ["supervisor"]
    trace = _as_list(state.get("route_trace"))
    for index, item in enumerate(trace):
        target = _mapping_value(item, "to", "to_agent")
        if target is not None:
            route.append(_normalise_name(target))
            if index < len(trace) - 1:
                route.append("supervisor")
    return route


def _observed_calls(state: Mapping[str, Any]) -> set[str]:
    observed: set[str] = set()
    for item in _as_list(state.get("agent_trace")):
        agent = _mapping_value(item, "agent")
        if agent is not None:
            observed.add(_normalise_name(agent))
    for item in _as_list(state.get("tool_trace")):
        tool = _mapping_value(item, "tool_name")
        if tool is not None:
            observed.add(_normalise_token(tool))
    if state.get("route_trace"):
        observed.add("supervisor")
    return observed


def _check_presence(expected: Sequence[str], observed: set[str]) -> EvaluationMark:
    if not expected:
        return "pass"
    missing = {_normalise_token(item) for item in expected} - observed
    return "fail" if missing else "pass"


def _check_absence(forbidden: Sequence[str], observed: set[str]) -> EvaluationMark:
    if not forbidden:
        return "pass"
    present = {_normalise_token(item) for item in forbidden} & observed
    return "fail" if present else "pass"


def _find_forbidden_outputs(
    state: Mapping[str, Any],
    forbidden_outputs: Sequence[str],
) -> list[str]:
    forbidden = {_normalise_token(item) for item in forbidden_outputs}
    if not forbidden:
        return []

    findings: list[str] = []
    output_fields = [
        field_name
        for _, (field_name, _) in CASE_RESULT_FIELDS.items()
    ] + ["final_report"]

    def visit(value: Any, path: str) -> None:
        if isinstance(value, BaseModel):
            visit(value.model_dump(mode="json"), path)
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_token = _normalise_token(key)
                child_path = f"{path}.{key}"
                if key_token in forbidden:
                    findings.append(child_path)
                visit(item, child_path)
            return
        if isinstance(value, (list, tuple, set)):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if isinstance(value, str) and _normalise_token(value) in forbidden:
            findings.append(path)

    for field_name in output_fields:
        if field_name in state and state[field_name] is not None:
            visit(state[field_name], field_name)
    return findings


def _boundary_violations(state: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    common_allowed = {
        "evidence",
        "open_questions",
        "tool_trace",
        "errors",
        "agent_call_counts",
        "total_agent_calls",
        "agent_trace",
        "route_trace",
    }
    for item in _as_list(state.get("agent_trace")):
        agent = _normalise_name(_mapping_value(item, "agent"))
        raw_schema = _mapping_value(item, "output_schema") or {}
        fields = _mapping_value(raw_schema, "fields") or []
        if not isinstance(fields, list) or agent not in _BOUNDARY_FORBIDDEN_FIELDS:
            continue
        forbidden = _BOUNDARY_FORBIDDEN_FIELDS[agent]
        for field in fields:
            field_token = _normalise_token(field)
            if field_token in forbidden:
                violations.append(f"{agent} emitted forbidden field: {field}")
            elif agent != "supervisor" and field_token not in common_allowed:
                expected_result = CASE_RESULT_FIELDS.get(agent, (None, None))[0]
                if field != expected_result:
                    violations.append(f"{agent} emitted unexpected field: {field}")
    return sorted(set(violations))


def _tool_error_count(state: Mapping[str, Any]) -> int:
    count = 0
    for item in _as_list(state.get("tool_trace")):
        status = _normalise_token(_mapping_value(item, "status"))
        error = _mapping_value(item, "error")
        if status not in {"ok", "success", "succeeded"} or error:
            count += 1
    return count


def _loop_cutoff_count(state: Mapping[str, Any]) -> int:
    count = 0
    for item in _as_list(state.get("agent_trace")):
        status = _normalise_token(_mapping_value(item, "status"))
        error = _normalise_token(_mapping_value(item, "error"))
        if any(token in f"{status} {error}" for token in ("limit", "cutoff", "loop")):
            count += 1
    for error in _as_list(state.get("errors")):
        text = _normalise_token(error)
        if any(token in text for token in ("limit", "cutoff", "loop")):
            count += 1
    return count


def _agent_call_counts(state: Mapping[str, Any]) -> tuple[int, int]:
    raw_counts = state.get("agent_call_counts")
    if not isinstance(raw_counts, Mapping):
        return 0, 0
    counts = [
        int(value)
        for value in raw_counts.values()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    return sum(counts), max(counts, default=0)


def _write_json(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class GoldenCaseRunner:
    """Run one or more Golden Cases against an injectable compiled Graph."""

    def __init__(
        self,
        *,
        graph_factory: GraphFactory | None = None,
        output_dir: str | Path | None = None,
        business_evaluator: BusinessEvaluator | None = None,
    ) -> None:
        self.graph_factory = graph_factory or (lambda _case: build_graph())
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self.business_evaluator = business_evaluator

    @staticmethod
    def _invoke_graph(graph: Any, initial_state: Mapping[str, Any]) -> Mapping[str, Any]:
        invoke = getattr(graph, "invoke", None)
        if callable(invoke):
            result = invoke(dict(initial_state))
        elif callable(graph):
            result = graph(dict(initial_state))
        else:
            raise TypeError("graph_factory must return an invokable or callable Graph")
        if not isinstance(result, Mapping):
            raise TypeError("Graph invocation must return a State mapping")
        return result

    def _business_evaluation(
        self,
        case: GoldenCase,
        state: Mapping[str, Any],
    ) -> GoldenCaseBusinessEvaluation:
        if self.business_evaluator is None:
            return GoldenCaseBusinessEvaluation()
        raw = self.business_evaluator(case, state)
        if not isinstance(raw, Mapping):
            raise TypeError("business_evaluator must return a mapping")
        return GoldenCaseBusinessEvaluation.model_validate(raw)

    def run_case(self, case_or_path: GoldenCase | str | Path) -> GoldenCaseRunReport:
        case = (
            case_or_path
            if isinstance(case_or_path, GoldenCase)
            else load_golden_case(case_or_path)
        )
        initial_state = initial_state_for_case(case)
        started_ns = perf_counter_ns()
        state: Mapping[str, Any] = {}
        invocation_errors: list[str] = []

        try:
            graph = self.graph_factory(case)
            state = self._invoke_graph(graph, initial_state)
        except Exception as exc:
            invocation_errors.append(f"{type(exc).__name__}: {exc}")

        latency_ms = (perf_counter_ns() - started_ns) / 1_000_000
        validation_errors = _validate_state(state) if state else []
        errors = [
            *invocation_errors,
            *validation_errors,
            *[str(item) for item in _as_list(state.get("errors"))],
        ]
        route = _route(state)
        observed = _observed_calls(state)
        expected = case.expected_behavior
        forbidden_findings = _find_forbidden_outputs(
            state,
            expected.forbidden_outputs,
        )
        boundary_violations = _boundary_violations(state)
        structural = GoldenCaseStructuralChecks(
            should_call=_check_presence(expected.should_call, observed),
            should_not_call=_check_absence(expected.should_not_call, observed),
            acceptable_route=(
                "pass"
                if route and route_string(route) in expected.acceptable_routes
                else "fail"
                if expected.acceptable_routes
                else "unknown"
            ),
            forbidden_outputs="fail" if forbidden_findings else "pass",
            boundary="fail" if boundary_violations else "pass",
        )
        agent_calls, max_agent_calls = _agent_call_counts(state)
        run_success = bool(state) and not invocation_errors and bool(state.get("final_report"))
        technical = GoldenCaseTechnicalMetrics(
            run_success=run_success,
            schema_pass=not validation_errors,
            tool_error_count=_tool_error_count(state),
            agent_calls=agent_calls,
            max_agent_calls=max_agent_calls,
            latency_ms=latency_ms,
            loop_cutoff_count=_loop_cutoff_count(state),
        )
        structural_failed = any(
            value == "fail"
            for value in structural.model_dump(mode="json").values()
        )
        status = (
            "error"
            if invocation_errors
            else "failed"
            if not technical.schema_pass or not run_success or structural_failed
            else "passed"
        )
        try:
            business = self._business_evaluation(case, state)
        except Exception as exc:
            errors.append(f"business_evaluator: {type(exc).__name__}: {exc}")
            business = GoldenCaseBusinessEvaluation()

        agent_outputs = {
            field_name: _json_safe(state.get(field_name))
            for _, (field_name, _) in CASE_RESULT_FIELDS.items()
        }
        report = GoldenCaseRunReport(
            case_id=case.case_id,
            status=status,
            technical_metrics=technical,
            structural_checks=structural,
            business_evaluation=business,
            route=route,
            agent_outputs=agent_outputs,
            agent_trace=_json_safe(_as_list(state.get("agent_trace"))),
            tool_trace=_json_safe(_as_list(state.get("tool_trace"))),
            final_report=(
                str(state.get("final_report"))
                if state.get("final_report") is not None
                else None
            ),
            boundary_violations=boundary_violations,
            errors=errors,
        )
        if self.output_dir is not None:
            _write_json(self.output_dir / f"{case.case_id}.run.json", report)
        return report

    def run_paths(self, paths: Sequence[str | Path]) -> GoldenCaseBatchReport:
        reports = [self.run_case(path) for path in paths]
        return self._batch_report(reports)

    def run_directory(
        self,
        case_dir: str | Path,
        *,
        pattern: str = "case-*.yaml",
    ) -> GoldenCaseBatchReport:
        paths = sorted(Path(case_dir).glob(pattern))
        return self.run_paths(paths)

    def _batch_report(self, reports: Sequence[GoldenCaseRunReport]) -> GoldenCaseBatchReport:
        count = len(reports)
        agent_calls = [item.technical_metrics.agent_calls for item in reports]
        latencies = [item.technical_metrics.latency_ms for item in reports]
        batch = GoldenCaseBatchReport(
            case_count=count,
            passed_count=sum(item.status == "passed" for item in reports),
            failed_count=sum(item.status == "failed" for item in reports),
            error_count=sum(item.status == "error" for item in reports),
            average_agent_calls=sum(agent_calls) / count if count else 0.0,
            maximum_agent_calls=max(agent_calls, default=0),
            average_latency_ms=sum(latencies) / count if count else 0.0,
            loop_cutoff_count=sum(
                item.technical_metrics.loop_cutoff_count for item in reports
            ),
            cases=list(reports),
        )
        if self.output_dir is not None:
            _write_json(self.output_dir / "golden-case-report.json", batch)
        return batch


def route_string(route: Sequence[str]) -> str:
    """Format a normalized route for comparison with Case expectations."""

    return " -> ".join(route)


__all__ = [
    "CASE_RESULT_FIELDS",
    "GoldenCaseRunner",
    "discover_golden_cases",
    "initial_state_for_case",
    "load_golden_case",
    "route_string",
]
