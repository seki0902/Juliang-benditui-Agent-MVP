"""Load the one business-Principle file belonging to a specialist Agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from schemas.common import AgentName


class PrincipleNotFoundError(FileNotFoundError):
    """Raised when the requested specialist Principle file is missing."""


@dataclass(frozen=True)
class PrincipleLoadResult:
    """Loaded Principle content plus non-fatal loader warnings."""

    agent_name: AgentName
    path: Path
    content: str
    warnings: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()


class PrincipleLoader:
    """Resolve and load only the Principle owned by a specialist Agent."""

    _AGENT_FILES = {
        AgentName.DATA_ANALYSIS: "data_analysis.md",
        AgentName.ADS_DIAGNOSIS: "ads_diagnosis.md",
        AgentName.CONTENT_REVIEW: "content_review.md",
        AgentName.STRATEGY: "strategy.md",
    }

    def __init__(self, principles_dir: Path | str | None = None):
        self.principles_dir = (
            Path(principles_dir)
            if principles_dir is not None
            else Path(__file__).resolve().parents[1] / "principles"
        )

    @classmethod
    def _specialist_name(cls, agent_name: AgentName | str) -> AgentName:
        if isinstance(agent_name, str) and agent_name.lower() == "supervisor":
            raise ValueError("Agent 'supervisor' does not load a business Principle file")
        try:
            normalized = agent_name if isinstance(agent_name, AgentName) else AgentName(agent_name)
        except ValueError as exc:
            raise ValueError(f"Unknown Agent name: {agent_name}") from exc
        if normalized not in cls._AGENT_FILES:
            raise ValueError(
                f"Agent '{normalized.value}' does not load a business Principle file"
            )
        return normalized

    def path_for(self, agent_name: AgentName | str) -> Path:
        normalized = self._specialist_name(agent_name)
        return self.principles_dir / self._AGENT_FILES[normalized]

    def load(self, agent_name: AgentName | str) -> PrincipleLoadResult:
        normalized = self._specialist_name(agent_name)
        path = self.path_for(normalized)
        if not path.is_file():
            raise PrincipleNotFoundError(
                f"Principle file not found for {normalized.value}: {path}"
            )

        content = path.read_text(encoding="utf-8-sig")
        warnings: tuple[str, ...] = ()
        if not content.strip():
            warnings = (f"Principle file is empty for {normalized.value}: {path}",)
        return PrincipleLoadResult(
            agent_name=normalized,
            path=path,
            content=content,
            warnings=warnings,
        )

    def load_text(self, agent_name: AgentName | str) -> str:
        """Convenience API for callers that only need the Markdown text."""
        return self.load(agent_name).content
