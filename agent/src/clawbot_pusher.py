"""Push a completed Local Growth Agent daily summary through PushPlus ClawBot.

The PushPlus token is intentionally loaded at runtime from a local file or an
environment variable.  It is never written to reports, logs, or source code.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from run_observer import clean


class ClawBotPushError(RuntimeError):
    pass


@dataclass(frozen=True)
class PushPlusConfig:
    token: str


def _token_file() -> Path:
    configured = os.getenv("PUSHPLUS_TOKEN_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    # Project-local credential supplied by the operator. This file is ignored
    # by Git and is outside generated report directories.
    return Path(__file__).resolve().parents[2] / "pushplus token.txt"


def load_pushplus_config() -> PushPlusConfig | None:
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        path = _token_file()
        if not path.exists():
            return None
        token = path.read_text(encoding="utf-8-sig").strip()
    if not token:
        raise ClawBotPushError("PushPlus Token 文件为空")
    if any(char.isspace() for char in token):
        raise ClawBotPushError("PushPlus Token 必须为单行文本，不能包含空格或换行")
    return PushPlusConfig(token=token)


def _fmt(value: Any) -> str:
    if value is None:
        return "待补录"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _money(value: Any) -> str:
    return "待补录" if value is None else f"{float(value):.2f} 元"


def _percent(value: Any) -> str:
    return "待补录" if value is None else f"{float(value) * 100:.2f}%"


def _named(item: dict[str, Any], name_key: str, id_key: str) -> str:
    return str(item.get(name_key) or item.get(id_key) or "未命名对象")


def _analysis_lines(report: dict[str, Any], recommendations: list[dict[str, Any]]) -> list[str]:
    """Turn the completed report into bounded, decision-oriented observations.

    A single day can reveal where delivery went, but it cannot by itself justify
    changing budget or status.  The wording therefore separates observations
    from executable recommendations.
    """
    metrics = report.get("yesterday") or {}
    plans = sorted(report.get("plan_summary") or [], key=lambda item: float(item.get("spend") or 0), reverse=True)
    materials = sorted(report.get("material_summary") or [], key=lambda item: float(item.get("spend") or 0), reverse=True)
    active_days = max((int(item.get("active_days") or 0) for item in plans), default=0)
    lines = ["", "【数据判断】"]
    if metrics.get("valid_leads") is None or metrics.get("contacted_leads") is None:
        lines.append(f"- 平台有 {int(metrics.get('leads') or 0)} 条留资，但有效留资/已对接数据尚未补录；暂时只能判断获客，不能判断线索质量或真实成本。")
    else:
        lines.append(
            f"- 平台留资 {int(metrics.get('leads') or 0)} 条，其中有效留资 {int(metrics.get('valid_leads') or 0)} 条、已对接 {int(metrics.get('contacted_leads') or 0)} 条。"
        )
    if active_days <= 1:
        lines.append("- 当前只有 1 个自然日数据。以下是投放观察，不构成调预算或暂停依据；今天保持预算和开关不动。")

    if plans:
        lines.extend(["", "【计划观察】"])
        for plan in plans[:3]:
            name = _named(plan, "unit_name", "unit_id")
            spend = float(plan.get("spend") or 0)
            leads = int(plan.get("leads") or 0)
            clicks = int(plan.get("clicks") or 0)
            if leads:
                lines.append(
                    f"- {name}：消耗 {_money(spend)}，贡献 {leads} 条留资，CTR {_percent(plan.get('ctr'))}、留资转化率 {_percent(plan.get('lead_conversion_rate'))}；是当天主要留资来源，继续观察，不因单日数据放量。"
                )
            else:
                ctr = plan.get("ctr")
                account_ctr = metrics.get("ctr")
                hint = "点击率低于账户整体" if ctr is not None and account_ctr is not None and float(ctr) < float(account_ctr) else "暂未形成留资"
                lines.append(
                    f"- {name}：消耗 {_money(spend)}，{clicks} 次点击、0 条留资，{hint}（CTR {_percent(ctr)}）；样本仍小，先累计数据，暂不降预算。"
                )

    if materials:
        lines.extend(["", "【素材观察】"])
        for material in materials[:2]:
            name = _named(material, "material_name", "material_id")
            spend = float(material.get("spend") or 0)
            leads = int(material.get("leads") or 0)
            clicks = int(material.get("clicks") or 0)
            if leads:
                lines.append(
                    f"- 《{name}》：消耗 {_money(spend)}，{leads} 条留资，留资成本 {_money(material.get('lead_cost'))}；保留观察，待补有效留资后再评价质量。"
                )
            else:
                lines.append(
                    f"- 《{name}》：消耗 {_money(spend)}，{clicks} 次点击、0 条留资；先观察后续转化，若连续多日仍无留资再检查素材承诺、定向和承接。"
                )

    lines.extend(["", "【今天要做】"])
    if recommendations:
        lines.append("- 以下建议已达到规则条件，但仍须逐条人工确认后才能执行：")
        for item in recommendations[:3]:
            target = item.get("object_name") or item.get("object_id") or "未命名对象"
            lines.append(f"  • {item.get('priority', 'P2')}｜{item.get('action', 'HOLD')}｜{target}")
    else:
        lines.append(f"- 检查 {report.get('data_date', '当日')} 的有效留资和已对接结果；系统今日不提出预算或暂停动作。")
    return lines


def render_summary(report: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
    """Render a compact, decision-oriented plaintext ClawBot report."""
    metrics = report.get("yesterday") or {}
    lines = [
        f"本地推运营日报｜{report.get('data_date', '未知日期')}",
        f"消耗：{_money(metrics.get('spend'))}｜平台留资：{_fmt(metrics.get('leads'))} 条｜留资成本：{_money(metrics.get('lead_cost'))}",
        f"曝光：{_fmt(metrics.get('impressions'))}｜点击：{_fmt(metrics.get('clicks'))}｜CTR：{_percent(metrics.get('ctr'))}｜点击后留资率：{_percent(metrics.get('lead_conversion_rate'))}",
        f"规则异常：{len(report.get('anomalies') or [])} 项｜待人工确认建议：{len(recommendations)} 条",
    ]
    if recommendations:
        lines.append("重点动作：")
        for item in recommendations[:3]:
            target = item.get("object_name") or item.get("object_id") or "未命名对象"
            lines.append(f"- {item.get('priority', 'P2')}｜{item.get('action', 'HOLD')}｜{target}")
    lines.extend(_analysis_lines(report, recommendations))
    lines.append("完整日报与导出证据已保存至本机日报目录；任何实际投放改动仍须逐条确认。")
    return "\n".join(lines)


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310 - fixed PushPlus endpoint
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ClawBotPushError(f"PushPlus 请求失败：{clean(exc)}") from exc
    except json.JSONDecodeError as exc:
        raise ClawBotPushError("PushPlus 返回了无法识别的响应") from exc


def push_daily_report(
    report: dict[str, Any],
    recommendations: list[dict[str, Any]],
    *,
    title: str | None = None,
    final_report: str | None = None,
) -> dict[str, Any]:
    """Push through the existing PushPlus/ClawBot transport.

    ``final_report`` is an optional compatibility seam for the Multi-Agent
    renderer. Omitting it preserves the legacy summary; providing it sends
    the already-rendered human-facing report through the same transport.
    """
    if final_report is not None:
        if not isinstance(final_report, str) or not final_report.strip():
            raise ClawBotPushError("final_report must be a non-empty string when provided")
        rendered_content = final_report
    else:
        rendered_content = render_summary(report, recommendations)

    if os.getenv('AGENT_DISABLE_PUSH') == '1':
        return {'status': 'disabled', 'reason': '本次验证不发送消息'}
    config = load_pushplus_config()
    if config is None:
        return {"status": "disabled", "reason": "未找到 PushPlus Token 文件或 PUSHPLUS_TOKEN 环境变量"}
    response = _post_json(
        "https://www.pushplus.plus/send/" + quote(config.token, safe=""),
        {
            "title": title or f"本地推日报｜{report.get('data_date', '未知日期')}",
            "content": rendered_content,
            "channel": "clawbot",
            "template": "txt",
        },
    )
    if response.get("code") != 200:
        raise ClawBotPushError(f"PushPlus 发送失败：{response.get('msg') or response.get('message') or '未知错误'}")
    response_data = response.get("data")
    message_id = response_data.get("messageId") if isinstance(response_data, dict) else response_data
    return {
        "status": "success",
        "data_date": report.get("data_date"),
        "channel": "clawbot",
        "message_id": message_id,
    }
