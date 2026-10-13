"""Feature-flag boundary between the legacy daily pipeline and Multi-Agent.

The switch is intentionally independent from either pipeline's business
implementation.  It selects exactly one injected runner, preserving the
legacy callable unchanged when ``AGENT_ENABLED`` is false.  When enabled but
no Multi-Agent runner has been wired, it fails closed instead of silently
running the legacy path and producing a misleading result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from config import Settings


class PipelineMode(StrEnum):
    """The two mutually exclusive daily-pipeline modes."""

    LEGACY = "legacy"
    MULTI_AGENT = "multi_agent"


class PipelineSwitchError(RuntimeError):
    """Base error for invalid or unavailable feature-flag routing."""


class AgentPipelineNotConfigured(PipelineSwitchError):
    """Raised when Multi-Agent is enabled without an injected runner."""


class PipelineRunnerError(PipelineSwitchError):
    """Raised when a selected runner returns an invalid result."""


@dataclass(frozen=True)
class DailyPipelineRequest:
    """Stable input contract shared by legacy and Multi-Agent runners."""

    browser_config_path: Path | None = None
    input_dir: Path | None = None
    output_root: Path | None = None
    state_db: Path | None = None
    force: bool = False
    report_date: str | None = None
    shimo_snapshot: Path | None = None
    plan_context: Path | None = None
    content_snapshot: Path | None = None


PipelineRunner = Callable[[DailyPipelineRequest], Mapping[str, Any]]


def resolve_pipeline_mode(settings: Settings | None = None) -> PipelineMode:
    """Resolve the mode from the centralized settings contract."""

    resolved = settings or Settings.from_env()
    return PipelineMode.MULTI_AGENT if resolved.agent_enabled else PipelineMode.LEGACY


def run_selected_pipeline(
    request: DailyPipelineRequest,
    *,
    settings: Settings | None = None,
    legacy_runner: PipelineRunner,
    agent_runner: PipelineRunner | None = None,
) -> dict[str, Any]:
    """Run exactly one pipeline selected by ``AGENT_ENABLED``.

    The returned mapping is copied so a runner cannot mutate the caller's
    object after the switch boundary.  No mode marker is added to legacy
    results, preserving the old output contract; Multi-Agent results receive
    ``pipeline_mode=multi_agent`` for auditability unless they already define
    that key.
    """

    if not isinstance(request, DailyPipelineRequest):
        raise TypeError("request must be a DailyPipelineRequest")
    if not callable(legacy_runner):
        raise TypeError("legacy_runner must be callable")
    if agent_runner is not None and not callable(agent_runner):
        raise TypeError("agent_runner must be callable when provided")

    resolved_settings = settings or Settings.from_env()
    mode = resolve_pipeline_mode(resolved_settings)
    if mode is PipelineMode.LEGACY:
        result = legacy_runner(request)
    else:
        if agent_runner is None:
            raise AgentPipelineNotConfigured(
                "AGENT_ENABLED=true but no Multi-Agent pipeline runner is configured"
            )
        result = agent_runner(request)

    if not isinstance(result, Mapping):
        raise PipelineRunnerError(
            f"{mode.value} pipeline runner must return a mapping, "
            f"got {type(result).__name__}"
        )

    output = dict(result)
    if mode is PipelineMode.MULTI_AGENT:
        output.setdefault("pipeline_mode", mode.value)
    return output


__all__ = [
    "AgentPipelineNotConfigured",
    "DailyPipelineRequest",
    "PipelineMode",
    "PipelineRunner",
    "PipelineRunnerError",
    "PipelineSwitchError",
    "resolve_pipeline_mode",
    "run_selected_pipeline",
]
