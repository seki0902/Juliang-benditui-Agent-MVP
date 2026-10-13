import os
import unittest
from unittest.mock import patch

from config import Settings
from services.llm_factory import (
    LLMConfigurationError,
    LLMDependencyError,
    LLMFactory,
    create_llm,
    resolve_llm_config,
)


class _FakeChatModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class LLMFactoryTests(unittest.TestCase):
    def _settings(self, **overrides):
        values = {
            "agent_enabled": True,
            "agent_provider": "deepseek",
            "agent_model": "deepseek-flash",
            "agent_temperature": 0.0,
            "agent_timeout_seconds": 60.0,
            "agent_max_retries": 2,
            "deepseek_api_key": "test-key",
            "deepseek_base_url": "https://api.deepseek.com",
        }
        values.update(overrides)
        return Settings(**values)

    def test_single_entry_creates_one_configured_chat_model(self):
        model = create_llm(self._settings(), client_cls=_FakeChatModel)

        self.assertIsInstance(model, _FakeChatModel)
        self.assertEqual(
            model.kwargs,
            {
                "model": "deepseek-flash",
                "api_key": "test-key",
                "base_url": "https://api.deepseek.com",
                "temperature": 0.0,
                "timeout": 60.0,
                "max_retries": 2,
            },
        )

    def test_provider_and_runtime_overrides_are_configurable(self):
        settings = self._settings(
            agent_provider="openai",
            agent_model="configured-model",
            openai_api_key="openai-test-key",
            openai_base_url="https://api.openai.com/v1",
        )
        model = LLMFactory(settings, _FakeChatModel).create(
            provider="openai-compatible",
            model="override-model",
            api_key="override-key",
            base_url="https://gateway.example/v1",
            temperature=0.2,
            timeout=45,
            max_retries=4,
        )

        self.assertEqual(model.kwargs["model"], "override-model")
        self.assertEqual(model.kwargs["api_key"], "override-key")
        self.assertEqual(model.kwargs["base_url"], "https://gateway.example/v1")
        self.assertEqual(model.kwargs["temperature"], 0.2)
        self.assertEqual(model.kwargs["timeout"], 45.0)
        self.assertEqual(model.kwargs["max_retries"], 4)

        resolved = resolve_llm_config(settings)
        self.assertEqual(resolved.provider, "openai")
        self.assertEqual(resolved.model, "configured-model")
        self.assertEqual(resolved.api_key, "openai-test-key")

    def test_settings_load_model_transport_and_provider_values_from_env(self):
        values = {
            "AGENT_ENABLED": "true",
            "AGENT_PROVIDER": "deepseek",
            "AGENT_MODEL": "deepseek-flash",
            "DEEPSEEK_API_KEY": "env-test-key",
            "AGENT_TEMPERATURE": "0.15",
            "AGENT_TIMEOUT_SECONDS": "45",
            "AGENT_MAX_RETRIES": "3",
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env()

        self.assertTrue(settings.agent_enabled)
        self.assertEqual(settings.agent_model, "deepseek-flash")
        self.assertEqual(settings.agent_temperature, 0.15)
        self.assertEqual(settings.agent_timeout_seconds, 45.0)
        self.assertEqual(settings.agent_max_retries, 3)
        self.assertEqual(settings.missing_for_agent(), [])

    def test_disabled_or_incomplete_configuration_fails_before_client_creation(self):
        with self.assertRaisesRegex(LLMConfigurationError, "AGENT_ENABLED"):
            create_llm(Settings(), client_cls=_FakeChatModel)

        incomplete = self._settings(deepseek_api_key=None)
        with self.assertRaisesRegex(LLMConfigurationError, "DEEPSEEK_API_KEY"):
            create_llm(incomplete, client_cls=_FakeChatModel)

    def test_invalid_provider_and_transport_values_are_rejected(self):
        settings = self._settings()
        invalid_values = [
            {"provider": "unsupported"},
            {"temperature": -0.1},
            {"timeout": 0},
            {"max_retries": -1},
        ]

        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                with self.assertRaises(LLMConfigurationError):
                    create_llm(settings, client_cls=_FakeChatModel, **overrides)

    def test_missing_provider_dependency_is_explicit(self):
        settings = self._settings()
        with patch(
            "services.llm_factory._load_chat_model_class",
            side_effect=LLMDependencyError("provider dependency missing"),
        ):
            with self.assertRaisesRegex(LLMDependencyError, "provider dependency"):
                create_llm(settings)


if __name__ == "__main__":
    unittest.main()
