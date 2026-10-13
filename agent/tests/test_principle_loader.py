from pathlib import Path
import tempfile
import unittest

from schemas.common import AgentName
from services.principle_loader import PrincipleLoader, PrincipleNotFoundError


class PrincipleLoaderTests(unittest.TestCase):
    def test_loads_each_specialist_principle_as_utf8(self):
        loader = PrincipleLoader()

        for agent_name in (
            AgentName.DATA_ANALYSIS,
            AgentName.ADS_DIAGNOSIS,
            AgentName.CONTENT_REVIEW,
            AgentName.STRATEGY,
        ):
            with self.subTest(agent_name=agent_name):
                result = loader.load(agent_name)
                self.assertEqual(result.agent_name, agent_name)
                self.assertTrue(result.content.startswith("#"))
                if agent_name in (
                    AgentName.DATA_ANALYSIS,
                    AgentName.ADS_DIAGNOSIS,
                    AgentName.CONTENT_REVIEW,
                    AgentName.STRATEGY,
                ):
                    self.assertNotIn("TODO", result.content)
                    marker = (
                        "P38"
                        if agent_name == AgentName.DATA_ANALYSIS
                        else (
                            "P39"
                            if agent_name == AgentName.ADS_DIAGNOSIS
                            else (
                                "P40"
                                if agent_name == AgentName.CONTENT_REVIEW
                                else "P41"
                            )
                        )
                    )
                    self.assertIn(marker, result.content)
                else:
                    self.assertIn("TODO", result.content)
                self.assertEqual(result.warnings, ())

    def test_missing_file_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            loader = PrincipleLoader(Path(temp_dir))

            with self.assertRaisesRegex(PrincipleNotFoundError, "data_analysis"):
                loader.load(AgentName.DATA_ANALYSIS)

    def test_empty_file_returns_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "strategy.md"
            path.write_text("  \n", encoding="utf-8")
            result = PrincipleLoader(Path(temp_dir)).load(AgentName.STRATEGY)

            self.assertTrue(result.is_empty)
            self.assertEqual(len(result.warnings), 1)
            self.assertIn("empty", result.warnings[0])

    def test_utf8_chinese_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "content_review.md"
            content = "# 内容原则\n\nTODO：由项目负责人填写。\n"
            path.write_text(content, encoding="utf-8")
            result = PrincipleLoader(Path(temp_dir)).load("content_review")

            self.assertEqual(result.content, content)
            self.assertEqual(result.agent_name, AgentName.CONTENT_REVIEW)

    def test_supervisor_and_finish_do_not_load_business_principles(self):
        loader = PrincipleLoader()

        for agent_name in (AgentName.FINISH, "supervisor"):
            with self.subTest(agent_name=agent_name):
                with self.assertRaisesRegex(ValueError, "does not load"):
                    loader.load(agent_name)


if __name__ == "__main__":
    unittest.main()
