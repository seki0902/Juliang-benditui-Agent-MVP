"""Read-only daily adapter using the same bounded runtime as shadow runs."""
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from config import Settings
from services.pipeline_switch import DailyPipelineRequest


def run_daily_agent(request: DailyPipelineRequest, *, settings=None, client=None):
    settings = settings or Settings.from_env()
    missing = settings.missing_for_agent() if client is None else []
    if request.input_dir is None:
        missing.append("--input-dir")
    if missing:
        return {"status": "blocked", "missing": missing, "push": "disabled"}
    source = Path(__file__).resolve().parents[1]
    inputs = request.input_dir.resolve()
    output = (request.output_root or source / "outputs" / "daily_reports" / "multi_agent").resolve()
    if output.is_relative_to(inputs) or inputs.is_relative_to(output):
        raise ValueError("output must be separate from input directory")
    report_date = request.report_date or (
        datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
    date.fromisoformat(report_date)
    args = SimpleNamespace(
        shimo_snapshot=request.shimo_snapshot, plan_context=request.plan_context,
        content_snapshot=request.content_snapshot, per_agent=2, max_total=8,
        max_tools=32, max_model_calls=40, timeout=45, deadline=600,
        task="分析本账户截至报告日的投放情况，保留不确定性，形成中文日报和供人工执行的建议。")
    from services.model_runtime import run_account
    return run_account(source, inputs, output, report_date, settings, args, client=client)
