"""Output contract for the Data Analysis Agent.

The models describe findings and handoff suggestions. They intentionally do
not contain business thresholds or force a graph route.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .common import AgentName, ConfidenceLevel, EvidenceItem, HandoffSuggestion


class AnalysisFinding(BaseModel):
    """An observation, anomaly, or conflict discovered in the data."""

    model_config = ConfigDict(extra="forbid")

    description: str
    object_type: str | None = None
    object_id: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class DiagnosisTarget(BaseModel):
    """A concrete question or object worth passing to Ads Diagnosis."""

    model_config = ConfigDict(extra="forbid")

    question: str
    object_type: str | None = None
    object_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class MissingDataItem(BaseModel):
    """A missing or insufficient input that limits the analysis."""

    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str
    impact: str | None = None
    requested_from: str | None = None


class DataAnalysisResult(BaseModel):
    """Structured result written by the Data Analysis Agent."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    observations: list[AnalysisFinding] = Field(default_factory=list)
    anomalies: list[AnalysisFinding] = Field(default_factory=list)
    conflicts: list[AnalysisFinding] = Field(default_factory=list)
    targets_for_diagnosis: list[DiagnosisTarget] = Field(default_factory=list)
    missing_data: list[MissingDataItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    handoff_suggestion: HandoffSuggestion | None = None
