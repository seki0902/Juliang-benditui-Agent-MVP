"""Environment-backed configuration for the read-only daily pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


_LOCAL_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _load_local_env(path: Path = _LOCAL_ENV_PATH) -> None:
    """Load an optional local env file without overwriting process variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name in {"AGENT_PROVIDER", "AGENT_MODEL", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"}:
            continue
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if value else None


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_int(name: str, default: int = 0) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float = 0.0) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class Settings:
    oceanengine_base_url: str = "https://api.oceanengine.com"
    oceanengine_token_url: str | None = None
    oceanengine_app_id: str | None = None
    oceanengine_app_secret: str | None = None
    oceanengine_refresh_token: str | None = None
    oceanengine_access_token: str | None = None
    oceanengine_local_account_id: str | None = None
    oceanengine_project_list_path: str = "/open_api/v3.0/local/project/list/"
    oceanengine_report_path: str | None = None
    oceanengine_report_method: str | None = None
    shimo_feedback_export: Path | None = None
    database_url: str | None = None
    agent_enabled: bool = False
    agent_provider: str = "deepseek"
    agent_model: str | None = None
    agent_temperature: float = 0.0
    agent_timeout_seconds: float = 60.0
    agent_max_retries: int = 2
    agent_max_total_calls: int = 0
    agent_max_data_calls: int = 0
    agent_max_diagnosis_calls: int = 0
    agent_max_content_calls: int = 0
    agent_max_strategy_calls: int = 0
    agent_trace_enabled: bool = False
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    pipeline_version: str = "p1-readonly-v1"
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_local_env()
        feedback = _env("SHIMO_FEEDBACK_EXPORT")
        settings = cls(
            oceanengine_base_url=_env("OCEANENGINE_BASE_URL", cls.oceanengine_base_url) or cls.oceanengine_base_url,
            oceanengine_token_url=_env("OCEANENGINE_TOKEN_URL"),
            oceanengine_app_id=_env("OCEANENGINE_APP_ID"),
            oceanengine_app_secret=_env("OCEANENGINE_APP_SECRET"),
            oceanengine_refresh_token=_env("OCEANENGINE_REFRESH_TOKEN"),
            oceanengine_access_token=_env("OCEANENGINE_ACCESS_TOKEN"),
            oceanengine_local_account_id=_env("OCEANENGINE_LOCAL_ACCOUNT_ID"),
            oceanengine_project_list_path=_env("OCEANENGINE_PROJECT_LIST_PATH", cls.oceanengine_project_list_path) or cls.oceanengine_project_list_path,
            oceanengine_report_path=_env("OCEANENGINE_REPORT_PATH"),
            oceanengine_report_method=_env("OCEANENGINE_REPORT_METHOD"),
            shimo_feedback_export=Path(feedback) if feedback else None,
            database_url=_env("DATABASE_URL"),
            agent_enabled=_env_bool("AGENT_ENABLED", cls.agent_enabled),
            agent_provider=_env("AGENT_PROVIDER", cls.agent_provider) or cls.agent_provider,
            agent_model=_env("AGENT_MODEL"),
            agent_temperature=_env_float("AGENT_TEMPERATURE", cls.agent_temperature),
            agent_timeout_seconds=_env_float(
                "AGENT_TIMEOUT_SECONDS", cls.agent_timeout_seconds
            ),
            agent_max_retries=_env_int("AGENT_MAX_RETRIES", cls.agent_max_retries),
            agent_max_total_calls=_env_int("AGENT_MAX_TOTAL_CALLS", cls.agent_max_total_calls),
            agent_max_data_calls=_env_int("AGENT_MAX_DATA_CALLS", cls.agent_max_data_calls),
            agent_max_diagnosis_calls=_env_int("AGENT_MAX_DIAGNOSIS_CALLS", cls.agent_max_diagnosis_calls),
            agent_max_content_calls=_env_int("AGENT_MAX_CONTENT_CALLS", cls.agent_max_content_calls),
            agent_max_strategy_calls=_env_int("AGENT_MAX_STRATEGY_CALLS", cls.agent_max_strategy_calls),
            agent_trace_enabled=_env_bool("AGENT_TRACE_ENABLED", cls.agent_trace_enabled),
            deepseek_api_key=_env("DEEPSEEK_API_KEY"),
            deepseek_base_url=_env("DEEPSEEK_BASE_URL", cls.deepseek_base_url) or cls.deepseek_base_url,
            openai_api_key=_env("OPENAI_API_KEY"),
            openai_base_url=_env("OPENAI_BASE_URL", cls.openai_base_url) or cls.openai_base_url,
            pipeline_version=_env("PIPELINE_VERSION", cls.pipeline_version) or cls.pipeline_version,
            timezone=_env("TIMEZONE", cls.timezone) or cls.timezone,
        )
        from services.local_model_config import ModelConfigStore
        return replace(settings, **ModelConfigStore().overrides(include_legacy=False))

    def missing_for_agent(self) -> list[str]:
        if not self.agent_enabled:
            return []
        missing = []
        provider = self.agent_provider.lower()
        if provider in {"deepseek", "deepseek-compatible"}:
            if not self.deepseek_api_key:
                missing.append("DEEPSEEK_API_KEY")
        elif provider in {"openai", "openai-compatible"}:
            if not self.openai_api_key:
                missing.append("OPENAI_API_KEY")
        else:
            missing.append("AGENT_PROVIDER=deepseek 或 openai")
        if not self.agent_model:
            missing.append("AGENT_MODEL")
        return missing

    def missing_for_project_probe(self) -> list[str]:
        missing = []
        if not self.oceanengine_local_account_id:
            missing.append("OCEANENGINE_LOCAL_ACCOUNT_ID")
        if not (self.oceanengine_access_token or self.oceanengine_refresh_token):
            missing.append("OCEANENGINE_ACCESS_TOKEN 或 OCEANENGINE_REFRESH_TOKEN")
        return missing

    def missing_for_token_refresh(self) -> list[str]:
        fields = {
            "OCEANENGINE_TOKEN_URL": self.oceanengine_token_url,
            "OCEANENGINE_APP_ID": self.oceanengine_app_id,
            "OCEANENGINE_APP_SECRET": self.oceanengine_app_secret,
            "OCEANENGINE_REFRESH_TOKEN": self.oceanengine_refresh_token,
        }
        return [name for name, value in fields.items() if not value]
