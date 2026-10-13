"""Command line entry point for deterministic local replay."""

from __future__ import annotations

import argparse
from pathlib import Path

from replay_core import ReplayConfig, run_replay


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Local Growth Agent local replay")
    parser.add_argument("--unit", required=True, type=Path, help="unit detail xlsx")
    parser.add_argument("--material", required=True, type=Path, help="material detail xlsx")
    parser.add_argument("--output", required=True, type=Path, help="output directory")
    parser.add_argument("--feedback", type=Path, default=None, help="optional Shimo lead feedback export")
    parser.add_argument("--include-latest-date", action="store_true", help="include the latest snapshot date")
    args = parser.parse_args()

    config = ReplayConfig(exclude_latest_date=not args.include_latest_date)
    report = run_replay(args.unit, args.material, args.output, args.feedback, config)
    print(f"report={args.output / 'replay_report.md'}")
    print(f"data_date={report['data_date']}")
    print(f"mtd_leads={report['mtd']['leads']}")
    print(f"mtd_contacted_leads={report['mtd']['contacted_leads']}")
    print(f"anomalies={len(report['anomalies'])}")
    print(f"reconciliation_pass={report['reconciliation']['all_pass']}")


if __name__ == "__main__":
    main()
