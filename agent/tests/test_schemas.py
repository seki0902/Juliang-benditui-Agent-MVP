import unittest

from pydantic import ValidationError

from schemas.ads_diagnosis import AdsDiagnosisResult, DiagnosisItem
from schemas.common import AgentName, ConfidenceLevel, EvidenceItem, RouteRecord
from schemas.content_review import ContentReviewResult
from schemas.data_analysis import DataAnalysisResult
from schemas.strategy import StrategyResult
from schemas.supervisor import SupervisorDecision


class SchemaContractTests(unittest.TestCase):
    def test_valid_minimal_inputs_pass_for_all_top_level_outputs(self):
        outputs = [
            DataAnalysisResult(summary="data"),
            AdsDiagnosisResult(summary="diagnosis"),
            ContentReviewResult(summary="content"),
            StrategyResult(overall_judgement="strategy"),
            SupervisorDecision(
                next_agent=AgentName.FINISH,
                task="finish",
                reason="complete",
                can_finish=True,
            ),
        ]

        self.assertEqual(len(outputs), 5)

    def test_missing_required_fields_fail(self):
        required_cases = [
            (DataAnalysisResult, {}),
            (AdsDiagnosisResult, {}),
            (ContentReviewResult, {}),
            (StrategyResult, {}),
            (SupervisorDecision, {}),
            (EvidenceItem, {}),
        ]

        for model, payload in required_cases:
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model.model_validate(payload)

    def test_invalid_enums_fail(self):
        invalid_cases = [
            (
                SupervisorDecision,
                {"next_agent": "unknown_agent", "task": "x", "reason": "x"},
            ),
            (
                RouteRecord,
                {"from_agent": "unknown_agent", "to_agent": "finish", "reason": "x"},
            ),
            (
                DiagnosisItem,
                {"statement": "x", "certainty": "certain"},
            ),
        ]

        for model, payload in invalid_cases:
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model.model_validate(payload)

    def test_forbidden_structures_are_rejected(self):
        forbidden_payloads = [
            (DataAnalysisResult, {"summary": "x", "budget_change": "-20%"}),
            (AdsDiagnosisResult, {"summary": "x", "pause_plan": True}),
            (ContentReviewResult, {"summary": "x", "bid_change": 1}),
            (StrategyResult, {"overall_judgement": "x", "scale_percentage": 20}),
            (
                SupervisorDecision,
                {
                    "next_agent": "finish",
                    "task": "x",
                    "reason": "x",
                    "final_strategy": "x",
                },
            ),
        ]

        for model, payload in forbidden_payloads:
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model.model_validate(payload)

    def test_json_serialization_and_deserialization_are_consistent(self):
        outputs = [
            DataAnalysisResult(summary="data", confidence=ConfidenceLevel.MEDIUM),
            AdsDiagnosisResult(summary="diagnosis", confidence=ConfidenceLevel.LOW),
            ContentReviewResult(summary="content", confidence=ConfidenceLevel.HIGH),
            StrategyResult(overall_judgement="strategy", confidence=ConfidenceLevel.UNKNOWN),
            SupervisorDecision(
                next_agent=AgentName.STRATEGY,
                task="synthesize",
                reason="all evidence available",
            ),
        ]

        for output in outputs:
            with self.subTest(model=type(output).__name__):
                restored = type(output).model_validate_json(output.model_dump_json())
                self.assertEqual(restored.model_dump(mode="json"), output.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
