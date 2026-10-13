import unittest

from agents.supervisor_agent import (
    DEFAULT_AGENT_CALL_LIMITS,
    DEFAULT_MAX_TOTAL_CALLS,
    SupervisorAgent,
    supervisor_node,
)
from schemas.common import AgentName, OpenQuestion
from schemas.supervisor import SupervisorDecision


class _CaseStructuredRunnable:
    def __init__(self, model: "_CaseModel") -> None:
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        if not self.model.decisions:
            raise AssertionError("the fake model received more decisions than configured")
        return self.model.decisions.pop(0).model_dump(mode="json")


class _CaseModel:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _CaseStructuredRunnable(self)


def _state(**overrides):
    state = {
        "run_id": "supervisor-test-run",
        "report_date": "2026-09-17",
        "task": "检查当前 State 并继续协作",
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": None,
        "ads_diagnosis_result": None,
        "content_review_result": None,
        "strategy_result": None,
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


def _decision(target: AgentName, *, can_finish: bool = False) -> SupervisorDecision:
    return SupervisorDecision(
        next_agent=target,
        task=(
            "整理当前 State 中仍缺少的专业结果。"
            if target is not AgentName.FINISH
            else "汇总已有 State 并结束本轮协作。"
        ),
        reason="基于当前 State 的结果槽位、开放问题和调用状态做编排判断。",
        can_finish=can_finish,
    )


class SupervisorAgentTests(unittest.TestCase):
    def test_node_can_select_each_professional_agent(self):
        for target in (
            AgentName.DATA_ANALYSIS,
            AgentName.ADS_DIAGNOSIS,
            AgentName.CONTENT_REVIEW,
            AgentName.STRATEGY,
        ):
            with self.subTest(target=target):
                model = _CaseModel([_decision(target)])
                update = SupervisorAgent(model=model).run(_state())

                self.assertEqual(update["supervisor_decision"].next_agent, target)
                self.assertFalse(update["supervisor_decision"].can_finish)
                self.assertNotIn("errors", update)
                self.assertEqual(len(model.structured_inputs), 1)
                self.assertIs(model.structured_schema, SupervisorDecision)
                self.assertFalse(model.structured_include_raw)

    def test_node_can_finish_without_business_conclusion(self):
        model = _CaseModel([_decision(AgentName.FINISH, can_finish=True)])
        update = supervisor_node(model=model, state=_state())
        decision = update["supervisor_decision"]

        self.assertIsInstance(decision, SupervisorDecision)
        self.assertEqual(decision.next_agent, AgentName.FINISH)
        self.assertTrue(decision.can_finish)
        self.assertEqual(set(decision.model_dump()), set(SupervisorDecision.model_fields))
        forbidden = {
            "budget_change",
            "bid_change",
            "pause_plan",
            "scale_percentage",
            "execution_status",
            "backend_action",
            "final_strategy",
            "material_judgement",
        }
        self.assertTrue(forbidden.isdisjoint(decision.model_dump()))

    def test_agent_limit_records_warning_and_allows_one_redecision(self):
        model = _CaseModel(
            [
                _decision(AgentName.DATA_ANALYSIS),
                _decision(AgentName.STRATEGY),
            ]
        )
        state = _state(agent_call_counts={"data_analysis": 2}, total_agent_calls=2)
        update = SupervisorAgent(model=model).run(state)

        self.assertEqual(update["supervisor_decision"].next_agent, AgentName.STRATEGY)
        self.assertEqual(len(model.structured_inputs), 2)
        self.assertEqual(len(update["errors"]), 1)
        self.assertIn("调用限制已达到", update["errors"][0])
        self.assertIn("技术保护状态", model.structured_inputs[1][-1].content)
        self.assertIn("data_analysis", model.structured_inputs[1][-1].content)

    def test_second_limited_decision_enters_safe_finalize(self):
        model = _CaseModel(
            [
                _decision(AgentName.DATA_ANALYSIS),
                _decision(AgentName.DATA_ANALYSIS),
            ]
        )
        state = _state(agent_call_counts={"data_analysis": 2}, total_agent_calls=2)
        update = SupervisorAgent(model=model).run(state)
        decision = update["supervisor_decision"]

        self.assertEqual(decision.next_agent, AgentName.FINISH)
        self.assertTrue(decision.can_finish)
        self.assertEqual(len(model.structured_inputs), 2)
        self.assertTrue(any("调用限制已达到" in error for error in update["errors"]))
        self.assertEqual(len(update["open_questions"]), 1)
        self.assertIsInstance(update["open_questions"][0], OpenQuestion)
        self.assertTrue(update["open_questions"][0].blocking)

    def test_total_limit_enters_safe_finalize_without_another_model_decision(self):
        model = _CaseModel([])
        state = _state(total_agent_calls=DEFAULT_MAX_TOTAL_CALLS)
        update = SupervisorAgent(model=model).run(state)

        self.assertEqual(update["supervisor_decision"].next_agent, AgentName.FINISH)
        self.assertTrue(update["supervisor_decision"].can_finish)
        self.assertEqual(model.structured_inputs, [])
        self.assertIn("total_agent_calls", update["errors"][0])
        self.assertIn("调用限制已达到", update["errors"][0])

    def test_runtime_allowed_agent_guard_is_redecidable(self):
        model = _CaseModel(
            [
                _decision(AgentName.CONTENT_REVIEW),
                _decision(AgentName.DATA_ANALYSIS),
            ]
        )
        update = SupervisorAgent(
            model=model,
            allowed_agents=(AgentName.DATA_ANALYSIS, AgentName.FINISH),
        ).run(_state())

        self.assertEqual(update["supervisor_decision"].next_agent, AgentName.DATA_ANALYSIS)
        self.assertEqual(len(model.structured_inputs), 2)
        self.assertIn("不在运行时允许集合", update["errors"][0])

    def test_default_limits_are_technical_and_not_written_as_state_results(self):
        self.assertEqual(DEFAULT_AGENT_CALL_LIMITS[AgentName.STRATEGY.value], 2)
        model = _CaseModel([_decision(AgentName.STRATEGY)])
        state = _state(agent_call_counts={"strategy": 1}, total_agent_calls=1)
        update = SupervisorAgent(model=model).run(state)

        self.assertEqual(update.keys(), {"supervisor_decision"})
        self.assertNotIn("agent_call_counts", update)
        self.assertNotIn("total_agent_calls", update)


if __name__ == "__main__":
    unittest.main()
