"""Schema-first invocation helpers for Agent model outputs.

Agent nodes should bind a Pydantic output contract through the model adapter's
``with_structured_output`` method.  This module adds a small, provider-neutral
boundary around that capability so callers always receive a validated
Pydantic instance and schema failures are explicit and bounded.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """Raised when a model cannot produce a valid instance of the requested schema."""

    def __init__(
        self,
        schema: type[BaseModel],
        *,
        attempts: int,
        cause: Exception,
    ) -> None:
        self.schema = schema
        self.attempts = attempts
        self.cause = cause
        super().__init__(
            f"structured output for {schema.__name__} failed after "
            f"{attempts} attempt(s): {cause}"
        )


def _require_schema(schema: type[SchemaT]) -> type[SchemaT]:
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise TypeError("schema must be a Pydantic BaseModel class")
    return schema


def _require_max_retries(max_retries: int) -> int:
    if isinstance(max_retries, bool) or not isinstance(max_retries, int):
        raise TypeError("max_retries must be a non-negative integer")
    if max_retries < 0:
        raise ValueError("max_retries must be a non-negative integer")
    return max_retries


def _record_attempt_metadata(model: Any, *, attempts: int, retries: int) -> None:
    """Expose bounded invocation metadata to the outer Graph when possible."""

    for name, value in (
        ("_agent_last_structured_attempts", attempts),
        ("_agent_last_structured_retries", retries),
    ):
        try:
            setattr(model, name, value)
        except Exception:
            # Provider clients may be immutable or use slots.  Trace fields
            # are optional, so inability to attach metadata is not a model
            # invocation failure.
            continue


def invoke_structured(
    model: Any,
    schema: type[SchemaT],
    payload: Any,
    *,
    max_retries: int = 0,
) -> SchemaT:
    """Invoke ``model`` with a Pydantic schema and return a validated result.

    The model adapter performs the provider-specific structured-output binding;
    this boundary deliberately does not parse free-form text or alter the
    business payload between attempts.  A retry repeats the exact same bound
    runnable and input, and is limited to ``max_retries`` additional attempts.

    ``include_raw=False`` is intentional: parsing/schema failures must surface
    to this boundary instead of being silently returned alongside raw text.
    """

    schema_class = _require_schema(schema)
    retry_count = _require_max_retries(max_retries)

    try:
        structured_model = model.with_structured_output(
            schema_class,
            include_raw=False,
        )
    except Exception as exc:
        _record_attempt_metadata(model, attempts=0, retries=0)
        raise StructuredOutputError(schema_class, attempts=0, cause=exc) from exc

    last_error: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            raw_result = structured_model.invoke(payload)
            result = schema_class.model_validate(raw_result)
            _record_attempt_metadata(
                model,
                attempts=attempt + 1,
                retries=attempt,
            )
            return result
        except Exception as exc:
            last_error = exc
            if attempt == retry_count:
                break

    # The loop always records an exception before reaching this point.
    assert last_error is not None
    _record_attempt_metadata(
        model,
        attempts=retry_count + 1,
        retries=retry_count,
    )
    raise StructuredOutputError(
        schema_class,
        attempts=retry_count + 1,
        cause=last_error,
    ) from last_error


__all__ = ["StructuredOutputError", "invoke_structured"]
