# Golden Case Evaluation v1

## MVP scope

The current MVP gate is the three de-identified Cases already in this
directory:

1. normal account;
2. plan-level quality break;
3. high CTR with low valid-lead rate.

Cases 4–6 and 7–10 are coverage expansion. They should be added when a
missing behavior is found in a run or before a formal regression release;
they are not a prerequisite for the first offline Runner loop.

The Runner does not inject `human_analysis` into the Graph. It injects only
`input_snapshot`, so the reference answer cannot leak into the evaluated
Agent prompt. Business dimensions that need semantic judgement remain
`unknown` unless a human or an explicitly supplied evaluator marks them.

## Technical metrics

`GoldenCaseTechnicalMetrics` records one value per Case:

- `run_success`: the Graph returned a State without an uncaught exception and
  produced a final report;
- `schema_pass`: result, trace, evidence, and open-question slots passed their
  Pydantic contracts;
- `tool_error_count`: Tool trace items with an error or non-success status;
- `agent_calls`: the sum of professional Agent call counters;
- `max_agent_calls`: the largest per-Agent call counter in this run;
- `latency_ms`: wall-clock duration of the local Graph invocation;
- `loop_cutoff_count`: trace/error entries indicating a call limit, loop, or
  cutoff.

The batch report derives `average_agent_calls`, `maximum_agent_calls`, and
`average_latency_ms`. It does not turn them into a quality score.

## Boundary metrics

The Runner checks the output fields recorded in `agent_trace` and the
serialized Agent outputs:

- Data Analysis must not emit diagnosis/content/strategy result fields or
  execution actions;
- Ads Diagnosis must not emit Content Review or Strategy result fields or
  execution actions;
- Content Review must not emit Strategy result fields or execution actions;
- Strategy may recommend a review direction but cannot emit backend mutation
  fields;
- Supervisor may route and protect the Graph but must not emit specialist
  business results or execution actions.

Every violation is retained as a concrete string in
`boundary_violations`. The target is zero; there is no partial boundary
credit.

## Business evaluation dimensions

The report keeps these dimensions independent:

- key observations match the human reference;
- diagnosis direction is acceptable;
- Content Review was necessary or appropriately skipped;
- core evidence was used;
- strategy stays within the acceptable action range;
- the run does not overstate insufficient evidence.

The default Runner records these as `unknown`, because route matching and
schema validation cannot prove semantic correctness. A later reviewer can
provide a `business_evaluator(case, state)` callback that returns individual
`pass`/`fail`/`unknown` marks. No aggregate score is required.

## Programmatic usage

```python
from services.golden_case_runner import GoldenCaseRunner

runner = GoldenCaseRunner(
    graph_factory=my_fixture_or_runtime_graph_factory,
    output_dir="outputs/golden_case_runs",
)
batch = runner.run_directory("docs/golden_cases")
```

The default graph factory is a no-network orchestration smoke path. For a
meaningful Agent evaluation, inject a factory that supplies the runtime
nodes/tools or deterministic offline fixture nodes.
