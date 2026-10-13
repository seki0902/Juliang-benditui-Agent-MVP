import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from config import Settings
from services.pipeline_switch import (
    AgentPipelineNotConfigured,
    DailyPipelineRequest,
    PipelineMode,
    PipelineRunnerError,
    resolve_pipeline_mode,
    run_selected_pipeline,
)


class PipelineSwitchTests(unittest.TestCase):
    def setUp(self):
        self.request = DailyPipelineRequest(
            input_dir=Path("fixture-input"),
            output_root=Path("fixture-output"),
            state_db=Path("fixture.sqlite3"),
        )

    def test_default_settings_select_legacy_mode(self):
        self.assertEqual(resolve_pipeline_mode(Settings()), PipelineMode.LEGACY)
        self.assertEqual(
            resolve_pipeline_mode(Settings(agent_enabled=True)),
            PipelineMode.MULTI_AGENT,
        )

    def test_disabled_flag_runs_legacy_only_and_preserves_result_shape(self):
        calls = []

        def legacy(request):
            calls.append(("legacy", request))
            return {"status": "legacy-fixture", "report": "old-report"}

        def agent(_request):
            calls.append(("agent", _request))
            return {"status": "agent-fixture"}

        result = run_selected_pipeline(
            self.request,
            settings=Settings(agent_enabled=False),
            legacy_runner=legacy,
            agent_runner=agent,
        )

        self.assertEqual(result, {"status": "legacy-fixture", "report": "old-report"})
        self.assertEqual(calls, [("legacy", self.request)])

    def test_enabled_flag_runs_multi_agent_only_and_marks_mode(self):
        calls = []

        def legacy(_request):
            calls.append("legacy")
            return {"status": "wrong-path"}

        def agent(request):
            calls.append(("agent", request))
            return {"status": "agent-fixture", "final_report": "new-report"}

        result = run_selected_pipeline(
            self.request,
            settings=Settings(agent_enabled=True),
            legacy_runner=legacy,
            agent_runner=agent,
        )

        self.assertEqual(result["status"], "agent-fixture")
        self.assertEqual(result["final_report"], "new-report")
        self.assertEqual(result["pipeline_mode"], "multi_agent")
        self.assertEqual(calls, [("agent", self.request)])

    def test_enabled_flag_fails_closed_without_agent_runner(self):
        legacy_called = False

        def legacy(_request):
            nonlocal legacy_called
            legacy_called = True
            return {"status": "legacy-fixture"}

        with self.assertRaises(AgentPipelineNotConfigured):
            run_selected_pipeline(
                self.request,
                settings=Settings(agent_enabled=True),
                legacy_runner=legacy,
            )

        self.assertFalse(legacy_called)

    def test_invalid_runner_result_is_rejected(self):
        with self.assertRaises(PipelineRunnerError):
            run_selected_pipeline(
                self.request,
                settings=Settings(agent_enabled=False),
                legacy_runner=lambda _request: ["not", "a", "mapping"],
            )

    def test_daily_job_exposes_the_same_switch_contract(self):
        import daily_job

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                daily_job,
                "run_selected_pipeline",
                return_value={"status": "agent-fixture"},
            ) as selected:
                result = daily_job.run_daily(
                    input_dir=Path("fixture-input"),
                    output_root=Path(temp_dir) / "reports",
                    settings=Settings(agent_enabled=True),
                    agent_runner=lambda _request: {"status": "agent-fixture"},
                )

        self.assertEqual(result, {"status": "agent-fixture"})
        request = selected.call_args.args[0]
        self.assertEqual(request.input_dir, Path("fixture-input"))
        self.assertTrue(selected.call_args.kwargs["settings"].agent_enabled)
        self.assertIs(selected.call_args.kwargs["legacy_runner"], daily_job._legacy_pipeline_runner)
        self.assertIsNotNone(selected.call_args.kwargs["agent_runner"])


if __name__ == "__main__":
    unittest.main()
