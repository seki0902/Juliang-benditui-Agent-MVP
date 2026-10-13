"""Auto-discover exported Local Push reports and generate a daily report.

This is the manual-import fallback for environments without Ocean Engine API
permissions. The operator only needs to place the exported files in one input
directory; the tool identifies unit, material, and optional feedback files by
their headers and reuses the deterministic replay pipeline.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd

from replay_core import ReplayConfig, run_replay


SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}
UNIT_SIGNATURE = {"单元ID", "单元名称", "日期", "消耗(元)", "展示次数", "点击次数", "线索留资数(计费时间)"}
MATERIAL_SIGNATURE = {"素材ID", "素材名称", "日期", "消耗(元)", "展示次数", "点击次数", "线索留资数(计费时间)"}
FEEDBACK_DATE_NAMES = {"lead_date", "线索日期", "日期"}
FEEDBACK_FLAG_NAMES = {"is_valid", "是否有效", "有效留资", "is_contacted", "是否对接"}


def _read_headers(path: Path) -> set[str]:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        frame = pd.read_excel(path, sheet_name=0, nrows=0)
    elif path.suffix.lower() == ".csv":
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                frame = pd.read_csv(path, nrows=0, encoding=encoding)
                break
            except UnicodeDecodeError as exc:
                last_error = exc
        else:
            raise ValueError(f"无法读取 CSV 文件编码: {path.name}") from last_error
    else:
        raise ValueError(f"不支持的输入文件类型: {path.name}")
    return {str(column).strip() for column in frame.columns}


def classify_export(path: Path) -> str | None:
    """Classify a file from its headers, independent of its filename."""
    headers = _read_headers(path)
    if UNIT_SIGNATURE.issubset(headers):
        return "unit"
    if MATERIAL_SIGNATURE.issubset(headers):
        return "material"
    if FEEDBACK_DATE_NAMES.intersection(headers) and FEEDBACK_FLAG_NAMES.intersection(headers):
        return "feedback"
    return None


def _select_one(candidates: list[Path], kind: str) -> Path:
    if not candidates:
        raise FileNotFoundError(f"输入目录中没有识别到{kind}文件")
    # Exported files are normally timestamped. Use the newest one when an
    # input directory contains older exports as well, and expose the choice.
    return max(candidates, key=lambda path: path.stat().st_mtime)


def discover_exports(input_dir: Path) -> dict[str, Path | None]:
    if not input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    files = [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES]
    classified: dict[str, list[Path]] = {"unit": [], "material": [], "feedback": []}
    for path in files:
        kind = classify_export(path)
        if kind is not None:
            classified[kind].append(path)
    return {
        "unit": _select_one(classified["unit"], "单元/计划报表"),
        "material": _select_one(classified["material"], "视频/素材报表"),
        "feedback": max(classified["feedback"], key=lambda path: path.stat().st_mtime)
        if classified["feedback"]
        else None,
    }


def _path_argument(value: str | None, input_dir: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else input_dir / path


def run_import(
    input_dir: Path,
    output: Path,
    unit: str | None = None,
    material: str | None = None,
    feedback: str | None = None,
    exclude_latest_date: bool = False,
) -> dict:
    """Run deterministic file import and return the generated report.

    This function is intentionally independent from argument parsing so the
    daily scheduler can call the same code as a manual operator run.
    """
    discovered = discover_exports(input_dir)
    unit_path = _path_argument(unit, input_dir) or discovered["unit"]
    material_path = _path_argument(material, input_dir) or discovered["material"]
    feedback_path = _path_argument(feedback, input_dir) if feedback else discovered["feedback"]

    if unit_path is None or material_path is None:
        raise RuntimeError("单元/计划报表和视频/素材报表都必须存在")
    for path in [unit_path, material_path, feedback_path]:
        if path is not None and not path.exists():
            raise FileNotFoundError(f"指定输入文件不存在: {path}")

    config = ReplayConfig(exclude_latest_date=exclude_latest_date)
    return run_replay(unit_path, material_path, output, feedback_path, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="自动识别导出的本地推报表并生成日报")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "inputs",
        help="放置巨量导出文件的目录",
    )
    parser.add_argument("--unit", type=str, help="可选：指定单元/计划报表文件名或路径")
    parser.add_argument("--material", type=str, help="可选：指定视频/素材报表文件名或路径")
    parser.add_argument("--feedback", type=str, help="可选：指定石墨反馈文件名或路径")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "outputs" / "daily_reports" / "latest",
        help="日报输出目录",
    )
    parser.add_argument(
        "--exclude-latest-date",
        action="store_true",
        help="排除输入文件最大日期；只有文件包含当天未完成数据时使用",
    )
    args = parser.parse_args()

    report = run_import(
        args.input_dir,
        args.output,
        unit=args.unit,
        material=args.material,
        feedback=args.feedback,
        exclude_latest_date=args.exclude_latest_date,
    )
    discovered = discover_exports(args.input_dir)
    unit_path = _path_argument(args.unit, args.input_dir) or discovered["unit"]
    material_path = _path_argument(args.material, args.input_dir) or discovered["material"]
    feedback_path = _path_argument(args.feedback, args.input_dir) if args.feedback else discovered["feedback"]
    print(f"unit={unit_path}")
    print(f"material={material_path}")
    print(f"feedback={feedback_path or '未提供，质量指标将标记缺失'}")
    print(f"report={args.output / 'replay_report.md'}")
    print(f"data_date={report['data_date']}")
    print(f"mtd_leads={report['mtd']['leads']}")
    print(f"mtd_contacted_leads={report['mtd']['contacted_leads']}")
    print(f"anomalies={len(report['anomalies'])}")
    print(f"reconciliation_pass={report['reconciliation']['all_pass']}")


if __name__ == "__main__":
    main()
