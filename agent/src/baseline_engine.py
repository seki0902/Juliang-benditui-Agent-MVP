"""Calendar windows and observed lifecycle states; missing days are never zero-filled."""
from __future__ import annotations

from typing import Any
import pandas as pd


def _ratio(numerator: float, denominator: float) -> float | None:
    if numerator is None or denominator is None or pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
        return None
    return round(float(numerator) / float(denominator), 6)


def _window_metrics(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    frame = frame[(frame["data_date"] >= start) & (frame["data_date"] <= end)]
    totals = {key: float(frame[key].sum()) for key in ("spend", "impressions", "clicks", "leads")}
    result = {**totals, "ctr": _ratio(totals["clicks"], totals["impressions"]),
              "cpc": _ratio(totals["spend"], totals["clicks"]),
              "lead_conversion_rate": _ratio(totals["leads"], totals["clicks"]),
              "lead_cost": _ratio(totals["spend"], totals["leads"]),
              "observed_days": int(frame["data_date"].nunique()),
              "spend_days": int(frame.loc[frame["spend"] > 0, "data_date"].nunique()),
              "start_date": start.date().isoformat(), "end_date": end.date().isoformat()}
    observed = set(frame["data_date"])
    result["missing_dates"] = [day.date().isoformat() for day in pd.date_range(start, end) if day not in observed]
    result["complete"] = not result["missing_dates"]
    # Robust summaries describe daily ratios, while window costs use summed counts.
    result["daily_statistics"] = {}
    for name, numerator, denominator in (("ctr", "clicks", "impressions"),
                                         ("lead_conversion_rate", "leads", "clicks"),
                                         ("lead_cost", "spend", "leads")):
        values = frame[numerator].div(frame[denominator].replace(0, float("nan"))).dropna()
        result["daily_statistics"][name] = {
            "median": float(values.median()) if not values.empty else None,
            "mean": float(values.mean()) if not values.empty else None,
            "min": float(values.min()) if not values.empty else None,
            "max": float(values.max()) if not values.empty else None,
        }
    return result


def build_baselines(df: pd.DataFrame, id_column: str, as_of_date: pd.Timestamp) -> list[dict[str, Any]]:
    if df.empty:
        return []
    df = df.copy()
    df["data_date"] = pd.to_datetime(df["data_date"]).dt.normalize()
    df = df[df["data_date"] <= as_of_date]
    result = []
    for entity_id, raw in df.groupby(id_column):
        frame = raw.groupby("data_date", as_index=False)[["spend", "impressions", "clicks", "leads"]].sum().sort_values("data_date")
        current = frame[frame["data_date"] == as_of_date]
        observed_status = "not_observed" if current.empty else ("spending" if current["spend"].sum() > 0 else "no_spend")
        active = frame[frame["spend"] > 0]
        active_days = int(active["data_date"].nunique())
        no_spend_days = 0
        by_date = frame.set_index("data_date")["spend"].to_dict()
        cursor = as_of_date
        while cursor in by_date and by_date[cursor] == 0:
            no_spend_days += 1
            cursor -= pd.Timedelta(days=1)
        windows = {f"{days}d": _window_metrics(frame, as_of_date - pd.Timedelta(days=days - 1), as_of_date) for days in (3, 7, 30)}
        windows["previous_7d"] = _window_metrics(frame, as_of_date - pd.Timedelta(days=9), as_of_date - pd.Timedelta(days=3))
        recent, previous = windows["3d"], windows["previous_7d"]
        if observed_status == "not_observed":
            stage = "not_observed"
        elif observed_status == "no_spend":
            stage = "dormant" if no_spend_days >= 3 else "no_spend"
        elif active_days <= 3:
            stage = "newly_observed"
        elif recent["observed_days"] < 3:
            stage = "insufficient_history"
        else:
            stage = "observing"
            sample_ok = min(recent["clicks"], previous["clicks"]) >= 30 and previous["observed_days"] >= 3
            cvr, baseline = recent["lead_conversion_rate"], previous["lead_conversion_rate"]
            if sample_ok and cvr is not None and baseline and cvr < baseline * 0.85:
                stage = "decline_candidate"
        result.append({
            "object_type": {"unit_id": "plan", "material_id": "material", "account_id": "account"}.get(id_column, "unknown"),
            "object_id": str(entity_id), "stage": stage, "observed_status": observed_status,
            "first_seen_date": frame["data_date"].min().date().isoformat(),
            "last_seen_date": frame["data_date"].max().date().isoformat(),
            "last_spend_date": active["data_date"].max().date().isoformat() if not active.empty else None,
            "active_days": active_days, "consecutive_no_spend_days": no_spend_days,
            "stage_basis": "observed_history_only", "windows": windows,
        })
    return result
