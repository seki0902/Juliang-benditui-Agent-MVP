"""Common serializable contracts shared by every Agent node.

These types describe evidence and orchestration metadata only. They do not
encode business thresholds, diagnosis rules, or strategy conclusions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class AgentName(StrEnum):
    """Finite set of destinations used by the graph contract."""

    DATA_ANALYSIS = "data_analysis"
    ADS_DIAGNOSIS = "ads_diagnosis"
    CONTENT_REVIEW = "content_review"
    STRATEGY = "strategy"
    FINISH = "finish"


class ConfidenceLevel(StrEnum):
    """Confidence in an observation or handoff, not business performance."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class OpenQuestion(BaseModel):
    """An unresolved question that an Agent or operator may need to answer."""

    model_config = ConfigDict(extra="forbid")

    question: str
    reason: str | None = None
    blocking: bool = False
    owner: str | None = None


class EvidenceItem(BaseModel):
    """Traceable evidence that can describe data or content."""

    model_config = ConfigDict(extra="forbid")

    evidence_type: str = Field(description="Evidence category, for example data or content")
    source: str = Field(description="Origin of the evidence")
    object_type: str = Field(description="Entity type, such as account, plan, material, or script")
    object_id: str | None = None
    metric_or_content: str = Field(description="Metric name or content fragment being evidenced")
    value: Any = None
    time_range: str | None = None
    note: str | None = None


class ToolCallRecord(BaseModel):
    """Auditable record of a tool request made during an Agent run."""

    model_config = ConfigDict(extra="forbid")

    # ``agent`` is deliberately a string-compatible enum field.  The default
    # keeps old State/checkpoint fixtures readable, while real Agent nodes
    # always write their concrete ``AgentName`` value.
    agent: AgentName | str = "unknown"
    tool_name: str
    call_id: str | None = None
    status: str = Field(description="Tool lifecycle status")
    arguments: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = Field(default=0.0, ge=0)
    timestamp: str | None = None
    # ``result`` is retained for compatibility with the Phase 5 contract.  It
    # must contain the same bounded summary as ``result_summary`` in runtime
    # traces; full Tool payloads belong only in the in-process ToolMessage.
    result: Any = None
    result_summary: Any = None
    error: str | None = None


class AgentTraceRecord(BaseModel):
    """Safe, local audit record for one Agent node invocation."""

    model_config = ConfigDict(extra="forbid")

    # Supervisor is intentionally not part of AgentName because it is an
    # orchestrator destination, so this field remains string-compatible.
    agent: AgentName | str
    run_id: str | None = None
    input_state_summary: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(description="Agent lifecycle status")
    duration_ms: float = Field(default=0.0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    token_usage: dict[str, Any] | None = None
    timestamp: str
    error: str | None = None


class RouteRecord(BaseModel):
    """A proposed or executed graph handoff between named destinations."""

    model_config = ConfigDict(extra="forbid")

    from_agent: AgentName
    to_agent: AgentName
    reason: str
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)


class RouteTraceRecord(BaseModel):
    """An auditable Supervisor decision recorded by the Graph router."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    step: int = Field(ge=1)
    from_agent: Literal["supervisor"] = Field(
        validation_alias=AliasChoices("from", "from_agent"),
        serialization_alias="from",
    )
    to_agent: AgentName = Field(
        validation_alias=AliasChoices("to", "to_agent"),
        serialization_alias="to",
    )
    reason: str
    task: str
    timestamp: str


class HandoffSuggestion(BaseModel):
    """A recommendation to the Supervisor, not a mandatory graph route."""

    model_config = ConfigDict(extra="forbid")

    suggested_agent: AgentName
    task: str
    reason: str
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
