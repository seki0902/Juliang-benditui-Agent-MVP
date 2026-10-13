from copy import deepcopy
from pathlib import Path
import unittest

import yaml
from pydantic import ValidationError

from schemas.golden_case import GoldenCase, GOLDEN_CASE_SCHEMA_VERSION


class GoldenCaseFormatTests(unittest.TestCase):
    CASE_DIR = Path(__file__).resolve().parents[1] / "docs" / "golden_cases"
    TEMPLATE_PATH = CASE_DIR / "case_template.yaml"

    def _template_data(self):
        return yaml.safe_load(self.TEMPLATE_PATH.read_text(encoding="utf-8"))

    def test_template_matches_the_versioned_case_contract(self):
        data = self._template_data()

        self.assertEqual(data["schema_version"], GOLDEN_CASE_SCHEMA_VERSION)
        self.assertEqual(
            set(data),
            {
                "schema_version",
                "case_id",
                "date",
                "description",
                "input_snapshot",
                "human_analysis",
                "expected_behavior",
            },
        )
        case = GoldenCase.model_validate(data)
        self.assertEqual(case.case_id, "case-template")
        self.assertEqual(case.input_snapshot.account["account_id"], "acct-example")
        self.assertIn("pause_plan", case.expected_behavior.forbidden_outputs)

    def test_missing_snapshot_values_can_be_explicitly_represented(self):
        data = deepcopy(self._template_data())
        data["input_snapshot"]["plans"] = None
        data["input_snapshot"]["materials"] = None
        data["input_snapshot"]["lead_quality"] = None
        data["input_snapshot"]["content_assets"] = None
        data["human_analysis"]["cannot_conclude"] = [
            "valid lead feedback is unavailable"
        ]

        case = GoldenCase.model_validate(data)
        self.assertIsNone(case.input_snapshot.plans)
        self.assertIn("valid lead feedback", case.human_analysis.cannot_conclude[0])

    def test_case_contract_rejects_unplanned_fields(self):
        data = deepcopy(self._template_data())
        data["unexpected"] = "do not add runtime fields here"

        with self.assertRaises(ValidationError):
            GoldenCase.model_validate(data)

        data = deepcopy(self._template_data())
        data["expected_behavior"]["budget_change"] = 100
        with self.assertRaises(ValidationError):
            GoldenCase.model_validate(data)

    def test_ten_deidentified_cases_load_offline(self):
        paths = sorted(self.CASE_DIR.glob("case-*.yaml"))

        self.assertEqual(
            [path.name for path in paths],
            [
                "case-001-normal-account.yaml",
                "case-002-plan-quality-break.yaml",
                "case-003-high-ctr-low-valid.yaml",
                "case-004-sample-shortage.yaml",
                "case-005-content-missing.yaml",
                "case-006-lead-quality-unavailable.yaml",
                "case-007-multiple-breaks.yaml",
                "case-008-verifiable-content-risk.yaml",
                "case-009-no-content-review.yaml",
                "case-010-strategy-evidence-gap.yaml",
            ],
        )

        cases = [
            GoldenCase.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            for path in paths
        ]
        self.assertEqual(
            [case.case_id for case in cases],
            [
                "case-001-normal-account",
                "case-002-plan-quality-break",
                "case-003-high-ctr-low-valid",
                "case-004-sample-shortage",
                "case-005-content-missing",
                "case-006-lead-quality-unavailable",
                "case-007-multiple-breaks",
                "case-008-verifiable-content-risk",
                "case-009-no-content-review",
                "case-010-strategy-evidence-gap",
            ],
        )

        (
            normal,
            plan_issue,
            high_ctr_low_valid,
            sample_shortage,
            content_missing,
            lead_quality_missing,
            multiple_breaks,
            verifiable_content_risk,
            no_content_review,
            strategy_evidence_gap,
        ) = cases
        self.assertEqual(normal.input_snapshot.account["valid_leads"], 8)
        self.assertEqual(plan_issue.input_snapshot.account["valid_leads"], 0)
        self.assertEqual(
            plan_issue.input_snapshot.plans[0]["spend_share_pct"], 90.8722
        )
        self.assertEqual(
            high_ctr_low_valid.input_snapshot.materials[0]["valid_lead_rate_pct"],
            20.0,
        )
        self.assertTrue(high_ctr_low_valid.human_analysis.content_review_needed)
        self.assertTrue(high_ctr_low_valid.input_snapshot.content_assets[0]["material_id"])
        self.assertEqual(sample_shortage.input_snapshot.account["clicks"], 5)
        self.assertIsNone(sample_shortage.input_snapshot.account["cpl"])
        self.assertFalse(sample_shortage.human_analysis.content_review_needed)
        self.assertTrue(content_missing.human_analysis.content_review_needed)
        self.assertEqual(
            content_missing.input_snapshot.content_assets[0]["material_id"],
            "material-e-01",
        )
        self.assertIsNone(lead_quality_missing.input_snapshot.lead_quality["valid_leads"])
        self.assertIsNone(
            lead_quality_missing.input_snapshot.lead_quality["contacted_leads"]
        )
        self.assertFalse(lead_quality_missing.human_analysis.content_review_needed)
        self.assertEqual(multiple_breaks.input_snapshot.plans[0]["leads"], 0)
        self.assertIsNone(multiple_breaks.input_snapshot.lead_quality["valid_leads"])
        self.assertTrue(verifiable_content_risk.human_analysis.content_review_needed)
        self.assertEqual(
            verifiable_content_risk.input_snapshot.content_assets[0]["asset_type"],
            "material_title",
        )
        self.assertFalse(no_content_review.human_analysis.content_review_needed)
        self.assertEqual(no_content_review.input_snapshot.account["clicks"], 5)
        self.assertIn(
            "data_analysis",
            strategy_evidence_gap.expected_behavior.acceptable_routes[0],
        )
        for case in cases:
            self.assertIn("budget_change", case.expected_behavior.forbidden_outputs)
            self.assertIn("backend_action", case.expected_behavior.forbidden_outputs)

    def test_template_documents_behavioral_not_exact_match_evaluation(self):
        readme = self.TEMPLATE_PATH.with_name("README.md").read_text(encoding="utf-8")

        self.assertIn("不是要求 Agent 逐字复制人工答案", readme)
        self.assertIn("3 天窗口", readme)
        self.assertIn("有效线索质量", readme)
        self.assertIn("forbidden_outputs", readme)
        self.assertIn("不直接执行投放", readme)


if __name__ == "__main__":
    unittest.main()
