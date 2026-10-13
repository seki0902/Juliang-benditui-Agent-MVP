# Historical E2E Dry Run

`services/historical_e2e_runner.py` connects the existing local replay path to the technical Multi-Agent Graph for a read-only historical run.

The flow is:

```text
local unit/material XLSX
  -> run_import / replay_core
  -> read-only account/plan/material metrics Tools
  -> technical Graph route
  -> graph output + historical E2E report
```

No platform API, budget change, bid change, pause operation, message, or delivery call is made. The `push` node is replaced with an explicit disabled result:

```json
{"status": "disabled", "reason": "historical_e2e_dry_run"}
```

## Run

From the `agent` directory, with the supplied account folders under `inputs/`:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
@'
from pathlib import Path
from tempfile import TemporaryDirectory
from services.historical_e2e_runner import HistoricalE2ERunner

input_root = next(path for path in Path('inputs').iterdir() if path.is_dir())
with TemporaryDirectory() as output_dir:
    report = HistoricalE2ERunner(input_root, output_dir).run()
    print(report['status'], report['account_count'], report['dynamic_route_count'])
'@ | py -3 -
```

The runner writes one replay folder and one `graph_output.json` per account, plus:

- `historical_e2e_report.json`
- `historical_e2e_report.md`

The default adapter uses the real exported XLSX files and real read-only metrics Tool implementations, but deterministic Graph nodes. It validates data handoff, route changes, Tool traces, schema-compatible Agent outputs, report generation, and the no-push boundary. It is not a substitute for a model-backed semantic evaluation or a production backend run.

When a replay anomaly points to an object outside the current 3-day window, the diagnosis step keeps that anomaly as evidence and queries a currently observed recent plan/material instead. This prevents a stale historical ID from turning the Tool check into a misleading `no_data` result.

## Current supplied data result

The three supplied account folders produce two technical routes:

- reconciled accounts: `supervisor -> data_analysis -> strategy -> finish`
- unreconciled account: `supervisor -> data_analysis -> ads_diagnosis -> strategy -> finish`

The unreconciled account is intentionally reported as `partial` at batch level because its unit/material totals do not reconcile. Its Graph status is still `success`, and its risk-control recommendation remains a report-only observation.

Quality and follow-up fields such as valid leads and contacted leads are absent from these exports. The report preserves those fields as missing; it does not replace them with zero or infer close-stage ROI.
