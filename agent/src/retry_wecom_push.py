"""Retry only an already-generated Enterprise WeChat daily-summary push."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wecom_pusher import push_daily_report


def main() -> None:
    parser = argparse.ArgumentParser(description="重试已生成日报的企业微信推送，不下载或重算数据")
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--recommendations-json", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report_json.read_text(encoding="utf-8"))
    recommendations = json.loads(args.recommendations_json.read_text(encoding="utf-8")).get("recommendations") or []
    print(json.dumps(push_daily_report(report, recommendations), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
