"""Output contract for the Content Review Agent.

The contract links material-level findings and content evidence to observed
performance. It contains testing suggestions only, not ad execution fields.
"""

from __future__ import annotations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import AgentName, ConfidenceLevel, EvidenceItem, HandoffSuggestion


class MaterialReviewed(BaseModel):
    """A material included in the current content review."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    material_type: str = "material"
    review_scope: str | None = None


class ContentFinding(BaseModel):
    """A finding about a material's content itself."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    category: str
    finding: str
    content_evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class PerformanceExplanation(BaseModel):
    """An explanation linking content characteristics to performance."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    performance_signal: str
    content_relationship: str
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class TestDirection(BaseModel):
    """A content testing direction, not an execution command."""

    __test__ = False

    model_config = ConfigDict(extra="forbid")

    direction: str
    reason: str
    target_material_ids: list[str] = Field(default_factory=list)
    expected_signal: str | None = None
    recheck_condition: str | None = None


class ContentUncertainty(BaseModel):
    """An unresolved content question or evidence limitation."""

    model_config = ConfigDict(extra="forbid")

    question: str
    reason: str
    needed_input: str | None = None


ContentHandoffSuggestion = HandoffSuggestion


class MaterialAssessment(BaseModel):
    """Material performance assessment; content text is not a prerequisite."""

    model_config = ConfigDict(extra="forbid")
    material_id: str
    plan_ids: list[str] = Field(default_factory=list)
    performance_summary: str
    problem_layer: str | None = None
    comparability_limits: list[str] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    test_direction: str | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class ContentReviewResult(BaseModel):
    """Structured result written by the Content Review Agent."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    content_analysis_status: Literal["missing", "partial", "complete"] = "missing"
    material_assessments: list[MaterialAssessment] = Field(default_factory=list)
    materials_reviewed: list[MaterialReviewed] = Field(default_factory=list)
    content_findings: list[ContentFinding] = Field(default_factory=list)
    performance_explanations: list[PerformanceExplanation] = Field(default_factory=list)
    content_evidence: list[EvidenceItem] = Field(default_factory=list)
    test_directions: list[TestDirection] = Field(default_factory=list)
    uncertainties: list[ContentUncertainty] = Field(default_factory=list)
    handoff_suggestion: HandoffSuggestion | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
