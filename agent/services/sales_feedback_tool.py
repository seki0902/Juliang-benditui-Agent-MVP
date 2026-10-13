"""Sales-feedback Tool with a real-source boundary.

The current project has lead-quality feedback and recommendation-execution
feedback, but neither is a CRM or sales-outcome source.  This module exposes a
future adapter contract and returns a structured ``unavailable`` result until
that contract is wired to a verified sales table or CRM.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd

from .metrics_tool_utils import normalise_id, resolve_window
from .tool_protocol import tool_protocol


_OBJECT_TYPE_ALIASES = {
    "account": "account",
    "账户": "account",
    "plan": "plan",
    "unit": "plan",
    "计划": "plan",
    "单元": "plan",
    "material": "material",
    "素材": "material",
}
_OBJECT_ID_COLUMNS = {"account": None, "plan": "unit_id", "material": "material_id"}
_DEFAULT_DATA = {
    "sales_leads": None,
    "qualified_leads": None,
    "contacted_leads": None,
    "won_deals": None,
    "revenue": None,
    "follow_up": None,
    "records": None,
}
_PAYLOAD_METADATA_FIELDS = {"source", "status", "warnings", "reason"}


def _non_sales_feedback_boundaries() -> list[dict[str, str]]:
    """Describe known feedback interfaces without presenting them as sales facts."""
    return [
        {
            "source": "replay_core.load_feedback",
            "scope": "线索有效性与是否对接",
            "status": "not_sales_outcome",
        },
        {
            "source": "StateStore.list_feedback",
            "scope": "建议执行状态、实际动作与观察结果",
            "status": "not_sales_outcome",
        },
    ]


class SalesFeedbackAdapter(Protocol):
    """Contract for a future CRM or sales-table implementation."""

    source: str

    @tool_protocol(tool_name="get_sales_feedback")
    def get_sales_feedback(
        self,
        object_type: str,
        object_id: str | None,
        start_date: str,
        end_date: str,
    ) -> Mapping[str, Any] | None:
        """Return already-scoped sales facts for an inclusive date window."""


def _normalise_object_type(object_type: str) -> str:
    normalized = str(object_type or "").strip().lower()
    if normalized not in _OBJECT_TYPE_ALIASES:
        raise ValueError("object_type must be one of: account, plan, material")
    return _OBJECT_TYPE_ALIASES[normalized]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, tuple, set, Mapping)):
        return bool(value)
    return True


def _has_sales_data(data: Mapping[str, Any]) -> bool:
    return any(_has_value(value) for value in data.values())


def _adapter_source(adapter: Any) -> str | None:
    return _clean_text(getattr(adapter, "source", None)) or "sales_feedback_adapter"


def _lookup(
    adapter: Any,
    object_type: str,
    object_id: str | None,
    start_date: str,
    end_date: str,
) -> Mapping[str, Any] | None:
    getter = getattr(adapter, "get_sales_feedback", None)
    if callable(getter):
        return getter(object_type, object_id, start_date, end_date)
    getter = getattr(adapter, "query", None)
    if callable(getter):
        return getter(object_type, object_id, start_date, end_date)
    if callable(adapter):
        return adapter(object_type, object_id, start_date, end_date)
    raise TypeError(
        "sales feedback adapter must expose get_sales_feedback(object_type, object_id, start_date, end_date)"
    )


def _time_window_info(report_date: str, time_window: str) -> tuple[pd.Timestamp, pd.Timestamp, dict[str, str]]:
    start, end, normalized = resolve_window(report_date, time_window)
    return start, end, {
        "name": normalized,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
    }


def _result(
    *,
    status: str,
    source: str | None,
    time_window: Mapping[str, str],
    object_scope: Mapping[str, Any],
    data: Mapping[str, Any] | None = None,
    sales_status: str,
    warnings: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "source": source,
        "time_window": dict(time_window),
        "object_scope": dict(object_scope),
        "data": dict(_DEFAULT_DATA if data is None else data),
        "data_completeness": {
            "status": "available" if status == "ok" else "unavailable",
            "sales_status": sales_status,
            "source_type": "crm_or_sales_table",
        },
        "non_sales_feedback_boundaries": _non_sales_feedback_boundaries(),
        "warnings": list(warnings or []),
    }
    if reason:
        result["reason"] = reason
        result["warnings"].append(reason)
    return result


@dataclass
class SalesFeedbackTool:
    """Query CRM/sales outcomes without treating other feedback as sales data."""

    adapter: SalesFeedbackAdapter | None = None

    @tool_protocol(tool_name="get_sales_feedback")
    def get_sales_feedback(
        self,
        report_date: str,
        time_window: str,
        object_type: str = "account",
        object_id: str | None = None,
    ) -> dict[str, Any]:
        canonical_type = _normalise_object_type(object_type)
        normalized_id = normalise_id(object_id) if object_id is not None else ""
        normalized_id = normalized_id or None
        _start, _end, window_info = _time_window_info(report_date, time_window)
        id_column = _OBJECT_ID_COLUMNS[canonical_type]
        object_scope = {
            "object_type": canonical_type,
            "object_id": normalized_id,
            "id_column": id_column,
            "status": "ready" if id_column is None or normalized_id else "missing_object_id",
        }

        if id_column is not None and normalized_id is None:
            return _result(
                status="unavailable",
                source=_adapter_source(self.adapter) if self.adapter is not None else None,
                time_window=window_info,
                object_scope=object_scope,
                sales_status="missing_object_id",
                reason="计划或素材销售反馈查询必须提供 object_id",
            )

        if self.adapter is None:
            return _result(
                status="unavailable",
                source=None,
                time_window=window_info,
                object_scope=object_scope,
                sales_status="missing_source",
                reason="当前没有 CRM、销售表或成交数据源；不能用线索质量反馈或建议执行反馈代替销售反馈",
            )

        source = _adapter_source(self.adapter)
        try:
            payload = _lookup(
                self.adapter,
                canonical_type,
                normalized_id,
                window_info["start_date"],
                window_info["end_date"],
            )
        except Exception as exc:  # Keep adapter failures inside the Tool contract.
            return _result(
                status="error",
                source=source,
                time_window=window_info,
                object_scope=object_scope,
                sales_status="adapter_error",
                reason=f"销售反馈 adapter 读取失败：{exc}",
            )

        if payload is None:
            return _result(
                status="unavailable",
                source=source,
                time_window=window_info,
                object_scope=object_scope,
                sales_status="no_records_in_window",
                reason="销售反馈来源在选定窗口内没有返回记录",
            )
        if not isinstance(payload, Mapping):
            return _result(
                status="error",
                source=source,
                time_window=window_info,
                object_scope=object_scope,
                sales_status="invalid_record",
                reason="销售反馈 adapter 返回的结果不是结构化对象",
            )

        if isinstance(payload.get("data"), Mapping):
            raw_data = payload["data"]
        else:
            raw_data = {
                key: value
                for key, value in payload.items()
                if key not in _PAYLOAD_METADATA_FIELDS
            }
        data = _json_safe(raw_data)
        if not isinstance(data, Mapping):
            data = {}
        payload_source = _clean_text(payload.get("source"))
        resolved_source = payload_source or source
        if not _has_sales_data(data):
            return _result(
                status="unavailable",
                source=resolved_source,
                time_window=window_info,
                object_scope=object_scope,
                sales_status="empty_record",
                reason="销售反馈来源返回了空记录，不能将其解释为销售结果为 0",
            )

        return _result(
            status="ok",
            source=resolved_source,
            time_window=window_info,
            object_scope=object_scope,
            data=data,
            sales_status="available",
        )


@tool_protocol(tool_name="get_sales_feedback")
def get_sales_feedback(
    report_date: str,
    time_window: str,
    object_type: str = "account",
    object_id: str | None = None,
    adapter: SalesFeedbackAdapter | None = None,
) -> dict[str, Any]:
    """Return CRM/sales feedback, or an explicit unavailable result."""
    return SalesFeedbackTool(adapter=adapter).get_sales_feedback(
        report_date=report_date,
        time_window=time_window,
        object_type=object_type,
        object_id=object_id,
    )
