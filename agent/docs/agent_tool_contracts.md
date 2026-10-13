# Agent Tool Data Contracts

版本：Phase 5 / Step 5.1  
状态：数据来源盘点；本文件不代表 Tool wrapper 已实现

## 1. 原则

- Agent 后续通过窄接口获取结构化结果，不直接拿整个 DataFrame 自行计算。
- Tool 优先复用现有确定性数据函数，不复制指标计算逻辑。
- 本轮只盘点来源、输入、输出和复用点，不实现七个 Tool。
- “已有函数”表示现有项目中存在可复用的确定性函数，不表示已经存在同名 Tool API。
- 缺少可靠数据源的 Tool 必须返回缺口，不能用猜测或其他数据冒充。

## 2. Tool 总览

| Tool | 数据来自哪里 | 输入 | 输出 | 是否已有函数 |
|---|---|---|---|---|
| `get_account_metrics` | 浏览器下载的单元/计划报表，经 `replay_core.load_platform_reports()` 标准化；可选石墨反馈 | `report_date`、`time_window`；运行上下文中的报表和反馈来源 | 账户消耗、曝光、点击、CTR、留资、CPL、有效留资、有效留资成本、对接量、对接成本、窗口和数据完整性 | 部分已有：`aggregate_account()`、`mtd_summary()`、`metric_row()`；尚无 Tool wrapper |
| `get_plan_metrics` | 标准化后的单元/计划日明细 `unit_id`；可选同 ID 反馈和历史 | `plan_id` / `unit_id`、`time_window` | 计划日明细、窗口汇总、阶段/状态观察、可用时的质量字段、数据完整性 | 部分已有：`aggregate_metrics()`、`summarize_entities()`、`diagnose_units()`、`build_plan_timeline()` |
| `get_material_metrics` | 标准化后的素材日明细 `material_id`；可选同 ID 反馈和历史 | `material_id`、`time_window` | 素材消耗、曝光、点击、CTR、留资、CPL、质量字段（有归因时）、窗口和数据完整性 | 部分已有：`aggregate_metrics()`、`summarize_entities()`；尚无 Tool wrapper |
| `get_lead_quality` | 可选石墨/人工反馈导出，经 `replay_core.load_feedback()` 解析 `is_valid` / `is_contacted` | 对象、`time_window`、反馈来源 | 有效留资、有效留资率/成本、对接量、对接成本、反馈缺失状态、归因范围说明 | 部分已有：`load_feedback()`、`aggregate_account()`、`aggregate_metrics()`、`mtd_summary()`；不可用时必须保留缺失 |
| `get_history_context` | 本地 SQLite 成功运行记录、账户/计划/素材历史和基线 | 账户/对象、`time_window`、`before_date` | 历史序列、3/7/30 日和前一 7 日基线、趋势证据、历史缺口 | 已有核心函数：`StateStore.load_history()`、`build_baselines()`、`enrich_with_history()` |
| `get_content_asset` | 当前源码未发现可靠的素材正文、口播稿、视频转写或内容资产存储入口；素材报表目前只有 ID、名称和投放指标 | `material_id` | 目标应为标题、口播稿/转写、Hook、卖点、CTA、内容标签及来源；当前只能明确返回“内容资产不可用” | 未发现可复用函数；不得用素材名称冒充正文 |
| `get_sales_feedback` | 当前没有 CRM/成交数据 Tool；`load_feedback()` 是线索有效/对接反馈，`StateStore.list_feedback()` 是人工执行反馈，不等同销售成交反馈 | 对象、`time_window`、反馈来源 | 目标应为销售承接、成交或跟进结果及来源；当前应返回“销售数据源未接入”及可用的非销售反馈范围 | 部分数据可读但没有同义函数；不能把执行反馈包装成销售反馈 |

## 3. 已有函数与数据边界

### 3.1 平台报表与指标

`agent/src/replay_core.py` 当前负责：

- `load_platform_reports()`：读取单元和素材导出，标准化为 `unit_id` / `material_id` 及基础平台指标。
- `aggregate_account()`：按日期汇总账户级平台指标，并在有反馈时合并账户级质量字段。
- `aggregate_metrics()`：按指定实体键汇总计划或素材的日指标。
- `summarize_entities()`：生成计划/素材期间摘要。
- `mtd_summary()`：计算本月累计和数据状态。
- `metric_row()`：统一计算 CTR、CPC、CPM、留资转化率、CPL、有效 CPL 和对接成本等派生指标。

这些函数接收 DataFrame 或反馈 DataFrame，尚不是面向 Agent 的 JSON Tool；Step 5.2 以后应在 wrapper 中适配输入和序列化，不复制其计算公式。

### 3.2 历史

- `StateStore.load_history()` 只读取当前账户、当前日期之前、成功且对账通过的历史记录。
- `build_baselines()` 提供窗口基线；缺失日期不补零。
- `enrich_with_history()` 将历史账户、计划和素材数据合并到当前报告并生成异常证据。

### 3.3 反馈与质量

`load_feedback()` 支持从 CSV/XLSX 读取线索日期、可选线索/计划/素材 ID、是否有效和是否对接。当前石墨反馈的稳定粒度可能是账户日级，不能把账户质量强行下分到没有可验证归因的计划或素材。

`StateStore.list_feedback()` 读取的是运营人员对系统建议的执行反馈，包括执行状态、实际动作和观察结果；它不是销售成交数据源。

### 3.4 内容资产与销售反馈缺口

当前源码和数据契约中没有已验收的素材正文/口播稿/视频转写读取入口，也没有 CRM 或成交结果读取入口。因此：

- `get_content_asset` 在实现前需要项目负责人指定内容资产来源和字段口径。
- `get_sales_feedback` 在实现前需要项目负责人指定 CRM、销售表或其他真实来源。
- 在来源确认前，相关 Agent 必须输出数据不足或无法归因，不能用名称、点击或执行反馈替代内容/销售事实。

## 4. 后续实现约束

1. 每个 Tool 只返回自身职责范围内的结构化数据、数据状态和证据来源。
2. Tool 不输出“账户很好”“应该加预算”等业务结论；结论由 Agent 结合 Principles 形成。
3. Tool 不执行预算、出价、暂停、开启、素材发布或删除。
4. Tool 结果必须可 JSON 序列化，并保留缺失数据说明。
5. 实现前若 `get_content_asset` 或 `get_sales_feedback` 的来源仍为空，应暂停该 Tool 实现并向项目负责人询问，不得自行选源。

