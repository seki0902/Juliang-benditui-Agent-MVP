"""Record the operator's execution result for a generated recommendation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from state_store import StateStore


ALLOWED_STATUSES = {"not_executed", "executed", "partially_executed", "incorrect", "observe"}


def main() -> None:
    parser = argparse.ArgumentParser(description="记录投放建议的人工执行反馈")
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--recommendation-id", required=True)
    parser.add_argument("--status", required=True, choices=sorted(ALLOWED_STATUSES))
    parser.add_argument("--actual-action")
    parser.add_argument("--actual-value")
    parser.add_argument("--operator")
    parser.add_argument("--result-json", help="可选：1日/3日观察结果 JSON")
    args = parser.parse_args()
    observed_result = json.loads(args.result_json) if args.result_json else {}
    with StateStore(args.state_db) as store:
        feedback_id = store.record_feedback(
            args.recommendation_id,
            args.status,
            actual_action=args.actual_action,
            actual_value=args.actual_value,
            operator=args.operator,
            observed_result=observed_result,
        )
    print(json.dumps({"feedback_id": feedback_id, "recommendation_id": args.recommendation_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
