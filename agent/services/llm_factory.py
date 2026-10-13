"""Centralized construction of the chat model used by Agent nodes.

The factory keeps provider configuration and transport controls in one place.
DeepSeek and OpenAI-compatible endpoints both use the LangChain OpenAI
adapter, so Agent modules do not instantiate separate model clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Callable

from config import Settings


DEFAULT_AGENT_TIMEOUT_SECONDS = 60.0
DEFAULT_AGENT_MAX_RETRIES = 2

_PROVIDER_ALIASES = {
    "deepseek": "deepseek",
    "deepseek-compatible": "deepseek",
    "openai": "openai",
    "openai-compatible": "openai",
}


class LLMFactoryError(RuntimeError):
    """Base error for model-factory configuration and dependency failures."""


class LLMConfigurationError(LLMFactoryError):
    """Raised when the model cannot be built from the available configuration."""


class LLMDependencyError(LLMFactoryError):
    """Raised when the configured provider adapter is not installed."""


@dataclass(frozen=True)
class LLMConfig:
    """Resolved, non-secret model settings passed to the provider adapter."""

    provider: str
    model: str
    base_url: str
    temperature: float
    timeout: float
    max_retries: int
    api_key: str = field(repr=False)

    def client_kwargs(self) -> dict[str, Any]:
        """Return the common constructor kwargs for ``ChatOpenAI``."""
        return {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }


def _normalise_provider(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    canonical = _PROVIDER_ALIASES.get(value)
    if canonical is None:
        supported = ", ".join(sorted(_PROVIDER_ALIASES))
        raise LLMConfigurationError(
            f"unsupported AGENT_PROVIDER={value or '<empty>'}; supported values: {supported}"
        )
    return canonical


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LLMConfigurationError(f"{field_name} is required to create the Agent model")
    return text


def _non_negative_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(f"{field_name} must be a number") from exc
    if not isfinite(number) or number < 0:
        raise LLMConfigurationError(f"{field_name} must be a finite non-negative number")
    return number


def _positive_float(value: Any, field_name: str) -> float:
    number = _non_negative_float(value, field_name)
    if number == 0:
        raise LLMConfigurationError(f"{field_name} must be greater than 0")
    return number


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise LLMConfigurationError(f"{field_name} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(f"{field_name} must be a non-negative integer") from exc
    if number < 0 or number != value:
        raise LLMConfigurationError(f"{field_name} must be a non-negative integer")
    return number


def resolve_llm_config(
    settings: Settings,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> LLMConfig:
    """Resolve and validate one provider-neutral model configuration."""
    if not settings.agent_enabled:
        raise LLMConfigurationError(
            "Agent model creation is disabled; set AGENT_ENABLED=true before creating a model"
        )

    canonical_provider = _normalise_provider(provider or settings.agent_provider)
    resolved_model = _required_text(model or settings.agent_model, "AGENT_MODEL")

    if canonical_provider == "deepseek":
        resolved_api_key = api_key or settings.deepseek_api_key
        resolved_base_url = base_url or settings.deepseek_base_url
        key_name = "DEEPSEEK_API_KEY"
    else:
        resolved_api_key = api_key or settings.openai_api_key
        resolved_base_url = base_url or settings.openai_base_url
        key_name = "OPENAI_API_KEY"

    resolved_api_key = _required_text(resolved_api_key, key_name)
    resolved_base_url = _required_text(resolved_base_url, "provider base URL")
    resolved_temperature = _non_negative_float(
        settings.agent_temperature if temperature is None else temperature,
        "AGENT_TEMPERATURE",
    )
    resolved_timeout = _positive_float(
        settings.agent_timeout_seconds if timeout is None else timeout,
        "AGENT_TIMEOUT_SECONDS",
    )
    resolved_max_retries = _non_negative_int(
        settings.agent_max_retries if max_retries is None else max_retries,
        "AGENT_MAX_RETRIES",
    )

    return LLMConfig(
        provider=canonical_provider,
        model=resolved_model,
        api_key=resolved_api_key,
        base_url=resolved_base_url,
        temperature=resolved_temperature,
        timeout=resolved_timeout,
        max_retries=resolved_max_retries,
    )


def _load_chat_model_class() -> Callable[..., Any]:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMDependencyError(
            "langchain-openai is required for the Agent model; install agent/requirements-p1.txt"
        ) from exc
    return ChatOpenAI


@dataclass
class LLMFactory:
    """Create the single chat-model client shared by future Agent nodes."""

    settings: Settings | None = None
    client_cls: Callable[..., Any] | None = None

    def create(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """Build one configured chat model without making a network request."""
        settings = self.settings or Settings.from_env()
        config = resolve_llm_config(
            settings,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
        client_cls = self.client_cls or _load_chat_model_class()
        return client_cls(**config.client_kwargs())


def create_llm(
    settings: Settings | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    client_cls: Callable[..., Any] | None = None,
) -> Any:
    """Canonical Agent entry point for constructing a chat model."""
    return LLMFactory(settings=settings, client_cls=client_cls).create(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )

