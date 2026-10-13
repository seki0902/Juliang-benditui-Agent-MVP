"""V2 prompt contract, independent of editorial headings."""
from pathlib import Path
import re
import pytest

PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "strategy.md").read_text(encoding="utf-8")

@pytest.mark.parametrize("requirement", ["StrategyResult","priority_actions","why_not_other_actions","recheck_condition","invalidation_condition","proposal","recommendation_only","need_more_evidence","evidence_request","不编造"])
def test_v2_role_evidence_and_output_contract(requirement):
    assert requirement in PROMPT

def test_business_thresholds_stay_in_principles_and_no_fixed_tool_route():
    assert not re.search(r"\d+\s*%", PROMPT)
    assert not re.search(r"必须调用\s+`?get_[a-z_]+", PROMPT)
    assert "Principles" in PROMPT
