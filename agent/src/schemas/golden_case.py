"""Golden Case format used for offline Multi-Agent evaluation.

Golden Cases describe a de-identified input snapshot, a human reference
analysis, and behavior-level acceptance criteria.  They are evaluation
fixtures, not execution requests and not prompts containing hidden answers.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


GOLDEN_CASE_SCHEMA_VERSION = "golden_case.v1"


class GoldenCaseInputSnapshot(BaseModel):
    """De-identified facts available to the evaluated run."""

    model_config = ConfigDict(extra="forbid")

    account: dict[str, Any] | None
    plans: list[dict[str, Any]] | None
    materials: list[dict[str, Any]] | None
    lead_quality: dict[str, Any] | None
    content_assets: list[dict[str, Any]] | None


class GoldenCaseHumanAnalysis(BaseModel):
    """Human reference used to judge key observations, not exact wording."""

    model_config = ConfigDict(extra="forbid")

    important_observations: list[str] = Field(default_factory=list)
    likely_diagnosis: list[str] = Field(default_factory=list)
    content_review_needed: bool
    strategy_direction: list[str] = Field(default_factory=list)
    cannot_conclude: list[str] = Field(default_factory=list)


class GoldenCaseExpectedBehavior(BaseModel):
    """Behavioral acceptance criteria for route, tools, and output boundaries."""

    model_config = ConfigDict(extra="forbid")

    should_call: list[str] = Field(default_factory=list)
    should_not_call: list[str] = Field(default_factory=list)
    acceptable_routes: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)


class GoldenCase(BaseModel):
    """Versioned, strict top-level Golden Case contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["golden_case.v1"] = GOLDEN_CASE_SCHEMA_VERSION
    case_id: str = Field(min_length=1)
    date: date
    description: str = Field(min_length=1)
    input_snapshot: GoldenCaseInputSnapshot
    human_analysis: GoldenCaseHumanAnalysis
    expected_behavior: GoldenCaseExpectedBehavior


__all__ = [
    "GOLDEN_CASE_SCHEMA_VERSION",
    "GoldenCase",
    "GoldenCaseExpectedBehavior",
    "GoldenCaseHumanAnalysis",
    "GoldenCaseInputSnapshot",
]
