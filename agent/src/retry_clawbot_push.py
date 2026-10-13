"""Retry only a completed report's PushPlus ClawBot delivery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from clawbot_pusher import push_daily_report


def main() -> None:
    parser = argparse.ArgumentParser(description="重试已生成日报的微信 ClawBot 推送；不会下载或重算数据")
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--recommendations-json", type=Path, required=True)
    parser.add_argument("--analysis-supplement", action="store_true", help="以分析补充标题发送，适合修正已发送的日报内容")
    args = parser.parse_args()
    report = json.loads(args.report_json.read_text(encoding="utf-8"))
    recommendations = json.loads(args.recommendations_json.read_text(encoding="utf-8")).get("recommendations") or []
    title = None
    if args.analysis_supplement:
        title = f"本地推日报分析补充｜{report.get('data_date', '未知日期')}"
    print(json.dumps(push_daily_report(report, recommendations, title=title), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
