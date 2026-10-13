"""Output contract for the Supervisor.

The Supervisor selects the next graph destination and tracks why. It does
not carry business conclusions or ad execution recommendations.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .common import AgentName, OpenQuestion


class SupervisorDecision(BaseModel):
    """A routing decision based on the current complete State."""

    model_config = ConfigDict(extra="forbid")

    next_agent: AgentName
    task: str
    reason: str
    open_question_to_resolve: OpenQuestion | None = None
    can_finish: bool = False

