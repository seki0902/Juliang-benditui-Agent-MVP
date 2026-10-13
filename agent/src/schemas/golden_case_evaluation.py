"""Contracts for offline Golden Case evaluation reports.

The evaluation contract keeps technical checks, boundary checks, and human
business judgement separate.  In particular, an offline run may be
technically valid while a semantic business item remains ``unknown`` and
requires review; the report deliberately has no single aggregate score.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


GOLDEN_CASE_RUN_SCHEMA_VERSION = "golden_case_run.v1"
EvaluationStatus = Literal["passed", "failed", "error"]
EvaluationMark = Literal["pass", "fail", "unknown"]


class GoldenCaseTechnicalMetrics(BaseModel):
    """Per-run technical metrics used by the evaluation dashboard."""

    model_config = ConfigDict(extra="forbid")

    run_success: bool
    schema_pass: bool
    tool_error_count: int = Field(default=0, ge=0)
    agent_calls: int = Field(default=0, ge=0)
    max_agent_calls: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    loop_cutoff_count: int = Field(default=0, ge=0)


class GoldenCaseStructuralChecks(BaseModel):
    """Checks that can be evaluated without judging the wording of findings."""

    model_config = ConfigDict(extra="forbid")

    should_call: EvaluationMark = "unknown"
    should_not_call: EvaluationMark = "unknown"
    acceptable_route: EvaluationMark = "unknown"
    forbidden_outputs: EvaluationMark = "unknown"
    boundary: EvaluationMark = "unknown"


class GoldenCaseBusinessEvaluation(BaseModel):
    """Business review dimensions; unknown is a valid and useful result."""

    model_config = ConfigDict(extra="forbid")

    key_observations: EvaluationMark = "unknown"
    diagnosis_direction: EvaluationMark = "unknown"
    content_call_necessity: EvaluationMark = "unknown"
    core_evidence_usage: EvaluationMark = "unknown"
    strategy_action_range: EvaluationMark = "unknown"
    evidence_sufficiency: EvaluationMark = "unknown"


class GoldenCaseRunReport(BaseModel):
    """Serializable report for one Golden Case Graph invocation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["golden_case_run.v1"] = GOLDEN_CASE_RUN_SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    status: EvaluationStatus
    technical_metrics: GoldenCaseTechnicalMetrics
    structural_checks: GoldenCaseStructuralChecks
    business_evaluation: GoldenCaseBusinessEvaluation
    route: list[str] = Field(default_factory=list)
    agent_outputs: dict[str, Any] = Field(default_factory=dict)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    final_report: str | None = None
    boundary_violations: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class GoldenCaseBatchReport(BaseModel):
    """Serializable report for a batch of Golden Case runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["golden_case_run.v1"] = GOLDEN_CASE_RUN_SCHEMA_VERSION
    case_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)
    average_agent_calls: float = Field(default=0.0, ge=0)
    maximum_agent_calls: int = Field(default=0, ge=0)
    average_latency_ms: float = Field(default=0.0, ge=0)
    loop_cutoff_count: int = Field(default=0, ge=0)
    cases: list[GoldenCaseRunReport] = Field(default_factory=list)


__all__ = [
    "EvaluationMark",
    "EvaluationStatus",
    "GOLDEN_CASE_RUN_SCHEMA_VERSION",
    "GoldenCaseBatchReport",
    "GoldenCaseBusinessEvaluation",
    "GoldenCaseRunReport",
    "GoldenCaseStructuralChecks",
    "GoldenCaseTechnicalMetrics",
]
