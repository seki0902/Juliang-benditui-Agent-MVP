import unittest

from langchain_core.messages import AIMessage, ToolMessage

from agents.data_analysis_agent import DataAnalysisAgent, data_analysis_node
from schemas.common import EvidenceItem, ToolCallRecord
from schemas.data_analysis import DataAnalysisResult
from state import apply_state_update


class _FakeStructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        return self.model.final_result


class _FakeToolModel:
    def __init__(self, planning_responses, final_result):
        self.planning_responses = list(planning_responses)
        self.final_result = final_result
        self.bound_tools = None
        self.planning_inputs = []
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.planning_inputs.append(list(messages))
        if self.planning_responses:
            return self.planning_responses.pop(0)
        return AIMessage(content="done")

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _FakeStructuredRunnable(self)


class DataAnalysisAgentTests(unittest.TestCase):
    def _state(self):
        return {
            "run_id": "run-1",
            "report_date": "2026-09-17",
            "task": "检查昨日账户表现并找出需要继续诊断的对象",
            "account_context": {"account_id": "acct-1"},
            "evidence": [],
        }

    def _result(self, summary="data observed"):
        return DataAnalysisResult(
            summary=summary,
            evidence=[
                EvidenceItem(
                    evidence_type="metric",
                    source="fixture.account",
                    object_type="account",
                    object_id="acct-1",
                    metric_or_content="spend",
                    value=42,
                    time_range="day",
                )
            ],
        )

    def test_node_uses_existing_context_without_tools(self):
        model = _FakeToolModel([], self._result("context is sufficient"))
        state = self._state()

        update = data_analysis_node(
            state,
            model=model,
            principles="fixture data-analysis principles",
            tools=None,
        )

        self.assertIsInstance(update["data_analysis_result"], DataAnalysisResult)
        self.assertEqual(
            update["data_analysis_result"].summary,
            "context is sufficient",
        )
        self.assertEqual(update["tool_trace"], [])
        self.assertEqual(len(update["evidence"]), 1)
        self.assertIsNone(model.bound_tools)
        self.assertIs(model.structured_schema, DataAnalysisResult)
        self.assertFalse(model.structured_include_raw)
        system_text = model.structured_inputs[0][0].content
        self.assertIn("你是数据分析 Agent", system_text)
        self.assertIn("fixture data-analysis principles", system_text)

    def test_node_loads_filled_project_principles_when_not_overridden(self):
        model = _FakeToolModel([], self._result("project principles loaded"))

        data_analysis_node(
            self._state(),
            model=model,
            tools=None,
        )

        system_text = model.structured_inputs[0][0].content
        self.assertIn("Data Analysis Principles", system_text)
        principle_text = system_text[system_text.rfind("# Data Analysis Principles") :]
        self.assertIn("P38", principle_text)
        self.assertIn("3天、5天", principle_text)
        self.assertNotIn("TODO", principle_text)

    def test_node_lets_model_select_and_execute_a_runtime_tool(self):
        calls = []

        def get_account_metrics(report_date, time_window):
            calls.append((report_date, time_window))
            return {
                "status": "ok",
                "source": "fixture.account",
                "time_window": {"name": time_window},
                "data": {"spend": 42},
                "warnings": [],
            }

        def get_history_context(**kwargs):
            raise AssertionError("the model selected account metrics, not history")

        model = _FakeToolModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_account_metrics",
                            "args": {
                                "report_date": "2026-09-17",
                                "time_window": "day",
                            },
                            "id": "call-account-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="evidence collected"),
            ],
            self._result("account evidence collected"),
        )

        update = DataAnalysisAgent(
            model=model,
            tools={
                "get_account_metrics": get_account_metrics,
                "get_history_context": get_history_context,
            },
            principles="fixture principles",
            max_tool_calls=2,
        ).run(self._state())

        self.assertEqual(calls, [("2026-09-17", "day")])
        self.assertEqual(len(model.bound_tools), 2)
        self.assertEqual(len(update["tool_trace"]), 1)
        self.assertIsInstance(update["tool_trace"][0], ToolCallRecord)
        self.assertEqual(update["tool_trace"][0].tool_name, "get_account_metrics")
        self.assertEqual(update["tool_trace"][0].status, "ok")
        self.assertIsInstance(update["data_analysis_result"], DataAnalysisResult)
        self.assertEqual(
            update["data_analysis_result"].summary,
            "account evidence collected",
        )
        tool_messages = [
            message
            for message in model.structured_inputs[0]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn('"spend": 42', tool_messages[0].content)

    def test_tool_results_and_agent_result_can_be_applied_to_state(self):
        model = _FakeToolModel([], self._result())
        state = self._state()

        update = DataAnalysisAgent(
            model=model,
            principles="fixture principles",
        ).run(state)
        next_state = apply_state_update(state, update)

        self.assertIsInstance(next_state["data_analysis_result"], DataAnalysisResult)
        self.assertEqual(len(next_state["evidence"]), 1)
        self.assertEqual(next_state["tool_trace"], [])

    def test_exact_tool_call_limit_does_not_create_a_spurious_error(self):
        def get_account_metrics(report_date, time_window):
            return {
                "status": "ok",
                "source": "fixture.account",
                "time_window": {"name": time_window},
                "data": {"spend": 42},
                "warnings": [],
            }

        model = _FakeToolModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_account_metrics",
                            "args": {
                                "report_date": "2026-09-17",
                                "time_window": "day",
                            },
                            "id": "call-account-limit",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            self._result("one tool call was enough"),
        )

        update = DataAnalysisAgent(
            model=model,
            tools={"get_account_metrics": get_account_metrics},
            principles="fixture principles",
            max_tool_calls=1,
        ).run(self._state())

        self.assertNotIn("errors", update)
        self.assertEqual(len(update["tool_trace"]), 1)

    def test_functional_entry_point_keeps_schema_and_strategy_boundary(self):
        model = _FakeToolModel([], self._result("no strategy fields"))

        update = data_analysis_node(
            self._state(),
            model=model,
            principles="fixture principles",
        )

        result = update["data_analysis_result"]
        self.assertEqual(set(result.model_dump()), set(DataAnalysisResult.model_fields))
        self.assertNotIn("budget_change", result.model_dump())
        self.assertNotIn("pause_plan", result.model_dump())


if __name__ == "__main__":
    unittest.main()
