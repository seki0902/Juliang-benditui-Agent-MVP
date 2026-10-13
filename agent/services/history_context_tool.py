"""Read-only historical context Tool backed by successful StateStore runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from baseline_engine import build_baselines
from replay_core import _to_bool, metric_row
from state_store import StateStore

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
_OBJECT_ID_COLUMNS = {"account": "account_id", "plan": "unit_id", "material": "material_id"}
_BASELINE_METRICS = (
    "spend",
    "impressions",
    "clicks",
    "leads",
    "ctr",
    "cpc",
    "lead_conversion_rate",
    "lead_cost",
)
_METRIC_ALIASES = {
    "cost": "spend",
    "cpl": "lead_cost",
    "cvr": "lead_conversion_rate",
}


def _normalise_object_type(object_type: str) -> str:
    normalized = str(object_type or "").strip().lower()
    if normalized not in _OBJECT_TYPE_ALIASES:
        raise ValueError("object_type must be one of: account, plan, material")
    return _OBJECT_TYPE_ALIASES[normalized]


def _normalise_metrics(metrics: Iterable[str] | None) -> list[str]:
    requested = list(metrics) if metrics is not None else list(_BASELINE_METRICS)
    normalized: list[str] = []
    allowed = set(_BASELINE_METRICS)
    for metric in requested:
        name = _METRIC_ALIASES.get(str(metric).strip().lower(), str(metric).strip())
        if name not in allowed:
            raise ValueError(
                f"unsupported history metric {metric!r}; allowed metrics: "
                + ", ".join(_BASELINE_METRICS)
            )
        if name not in normalized:
            normalized.append(name)
    return normalized


def _empty_snapshot(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    dates = pd.date_range(start, end)
    return {
        **{metric: None for metric in _BASELINE_METRICS},
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "observed_days": 0,
        "spend_days": 0,
        "missing_dates": [day.date().isoformat() for day in dates],
        "complete": False,
        "daily_statistics": {
            metric: {"median": None, "mean": None, "min": None, "max": None}
            for metric in ("ctr", "lead_conversion_rate", "lead_cost")
        },
    }


def _optional_sum(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    values = frame[column]
    if values.isna().any():
        return None
    return float(values.sum())


def _custom_snapshot(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, Any]:
    """Aggregate a non-standard window with the replay metric contract."""
    current = frame[(frame["data_date"] >= start) & (frame["data_date"] <= end)]
    if current.empty:
        return _empty_snapshot(start, end)

    values = metric_row(
        float(current["spend"].sum()),
        float(current["impressions"].sum()),
        float(current["clicks"].sum()),
        float(current["leads"].sum()),
        _optional_sum(current, "valid_leads"),
        float(current["private_messages"].sum())
        if "private_messages" in current.columns
        else 0.0,
        _optional_sum(current, "contacted_leads"),
    )
    observed = set(current["data_date"].dt.normalize())
    dates = pd.date_range(start, end)
    result = {
        **{key: values.get(key) for key in _BASELINE_METRICS},
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "observed_days": int(len(observed)),
        "spend_days": int(current.loc[current["spend"] > 0, "data_date"].nunique()),
        "missing_dates": [
            day.date().isoformat() for day in dates if day not in observed
        ],
    }
    result["complete"] = not result["missing_dates"]
    result["daily_statistics"] = {}
    for name, numerator, denominator in (
        ("ctr", "clicks", "impressions"),
        ("lead_conversion_rate", "leads", "clicks"),
        ("lead_cost", "spend", "leads"),
    ):
        values = current[numerator].div(
            current[denominator].replace(0, float("nan"))
        ).dropna()
        result["daily_statistics"][name] = {
            "median": float(values.median()) if not values.empty else None,
            "mean": float(values.mean()) if not values.empty else None,
            "min": float(values.min()) if not values.empty else None,
            "max": float(values.max()) if not values.empty else None,
        }
    return result


def _as_of_window(
    as_of_date: pd.Timestamp, window_name: str
) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    if window_name == "previous_7d":
        start = as_of_date - pd.Timedelta(days=9)
        end = as_of_date - pd.Timedelta(days=3)
        return start, end, window_name
    start, end, normalized = resolve_window(as_of_date.date().isoformat(), window_name)
    return start, end, normalized


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _daily_series(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    metrics: list[str],
    id_column: str,
) -> list[dict[str, Any]]:
    current = frame[(frame["data_date"] >= start) & (frame["data_date"] <= end)]
    result = []
    for _, row in current.sort_values("data_date").iterrows():
        item = {
            "data_date": row["data_date"].date().isoformat(),
            "object_id": _json_value(row.get(id_column)),
        }
        for metric in metrics:
            item[metric] = _json_value(row.get(metric))
        result.append(item)
    return result


def _empty_trend(metrics: list[str]) -> dict[str, Any]:
    return {
        metric: {
            "current_value": None,
            "comparison_value": None,
            "absolute_change": None,
            "relative_change": None,
        }
        for metric in metrics
    }


def _trend(
    current: dict[str, Any], comparison: dict[str, Any], metrics: list[str]
) -> dict[str, Any]:
    result = {}
    for metric in metrics:
        current_value = current.get(metric)
        comparison_value = comparison.get(metric)
        absolute_change = None
        relative_change = None
        if current_value is not None and comparison_value is not None:
            absolute_change = round(float(current_value) - float(comparison_value), 6)
            if comparison_value != 0:
                relative_change = round(
                    float(current_value) / float(comparison_value) - 1, 6
                )
        result[metric] = {
            "current_value": current_value,
            "comparison_value": comparison_value,
            "absolute_change": absolute_change,
            "relative_change": relative_change,
        }
    return result


def _snapshot_from_baseline(
    frame: pd.DataFrame,
    baseline: dict[str, Any] | None,
    as_of_date: pd.Timestamp,
    window_name: str,
) -> tuple[dict[str, Any], str]:
    if baseline and window_name in baseline.get("windows", {}):
        snapshot = dict(baseline["windows"][window_name])
        if snapshot.get("observed_days") == 0:
            for metric in _BASELINE_METRICS:
                snapshot[metric] = None
        return snapshot, window_name
    start, end, normalized = _as_of_window(as_of_date, window_name)
    return _custom_snapshot(frame, start, end), normalized


@dataclass
class HistoryContextTool:
    """Read historical facts for one account, plan or material."""

    state_store: StateStore | None = None
    account_id: str | None = None

    @classmethod
    def from_state_store_path(
        cls, state_store_path: Path | str, account_id: str
    ) -> "HistoryContextTool":
        path = Path(state_store_path)
        if not path.exists():
            return cls(state_store=None, account_id=account_id)
        return cls(state_store=StateStore(path), account_id=account_id)

    @tool_protocol(tool_name="get_history_context")
    def get_history_context(
        self,
        object_type: str,
        object_id: str | None,
        before_date: str,
        time_window: str,
        comparison_window: str = "previous_7d",
        metrics: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        canonical_type = _normalise_object_type(object_type)
        selected_metrics = _normalise_metrics(metrics)
        normalized_id = normalise_id(object_id) if object_id is not None else ""
        if canonical_type == "account":
            normalized_id = normalized_id or "account"
        else:
            normalized_id = normalized_id or None

        before = pd.Timestamp(before_date).normalize()
        as_of_date = before - pd.Timedelta(days=1)
        current_start, current_end, current_window_name = _as_of_window(
            as_of_date, time_window.strip().lower()
        )
        comparison_start, comparison_end, comparison_window_name = _as_of_window(
            as_of_date, comparison_window.strip().lower()
        )
        time_window_info = {
            "name": current_window_name,
            "start_date": current_start.date().isoformat(),
            "end_date": current_end.date().isoformat(),
        }
        comparison_info = {
            "name": comparison_window_name,
            "start_date": comparison_start.date().isoformat(),
            "end_date": comparison_end.date().isoformat(),
        }
        object_scope = {
            "object_type": canonical_type,
            "object_id": normalized_id,
            "id_column": _OBJECT_ID_COLUMNS[canonical_type]
            if canonical_type != "account"
            else None,
            "account_id": self.account_id,
        }
        source = "state_store.successful_runs" if self.state_store is not None else None
        base_kwargs = {
            "source": source,
            "time_window": time_window_info,
            "object_scope": object_scope,
            "data_completeness": {
                "status": "unavailable",
                "history_status": "missing_state_store",
                "observed_history_days": 0,
                "first_observed_date": None,
                "last_observed_date": None,
            },
        }

        if canonical_type != "account" and normalized_id is None:
            return {
                "status": "unavailable",
                **base_kwargs,
                "comparison_window": comparison_info,
                "current_window": _empty_snapshot(current_start, current_end),
                "history_window": _empty_snapshot(comparison_start, comparison_end),
                "trend": _empty_trend(selected_metrics),
                "daily_series": [],
                "warnings": ["计划或素材历史查询必须提供 object_id"],
            }
        if self.state_store is None or not self.account_id:
            return {
                "status": "unavailable",
                **base_kwargs,
                "comparison_window": comparison_info,
                "current_window": _empty_snapshot(current_start, current_end),
                "history_window": _empty_snapshot(comparison_start, comparison_end),
                "trend": _empty_trend(selected_metrics),
                "daily_series": [],
                "warnings": ["没有可用的 StateStore 或 account_id"],
            }

        history = self.state_store.load_history(self.account_id, before.date().isoformat())
        rows = history[canonical_type]
        id_column = _OBJECT_ID_COLUMNS[canonical_type]
        frame = pd.DataFrame(rows)
        if frame.empty:
            empty_current = _empty_snapshot(current_start, current_end)
            empty_comparison = _empty_snapshot(comparison_start, comparison_end)
            return {
                "status": "unavailable",
                **base_kwargs,
                "comparison_window": comparison_info,
                "current_window": empty_current,
                "history_window": empty_comparison,
                "trend": _empty_trend(selected_metrics),
                "daily_series": [],
                "warnings": ["StateStore 中没有该对象的成功历史记录，不能将其解释为指标为 0"],
            }

        if canonical_type == "account":
            frame["account_id"] = "account"
        elif id_column not in frame.columns:
            return {
                "status": "unavailable",
                **base_kwargs,
                "comparison_window": comparison_info,
                "current_window": _empty_snapshot(current_start, current_end),
                "history_window": _empty_snapshot(comparison_start, comparison_end),
                "trend": _empty_trend(selected_metrics),
                "daily_series": [],
                "warnings": [f"历史记录缺少对象字段 {id_column}，无法完成范围过滤"],
            }
        frame[id_column] = frame[id_column].map(normalise_id)
        if canonical_type != "account":
            frame = frame[frame[id_column] == normalized_id].copy()
        frame["data_date"] = pd.to_datetime(frame["data_date"], errors="coerce").dt.normalize()
        frame = frame[frame["data_date"].notna()].copy()
        required_columns = {"data_date", "spend", "impressions", "clicks", "leads"}
        missing_columns = sorted(required_columns - set(frame.columns))
        if missing_columns:
            return {
                "status": "unavailable",
                **base_kwargs,
                "comparison_window": comparison_info,
                "current_window": _empty_snapshot(current_start, current_end),
                "history_window": _empty_snapshot(comparison_start, comparison_end),
                "trend": _empty_trend(selected_metrics),
                "daily_series": [],
                "warnings": [
                    "历史记录缺少指标字段：" + ", ".join(missing_columns)
                ],
            }
        if frame.empty:
            return {
                "status": "unavailable",
                **base_kwargs,
                "comparison_window": comparison_info,
                "current_window": _empty_snapshot(current_start, current_end),
                "history_window": _empty_snapshot(comparison_start, comparison_end),
                "trend": _empty_trend(selected_metrics),
                "daily_series": [],
                "warnings": ["该对象没有成功历史记录，不能将其解释为指标为 0"],
            }

        baseline_rows = build_baselines(frame, id_column, as_of_date)
        baseline = baseline_rows[0] if baseline_rows else None
        current_snapshot, _ = _snapshot_from_baseline(
            frame, baseline, as_of_date, current_window_name
        )
        comparison_snapshot, _ = _snapshot_from_baseline(
            frame, baseline, as_of_date, comparison_window_name
        )
        trend = _trend(current_snapshot, comparison_snapshot, selected_metrics)
        observed_dates = sorted(frame["data_date"].dt.date.astype(str).unique())
        warnings: list[str] = []
        if not current_snapshot.get("complete") or not comparison_snapshot.get("complete"):
            warnings.append("历史窗口存在缺失日期；缺失日期未补为 0")
        if comparison_snapshot.get("observed_days", 0) == 0:
            warnings.append("对比窗口没有历史记录，无法计算变化")
        status = "ok" if current_snapshot.get("complete") and comparison_snapshot.get("complete") else "partial"
        completeness = {
            "status": "available" if status == "ok" else "partial",
            "history_status": "available",
            "observed_history_days": len(observed_dates),
            "first_observed_date": observed_dates[0] if observed_dates else None,
            "last_observed_date": observed_dates[-1] if observed_dates else None,
            "current_window_complete": current_snapshot.get("complete", False),
            "comparison_window_complete": comparison_snapshot.get("complete", False),
            "current_missing_dates": current_snapshot.get("missing_dates", []),
            "comparison_missing_dates": comparison_snapshot.get("missing_dates", []),
        }
        object_scope["status"] = "filtered" if canonical_type != "account" else "account"
        object_scope["history_rows"] = len(frame)
        series_start = min(current_start, comparison_start)
        series_end = max(current_end, comparison_end)
        return {
            "status": status,
            "source": source,
            "account_id": self.account_id,
            "before_date": before.date().isoformat(),
            "as_of_date": as_of_date.date().isoformat(),
            "object_scope": object_scope,
            "time_window": time_window_info,
            "comparison_window": comparison_info,
            "current_window": current_snapshot,
            "history_window": comparison_snapshot,
            "trend": trend,
            "requested_metrics": selected_metrics,
            "observed_dates": observed_dates,
            "daily_series": _daily_series(
                frame, series_start, series_end, selected_metrics, id_column
            ),
            "baseline_windows": baseline.get("windows", {}) if baseline else {},
            "data_completeness": completeness,
            "warnings": warnings,
        }


@tool_protocol(tool_name="get_history_context")
def get_history_context(
    object_type: str,
    object_id: str | None,
    before_date: str,
    time_window: str,
    comparison_window: str = "previous_7d",
    metrics: Iterable[str] | None = None,
    account_id: str | None = None,
    state_store_path: Path | str | None = None,
) -> dict[str, Any]:
    """Read historical context from an existing StateStore without writing it."""
    if state_store_path is None:
        tool = HistoryContextTool(state_store=None, account_id=account_id)
        return tool.get_history_context(
            object_type,
            object_id,
            before_date,
            time_window,
            comparison_window,
            metrics,
        )

    path = Path(state_store_path)
    if not path.exists():
        tool = HistoryContextTool(state_store=None, account_id=account_id)
        return tool.get_history_context(
            object_type,
            object_id,
            before_date,
            time_window,
            comparison_window,
            metrics,
        )
    with StateStore(path) as store:
        tool = HistoryContextTool(state_store=store, account_id=account_id)
        return tool.get_history_context(
            object_type,
            object_id,
            before_date,
            time_window,
            comparison_window,
            metrics,
        )
