import unittest

from agents.ads_diagnosis_agent import AdsDiagnosisAgent
from agents.content_review_agent import ContentReviewAgent
from agents.data_analysis_agent import DataAnalysisAgent
from agents.strategy_agent import StrategyAgent
from agents.supervisor_agent import SupervisorAgent
from schemas.ads_diagnosis import AdsDiagnosisResult
from schemas.common import AgentName
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.strategy import StrategyResult
from schemas.supervisor import SupervisorDecision
from services.principle_loader import PrincipleLoader


class _StructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        return self.model.result.model_dump(mode="json")


class _StructuredModel:
    def __init__(self, result):
        self.result = result
        self.structured_inputs = []
        self.structured_schema = None
        self.structured_include_raw = None

    def with_structured_output(self, schema, *, include_raw=False):
        self.structured_schema = schema
        self.structured_include_raw = include_raw
        return _StructuredRunnable(self)


def _state():
    return {
        "run_id": "principle-injection-test",
        "report_date": "2026-09-17",
        "task": "verify principle injection boundaries",
        "account_context": {"account_id": "acct-1"},
        "data_analysis_result": None,
        "ads_diagnosis_result": None,
        "content_review_result": None,
        "strategy_result": None,
        "evidence": [],
        "open_questions": [],
        "agent_call_counts": {},
        "total_agent_calls": 0,
        "route_trace": [],
        "tool_trace": [],
        "errors": [],
    }


class PrincipleInjectionTests(unittest.TestCase):
    def setUp(self):
        self.loader = PrincipleLoader()
        self.principles = {
            AgentName.DATA_ANALYSIS: self.loader.load_text(AgentName.DATA_ANALYSIS),
            AgentName.ADS_DIAGNOSIS: self.loader.load_text(AgentName.ADS_DIAGNOSIS),
            AgentName.CONTENT_REVIEW: self.loader.load_text(AgentName.CONTENT_REVIEW),
            AgentName.STRATEGY: self.loader.load_text(AgentName.STRATEGY),
        }

    def test_each_specialist_receives_only_its_own_principles(self):
        specialists = (
            (AgentName.DATA_ANALYSIS, DataAnalysisAgent, DataAnalysisResult(summary="data")),
            (AgentName.ADS_DIAGNOSIS, AdsDiagnosisAgent, AdsDiagnosisResult(summary="diagnosis")),
            (AgentName.CONTENT_REVIEW, ContentReviewAgent, ContentReviewResult(summary="content")),
            (AgentName.STRATEGY, StrategyAgent, StrategyResult(overall_judgement="strategy")),
        )

        for agent_name, agent_class, result in specialists:
            with self.subTest(agent=agent_name):
                model = _StructuredModel(result)
                kwargs = {"model": model}
                if agent_name in (
                    AgentName.DATA_ANALYSIS,
                    AgentName.ADS_DIAGNOSIS,
                    AgentName.CONTENT_REVIEW,
                ):
                    kwargs["tools"] = None
                agent_class(**kwargs).run(_state())

                self.assertEqual(len(model.structured_inputs), 1)
                system_text = model.structured_inputs[0][0].content
                self.assertIn(self.principles[agent_name], system_text)
                for other_name, other_text in self.principles.items():
                    if other_name != agent_name:
                        self.assertNotIn(other_text, system_text)

    def test_supervisor_does_not_receive_any_business_principles(self):
        model = _StructuredModel(
            SupervisorDecision(
                next_agent=AgentName.FINISH,
                task="finish orchestration",
                reason="the injection boundary test is complete",
                can_finish=True,
            )
        )

        SupervisorAgent(model=model).run(_state())

        self.assertEqual(len(model.structured_inputs), 1)
        system_text = model.structured_inputs[0][0].content
        for principle_text in self.principles.values():
            self.assertNotIn(principle_text, system_text)
        self.assertNotIn("# Data Analysis Principles", system_text)
        self.assertNotIn("# Ads Diagnosis Principles", system_text)
        self.assertNotIn("# Content Review Principles", system_text)
        self.assertNotIn("# Strategy Principles", system_text)


if __name__ == "__main__":
    unittest.main()
