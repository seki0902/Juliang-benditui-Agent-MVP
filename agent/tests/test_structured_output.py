import unittest

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from schemas.data_analysis import DataAnalysisResult
from services.structured_output import StructuredOutputError, invoke_structured


class _FakeStructuredRunnable:
    def __init__(self, model, responses):
        self.model = model
        self.responses = responses

    def invoke(self, payload):
        self.model.payloads.append(payload)
        index = min(self.model.invocation_count, len(self.responses) - 1)
        self.model.invocation_count += 1
        return self.responses[index]


class _FakeStructuredModel:
    def __init__(self, responses):
        self.responses = responses
        self.bound_schema = None
        self.include_raw = None
        self.payloads = []
        self.invocation_count = 0

    def with_structured_output(self, schema, *, include_raw=False):
        self.bound_schema = schema
        self.include_raw = include_raw
        return _FakeStructuredRunnable(self, self.responses)


class _BindingFailureModel:
    def with_structured_output(self, schema, *, include_raw=False):
        raise NotImplementedError("structured output is not supported")


class _NativeLangChainFakeModel(BaseChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        schema = tools[0]
        return RunnableLambda(
            lambda _: AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": schema.__name__,
                        "args": {"summary": "native stable"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("the smoke runnable should not call _generate")

    @property
    def _llm_type(self):
        return "native-langchain-fake"


class StructuredOutputTests(unittest.TestCase):
    def test_current_langchain_binding_returns_a_pydantic_instance(self):
        result = invoke_structured(
            _NativeLangChainFakeModel(),
            DataAnalysisResult,
            "summarise the imported report",
        )

        self.assertIsInstance(result, DataAnalysisResult)
        self.assertEqual(result.summary, "native stable")

    def test_smoke_returns_the_existing_pydantic_output_contract(self):
        model = _FakeStructuredModel([{"summary": "stable"}])
        payload = {"request": "summarise the imported report"}

        result = invoke_structured(model, DataAnalysisResult, payload)

        self.assertIsInstance(result, DataAnalysisResult)
        self.assertEqual(result.summary, "stable")
        self.assertIs(model.bound_schema, DataAnalysisResult)
        self.assertFalse(model.include_raw)
        self.assertEqual(model.payloads, [payload])

    def test_schema_failure_is_explicit_without_free_text_parsing(self):
        model = _FakeStructuredModel(
            [{"summary": "unsafe", "pause_plan": True}]
        )

        with self.assertRaises(StructuredOutputError) as context:
            invoke_structured(model, DataAnalysisResult, {"request": "check"})

        self.assertEqual(context.exception.attempts, 1)
        self.assertIsInstance(context.exception.cause, ValidationError)
        self.assertIn("DataAnalysisResult", str(context.exception))

    def test_schema_failure_retries_with_the_same_payload_and_schema(self):
        model = _FakeStructuredModel(
            [
                {"summary": "invalid", "pause_plan": True},
                {"summary": "valid after retry"},
            ]
        )
        payload = {"request": "check", "facts": ["same input"]}

        result = invoke_structured(
            model,
            DataAnalysisResult,
            payload,
            max_retries=1,
        )

        self.assertEqual(result.summary, "valid after retry")
        self.assertEqual(model.invocation_count, 2)
        self.assertIs(model.payloads[0], payload)
        self.assertIs(model.payloads[1], payload)
        self.assertIs(model.bound_schema, DataAnalysisResult)

    def test_retry_exhaustion_is_bounded_and_reports_attempt_count(self):
        model = _FakeStructuredModel(
            [{"summary": "invalid", "pause_plan": True}]
        )

        with self.assertRaises(StructuredOutputError) as context:
            invoke_structured(
                model,
                DataAnalysisResult,
                {"request": "check"},
                max_retries=2,
            )

        self.assertEqual(context.exception.attempts, 3)
        self.assertEqual(model.invocation_count, 3)

    def test_binding_failure_is_explicit(self):
        with self.assertRaises(StructuredOutputError) as context:
            invoke_structured(
                _BindingFailureModel(),
                DataAnalysisResult,
                {"request": "check"},
            )

        self.assertEqual(context.exception.attempts, 0)
        self.assertIsInstance(context.exception.cause, NotImplementedError)

    def test_invalid_schema_and_retry_values_fail_fast(self):
        model = _FakeStructuredModel([{"summary": "ok"}])

        with self.assertRaises(TypeError):
            invoke_structured(model, dict, {"request": "check"})
        with self.assertRaises(TypeError):
            invoke_structured(model, DataAnalysisResult, {}, max_retries=True)
        with self.assertRaises(ValueError):
            invoke_structured(model, DataAnalysisResult, {}, max_retries=-1)


if __name__ == "__main__":
    unittest.main()
