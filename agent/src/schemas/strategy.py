"""Output contract for the Strategy Agent.

Priority actions are recommendations for review, not executable ad changes.
Concrete proposals are allowed; no field authorizes a backend mutation.
"""

from __future__ import annotations

from typing import Literal
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import ConfidenceLevel, EvidenceItem


class AdjustmentProposal(BaseModel):
    """A checked monetary suggestion; never a backend command."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    parameter: Literal["budget", "bid"]
    current_value: float = Field(gt=0)
    proposed_value: float = Field(gt=0)
    change_percent: float
    evidence_source: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_arithmetic(self):
        if abs(self.change_percent) < 10 or abs(self.change_percent) > 30:
            raise ValueError("adjustment magnitude must be 10%–30%")
        expected = Decimal(str(self.current_value)) * (1 + Decimal(str(self.change_percent)) / 100)
        if abs(Decimal(str(self.proposed_value)) - expected) > Decimal("0.01"):
            raise ValueError("proposed value does not match current value and percentage")
        return self


class PriorityAction(BaseModel):
    """A ranked strategy suggestion, not an execution command."""

    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1, description="Ordinal priority; lower number means earlier review")
    target_type: str
    target_id: str | None = None
    action_type: str
    reason: str
    expected_signal: str
    recheck_condition: str
    problem_layer: str | None = None
    why_not_other_actions: str | None = None
    invalidation_condition: str | None = None
    proposal: AdjustmentProposal | None = None
    recommendation_only: Literal[True] = True


class ObservationItem(BaseModel):
    """An item that should remain under observation without an immediate change."""

    model_config = ConfigDict(extra="forbid")

    target_type: str
    target_id: str | None = None
    observation: str
    recheck_condition: str
    evidence_ids: list[str] = Field(default_factory=list)


class StrategyEvidenceRequest(BaseModel):
    """Additional evidence requested before forming a stronger strategy."""

    model_config = ConfigDict(extra="forbid")

    request: str
    reason: str
    source_hint: str | None = None
    target_type: str | None = None
    target_id: str | None = None


class StrategyResult(BaseModel):
    """Structured result written by the Strategy Agent."""

    model_config = ConfigDict(extra="forbid")

    overall_judgement: str
    priority_actions: list[PriorityAction] = Field(default_factory=list)
    observation_items: list[ObservationItem] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    need_more_evidence: bool = False
    evidence_request: StrategyEvidenceRequest | None = None
    cannot_conclude: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
