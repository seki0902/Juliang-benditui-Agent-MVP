"""Shared input helpers for the read-only metrics Tools."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


_WINDOW_DAYS = {"1d": 1, "3d": 3, "7d": 7, "30d": 30}

METRIC_FIELDS = (
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "leads",
    "lead_conversion_rate",
    "lead_cost",
    "valid_leads",
    "valid_lead_rate",
    "valid_lead_cost",
    "private_messages",
    "contacted_leads",
    "contact_cost",
)


def resolve_window(report_date: str, time_window: str) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    """Resolve an inclusive calendar-date window used by metrics Tools."""
    end = pd.Timestamp(report_date).normalize()
    normalized = str(time_window).strip().lower()
    if normalized in {"day", "daily"}:
        normalized = "1d"
    if normalized == "mtd":
        return end.replace(day=1), end, normalized
    if normalized in _WINDOW_DAYS:
        return end - pd.Timedelta(days=_WINDOW_DAYS[normalized] - 1), end, normalized
    match = re.fullmatch(r"(\d+)d", normalized)
    if match and int(match.group(1)) > 0:
        days = int(match.group(1))
        return end - pd.Timedelta(days=days - 1), end, normalized
    raise ValueError("time_window must be day, mtd, or a positive Nd window such as 7d")


def valid_lead_rate(valid_leads: Any, leads: Any) -> float | None:
    """Calculate the quality rate while preserving unavailable values."""
    if valid_leads is None or leads is None or pd.isna(valid_leads) or pd.isna(leads) or leads == 0:
        return None
    return round(float(valid_leads) / float(leads), 6)


def normalise_id(value: Any) -> str:
    """Normalize an entity ID for joins while preserving unavailable values."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def optional_text(value: Any) -> str | None:
    """Return a clean optional text value without turning missing data into text."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def empty_metrics() -> dict[str, Any]:
    return {field: None for field in METRIC_FIELDS}


def metric_payload(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Expose computed metric fields without returning a DataFrame."""
    if summary is None:
        return empty_metrics()
    metrics = {
        field: summary.get(field)
        for field in METRIC_FIELDS
        if field != "valid_lead_rate"
    }
    metrics["valid_lead_rate"] = valid_lead_rate(
        summary.get("valid_leads"), summary.get("leads")
    )
    return metrics


def metric_aliases(metrics: dict[str, Any]) -> dict[str, Any]:
    """Keep account-style aliases available on entity Tool results."""
    return {
        "cost": metrics.get("spend"),
        "spend": metrics.get("spend"),
        "impressions": metrics.get("impressions"),
        "clicks": metrics.get("clicks"),
        "ctr": metrics.get("ctr"),
        "conversions": metrics.get("leads"),
        "leads": metrics.get("leads"),
        "cvr": metrics.get("lead_conversion_rate"),
        "lead_conversion_rate": metrics.get("lead_conversion_rate"),
        "cpl": metrics.get("lead_cost"),
        "lead_cost": metrics.get("lead_cost"),
        "valid_leads": metrics.get("valid_leads"),
        "valid_lead_rate": metrics.get("valid_lead_rate"),
        "valid_cpl": metrics.get("valid_lead_cost"),
        "valid_lead_cost": metrics.get("valid_lead_cost"),
        "contacted_leads": metrics.get("contacted_leads"),
        "contact_cost": metrics.get("contact_cost"),
    }
