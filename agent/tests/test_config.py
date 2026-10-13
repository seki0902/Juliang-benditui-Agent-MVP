import os
import unittest
from unittest.mock import patch

from config import Settings


class SettingsTests(unittest.TestCase):
    def test_deepseek_config_is_centralized_and_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertFalse(settings.agent_enabled)
        self.assertEqual(settings.agent_provider, "deepseek")
        self.assertIsNone(settings.agent_model)
        self.assertEqual(settings.deepseek_base_url, "https://api.deepseek.com")
        self.assertEqual(settings.agent_timeout_seconds, 60.0)
        self.assertEqual(settings.agent_max_retries, 2)
        self.assertEqual(settings.missing_for_agent(), [])

    def test_deepseek_config_reads_local_runtime_values(self):
        values = {
            "AGENT_ENABLED": "true",
            "AGENT_PROVIDER": "deepseek",
            "AGENT_MODEL": "deepseek-flash",
            "DEEPSEEK_API_KEY": "sk-test-only",
            "AGENT_TEMPERATURE": "0.2",
            "AGENT_TIMEOUT_SECONDS": "45",
            "AGENT_MAX_RETRIES": "3",
            "AGENT_MAX_TOTAL_CALLS": "20",
            "AGENT_TRACE_ENABLED": "yes",
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env()

        self.assertTrue(settings.agent_enabled)
        self.assertEqual(settings.agent_model, "deepseek-flash")
        self.assertEqual(settings.deepseek_api_key, "sk-test-only")
        self.assertEqual(settings.agent_temperature, 0.2)
        self.assertEqual(settings.agent_timeout_seconds, 45.0)
        self.assertEqual(settings.agent_max_retries, 3)
        self.assertEqual(settings.agent_max_total_calls, 20)
        self.assertTrue(settings.agent_trace_enabled)
        self.assertEqual(settings.missing_for_agent(), [])


if __name__ == "__main__":
    unittest.main()
