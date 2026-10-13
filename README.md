# Juliang Benditui Agent MVP

This repository contains two related deliverables: an installable local advertising analysis skill and the Local Growth Agent application.

## Repository layout

- `skill/douyin-local-push-analysis/` contains the reusable skill workflow and references.
- `agent/agents/` contains the specialist agents and supervisor.
- `agent/services/` contains shared tools, routing, context, reporting, and runtime services.
- `agent/src/` contains data import, browser integration, state, execution controls, and storage code.
- `agent/principles/` and `agent/prompts/` contain the agent guidance loaded at runtime.
- `agent/docs/` contains the current product, data, tool, and operating contracts. Start with [Local Push Agent V1](agent/docs/LOCAL_PUSH_AGENT_V1.md) and [V3 development design](agent/docs/Local_Growth_Agent_V3_重构开发方案.md).
- `agent/tests/` contains unit, contract, and synthetic golden-case tests.

## Data and credentials

Keep real ad exports, browser profiles, API keys, databases, generated reports, and local run artifacts out of Git. Use the example configuration files as templates and keep real values in local ignored files.