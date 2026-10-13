"""Create the approved daily feedback headers in P2:W2."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from shimo_web_sheet import initialize_feedback_headers

if __name__ == '__main__':
    print(json.dumps(initialize_feedback_headers(), ensure_ascii=False))
