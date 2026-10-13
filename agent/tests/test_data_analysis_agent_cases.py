import unittest
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agents.data_analysis_agent import DataAnalysisAgent
from schemas.common import EvidenceItem
from schemas.data_analysis import (
    AnalysisFinding,
    DataAnalysisResult,
    DiagnosisTarget,
    MissingDataItem,
)


def _evidence(metric, value, *, object_type="account", object_id="acct-1"):
    return EvidenceItem(
        evidence_type="metric",
        source="fixture.data_analysis",
        object_type=object_type,
        object_id=object_id,
        metric_or_content=metric,
        value=value,
        time_range="day",
    )


@dataclass(frozen=True)
class _DataAnalysisCase:
    name: str
    state: dict[str, Any]
    result: DataAnalysisResult
    tool_calls: tuple[dict[str, Any], ...] = ()
    tool_names: tuple[str, ...] = ()


class _CaseStructuredRunnable:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        self.model.structured_inputs.append(list(messages))
        # Return a JSON-shaped object so the Node, rather than the fixture,
        # exercises the shared Pydantic validation boundary.
        return self.model.case.result.model_dump(mode="json")


class _CaseModel:
    def __init__(self, case):
        self.case = case
        self.bound_tools = None
        self.planning_inputs = []
        self.structured_inputs = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.planning_inputs.append(list(messages))
        if self.case.tool_calls:
            calls = self.case.tool_calls
            self.case = _DataAnalysisCase(
                name=self.case.name,
                state=self.case.state,
                result=self.case.result,
                tool_calls=(),
                tool_names=self.case.tool_names,
            )
            return AIMessage(content="", tool_calls=list(calls))
        return AIMessage(content="已有上下文足够，进入结构化输出")

    def with_structured_output(self, schema, *, include_raw=False):
        return _CaseStructuredRunnable(self)


def _tool_fixture(name: str):
    def tool(**arguments):
        return {
            "status": "ok",
            "source": f"fixture.{name}",
            "time_window": {
                "name": arguments.get("time_window", "day"),
                "start_date": "2026-09-17",
                "end_date": "2026-09-17",
            },
            "data": {
                "object_id": arguments.get("plan_id")
                or arguments.get("material_id")
                or "acct-1",
                "fixture": name,
            },
            "warnings": [],
        }

    return tool


def _base_state(task: str):
    return {
        "run_id": "mock-run",
        "report_date": "2026-09-17",
        "task": task,
        "account_context": {"account_id": "acct-1"},
        "evidence": [],
    }


def _case_fixtures():
    account_evidence = _evidence("spend", 100)
    plan_evidence = _evidence(
        "lead_cost",
        48,
        object_type="plan",
        object_id="plan-1",
    )
    ctr_evidence = _evidence(
        "ctr",
        0.42,
        object_type="material",
        object_id="material-1",
    )
    valid_rate_evidence = _evidence(
        "valid_lead_rate",
        0.08,
        object_type="material",
        object_id="material-1",
    )

    return (
        _DataAnalysisCase(
            name="A_account_normal",
            state=_base_state("检查账户是否存在可确认的数据异常"),
            result=DataAnalysisResult(
                summary="当前窗口内未发现可确认的异常。",
                observations=[
                    AnalysisFinding(
                        description="账户当前窗口有可用的基础指标记录。",
                        object_type="account",
                        object_id="acct-1",
                        evidence=[account_evidence],
                    )
                ],
                evidence=[account_evidence],
            ),
        ),
        _DataAnalysisCase(
            name="B_plan_cost_needs_diagnosis",
            state=_base_state("检查计划成本变化并找出值得继续诊断的对象"),
            result=DataAnalysisResult(
                summary="计划 plan-1 的成本表现出现需要进一步诊断的变化。",
                anomalies=[
                    AnalysisFinding(
                        description="计划成本相对当前可比数据出现需要复核的变化。",
                        object_type="plan",
                        object_id="plan-1",
                        evidence=[plan_evidence],
                    )
                ],
                targets_for_diagnosis=[
                    DiagnosisTarget(
                        question="进一步诊断计划 plan-1 成本变化的原因。",
                        object_type="plan",
                        object_id="plan-1",
                    )
                ],
                evidence=[plan_evidence],
            ),
            tool_calls=(
                {
                    "name": "get_plan_metrics",
                    "args": {
                        "plan_id": "plan-1",
                        "report_date": "2026-09-17",
                        "time_window": "day",
                    },
                    "id": "case-b-plan",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_plan_metrics", "get_account_metrics"),
        ),
        _DataAnalysisCase(
            name="C_frontend_quality_conflict",
            state=_base_state("检查点击表现与有效线索质量是否一致"),
            result=DataAnalysisResult(
                summary="点击率与有效线索率方向不一致，构成需要复核的数据冲突。",
                conflicts=[
                    AnalysisFinding(
                        description="前端点击率与后端有效线索率的表现方向不一致。",
                        object_type="material",
                        object_id="material-1",
                        evidence=[ctr_evidence, valid_rate_evidence],
                    )
                ],
                targets_for_diagnosis=[
                    DiagnosisTarget(
                        question="进一步诊断 material-1 的点击与线索质量关系。",
                        object_type="material",
                        object_id="material-1",
                    )
                ],
                evidence=[ctr_evidence, valid_rate_evidence],
            ),
            tool_calls=(
                {
                    "name": "get_lead_quality",
                    "args": {
                        "report_date": "2026-09-17",
                        "time_window": "day",
                        "object_type": "material",
                        "object_id": "material-1",
                    },
                    "id": "case-c-quality",
                    "type": "tool_call",
                },
            ),
            tool_names=("get_lead_quality", "get_material_metrics"),
        ),
        _DataAnalysisCase(
            name="D_missing_quality_data",
            state=_base_state("判断当前数据是否足以确认线索质量变化"),
            result=DataAnalysisResult(
                summary="当前数据不足，不能确认线索质量变化。",
                missing_data=[
                    MissingDataItem(
                        field="valid_leads",
                        reason="当前窗口没有可用的线索质量反馈记录。",
                        impact="无法验证前端留资与有效线索质量之间的关系。",
                        requested_from="feedback_export",
                    )
                ],
                evidence=[],
            ),
        ),
    )


class DataAnalysisAgentCaseTests(unittest.TestCase):
    def test_four_mock_cases_pass_the_core_behavior_checks(self):
        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                tools = {
                    name: _tool_fixture(name)
                    for name in case.tool_names
                }

                update = DataAnalysisAgent(
                    model=model,
                    tools=tools or None,
                    principles="fixture principles; no business thresholds",
                    max_tool_calls=4,
                ).run(case.state)
                result = update["data_analysis_result"]

                self.assertIsInstance(result, DataAnalysisResult)
                self.assertIn("data_analysis_result", update)
                self.assertIn("evidence", update)
                self.assertIn("tool_trace", update)
                self.assertEqual(
                    len(update["tool_trace"]),
                    len(case.tool_calls),
                )

                if case.name == "A_account_normal":
                    self.assertFalse(result.anomalies)
                    self.assertFalse(result.conflicts)
                    self.assertFalse(result.targets_for_diagnosis)
                elif case.name == "B_plan_cost_needs_diagnosis":
                    self.assertTrue(result.anomalies)
                    self.assertEqual(
                        result.targets_for_diagnosis[0].object_id,
                        "plan-1",
                    )
                    self.assertEqual(update["tool_trace"][0].tool_name, "get_plan_metrics")
                elif case.name == "C_frontend_quality_conflict":
                    self.assertTrue(result.conflicts)
                    self.assertTrue(result.targets_for_diagnosis)
                    output_text = result.model_dump_json().lower()
                    self.assertNotIn("hook", output_text)
                    self.assertEqual(update["tool_trace"][0].tool_name, "get_lead_quality")
                elif case.name == "D_missing_quality_data":
                    self.assertTrue(result.missing_data)
                    self.assertEqual(result.missing_data[0].field, "valid_leads")

                self.assertTrue(
                    {"budget_change", "bid_change", "pause_plan", "scale_percentage"}.isdisjoint(
                        result.model_dump()
                    )
                )

    def test_tool_message_is_present_only_when_a_case_selected_a_tool(self):
        for case in _case_fixtures():
            with self.subTest(case=case.name):
                model = _CaseModel(case)
                tools = {
                    name: _tool_fixture(name)
                    for name in case.tool_names
                }
                DataAnalysisAgent(
                    model=model,
                    tools=tools or None,
                    principles="fixture principles",
                ).run(case.state)
                tool_messages = [
                    message
                    for message in model.structured_inputs[0]
                    if isinstance(message, ToolMessage)
                ]
                self.assertEqual(len(tool_messages), len(case.tool_calls))


if __name__ == "__main__":
    unittest.main()
