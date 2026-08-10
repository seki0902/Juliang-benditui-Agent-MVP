# Juliang Benditui Agent MVP

This repository separates the reusable Codex skill from the Local Growth Agent product materials.

## Structure

```text
skill/
  douyin-local-push-analysis/
    SKILL.md
    agents/
    references/

agent/
  docs/
    Local_Growth_Agent_PRD_V2.0.md
    Local_Growth_Agent_MVP开发方案.md
    Local_Growth_Agent_Handoff.md
    本地推报表分析建议.md
    下一步素材测试与建计划执行方案.md
  prompts/
  rules/
  src/
```

## Skill

`skill/douyin-local-push-analysis` is the installable analysis skill. It contains the domain workflow, local-push rules, paid-ads diagnosis playbook, and output structure for Douyin Local Push / 巨量本地推 report analysis.

## Agent

`agent/` contains the Local Growth Agent product and MVP materials. It is the future implementation area for data import, validation, metric calculation, anomaly detection, report generation, and feedback loops.

## Data Policy

Raw ad reports, screenshots, exported Excel files, and local scratch files are not committed by default. Put sanitized examples under a future `agent/examples/` directory when needed.
