# Golden Case Format v1

## MVP case count

Three de-identified Cases are enough for the first MVP gate: one normal
account, one plan-level quality break, and one high-CTR/low-valid-lead case.
Cases 4–10 are coverage expansion, not a reason to block the first Runner.
Add them when evaluation exposes a missing behavior or before formal
regression release.

Golden Case 是离线评估用的、经过脱敏的输入快照和行为验收契约。它用于判断 Multi-Agent 是否发现关键问题、使用了合适的证据、选择了可接受的路线，并遵守“不确定时不强行下结论”和“不直接执行投放”的边界。

Golden Case 不是生产任务，也不是要求 Agent 逐字复制人工答案的 prompt。评估重点是关键观察、诊断方向、是否需要 Content Review、策略边界、证据缺口和允许的路线。

## 目录约定

```text
docs/golden_cases/
├── README.md
├── case_template.yaml
└── case-*.yaml
```

`case_template.yaml` 只作为模板，不计入实际案例数量。后续案例使用 `case-*.yaml`，`case_id` 必须稳定且唯一。

## 顶层结构

```yaml
schema_version: golden_case.v1
case_id: case-001
date: 2026-09-18
description: ...

input_snapshot:
  account: ...
  plans: ...
  materials: ...
  lead_quality: ...
  content_assets: ...

human_analysis:
  important_observations: ...
  likely_diagnosis: ...
  content_review_needed: false
  strategy_direction: ...
  cannot_conclude: ...

expected_behavior:
  should_call: ...
  should_not_call: ...
  acceptable_routes: ...
  forbidden_outputs: ...
```

该结构由 `src/schemas/golden_case.py` 中的 Pydantic 模型校验，顶层和评估契约不允许额外字段；快照内部的账户、计划、素材和线索明细允许保留业务字段，以便承载不同报表粒度。

## 字段语义

### `input_snapshot`

- `account`：账户目标、报告日期、观察窗口、基础配置和账户级指标。
- `plans`：计划级指标和状态，可区分稳定计划、测试计划、搜索承接计划。
- `materials`：素材级指标、素材类型、版本、审核状态和关联计划。
- `lead_quality`：有效线索、触达、预约、成交、跟进时效或回传状态。缺失时使用 `null`，不要用 `0` 代替未知。
- `content_assets`：标题、口播稿、字幕/转写、Hook、卖点、CTA、内容版本和可用性。没有素材内容时使用 `null` 或空列表，并在人工分析中写明限制。

快照应尽量保持同一对象、同一时间窗口和同一粒度。需要表达单日异常时，也要保留可比的 3 天窗口或明确标记样本不足。

### `human_analysis`

这是脱离 Agent 运行后的人工参考判断，不直接注入被测 Agent。它不要求和 Agent 逐句相同，而是提供评估关键点：

- `important_observations`：必须看到的事实，例如 CTR/CVR/有效率关系、素材疲劳或跟进缺口。
- `likely_diagnosis`：有证据支持的可能原因，保留“likely/hypothesis”层级，不写未经证明的因果。
- `content_review_needed`：是否存在明确素材对象和内容问题，需要进入 Content Review。
- `strategy_direction`：策略层允许提出的方向，例如保持观察、补证据、隔离测试或小步验证，不写已执行动作。
- `cannot_conclude`：证据不足、冲突或内容缺失时必须保留的不可判断事项。

### `expected_behavior`

- `should_call`：允许或期望调用的 Agent/Tool 标识，例如 `data_analysis`、`ads_diagnosis`、`content_review`、`strategy`、`get_plan_metrics`。
- `should_not_call`：当前 Case 不应调用的 Agent/Tool。没有足够理由时，不要求固定调用所有 Tool。
- `acceptable_routes`：允许的高层路线字符串，例如 `supervisor -> data_analysis -> ads_diagnosis -> finish`。允许多条路线时列出多条，不要求单一路线。
- `forbidden_outputs`：禁止出现的执行字段、越界结论或虚构内容，例如 `budget_change`、`pause_plan`、`guaranteed_roi`、把相关性写成确定因果。

验收应优先检查行为类别和边界，而不是 exact-match 文本。案例可以接受多个合理诊断方向，但不能接受无证据扩量、停投、虚构素材、伪造历史或把建议写成已执行。

## 编写规则

1. 使用脱敏账户、计划、素材和线索 ID，不写手机号、微信号、姓名、API Key、Cookie 或其他个人敏感信息。
2. 保留真实的缺失值、冲突证据和观察窗口，不为了让 Case“完整”而补造指标。
3. 优先覆盖 3 天窗口、Click/Convert/Close 漏斗、通投/搜索、新旧计划、素材类型、有效线索质量和跟进时效。
4. `content_review_needed: true` 时，必须在 `materials` 或 `content_assets` 中提供具体 `material_id` 和原因；内容缺失时，人工答案应允许“不确定”。
5. `strategy_direction` 只写建议方向和复核条件，不写真实后台执行参数；`forbidden_outputs` 应显式列出本 Case 的边界。
6. 不把低 CPL、单日波动、平台提示或单个 Agent 的猜测直接写成最终业务结论。
7. 若人工判断也无法确定，应在 `cannot_conclude` 中明确缺口和需要的证据。

## 校验方式

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
py -3 -c "import yaml; from pathlib import Path; from schemas.golden_case import GoldenCase; GoldenCase.model_validate(yaml.safe_load(Path('docs/golden_cases/case_template.yaml').read_text(encoding='utf-8'))); print('valid')"
```

Step 17.1 只建立格式和校验。案例录入、批量运行、路线对照和评分分别在后续 Golden Case Steps 与 Evaluation Steps 中完成。
