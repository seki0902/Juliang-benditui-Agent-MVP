"""Enterprise WeChat daily-summary sender for a configured internal user.

Credentials are loaded exclusively from environment variables. This module
only sends a report summary; it has no capability to change advertising data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class WeComPushError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeComConfig:
    corp_id: str
    corp_secret: str
    agent_id: int
    to_user: str
    shimo_url: str


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def load_wecom_config() -> WeComConfig | None:
    values = {
        "corp_id": _env("WECOM_CORP_ID"),
        "corp_secret": _env("WECOM_CORP_SECRET"),
        "agent_id": _env("WECOM_AGENT_ID"),
        "to_user": _env("WECOM_TO_USER"),
    }
    if not any(values.values()):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise WeComPushError(f"企业微信配置不完整，缺少：{', '.join(missing)}")
    try:
        agent_id = int(values["agent_id"])
    except ValueError as exc:
        raise WeComPushError("WECOM_AGENT_ID 必须是数字") from exc
    return WeComConfig(
        corp_id=values["corp_id"], corp_secret=values["corp_secret"], agent_id=agent_id,
        to_user=values["to_user"], shimo_url=_env("SHIMO_SHEET_URL"),
    )


def _request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"} if body else {})
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310 - fixed HTTPS API endpoints below
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise WeComPushError(f"企业微信请求失败：{exc}") from exc


def _api_result(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _request_json(url, payload)
    if result.get("errcode") not in (0, None):
        raise WeComPushError(f"企业微信接口返回错误 {result.get('errcode')}：{result.get('errmsg', 'unknown')}")
    return result


def _fmt(value: Any) -> str:
    if value is None:
        return "待补录"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_summary(report: dict[str, Any], recommendations: list[dict[str, Any]], config: WeComConfig) -> str:
    metrics = report.get("yesterday") or {}
    lines = [
        f"## 本地推运营日报｜{report.get('data_date')}",
        f"> 账户消耗：**{_fmt(metrics.get('spend'))}**",
        f"> 平台留资：**{_fmt(metrics.get('leads'))}**｜留资成本：**{_fmt(metrics.get('lead_cost'))}**",
        f"> 有效留资：**{_fmt(metrics.get('valid_leads'))}**｜有效留资成本：**{_fmt(metrics.get('valid_lead_cost'))}**",
        f"> 异常：**{len(report.get('anomalies') or [])}** 项｜待人工确认建议：**{len(recommendations)}** 条",
    ]
    for item in recommendations[:3]:
        target = item.get("object_name") or item.get("object_id") or "未命名对象"
        lines.append(f"> {item.get('priority', 'P2')}｜{item.get('action')}｜{target}")
    if len(recommendations) > 3:
        lines.append(f"> 其余 {len(recommendations) - 3} 条请查看石墨日报。")
    if config.shimo_url:
        lines.append(f"[打开石墨日报]({config.shimo_url})")
    return "\n".join(lines)


def push_daily_report(report: dict[str, Any], recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    """Send the configured user's summary, or explicitly return disabled."""
    config = load_wecom_config()
    if config is None:
        return {"status": "disabled", "reason": "未配置企业微信环境变量"}
    token_url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken?" + urlencode({
        "corpid": config.corp_id, "corpsecret": config.corp_secret,
    })
    token = str(_api_result(token_url).get("access_token") or "")
    if not token:
        raise WeComPushError("企业微信未返回 access_token")
    content = render_summary(report, recommendations, config)
    result = _api_result(
        "https://qyapi.weixin.qq.com/cgi-bin/message/send?" + urlencode({"access_token": token}),
        {
            "touser": config.to_user,
            "msgtype": "markdown",
            "agentid": config.agent_id,
            "markdown": {"content": content},
            "safe": 0,
        },
    )
    return {
        "status": "success", "data_date": report.get("data_date"),
        "recipient": config.to_user, "msgid": result.get("msgid"),
    }
