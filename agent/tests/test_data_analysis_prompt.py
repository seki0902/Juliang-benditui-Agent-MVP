"""V2 prompt contract, independent of editorial headings."""
from pathlib import Path
import re
import pytest

PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "data_analysis.md").read_text(encoding="utf-8")

@pytest.mark.parametrize("requirement", ["DataAnalysisResult","observations/anomalies","missing_data","evidence","confidence","handoff_suggestion","不能自行补造"])
def test_v2_role_evidence_and_output_contract(requirement):
    assert requirement in PROMPT

def test_business_thresholds_stay_in_principles_and_no_fixed_tool_route():
    assert not re.search(r"\d+\s*%", PROMPT)
    assert not re.search(r"必须调用\s+`?get_[a-z_]+", PROMPT)
    assert "Principles" in PROMPT
