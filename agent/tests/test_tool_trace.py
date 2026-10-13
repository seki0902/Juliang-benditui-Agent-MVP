import json
import unittest

from schemas.common import AgentName, ToolCallRecord
from services.tool_trace import (
    REDACTED_VALUE,
    json_trace_payload,
    make_tool_call_record,
    sanitize_trace_arguments,
    summarize_tool_result,
)


class ToolTraceTests(unittest.TestCase):
    def test_record_contains_agent_call_identity_timing_and_summary(self):
        record = make_tool_call_record(
            agent=AgentName.DATA_ANALYSIS,
            tool_name="get_account_metrics",
            call_id="call-42",
            arguments={"report_date": "2026-09-17", "time_window": "day"},
            status="ok",
            result={
                "status": "ok",
                "source": "fixture.account",
                "time_window": "day",
                "data": {"spend": 42, "rows": [{"plan_id": "plan-1"}]},
                "warnings": [],
            },
            duration_ms=2.5,
        )

        self.assertIsInstance(record, ToolCallRecord)
        self.assertEqual(record.agent, AgentName.DATA_ANALYSIS)
        self.assertEqual(record.tool_name, "get_account_metrics")
        self.assertEqual(record.call_id, "call-42")
        self.assertEqual(record.duration_ms, 2.5)
        self.assertTrue(record.timestamp)
        self.assertEqual(record.result_summary["status"], "ok")
        self.assertEqual(record.result_summary["data"]["type"], "object")
        self.assertNotIn("rows", record.result_summary["data"])
        self.assertEqual(record.result, record.result_summary)

        payload = json.loads(json_trace_payload(record))
        self.assertEqual(payload["agent"], "data_analysis")
        self.assertEqual(payload["call_id"], "call-42")

    def test_arguments_and_result_summary_redact_credentials_and_bound_payloads(self):
        secret = "super-secret-token-value"
        arguments = sanitize_trace_arguments(
            {
                "api_key": secret,
                "headers": {"Authorization": f"Bearer {secret}"},
                "nested": {"refresh_token": secret, "report_date": "2026-09-17"},
                "long_text": "x" * 10_000,
            }
        )
        self.assertEqual(arguments["api_key"], REDACTED_VALUE)
        self.assertEqual(arguments["headers"]["Authorization"], REDACTED_VALUE)
        self.assertEqual(arguments["nested"]["refresh_token"], REDACTED_VALUE)
        self.assertLess(len(arguments["long_text"]), 300)
        self.assertNotIn(secret, json.dumps(arguments, ensure_ascii=False))

        summary = summarize_tool_result(
            {
                "status": "partial",
                "source": "fixture",
                "time_window": "day",
                "data": {"api_key": secret, "rows": list(range(1000))},
                "warnings": [f"authorization={secret}"],
            }
        )
        serialised = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(secret, serialised)
        self.assertEqual(summary["data"]["type"], "object")
        self.assertEqual(summary["data"]["fields"], ["rows"])
        self.assertLess(len(serialised), 2_000)

    def test_error_text_is_safe_and_trace_keeps_failure_status(self):
        secret = "abc123"
        record = make_tool_call_record(
            agent=AgentName.CONTENT_REVIEW,
            tool_name="get_content_asset",
            call_id="call-error",
            arguments={"material_id": "material-1"},
            status="error",
            result={
                "status": "error",
                "source": None,
                "time_window": None,
                "data": {},
                "warnings": [f"api_key={secret}"],
                "error_type": "RuntimeError",
            },
            duration_ms=0,
            error=f"api_key={secret}",
        )
        serialised = record.model_dump_json()
        self.assertNotIn(secret, serialised)
        self.assertEqual(record.status, "error")
        self.assertIn(REDACTED_VALUE, serialised)


if __name__ == "__main__":
    unittest.main()
