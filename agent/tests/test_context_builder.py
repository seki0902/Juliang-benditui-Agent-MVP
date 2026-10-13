import unittest
from datetime import datetime

from graph import build_graph
from schemas.common import AgentName
from schemas.supervisor import SupervisorDecision
from services.context_builder import (
    BootstrapContextError,
    DEFAULT_TASK,
    ContextBuilder,
    bootstrap_context,
)


class BootstrapContextTests(unittest.TestCase):
    def test_bootstrap_initializes_complete_state_without_business_results(self):
        state = {
            "report_date": "2026-09-17",
            "task": "检查当前账户的投放数据",
            "account_context": {"account_id": "acct-1", "account_name": "fixture"},
        }

        update = bootstrap_context(
            state,
            run_id_factory=lambda: "run-fixture",
        )

        self.assertEqual(update["run_id"], "run-fixture")
        self.assertEqual(update["report_date"], "2026-09-17")
        self.assertEqual(update["task"], "检查当前账户的投放数据")
        self.assertEqual(update["account_context"], state["account_context"])
        self.assertEqual(update["agent_call_counts"], {})
        self.assertEqual(update["total_agent_calls"], 0)
        for field in (
            "open_questions",
            "evidence",
            "route_trace",
            "tool_trace",
            "agent_trace",
            "errors",
        ):
            with self.subTest(field=field):
                self.assertEqual(update[field], [])
        for field in (
            "data_analysis_result",
            "ads_diagnosis_result",
            "content_review_result",
            "strategy_result",
            "supervisor_decision",
            "final_report",
            "push_result",
        ):
            with self.subTest(field=field):
                self.assertIsNone(update[field])

    def test_missing_entry_metadata_uses_date_clock_and_neutral_task(self):
        update = bootstrap_context(
            {},
            run_id_factory=lambda: "run-empty-input",
            clock=lambda: datetime(2026, 9, 17, 23, 59),
        )

        self.assertEqual(update["run_id"], "run-empty-input")
        self.assertEqual(update["report_date"], "2026-09-16")
        self.assertEqual(update["task"], DEFAULT_TASK)
        self.assertEqual(update["account_context"], {})

    def test_loader_adds_only_lightweight_context_and_sensitive_values_are_removed(self):
        loader_states = []

        def load_context(state):
            loader_states.append(state)
            return {
                "account_id": "acct-loaded",
                "project_id": "project-1",
                "api_key": "should-not-enter-state",
                "nested": {"access_token": "also-hidden", "label": "local"},
                "account_metrics": {"spend": 100},
                "initial_findings": ["must be computed by an Agent"],
            }

        update = bootstrap_context(
            {
                "report_date": "2026-09-17",
                "task": "读取入口上下文",
                "account_context": {"account_name": "fixture"},
            },
            account_context_loader=load_context,
            account_context={"account_id": "acct-override"},
            run_id_factory=lambda: "run-context",
        )

        self.assertEqual(len(loader_states), 1)
        self.assertEqual(update["account_context"]["account_id"], "acct-override")
        self.assertEqual(update["account_context"]["project_id"], "project-1")
        self.assertNotIn("api_key", update["account_context"])
        self.assertNotIn("access_token", update["account_context"]["nested"])
        self.assertNotIn("account_metrics", update["account_context"])
        self.assertNotIn("initial_findings", update["account_context"])
        self.assertEqual(update["account_context"]["nested"]["label"], "local")

    def test_bootstrap_preserves_resume_state_and_does_not_mutate_input(self):
        state = {
            "run_id": "run-resume",
            "report_date": "2026-09-17",
            "task": "恢复协作",
            "account_context": {"account_id": "acct-1"},
            "agent_call_counts": {"data_analysis": 1},
            "total_agent_calls": 1,
            "route_trace": [{"to_agent": "data_analysis"}],
            "errors": ["previous warning"],
        }

        update = ContextBuilder().build(state)

        self.assertEqual(update["run_id"], "run-resume")
        self.assertEqual(update["agent_call_counts"], {"data_analysis": 1})
        self.assertEqual(update["total_agent_calls"], 1)
        self.assertEqual(update["route_trace"], [{"to_agent": "data_analysis"}])
        self.assertEqual(update["errors"], ["previous warning"])
        self.assertIsNot(update["agent_call_counts"], state["agent_call_counts"])
        self.assertIsNot(update["route_trace"], state["route_trace"])

    def test_invalid_state_containers_fail_closed(self):
        with self.assertRaisesRegex(BootstrapContextError, "must be a list"):
            bootstrap_context(
                {
                    "report_date": "2026-09-17",
                    "task": "检查",
                    "route_trace": {},
                }
            )

        with self.assertRaisesRegex(BootstrapContextError, "non-negative integer"):
            bootstrap_context(
                {
                    "report_date": "2026-09-17",
                    "task": "检查",
                    "agent_call_counts": {"data_analysis": -1},
                }
            )

        with self.assertRaisesRegex(BootstrapContextError, "lightweight"):
            bootstrap_context(
                {
                    "report_date": "2026-09-17",
                    "task": "检查",
                    "account_context": {"opaque": object()},
                }
            )

    def test_graph_uses_real_bootstrap_by_default(self):
        observed_states = []

        def supervisor(state):
            observed_states.append(dict(state))
            return {
                "supervisor_decision": SupervisorDecision(
                    next_agent=AgentName.FINISH,
                    task="交由报告层汇总已有 State",
                    reason="入口上下文已准备完成",
                    can_finish=True,
                )
            }

        graph = build_graph(
            node_overrides={
                "supervisor": supervisor,
                "report_renderer": lambda _state: {"final_report": "fixture"},
                "push": lambda _state: {"push_result": {"status": "skipped"}},
            }
        )
        result = graph.invoke(
            {
                "report_date": "2026-09-17",
                "task": "检查入口",
                "account_context": {"account_id": "acct-1"},
            }
        )

        self.assertEqual(len(observed_states), 1)
        self.assertEqual(observed_states[0]["account_context"]["account_id"], "acct-1")
        self.assertEqual(observed_states[0]["agent_call_counts"], {})
        self.assertEqual(observed_states[0]["route_trace"], [])
        self.assertEqual(result["final_report"], "fixture")
        self.assertEqual(result["push_result"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
