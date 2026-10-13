"""Run the five existing model-backed Agents locally, with delivery disabled."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
import inspect
import json
import math
from pathlib import Path
import re
import sys
from time import monotonic
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.runtime_review import inspect_state, json_safe


class Audit:
    """Separate full local evidence archive; never inserted into model State."""
    def __init__(self, folder, secrets=()):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.secrets = tuple(s for s in secrets if s)
        self.sequence = 0
        self.agent = None
        self.turn = 0

    def clean(self, value):
        from services.tool_trace import is_sensitive_key
        value = json_safe(value)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: "[REDACTED]" if is_sensitive_key(key) else self.clean(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
            return value
        return value

    def write(self, name, value):
        (self.folder / name).write_text(
            json.dumps(self.clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def event(self, kind, **payload):
        self.sequence += 1
        record = dict(sequence=self.sequence, timestamp=datetime.now(timezone.utc).isoformat(),
                      agent=self.agent, turn=self.turn, kind=kind, **payload)
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.clean(record), ensure_ascii=False, allow_nan=False) + "\n")

    def node(self, name, function):
        def invoke(state):
            self.agent = name
            self.turn += 1
            self.event("agent_input", state=state)
            try:
                update = function(state)
            except Exception as exc:
                self.event("agent_error", error_type=type(exc).__name__,
                           message=self.clean(str(exc))[:2000])
                raise
            self.event("agent_output", update=update)
            return update
        return invoke


class AuditedModel:
    """Bounded model adapter recording parsed outputs and Tool selections."""
    def __init__(self, model, audit, *, max_calls=40, deadline_seconds=600,
                 structured_method="function_calling"):
        self.model, self.audit = model, audit
        self.max_calls, self.calls = max_calls, 0
        self.deadline = monotonic() + deadline_seconds
        self.structured_method = structured_method

    def _bound(self, runnable, phase):
        owner = self

        def compact_factual_messages(messages):
            """Drop old Tool-call protocol turns while retaining bounded facts.

            A provider can occasionally answer a schema-bound final turn with a
            stale Tool name from the preceding investigation transcript.  The
            retry must therefore retain facts but not the old Tool-call frames.
            This is Shadow-only transport recovery: the schema remains the
            acceptance boundary and no facts are synthesized.
            """
            from langchain_core.messages import HumanMessage

            digest = []
            for message in messages:
                if getattr(message, "type", None) == "tool":
                    digest.append(
                        {
                            "tool": getattr(message, "name", None),
                            "content": str(getattr(message, "content", ""))[:2500],
                        }
                    )
            return list(messages[:2]) + [
                HumanMessage(
                    content=(
                        "请只根据以下已返回且可能被截断的 Tool 事实输出要求的 Schema。"
                        "不要调用 Tool、不要补造数据；缺失或不可用字段必须保留为不确定。\n"
                        + json.dumps(digest, ensure_ascii=False)
                    )
                )
            ], len(digest)

        class Bound:
            def invoke(self, messages):
                if owner.calls >= owner.max_calls or monotonic() >= owner.deadline:
                    raise RuntimeError("shadow model call/time budget reached")
                owner.calls += 1
                if phase != "tool_selection":
                    messages = complete_tool_responses(messages, owner.audit)
                owner.audit.event("model_input", phase=phase, call=owner.calls, messages=messages)
                try:
                    result = runnable.invoke(messages)
                    if result is None and phase != "tool_selection":
                        # Some OpenAI-compatible DeepSeek responses return an
                        # empty parsed object after a long Tool transcript.
                        # Retry once with a compact, factual evidence digest;
                        # do not invent or transform any Tool values.
                        compact, tool_result_count = compact_factual_messages(messages)
                        owner.audit.event("structured_compact_retry", phase=phase,
                                          tool_result_count=tool_result_count)
                        result = runnable.invoke(compact)
                except Exception as exc:
                    from pydantic import ValidationError
                    if (
                        type(exc).__name__ == "OutputParserException"
                        and phase != "tool_selection"
                        and owner.calls < owner.max_calls
                        and monotonic() < owner.deadline
                    ):
                        compact, tool_result_count = compact_factual_messages(messages)
                        owner.calls += 1
                        owner.audit.event(
                            "structured_protocol_retry",
                            phase=phase,
                            call=owner.calls,
                            tool_result_count=tool_result_count,
                        )
                        owner.audit.event(
                            "model_input",
                            phase=f"{phase}_protocol_retry",
                            call=owner.calls,
                            messages=compact,
                        )
                        try:
                            result = runnable.invoke(compact)
                        except Exception as retry_exc:
                            owner.audit.event(
                                "model_error",
                                phase=f"{phase}_protocol_retry",
                                call=owner.calls,
                                error_type=type(retry_exc).__name__,
                                status_code=getattr(retry_exc, "status_code", None),
                                message=owner.audit.clean(str(retry_exc))[:1200],
                            )
                            raise RuntimeError(
                                f"model protocol retry failed ({type(retry_exc).__name__})"
                            ) from None
                        owner.audit.event(
                            "model_output",
                            phase=f"{phase}_protocol_retry",
                            call=owner.calls,
                            result=result,
                        )
                        return result
                    if (isinstance(exc, ValidationError) and phase != "tool_selection"
                            and owner.calls < owner.max_calls and monotonic() < owner.deadline):
                        from langchain_core.messages import HumanMessage
                        owner.audit.event("schema_repair_retry", phase=phase,
                                          validation_error=owner.audit.clean(str(exc))[:1600])
                        owner.calls += 1
                        repair_messages = list(messages) + [HumanMessage(content=(
                            "上一次输出未通过目标 Schema 校验。只修正字段类型或枚举值，"
                            "保持事实、证据、不确定性和业务含义不变。校验错误：\n"
                            + owner.audit.clean(str(exc))[:1600]
                        ))]
                        owner.audit.event("model_input", phase=f"{phase}_schema_repair",
                                          call=owner.calls, messages=repair_messages)
                        try:
                            result = runnable.invoke(repair_messages)
                        except Exception as repair_exc:
                            owner.audit.event("model_error", phase=f"{phase}_schema_repair",
                                              call=owner.calls, error_type=type(repair_exc).__name__,
                                              status_code=getattr(repair_exc, "status_code", None),
                                              message=owner.audit.clean(str(repair_exc))[:1200])
                            raise RuntimeError(f"model schema repair failed ({type(repair_exc).__name__})") from None
                        owner.audit.event("model_output", phase=f"{phase}_schema_repair",
                                          call=owner.calls, result=result)
                        return result
                    # Do not persist HTTP exception bodies or credential-bearing requests.
                    owner.audit.event("model_error", phase=phase, call=owner.calls,
                                      error_type=type(exc).__name__,
                                      status_code=getattr(exc, "status_code", None),
                                      message=owner.audit.clean(str(exc))[:1200])
                    raise RuntimeError(f"model request failed ({type(exc).__name__})") from None
                owner.audit.event("model_output", phase=phase, call=owner.calls, result=result)
                return result
        return Bound()

    def bind_tools(self, tools):
        return self._bound(self.model.bind_tools(tools), "tool_selection")

    def with_structured_output(self, schema, **kwargs):
        return self._bound(self.model.with_structured_output(
            schema, method=self.structured_method, **kwargs), schema.__name__)


def complete_tool_responses(messages, audit):
    """Close unexecuted requests after a node's Tool budget, without inventing facts."""
    from langchain_core.messages import AIMessage, ToolMessage
    completed = []
    pending = {}

    def close_pending():
        for call_id, name in pending.items():
            result = {"status": "unavailable", "source": "runtime_tool_budget", "data": {},
                      "warnings": ["Tool request was not executed because the node stopped its Tool loop."]}
            completed.append(ToolMessage(content=json.dumps(result), tool_call_id=call_id, name=name))
            audit.event("tool_not_executed", tool=name, call_id=call_id, result=result)
        pending.clear()

    for message in messages:
        if isinstance(message, ToolMessage):
            pending.pop(message.tool_call_id, None)
        else:
            close_pending()
        completed.append(message)
        if isinstance(message, AIMessage):
            pending.update({call['id']: call['name'] for call in message.tool_calls})
    close_pending()
    return completed


def build_data_status(units, materials, report_date):
    """Return deterministic export coverage and unit/material reconciliation facts."""
    import pandas as pd
    from replay_core import reconcile
    end = pd.Timestamp(report_date)
    unit_rows = units[pd.to_datetime(units["data_date"]) <= end].copy()
    material_rows = materials[pd.to_datetime(materials["data_date"]) <= end].copy()
    status = reconcile(unit_rows, material_rows, {})
    status["report_date_cutoff"] = report_date
    status["unit_observed_min_date"] = str(pd.to_datetime(unit_rows["data_date"]).min())[:10] if not unit_rows.empty else None
    status["unit_observed_max_date"] = str(pd.to_datetime(unit_rows["data_date"]).max())[:10] if not unit_rows.empty else None
    status["material_observed_min_date"] = str(pd.to_datetime(material_rows["data_date"]).min())[:10] if not material_rows.empty else None
    status["material_observed_max_date"] = str(pd.to_datetime(material_rows["data_date"]).max())[:10] if not material_rows.empty else None
    return status


def build_inactivity_summary(units, materials, report_date, min_inactive_days=7, sample_limit=10):
    """Build bounded observed inactivity counts for the initial account context."""
    import pandas as pd
    cutoff = pd.Timestamp(report_date)
    result = {"report_date_cutoff": report_date, "min_inactive_days": min_inactive_days}
    for object_type, frame, id_column in (("plan", units, "unit_id"), ("material", materials, "material_id")):
        frame = frame[pd.to_datetime(frame["data_date"]) <= cutoff].copy()
        rows = []
        for object_id, group in frame.groupby(id_column, sort=True):
            dates = pd.to_datetime(group["data_date"])
            spend = pd.to_numeric(group["spend"], errors="coerce").fillna(0)
            spend_dates = dates[spend > 0]
            if spend_dates.empty:
                continue
            last_spend = spend_dates.max()
            inactive_days = int((cutoff - last_spend).days)
            if inactive_days >= min_inactive_days:
                rows.append({"id": str(object_id), "last_spend_date": str(last_spend)[:10],
                             "last_observed_date": str(dates.max())[:10],
                             "inactive_days_after_last_spend": inactive_days})
        rows.sort(key=lambda row: (-row["inactive_days_after_last_spend"], row["id"]))
        result[object_type] = {"total_matching": len(rows), "sample_items": rows[:sample_limit]}
    result["warning"] = "连续未消耗是观察事实，不能单独解释为暂停、下线或预算耗尽。"
    return result


def build_tools(units, materials, report_date, audit, *, account_id="unknown",
                shimo_path=None, plan_context_path=None, content_path=None):
    """Reuse fact providers; unavailable sources stay unavailable."""
    import pandas as pd
    from langchain_core.tools import StructuredTool
    from services.account_metrics_tool import AccountMetricsTool
    from services.plan_metrics_tool import PlanMetricsTool
    from services.material_metrics_tool import MaterialMetricsTool
    from services.lead_quality_tool import LeadQualityTool
    from services.history_context_tool import HistoryContextTool
    from services.sales_feedback_tool import SalesFeedbackTool
    from services.v2_context_tools import V2ContextTools

    end = pd.Timestamp(report_date)
    units = units[pd.to_datetime(units["data_date"]) <= end].copy()
    materials = materials[pd.to_datetime(materials["data_date"]) <= end].copy()
    context = V2ContextTools(account_id, report_date, shimo_path=shimo_path,
                             plan_context_path=plan_context_path, content_path=content_path)
    providers = [AccountMetricsTool(units), PlanMetricsTool(units),
                 MaterialMetricsTool(materials), LeadQualityTool(units=units, materials=materials),
                 context, HistoryContextTool(), SalesFeedbackTool()]
    names = ["get_account_metrics", "get_plan_metrics", "get_material_metrics",
             "get_lead_quality", "get_content_asset", "get_history_context", "get_sales_feedback"]
    descriptions = [
        "查询账户实际指标；report_date 为 ISO 日期，time_window 可用 3d/7d/30d。缺质量反馈不会补零。",
        "按 plan_id 查询实际计划指标；先用 list_entities 获取 ID。支持 3d/7d/30d。",
        "按 material_id 查询实际素材指标；先用 list_entities 获取 ID。支持 3d/7d/30d。",
        "查询账户/计划/素材有效线索质量；无反馈时返回 unavailable，不代表零。",
        "查询素材标题、口播、字幕和画面要点；读取报告日以前的正文快照，缺失返回 unavailable。",
        "查询已保存历史基线；当前未挂接历史数据库，可改用指标 Tool 查询不同历史日期窗口。",
        "查询销售跟进事实；当前未接入 CRM，缺失时返回 unavailable。",
    ]
    data_status = build_data_status(units, materials, report_date)
    data_status["inactivity_coverage"] = build_inactivity_summary(units, materials, report_date)

    def get_data_status() -> dict:
        """查询导出日期覆盖与计划/素材报表总量对账；这是确定性数据质量事实，不做业务判断。"""
        return {"status": "ok" if data_status["all_pass"] else "partial",
                "source": "replay_core.reconcile", "time_window": {"end_date": report_date},
                "data": data_status,
                "warnings": [] if data_status["all_pass"] else ["unit/material totals do not reconcile"]}

    def get_entity_metrics_batch(object_type: str, object_ids: list[str],
                                 report_date: str, time_window: str) -> dict:
        """一次查询最多50个计划或素材的同窗口指标；只返回事实，适合横向比较，避免逐ID调用。"""
        if object_type not in {"plan", "material"}:
            raise ValueError("object_type must be plan or material")
        if not object_ids or len(object_ids) > 50:
            raise ValueError("object_ids must contain 1..50 IDs")
        provider = providers[1] if object_type == "plan" else providers[2]
        method = provider.get_plan_metrics if object_type == "plan" else provider.get_material_metrics
        argument = "plan_id" if object_type == "plan" else "material_id"
        items = []
        for object_id in object_ids:
            result = method(**{argument: object_id, "report_date": report_date,
                               "time_window": time_window})
            items.append({"object_id": str(object_id), "result": result})
        return {"status": "ok", "source": "batch_readonly_metrics",
                "time_window": time_window, "data": {"object_type": object_type,
                "item_count": len(items), "items": items}, "warnings": []}

    def list_entities(object_type: str, offset: int = 0, limit: int = 50) -> dict:
        """按 ID 分页列出截至报告日观察到的计划/素材及最后出现日期，不做业务筛选。"""
        if object_type not in {"plan", "material"} or offset < 0 or not 1 <= limit <= 100:
            raise ValueError("object_type must be plan/material; offset >= 0; limit 1..100")
        frame, column = (units, "unit_id") if object_type == "plan" else (materials, "material_id")
        latest = frame.groupby(column)["data_date"].max().sort_index()
        rows = [{"id": str(key), "last_seen_date": str(value)[:10]}
                for key, value in latest.iloc[offset:offset + limit].items()]
        return {"status": "ok", "source": "local_export_catalog", "time_window": {"end_date": report_date},
                "data": {"object_type": object_type, "total": len(latest), "items": rows,
                         "next_offset": offset + limit if offset + limit < len(latest) else None}, "warnings": []}

    def get_inactive_entities(object_type: str, min_inactive_days: int = 7,
                              limit: int = 50) -> dict:
        """汇总截至报告日已有消耗但连续未再消耗的计划/素材；只返回观察日期事实，不解释为暂停或下线。"""
        if object_type not in {"plan", "material"}:
            raise ValueError("object_type must be plan or material")
        if not 1 <= min_inactive_days <= 3650 or not 1 <= limit <= 100:
            raise ValueError("min_inactive_days must be 1..3650 and limit 1..100")
        summary = build_inactivity_summary(units, materials, report_date,
                                           min_inactive_days, sample_limit=limit)
        rows = summary[object_type]["sample_items"]
        total_matching = summary[object_type]["total_matching"]
        return {"status": "ok", "source": "local_export_catalog",
                "time_window": {"end_date": report_date},
                "data": {"object_type": object_type, "total_matching": total_matching,
                         "items": rows[:limit], "min_inactive_days": min_inactive_days},
                "warnings": [summary["warning"]] if rows else []}

    functions = [getattr(provider, name) for provider, name in zip(providers, names)]
    functions.append(get_data_status)
    descriptions.append(get_data_status.__doc__)
    functions.append(get_entity_metrics_batch)
    descriptions.append(get_entity_metrics_batch.__doc__)
    functions.append(list_entities)
    descriptions.append(list_entities.__doc__)
    functions.append(get_inactive_entities)
    descriptions.append(get_inactive_entities.__doc__)
    context_descriptions = {
        "get_deep_conversion": "查询石墨有效/无效(深转)日汇总。object_type 默认 account；取 plan 或 material 时必须同时给出 object_id，"
                               "否则该次调用无效。本账户没有计划级/素材级石墨行时，实体范围返回 unavailable，不能把账户汇总分摊给实体。"
                               "窗口内任一日缺记录或值为空时，合计与深转成本保持缺失，不补零。",
        "get_plan_context": "按 plan_id 查询报告日以前的计划设置与修改历史快照。无快照时返回 unavailable，"
                            "不能据此推断暂停、下线、预算耗尽或出价变化。",
        "get_material_plan_comparison": "按 material_id 查询该素材已知的计划归属与设置；material_id 必须来自 "
                                        "list_entities(object_type=\"material\")，传计划ID只会得到空结果。",
        "get_adjustment_options": "按 plan_id 计算预算/出价的 ±10%~30% 候选数值，供策略引用；无计划设置快照时返回 unavailable，"
                                  "返回值只是计算候选，不是执行参数。",
    }
    for name in ("get_deep_conversion", "get_plan_context", "get_material_plan_comparison", "get_adjustment_options"):
        function = getattr(context, name)
        functions.append(function)
        descriptions.append(context_descriptions[name])
    tools = {}
    for function, description in zip(functions, descriptions):
        signature = inspect.signature(function)

        def wrap(function, signature):
            @wraps(function)
            def execute(*args, **kwargs):
                arguments = dict(signature.bind(*args, **kwargs).arguments)
                for field in ("report_date", "before_date"):
                    if arguments.get(field) and date.fromisoformat(arguments[field]) > date.fromisoformat(report_date):
                        raise ValueError("query date exceeds this historical run's cutoff")
                result = function(*args, **kwargs)
                audit.event("tool_result", tool=function.__name__, arguments=arguments, result=result)
                return result
            return execute

        tools[function.__name__] = StructuredTool.from_function(
            func=wrap(function, signature), name=function.__name__, description=description)
    return tools


def build_model_graph(model, tools, audit, *, per_agent=2, max_total=8, max_tools=8):
    from graph import build_graph
    from agents.supervisor_agent import SupervisorAgent
    from agents.data_analysis_agent import DataAnalysisAgent
    from agents.ads_diagnosis_agent import AdsDiagnosisAgent
    from agents.content_review_agent import ContentReviewAgent
    from agents.strategy_agent import StrategyAgent

    limits = {name: per_agent for name in ("data_analysis", "ads_diagnosis", "content_review", "strategy")}
    data_names = set(tools) - {"get_content_asset", "get_sales_feedback", "get_adjustment_options", "get_lead_quality"}
    content_names = {"get_content_asset", "get_material_metrics", "get_plan_metrics", "get_history_context", "list_entities",
                     "get_entity_metrics_batch", "get_deep_conversion", "get_plan_context", "get_material_plan_comparison"}
    nodes = {
        "supervisor": SupervisorAgent(model, agent_call_limits=limits, max_total_calls=max_total, structured_retries=1),
        "data_analysis_agent": DataAnalysisAgent(model, tools=[tools[n] for n in sorted(data_names)], max_tool_calls=max_tools, structured_retries=1),
        "ads_diagnosis_agent": AdsDiagnosisAgent(model, tools=[tools[n] for n in sorted(data_names)], max_tool_calls=max_tools, structured_retries=1),
        "content_review_agent": ContentReviewAgent(model, tools=[tools[n] for n in sorted(content_names & set(tools))], max_tool_calls=min(max_tools, 8), structured_retries=1),
        "strategy_agent": StrategyAgent(model, structured_retries=1,
                                         tools=[tools["get_adjustment_options"]] if "get_adjustment_options" in tools else None),
    }
    overrides = {name: audit.node(name.removesuffix("_agent"), node) for name, node in nodes.items()}
    overrides["push"] = lambda state: {"push_result": {"status": "disabled", "reason": "local_model_shadow"}}
    return build_graph(node_overrides=overrides, agent_call_limits=limits, max_total_calls=max_total)


def run_account(source, inputs, output, report_date, settings, args, *, client=None):
    from replay_core import load_platform_reports
    from run_import import discover_exports
    from services.llm_factory import create_llm
    from services.golden_case_runner import CASE_RESULT_FIELDS

    exports = discover_exports(inputs)
    if not exports.get("unit") or not exports.get("material"):
        raise ValueError("unit/material exports are required")
    units, materials, _ = load_platform_reports(exports["unit"], exports["material"])
    data_status = build_data_status(units, materials, report_date)
    inactivity_summary = build_inactivity_summary(units, materials, report_date)
    # The shared AccountContext contract preserves the deterministic data_status
    # container; keep the bounded inactivity facts inside it rather than adding
    # an extra top-level field that older State schemas may discard.
    data_status["inactivity_coverage"] = inactivity_summary
    run_id = f"model_shadow_{report_date}_{uuid4().hex[:10]}"
    audit = Audit(output / run_id, (settings.deepseek_api_key, settings.openai_api_key))
    audit.write("manifest.json", {"run_id": run_id, "report_date": report_date,
                                "principles_version": "v2",
                                "context_snapshots": {
                                    name: str(getattr(args, name)) if getattr(args, name, None) else None
                                    for name in ("shimo_snapshot", "plan_context", "content_snapshot")},
                                "provider": settings.agent_provider, "model": settings.agent_model,
                                "mode": "model_backed" if client is None else "injected_test_model",
                                "inputs": [str(exports["unit"]), str(exports["material"])],
                                "push": "disabled", "limits": {"per_agent": args.per_agent, "total": args.max_total,
                                "tools_per_turn": args.max_tools, "model_calls": args.max_model_calls}})
    try:
        from services.v2_context_tools import V2ContextTools
        coverage = V2ContextTools(inputs.name, report_date, shimo_path=getattr(args, "shimo_snapshot", None))
        data_status["deep_conversion_coverage"] = {
            window: coverage.get_deep_conversion(report_date, window)["data"]
            for window in ("1d", "3d", "5d", "30d")}
        tools = build_tools(units, materials, report_date, audit, account_id=inputs.name,
                            shimo_path=getattr(args, "shimo_snapshot", None),
                            plan_context_path=getattr(args, "plan_context", None),
                            content_path=getattr(args, "content_snapshot", None))
        if client is None:
            client = create_llm(settings, timeout=args.timeout, max_retries=0)
            if settings.agent_provider.lower() in {"deepseek", "deepseek-compatible"}:
                # Named tool_choice used by structured output requires non-thinking mode.
                # https://api-docs.deepseek.com/api/create-chat-completion/
                client = client.model_copy(update={"extra_body": {"thinking": {"type": "disabled"}}})
        model = AuditedModel(client, audit, max_calls=args.max_model_calls,
                             deadline_seconds=args.deadline)
        graph = build_model_graph(model, tools, audit, per_agent=args.per_agent,
                                  max_total=args.max_total, max_tools=args.max_tools)
        state = graph.invoke({"run_id": run_id, "report_date": report_date,
                              "task": args.task,
                              "account_context": {"account_id": inputs.name,
                                  "data_source": "local_historical_exports",
                                  "data_status": data_status,
                                  "constraints": "V2：报告日完整一天，结合3d/5d/30d；深转来自石墨，有效=加微信；缺深转只限制相关判断，不要求ROI或销售流程；素材无需正文也可分析表现。"}},
                             config={"recursion_limit": 4 * args.max_total + 16})
        audit.write("state.json", state)
        (audit.folder / "report.md").write_text(audit.clean(state.get("final_report") or ""), encoding="utf-8")
        review = inspect_state(state, {name: schema for name, (_, schema) in CASE_RESULT_FIELDS.items()})
        if not state.get("strategy_result"):
            review["issues"].append("missing strategy result: run ended before analysis completed")
            review["structural_status"] = "fail"
        state_errors = [str(item) for item in (state.get("errors") or [])]
        bounded_only = bool(state_errors) and all("Tool call limit reached" in item for item in state_errors)
        operational_status = (
            "partial" if bounded_only and state.get("final_report") and state.get("strategy_result")
            else review["structural_status"]
        )
        if operational_status == "pass" and data_status.get("all_pass") is False:
            operational_status = "partial"
        review.update(run_id=run_id, model_calls=model.calls,
                      reconciliation_pass=data_status.get("all_pass"),
                      semantic_status="pending_review",
                      operational_status=operational_status,
                      bounded_limit_errors=state_errors if bounded_only else [])
        audit.write("review.json", review)
        return {"run_id": run_id, "status": operational_status, "model_calls": model.calls,
                "agents": [name for name, used in review["agent_coverage"].items() if used],
                "report": str(audit.folder / "report.md"), "data_date": report_date, "push": "disabled"}
    except Exception as exc:
        audit.write("failure.json", {"status": "failed", "error_type": type(exc).__name__})
        return {"run_id": run_id, "status": "fail", "error_type": type(exc).__name__}


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True, help="one account export folder")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-date", default=(datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat())
    parser.add_argument("--shimo-snapshot", type=Path, help="account-bound Shimo daily JSON snapshot (read-only)")
    parser.add_argument("--plan-context", type=Path, help="account-bound dated plan settings JSON snapshot (read-only)")
    parser.add_argument("--content-snapshot", type=Path, help="account-bound dated material text JSON snapshot (read-only)")
    parser.add_argument("--model", help="process-only model override")
    parser.add_argument("--preflight", action="store_true", help="validate config without network")
    parser.add_argument("--per-agent", type=positive, default=2)
    parser.add_argument("--max-total", type=positive, default=8)
    parser.add_argument("--max-tools", type=positive, default=32,
                        help="per-turn cap for Data/Diagnosis; Content is capped at 8")
    parser.add_argument("--max-model-calls", type=positive, default=40)
    parser.add_argument("--timeout", type=positive, default=45)
    parser.add_argument("--deadline", type=positive, default=600)
    parser.add_argument("--task", default="分析本账户截至报告日的投放情况，按各自职责查询必要事实、保留不确定性，并形成中文日报和策略建议。")
    args = parser.parse_args()
    source, inputs, output = args.source.resolve(), args.inputs.resolve(), args.output.resolve()
    if output.is_relative_to(source) or output.is_relative_to(inputs):
        parser.error("output must be outside source and input directories")
    date.fromisoformat(args.report_date)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(source / "src"), str(source)]
    from config import Settings
    settings = Settings.from_env()
    settings = replace(settings, agent_enabled=True, agent_model=args.model or settings.agent_model)
    missing = settings.missing_for_agent()
    if args.preflight or missing:
        print(json.dumps({"status": "blocked" if missing else "ready", "missing": missing,
                          "provider": settings.agent_provider, "model": settings.agent_model,
                          "push": "disabled"}, ensure_ascii=False))
        return 2 if missing else 0
    result = run_account(source, inputs, output, args.report_date, settings, args)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"pass", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
