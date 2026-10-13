"""Coverage of owner-provided V2 principles; no model judgement certification."""
from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parents[1] / "principles"

@pytest.mark.parametrize("name, markers", [
    ("data_analysis", ["P07", "P09", "P38", "P48", "P57"]),
    ("ads_diagnosis", ["P16", "P26", "P39", "P47", "P60", "P64"]),
    ("content_review", ["P14", "P20", "P40", "P48", "正文", "content_analysis_status"]),
    ("strategy", ["P03", "P41", "P42", "P43", "P64"]),
])
def test_role_principles_implement_owner_contract(name, markers):
    text = (ROOT / (name + ".md")).read_text(encoding="utf-8")
    assert "TODO" not in text
    for marker in markers:
        assert marker in text
    for marker in ["石墨", "有效＝加微信", "3天", "5天", "2–5天", "10%–30%"]:
        assert marker in text

def test_all_64_owner_rules_are_assigned():
    texts = "\n".join(p.read_text(encoding="utf-8") for p in ROOT.glob("*.md"))
    ids = {int(x) for x in re.findall(r"^- P(\d+)", texts, re.M)}
    assert set(range(1, 65)) <= ids
