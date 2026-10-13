import json
import tempfile
import unittest
from pathlib import Path

from graph import build_graph
from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.common import AgentName
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.golden_case import GoldenCase
from schemas.strategy import StrategyResult
from schemas.supervisor import SupervisorDecision
from services.golden_case_runner import (
    GoldenCaseRunner,
    discover_golden_cases,
    initial_state_for_case,
    load_golden_case,
)


CASE_DIR = Path(__file__).resolve().parents[1] / "docs" / "golden_cases"


def _decision(target: str) -> SupervisorDecision:
    agent = AgentName(target)
    return SupervisorDecision(
        next_agent=agent,
        task=f"fixture task for {target}",
        reason=f"fixture route to {target}",
        can_finish=agent is AgentName.FINISH,
    )


def _fixture_graph_factory(case: GoldenCase):
    route_tokens = case.expected_behavior.acceptable_routes[0].split(" -> ")
    targets = route_tokens[1::2]
    supervisor_calls = 0

    def supervisor(_state):
        nonlocal supervisor_calls
        target = targets[supervisor_calls]
        supervisor_calls += 1
        return {"supervisor_decision": _decision(target)}

    result_updates = {
        "data_analysis": {
            "data_analysis_result": DataAnalysisResult(
                summary=f"fixture data result for {case.case_id}"
            )
        },
        "ads_diagnosis": {
            "ads_diagnosis_result": AdsDiagnosisResult(
                summary=f"fixture diagnosis result for {case.case_id}"
            )
        },
        "content_review": {
            "content_review_result": ContentReviewResult(
                summary=f"fixture content result for {case.case_id}"
            )
        },
        "strategy": {
            "strategy_result": StrategyResult(
                overall_judgement=f"fixture strategy result for {case.case_id}"
            )
        },
    }
    overrides = {
        "supervisor": supervisor,
        "push": lambda _state: {"push_result": {"status": "skipped"}},
    }
    for target in targets:
        if target == "finish":
            continue
        overrides[f"{target}_agent"] = lambda _state, name=target: result_updates[name]
    return build_graph(node_overrides=overrides, max_total_calls=20)


class GoldenCaseRunnerTests(unittest.TestCase):
    def test_ten_cases_are_discoverable_and_fixture_excludes_human_answer(self):
        paths = discover_golden_cases(CASE_DIR)
        self.assertEqual(len(paths), 10)

        case = load_golden_case(paths[0])
        state = initial_state_for_case(case)
        self.assertEqual(state["account_context"]["golden_case_id"], case.case_id)
        self.assertIn("golden_case_fixture", state["account_context"])
        self.assertNotIn("human_analysis", state["account_context"]["golden_case_fixture"])

    def test_runner_executes_all_ten_cases_and_saves_structured_reports(self):
        with tempfile.TemporaryDirectory() as output_dir:
            runner = GoldenCaseRunner(
                graph_factory=_fixture_graph_factory,
                output_dir=output_dir,
            )
            batch = runner.run_directory(CASE_DIR)

            self.assertEqual(batch.case_count, 10)
            self.assertEqual(batch.passed_count, 10)
            self.assertEqual(batch.failed_count, 0)
            self.assertEqual(batch.error_count, 0)
            self.assertGreater(batch.average_agent_calls, 0)
            self.assertGreater(batch.maximum_agent_calls, 0)
            self.assertEqual(batch.loop_cutoff_count, 0)
            self.assertTrue(
                all(
                    case.business_evaluation.key_observations == "unknown"
                    for case in batch.cases
                )
            )

            self.assertTrue(
                Path(output_dir, "golden-case-report.json").is_file()
            )
            saved = json.loads(
                Path(output_dir, "golden-case-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(saved["case_count"], 10)
            self.assertEqual(
                saved["cases"][0]["schema_version"], "golden_case_run.v1"
            )
            for case in batch.cases:
                self.assertTrue(
                    Path(output_dir, f"{case.case_id}.run.json").is_file()
                )
                self.assertEqual(case.structural_checks.boundary, "pass")
                self.assertTrue(case.agent_outputs["supervisor_decision"])
                self.assertTrue(case.final_report)

    def test_forbidden_action_is_a_structural_failure_not_a_business_score(self):
        case = load_golden_case(CASE_DIR / "case-001-normal-account.yaml")

        class BadGraph:
            def invoke(self, _state):
                return {
                    "final_report": "fixture report",
                    "strategy_result": {
                        "overall_judgement": "fixture",
                        "priority_actions": [
                            {
                                "priority": 1,
                                "target_type": "plan",
                                "action_type": "pause_plan",
                                "reason": "fixture",
                                "expected_signal": "fixture",
                                "recheck_condition": "fixture",
                            }
                        ],
                    },
                }

        report = GoldenCaseRunner(graph_factory=lambda _case: BadGraph()).run_case(case)
        self.assertEqual(report.status, "failed")
        self.assertEqual(report.structural_checks.forbidden_outputs, "fail")
        self.assertEqual(report.business_evaluation.strategy_action_range, "unknown")
        self.assertTrue(report.errors == [])


if __name__ == "__main__":
    unittest.main()
