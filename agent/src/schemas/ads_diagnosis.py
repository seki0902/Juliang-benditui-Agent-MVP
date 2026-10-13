"""Output contract for the Ads Diagnosis Agent.

The contract can represent a likely cause or a hypothesis and can request
content review or additional evidence. It intentionally has no execution
fields such as budget changes, bid changes, or plan pausing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import AgentName, ConfidenceLevel, EvidenceItem, HandoffSuggestion


class DiagnosisCertainty(StrEnum):
    """Whether a diagnosis is relatively clear or remains a hypothesis."""

    LIKELY = "likely"
    HYPOTHESIS = "hypothesis"


class DiagnosisItem(BaseModel):
    """A diagnosis statement tied to an object and supporting evidence."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    problem_layer: str | None = None
    certainty: DiagnosisCertainty
    object_type: str | None = None
    object_id: str | None = None
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class SuspectedCause(BaseModel):
    """A possible cause, explicitly marked as likely or hypothetical."""

    model_config = ConfigDict(extra="forbid")

    cause: str
    certainty: DiagnosisCertainty
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN


class UncertaintyItem(BaseModel):
    """An unresolved point that limits the diagnosis."""

    model_config = ConfigDict(extra="forbid")

    question: str
    reason: str
    evidence_needed: list[str] = Field(default_factory=list)


class ContentTarget(BaseModel):
    """A material or content object requested for review."""

    model_config = ConfigDict(extra="forbid")

    object_type: str = "material"
    object_id: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    """A request for additional data needed to validate a diagnosis."""

    model_config = ConfigDict(extra="forbid")

    request: str
    reason: str
    source_hint: str | None = None
    object_type: str | None = None
    object_id: str | None = None


DiagnosisHandoffSuggestion = HandoffSuggestion


class AdsDiagnosisResult(BaseModel):
    """Structured result written by the Ads Diagnosis Agent."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    diagnoses: list[DiagnosisItem] = Field(default_factory=list)
    suspected_causes: list[SuspectedCause] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    uncertainties: list[UncertaintyItem] = Field(default_factory=list)
    need_content_review: bool = False
    content_targets: list[ContentTarget] = Field(default_factory=list)
    evidence_needed: list[EvidenceRequest] = Field(default_factory=list)
    handoff_suggestion: HandoffSuggestion | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
