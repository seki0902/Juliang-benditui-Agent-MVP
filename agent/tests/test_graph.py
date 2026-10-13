import unittest
from datetime import datetime

from graph import (
    GRAPH_NODE_NAMES,
    PROFESSIONAL_NODE_NAMES,
    SUPERVISOR_ROUTE_MAP,
    GraphBuildError,
    GraphRoutingError,
    build_graph,
    conditional_route,
    route_from_supervisor,
)
from schemas.common import AgentName
from schemas.data_analysis import DataAnalysisResult
from schemas.supervisor import SupervisorDecision


def _decision(target: AgentName, *, can_finish: bool = False) -> SupervisorDecision:
    return SupervisorDecision(
        next_agent=target,
        task="处理当前 State 的下一项协作任务。",
        reason="由 Supervisor 根据当前 State 决定 Graph 下一节点。",
        can_finish=can_finish,
    )


def _state(**overrides):
    state = {
        "run_id": "graph-test-run",
        "report_date": "2026-09-17",
        "task": "检查 Graph 编排",
        "account_context": {"account_id": "acct-1"},
        "open_questions": [],
        "evidence": [],
        "agent_call_counts": {},
        "total_agent_calls": 0,
        "route_trace": [],
        "tool_trace": [],
        "errors": [],
    }
    state.update(overrides)
    return state


class GraphSkeletonTests(unittest.TestCase):
    def test_graph_compiles_with_all_required_nodes(self):
        graph = build_graph()
        drawable = graph.get_graph()
        nodes = set(drawable.nodes)

        self.assertTrue(set(GRAPH_NODE_NAMES).issubset(nodes))
        self.assertIn("__start__", nodes)
        self.assertIn("__end__", nodes)

    def test_supervisor_route_map_covers_four_agents_and_finish(self):
        expected = {
            AgentName.DATA_ANALYSIS: "data_analysis_agent",
            AgentName.ADS_DIAGNOSIS: "ads_diagnosis_agent",
            AgentName.CONTENT_REVIEW: "content_review_agent",
            AgentName.STRATEGY: "strategy_agent",
            AgentName.FINISH: "report_renderer",
        }

        self.assertEqual(
            SUPERVISOR_ROUTE_MAP,
            {agent.value: destination for agent, destination in expected.items()},
        )
        for target, destination in expected.items():
            with self.subTest(target=target):
                decision = _decision(target, can_finish=target is AgentName.FINISH)
                self.assertEqual(
                    route_from_supervisor({"supervisor_decision": decision}),
                    destination,
                )
                self.assertEqual(
                    conditional_route({"supervisor_decision": decision}),
                    destination,
                )

    def test_conditional_route_ignores_business_payload_and_uses_only_decision_target(self):
        decision = _decision(AgentName.CONTENT_REVIEW)
        state = {
            "supervisor_decision": decision,
            "ctr": 0.99,
            "cpl": 0.01,
            "anomalies": ["业务字段不应参与 Graph 路由"],
            "data_analysis_result": {"summary": "业务结果由 Agent 负责"},
        }

        self.assertEqual(conditional_route(state), "content_review_agent")

    def test_each_professional_node_returns_to_supervisor_without_fixed_business_edges(self):
        graph = build_graph()
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

        for node_name in PROFESSIONAL_NODE_NAMES:
            with self.subTest(node=node_name):
                self.assertIn((node_name, "supervisor"), edges)

        self.assertNotIn(("data_analysis_agent", "ads_diagnosis_agent"), edges)
        self.assertNotIn(("ads_diagnosis_agent", "content_review_agent"), edges)
        self.assertNotIn(("content_review_agent", "strategy_agent"), edges)

    def test_conditional_edges_reach_each_selected_destination(self):
        expected_routes = {
            AgentName.DATA_ANALYSIS: "data_analysis_agent",
            AgentName.ADS_DIAGNOSIS: "ads_diagnosis_agent",
            AgentName.CONTENT_REVIEW: "content_review_agent",
            AgentName.STRATEGY: "strategy_agent",
            AgentName.FINISH: "report_renderer",
        }

        for target, destination in expected_routes.items():
            with self.subTest(target=target):
                supervisor_calls = 0
                visited = []

                def supervisor(_state):
                    nonlocal supervisor_calls
                    supervisor_calls += 1
                    if supervisor_calls == 1:
                        return {
                            "supervisor_decision": _decision(
                                target,
                                can_finish=target is AgentName.FINISH,
                            )
                        }
                    return {
                        "supervisor_decision": _decision(
                            AgentName.FINISH,
                            can_finish=True,
                        )
                    }

                def selected(_state, *, node_name=destination):
                    visited.append(node_name)
                    return {}

                def report(_state):
                    visited.append("report_renderer")
                    return {"final_report": "fixture"}

                overrides = {
                    "supervisor": supervisor,
                    "report_renderer": report,
                    "push": lambda _state: {"push_result": {"status": "skipped"}},
                }
                if destination != "report_renderer":
                    overrides[destination] = selected
                result = build_graph(node_overrides=overrides).invoke(
                    {
                        "report_date": "2026-09-17",
                        "task": "验证条件路由",
                        "account_context": {"account_id": "acct-1"},
                    }
                )

                self.assertIn(destination, visited)
                self.assertEqual(result["push_result"]["status"], "skipped")

    def test_injected_nodes_can_follow_supervisor_to_report_and_push(self):
        supervisor_calls = []

        def bootstrap(_state):
            return {"account_context": {"account_id": "acct-1"}}

        def supervisor(state):
            supervisor_calls.append(dict(state))
            if state.get("data_analysis_result") is None:
                decision = _decision(AgentName.DATA_ANALYSIS)
            else:
                decision = _decision(AgentName.FINISH, can_finish=True)
            return {
                "supervisor_decision": decision,
                "route_trace": [{"step": 999, "from": "spoofed_node"}],
            }

        def data_analysis(_state):
            return {
                "data_analysis_result": DataAnalysisResult(summary="fixture data result"),
                "route_trace": [{"step": 1000, "from": "spoofed_agent"}],
            }

        def report(_state):
            return {"final_report": "fixture report"}

        def push(_state):
            return {"push_result": {"status": "skipped"}}

        graph = build_graph(
            node_overrides={
                "bootstrap_context": bootstrap,
                "supervisor": supervisor,
                "data_analysis_agent": data_analysis,
                "report_renderer": report,
                "push": push,
            }
        )
        result = graph.invoke(_state())

        self.assertEqual(result["data_analysis_result"].summary, "fixture data result")
        self.assertEqual(result["final_report"], "fixture report")
        self.assertEqual(result["push_result"]["status"], "skipped")
        self.assertEqual(len(supervisor_calls), 2)
        self.assertEqual(len(result["route_trace"]), 2)
        self.assertEqual(
            [record.to_agent for record in result["route_trace"]],
            [AgentName.DATA_ANALYSIS, AgentName.FINISH],
        )
        self.assertEqual([record.step for record in result["route_trace"]], [1, 2])
        for record in result["route_trace"]:
            with self.subTest(record=record):
                self.assertEqual(record.from_agent, "supervisor")
                self.assertTrue(record.task)
                self.assertTrue(record.reason)
                self.assertIsNotNone(datetime.fromisoformat(record.timestamp))
        trace_payload = result["route_trace"][0].model_dump(mode="json", by_alias=True)
        self.assertEqual(
            set(trace_payload),
            {"step", "from", "to", "reason", "task", "timestamp"},
        )
        self.assertEqual(trace_payload["from"], "supervisor")

    def test_invalid_or_missing_supervisor_decision_fails_closed(self):
        with self.assertRaises(GraphRoutingError):
            route_from_supervisor({})

        with self.assertRaises(GraphRoutingError):
            route_from_supervisor(
                {"supervisor_decision": {"next_agent": "unknown", "task": "x", "reason": "x"}}
            )

        with self.assertRaises(GraphRoutingError):
            route_from_supervisor(
                {"supervisor_decision": _decision(AgentName.FINISH, can_finish=False)}
            )

        with self.assertRaises(GraphRoutingError):
            route_from_supervisor([])

        with self.assertRaises(GraphRoutingError):
            route_from_supervisor(
                {"supervisor_decision": {"next_agent": "content_review"}}
            )

    def test_invalid_node_override_is_rejected_before_compile(self):
        with self.assertRaises(GraphBuildError):
            build_graph(node_overrides={"not_a_node": lambda _state: {}})

        with self.assertRaises(GraphBuildError):
            build_graph(node_overrides={"supervisor": object()})

    def test_call_limit_configuration_is_validated_at_build_time(self):
        graph = build_graph(
            agent_call_limits={
                AgentName.DATA_ANALYSIS: 1,
                "ads_diagnosis": 3,
            },
            max_total_calls=5,
        )
        self.assertIsNotNone(graph)

        invalid_configs = [
            {"agent_call_limits": {"unknown_agent": 1}},
            {"agent_call_limits": {"finish": 1}},
            {"agent_call_limits": {"data_analysis": -1}},
            {"agent_call_limits": {"data_analysis": True}},
            {"max_total_calls": -1},
            {"max_total_calls": True},
        ]
        for kwargs in invalid_configs:
            with self.subTest(kwargs=kwargs), self.assertRaises(GraphBuildError):
                build_graph(**kwargs)

    def test_repeating_professional_route_stops_at_per_agent_limit(self):
        calls = {"supervisor": 0, "data_analysis": 0}
        report_states = []

        def supervisor(_state):
            calls["supervisor"] += 1
            return {"supervisor_decision": _decision(AgentName.DATA_ANALYSIS)}

        def data_analysis(_state):
            calls["data_analysis"] += 1
            return {
                "data_analysis_result": DataAnalysisResult(
                    summary=f"fixture data result {calls['data_analysis']}"
                )
            }

        def report(state):
            report_states.append(dict(state))
            return {"final_report": "limit report"}

        result = build_graph(
            node_overrides={
                "supervisor": supervisor,
                "data_analysis_agent": data_analysis,
                "report_renderer": report,
            },
            agent_call_limits={"data_analysis": 2},
            max_total_calls=8,
        ).invoke(_state())

        self.assertEqual(calls["data_analysis"], 2)
        self.assertEqual(calls["supervisor"], 3)
        self.assertEqual(result["agent_call_counts"]["data_analysis"], 2)
        self.assertEqual(result["total_agent_calls"], 2)
        self.assertEqual(result["final_report"], "limit report")
        self.assertEqual(
            result["supervisor_decision"].next_agent,
            AgentName.FINISH,
        )
        self.assertTrue(result["supervisor_decision"].can_finish)
        self.assertTrue(any("data_analysis" in error for error in result["errors"]))
        self.assertTrue(any(question.blocking for question in result["open_questions"]))
        self.assertEqual(report_states[-1]["total_agent_calls"], 2)
        self.assertEqual(
            [record.to_agent for record in result["route_trace"]],
            [AgentName.DATA_ANALYSIS, AgentName.DATA_ANALYSIS, AgentName.FINISH],
        )
        self.assertEqual([record.step for record in result["route_trace"]], [1, 2, 3])

    def test_total_professional_call_limit_stops_mixed_route(self):
        targets = [
            AgentName.DATA_ANALYSIS,
            AgentName.ADS_DIAGNOSIS,
            AgentName.STRATEGY,
            AgentName.CONTENT_REVIEW,
        ]
        supervisor_calls = 0
        professional_calls = {name.value: 0 for name in targets}

        def supervisor(_state):
            nonlocal supervisor_calls
            target = targets[min(supervisor_calls, len(targets) - 1)]
            supervisor_calls += 1
            return {"supervisor_decision": _decision(target)}

        def professional(_state, *, target_name):
            professional_calls[target_name] += 1
            return {}

        overrides = {"supervisor": supervisor}
        for target in targets:
            overrides[f"{target.value}_agent"] = (
                lambda state, target_name=target.value: professional(
                    state,
                    target_name=target_name,
                )
            )

        result = build_graph(
            node_overrides=overrides,
            agent_call_limits={target.value: 10 for target in targets},
            max_total_calls=3,
        ).invoke(_state())

        self.assertEqual(supervisor_calls, 3)
        self.assertEqual(
            professional_calls,
            {
                "data_analysis": 1,
                "ads_diagnosis": 1,
                "strategy": 1,
                "content_review": 0,
            },
        )
        self.assertEqual(result["total_agent_calls"], 3)
        self.assertEqual(result["agent_call_counts"]["data_analysis"], 1)
        self.assertEqual(result["agent_call_counts"]["ads_diagnosis"], 1)
        self.assertEqual(result["agent_call_counts"]["strategy"], 1)
        self.assertTrue(any("total_agent_calls=3" in error for error in result["errors"]))
        self.assertEqual(result["supervisor_decision"].next_agent, AgentName.FINISH)
        self.assertEqual(
            [record.to_agent for record in result["route_trace"]],
            [
                AgentName.DATA_ANALYSIS,
                AgentName.ADS_DIAGNOSIS,
                AgentName.STRATEGY,
                AgentName.FINISH,
            ],
        )
        self.assertEqual([record.step for record in result["route_trace"]], [1, 2, 3, 4])

    def test_professional_agent_failure_is_safe_and_counted(self):
        calls = {"supervisor": 0, "data_analysis": 0}

        def supervisor(_state):
            calls["supervisor"] += 1
            return {"supervisor_decision": _decision(AgentName.DATA_ANALYSIS)}

        def broken_data_analysis(_state):
            calls["data_analysis"] += 1
            raise RuntimeError("fixture failure")

        result = build_graph(
            node_overrides={
                "supervisor": supervisor,
                "data_analysis_agent": broken_data_analysis,
                "report_renderer": lambda _state: {"final_report": "failure report"},
            }
        ).invoke(_state())

        self.assertEqual(calls["data_analysis"], 1)
        self.assertEqual(result["agent_call_counts"]["data_analysis"], 1)
        self.assertEqual(result["total_agent_calls"], 1)
        self.assertEqual(result["final_report"], "failure report")
        self.assertTrue(any("RuntimeError" in error for error in result["errors"]))
        self.assertEqual(result["supervisor_decision"].next_agent, AgentName.FINISH)
        self.assertEqual(
            [record.to_agent for record in result["route_trace"]],
            [AgentName.DATA_ANALYSIS, AgentName.FINISH],
        )
        self.assertEqual([record.step for record in result["route_trace"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
