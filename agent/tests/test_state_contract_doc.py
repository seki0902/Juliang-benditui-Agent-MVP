from pathlib import Path
import unittest
from typing import get_type_hints

from state import MultiAgentState


class StateContractDocTests(unittest.TestCase):
    def test_contract_doc_mentions_every_state_field_and_required_sections(self):
        doc_path = Path(__file__).resolve().parents[1] / "docs" / "agent_state_contract.md"
        text = doc_path.read_text(encoding="utf-8")

        for field in get_type_hints(MultiAgentState):
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", text)

        for section in ("谁写", "谁读", "何时更新", "是否可为空", "覆盖字段", "追加字段"):
            with self.subTest(section=section):
                self.assertIn(section, text)


if __name__ == "__main__":
    unittest.main()
