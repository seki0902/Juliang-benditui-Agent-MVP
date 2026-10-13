"""Read-only V2 context and Shimo daily summaries. No business diagnosis."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .metrics_tool_utils import normalise_id, resolve_window


def _load(path, account_id):
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("account_id") != account_id:
        raise ValueError("context file must explicitly match the run account_id")
    rows = payload.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("context rows must be a list of objects")
    return rows


def _number(value, *, count=False):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a metric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (count and not result.is_integer()):
        raise ValueError("metric must be finite and nonnegative; counts must be integers")
    return int(result) if count else result


class V2ContextTools:
    """Account-bound snapshots; missing entity detail never becomes allocation."""

    def __init__(self, account_id, cutoff, *, shimo_path=None, plan_context_path=None, content_path=None):
        self.account_id = account_id
        self.cutoff = date.fromisoformat(cutoff)
        self.shimo = _load(shimo_path, account_id)
        self.context = _load(plan_context_path, account_id)
        self.assets = _load(content_path, account_id)

    def get_content_asset(self, material_id: str) -> dict:
        """Read dated material title/script/transcript/visual notes up to the run cutoff."""
        from .content_asset_tool import ContentAssetTool
        rows = [r for r in self.assets if normalise_id(r.get("material_id")) == normalise_id(material_id)
                and date.fromisoformat(str(r["data_date"])) <= self.cutoff]
        rows.sort(key=lambda r: r["data_date"])
        if len({r["data_date"] for r in rows}) != len(rows):
            raise ValueError("duplicate dated content versions")
        return ContentAssetTool.from_mapping(
            {material_id: rows[-1]} if rows else {}, source="dated_content_snapshot"
        ).get_content_asset(material_id)

    def _end(self, report_date):
        end = date.fromisoformat(report_date)
        if end > self.cutoff:
            raise ValueError("query date exceeds run cutoff")
        return end

    def _result(self, data, report_date, *, available=True, warnings=None):
        return {"status": "ok" if available else "unavailable",
                "source": "v2_readonly_context", "time_window": {"end_date": report_date},
                "data": data, "warnings": warnings or []}

    def get_deep_conversion(self, report_date: str, time_window: str,
                            object_type: str = "account", object_id: str | None = None) -> dict:
        """Read Shimo effective/ineffective counts; never infer plan data from account totals."""
        self._end(report_date)
        if object_type not in {"account", "plan", "material"}:
            raise ValueError("object_type must be account, plan or material")
        start, end, _ = resolve_window(report_date, time_window)
        id_key = {"plan": "plan_id", "material": "material_id"}.get(object_type)
        if id_key and not object_id:
            raise ValueError("entity scope requires object_id")
        rows = []
        for row in self.shimo:
            day = date.fromisoformat(str(row["data_date"]))
            if not start.date() <= day <= end.date():
                continue
            # Account rows and entity rows are separate populations, not additive.
            if id_key:
                if normalise_id(row.get(id_key)) != normalise_id(object_id):
                    continue
            elif row.get("plan_id") or row.get("material_id"):
                continue
            rows.append(row)
        dates = [str(row["data_date"]) for row in rows]
        if len(set(dates)) != len(dates):
            raise ValueError("duplicate Shimo daily rows for requested scope")
        wanted = {(start.date() + timedelta(days=n)).isoformat()
                  for n in range((end.date() - start.date()).days + 1)}
        missing = sorted(wanted - set(dates))
        totals = {}
        for field in ("valid_leads", "invalid_leads", "spend"):
            values = [_number(row.get(field), count=field != "spend") for row in rows]
            totals[field] = sum(values) if values and not missing and all(v is not None for v in values) else None
        valid, spend = totals["valid_leads"], totals["spend"]
        # A day can be absent from the sheet, or present with an empty cell.  The
        # two are different facts for the operator, so name the empty days per
        # field instead of reporting one generic gap.
        blank = {field: sorted(str(row["data_date"]) for row in rows
                               if _number(row.get(field), count=field != "spend") is None)
                 for field in ("valid_leads", "invalid_leads", "spend")}
        blank = {field: days for field, days in blank.items() if days}
        data = {**totals, "deep_conversions": valid,
                "deep_conversion_cost": round(spend / valid, 2) if valid and spend is not None else None,
                "definition": "有效=加微信；无效=未加微信；深转来自石墨",
                "object_type": object_type, "object_id": object_id,
                "missing_dates": missing, "blank_value_dates": blank,
                "available_days": sorted(dates),
                "feedback_delivery_status": "unknown"}
        warnings = []
        if missing:
            warnings.append("石墨表中以下日期没有记录：" + "、".join(missing)
                            + "；不补零、不向下分配账户汇总。")
        if blank:
            rendered = "；".join(f"{field} 为 " + "、".join(days) for field, days in blank.items())
            warnings.append("以下日期在石墨表中有记录但值为空：" + rendered
                            + "；相应字段的合计与深转成本保持缺失，不补零、不向下分配账户汇总。")
        if spend is None:
            warnings.append("缺少同范围同窗口消耗，深转成本不可计算。")
        result = self._result(data, report_date, available=bool(rows), warnings=warnings)
        result["source"] = "shimo_daily_snapshot"
        if rows and (missing or valid is None or spend is None):
            result["status"] = "partial"
        return result

    def get_plan_context(self, plan_id: str, report_date: str) -> dict:
        """Return dated settings and change snapshots up to the requested day."""
        end = self._end(report_date)
        rows = [row for row in self.context
                if normalise_id(row.get("plan_id")) == normalise_id(plan_id)
                and date.fromisoformat(str(row["data_date"])) <= end]
        rows.sort(key=lambda row: row["data_date"])
        if len({row["data_date"] for row in rows}) != len(rows):
            raise ValueError("duplicate plan daily snapshots")
        return self._result({"plan_id": plan_id, "latest": rows[-1] if rows else None,
                             "history": rows}, report_date, available=bool(rows),
                            warnings=[] if rows else ["无计划设置及修改记录，不能推断状态。"])

    def get_material_plan_comparison(self, material_id: str, report_date: str) -> dict:
        """Return latest known plan memberships/settings, not invented material attribution."""
        end = self._end(report_date)
        latest = {}
        for row in sorted(self.context, key=lambda r: r["data_date"]):
            if date.fromisoformat(str(row["data_date"])) <= end:
                latest[normalise_id(row["plan_id"])] = row
        matches = [row for row in latest.values()
                   if normalise_id(material_id) in {normalise_id(x) for x in row.get("material_ids", [])}]
        return self._result({"material_id": material_id, "plans": matches}, report_date,
                            available=bool(matches), warnings=[
                                "先核对目标、流量、预算、出价、竞价方式、定向、时段及阶段；计划汇总不等于素材分计划指标。"])

    def get_adjustment_options(self, plan_id: str, report_date: str) -> dict:
        """Compute review-only bid/budget options from recorded settings; do not choose one."""
        context = self.get_plan_context(plan_id, report_date)
        latest = context["data"]["latest"]
        options = []
        if latest:
            for parameter in ("budget", "bid"):
                current = _number(latest.get(parameter))
                if not current:
                    continue
                for percent in (-30, -20, -10, 10, 20, 30):
                    proposed = (Decimal(str(current)) * (1 + Decimal(percent) / 100)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP)
                    options.append({"parameter": parameter, "current_value": current,
                                    "proposed_value": float(proposed), "change_percent": percent,
                                    "evidence_source": f"plan_context:{plan_id}:{latest['data_date']}"})
        return self._result({"plan_id": plan_id, "options": options,
                             "recommendation_only": True}, report_date, available=bool(options),
                            warnings=["数值仅为计算候选，是否调整由策略依据证据判断，人工执行。"])
