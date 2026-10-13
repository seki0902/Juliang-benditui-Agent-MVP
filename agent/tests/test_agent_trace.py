import json
import unittest
from datetime import datetime

from graph import build_graph
from schemas.common import AgentName, AgentTraceRecord
from schemas.data_analysis import DataAnalysisResult
from schemas.supervisor import SupervisorDecision
from services.agent_trace import (
    extract_retry_count,
    extract_token_usage,
    json_agent_trace_payload,
    make_agent_trace_record,
    retry_count_from_error,
    summarize_agent_state,
)
from services.structured_output import StructuredOutputError
from services.structured_output import invoke_structured


class _UsageNode:
    class _Model:
        token_usage = {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "api_key": "must-not-be-copied",
        }

    model = _Model()

    def __call__(self, _state):
        return {}


class AgentTraceServiceTests(unittest.TestCase):
    def test_state_summary_and_output_schema_are_bounded(self):
        secret = "very-secret-value"
        state = {
            "run_id": "run-1",
            "report_date": "2026-09-18",
            "task": f"检查 api_key={secret}",
            "account_context": {
                "account_id": "acct-1",
                "api_key": secret,
                "nested": {"access_token": secret},
            },
            "evidence": [{"value": secret} for _ in range(100)],
            "errors": [],
        }
        record = make_agent_trace_record(
            agent="data_analysis",
            state=state,
            output={"data_analysis_result": DataAnalysisResult(summary="observed")},
            status="succeeded",
            duration_ms=1.5,
            token_usage={"input_tokens": 3, "output_tokens": 2, "secret": secret},
        )

        self.assertIsInstance(record, AgentTraceRecord)
        self.assertEqual(record.agent, AgentName.DATA_ANALYSIS)
        self.assertEqual(record.run_id, "run-1")
        self.assertEqual(record.output_schema["result_schemas"]["data_analysis_result"]["schema"], "DataAnalysisResult")
        self.assertEqual(record.token_usage, {"input_tokens": 3, "output_tokens": 2})
        payload = record.model_dump_json()
        self.assertNotIn(secret, payload)
        self.assertIsNotNone(datetime.fromisoformat(record.timestamp))
        self.assertEqual(json.loads(json_agent_trace_payload(record))["status"], "succeeded")

        summary = summarize_agent_state(state)
        self.assertEqual(summary["account_context_keys"], ["account_id", "nested"])
        self.assertEqual(summary["collection_counts"]["evidence"], 100)
        self.assertNotIn(secret, json.dumps(summary, ensure_ascii=False))

    def test_runtime_usage_and_structured_retry_metadata_are_optional_and_bounded(self):
        usage = extract_token_usage(_UsageNode())
        self.assertEqual(
            usage,
            {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        )
        self.assertEqual(
            retry_count_from_error(
                StructuredOutputError(
                    DataAnalysisResult,
                    attempts=3,
                    cause=ValueError("invalid output"),
                )
            ),
            2,
        )
        self.assertEqual(retry_count_from_error(ValueError("no metadata")), 0)

    def test_successful_structured_retry_is_exposed_to_trace_boundary(self):
        class Runnable:
            def __init__(self, model):
                self.model = model

            def invoke(self, _payload):
                response = self.model.responses[min(self.model.calls, 1)]
                self.model.calls += 1
                return response

        class Model:
            responses = [{"summary": "invalid", "pause_plan": True}, {"summary": "valid"}]
            calls = 0

            def with_structured_output(self, _schema, *, include_raw=False):
                return Runnable(self)

        model = Model()
        result = invoke_structured(
            model,
            DataAnalysisResult,
            {"request": "retry"},
            max_retries=1,
        )
        self.assertEqual(result.summary, "valid")
        self.assertEqual(extract_retry_count(model), 1)


class AgentTraceGraphTests(unittest.TestCase):
    @staticmethod
    def _decision(target: AgentName, *, can_finish: bool = False):
        return SupervisorDecision(
            next_agent=target,
            task="处理当前 State 的下一项协作任务。",
            reason="由 Supervisor 根据当前 State 决定 Graph 下一节点。",
            can_finish=can_finish,
        )

    def test_graph_records_supervisor_and_specialist_boundaries(self):
        supervisor_calls = {"count": 0}

        def supervisor(_state):
            supervisor_calls["count"] += 1
            if supervisor_calls["count"] == 1:
                return {"supervisor_decision": self._decision(AgentName.DATA_ANALYSIS)}
            return {
                "supervisor_decision": self._decision(
                    AgentName.FINISH,
                    can_finish=True,
                )
            }

        result = build_graph(
            node_overrides={
                "supervisor": supervisor,
                "data_analysis_agent": lambda _state: {
                    "data_analysis_result": DataAnalysisResult(summary="fixture")
                },
                "report_renderer": lambda _state: {"final_report": "fixture report"},
                "push": lambda _state: {"push_result": {"status": "skipped"}},
            }
        ).invoke(
            {
                "report_date": "2026-09-18",
                "task": "记录 Agent Trace",
                "account_context": {"account_id": "acct-1"},
            }
        )

        trace = result["agent_trace"]
        self.assertEqual(
            [record.agent for record in trace],
            ["supervisor", AgentName.DATA_ANALYSIS, "supervisor"],
        )
        self.assertEqual(trace[1].output_schema["result_schemas"]["data_analysis_result"]["schema"], "DataAnalysisResult")
        self.assertTrue(all(record.duration_ms >= 0 for record in trace))
        self.assertTrue(all(record.status == "succeeded" for record in trace))
        self.assertTrue(all(record.retry_count == 0 for record in trace))
        self.assertTrue(all(record.timestamp for record in trace))

    def test_graph_records_failure_and_structured_retry_count(self):
        def supervisor(_state):
            return {"supervisor_decision": self._decision(AgentName.DATA_ANALYSIS)}

        def broken_agent(_state):
            raise StructuredOutputError(
                DataAnalysisResult,
                attempts=3,
                cause=ValueError("api_key=do-not-log"),
            )

        result = build_graph(
            node_overrides={
                "supervisor": supervisor,
                "data_analysis_agent": broken_agent,
                "report_renderer": lambda _state: {"final_report": "safe finish"},
                "push": lambda _state: {"push_result": {"status": "skipped"}},
            }
        ).invoke(
            {
                "report_date": "2026-09-18",
                "task": "记录失败 Agent Trace",
                "account_context": {"account_id": "acct-1"},
            }
        )

        failed = [record for record in result["agent_trace"] if record.agent == AgentName.DATA_ANALYSIS]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].status, "failed")
        self.assertEqual(failed[0].retry_count, 2)
        self.assertNotIn("do-not-log", failed[0].model_dump_json())
        self.assertEqual(result["final_report"], "safe finish")


if __name__ == "__main__":
    unittest.main()
