# Local Growth Agent

This directory is for the Agent product implementation and planning materials.

## Current Contents

- `docs/`: PRD, MVP development plan, handoff, report analysis, and next-step execution plan.
- `rules/`: placeholder for anomaly rules, health scoring rules, and strategy rules.
- `prompts/`: placeholder for report and strategy prompts.
- `src/`: placeholder for data import, metric calculation, anomaly detection, and report generation code.

## MVP Direction

Start with a local decision-support workflow:

```text
Excel/CSV input -> data quality checks -> metrics -> anomalies -> strategy actions -> Markdown report
```

Do not implement automatic budget changes, campaign pauses, or platform operations in the MVP.
