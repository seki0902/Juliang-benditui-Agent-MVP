# Multi-Agent State Contract

版本：Phase 3 / Step 3.3  
状态：技术骨架，未接入真实 Agent Node

## 1. 目的与边界

`MultiAgentState` 保存本轮分析已经知道的事实、结构化 Agent 结果、未解决问题、证据和可审计 trace。它不是 Prompt 容器，也不是广告执行状态容器。

本 Contract 遵循以下边界：

- Agent 结果使用 `agent/src/schemas/` 中的 Pydantic Schema。
- Agent 结果由对应 Agent 写入，后续 Agent 和 Supervisor 读取。
- `open_questions`、`evidence`、`route_trace`、`tool_trace`、`agent_trace`、`errors` 只能按追加语义更新。
- 结果和单值上下文字段按覆盖语义更新。
- 本 State 不保存预算、出价、暂停、放量或后台执行状态。
- `total=False` 表示运行早期字段可以尚未出现；调用方应区分“未出现”和“明确为空”。

## 2. 字段契约

| 字段 | 类型 | 谁写 | 谁读 | 何时更新 | 是否可为空 / 未出现 | 说明 |
|---|---|---|---|---|---|---|
| `run_id` | `str` | 运行入口 | 所有 Agent、日志、报告 | 运行开始时写入 | 开始前可未出现；正式运行不应为空 | 本轮运行唯一标识 |
| `report_date` | `str` | 运行入口 / 数据入口 | 所有 Agent、报告 | 输入日期确定后写入 | 输入校验前可未出现 | 报表统计日期 |
| `task` | `str` | 调用方 / Supervisor | 所有 Agent | 初始化或任务重定义时覆盖 | 初始任务不可为空 | 当前协作目标 |
| `account_context` | `dict[str, Any]` | 运行入口 / 数据入口 | Supervisor、所有专业 Agent | 上下文准备或刷新时覆盖 | 可为空字典；不得放秘密 | 账户和运行上下文 |
| `data_analysis_result` | `DataAnalysisResult \| None` | Data Analysis Agent | Supervisor、Ads Diagnosis、Strategy | Agent 完成后覆盖 | 初始可未出现或为 `None` | 数据观察、异常和诊断目标 |
| `ads_diagnosis_result` | `AdsDiagnosisResult \| None` | Ads Diagnosis Agent | Supervisor、Content Review、Strategy | Agent 完成后覆盖 | 初始可未出现或为 `None` | 投放层原因和证据需求 |
| `content_review_result` | `ContentReviewResult \| None` | Content Review Agent | Supervisor、Strategy | Agent 完成后覆盖 | 初始可未出现或为 `None` | 内容发现和表现解释 |
| `strategy_result` | `StrategyResult \| None` | Strategy Agent | Supervisor、报告、人工复核 | Agent 完成后覆盖 | 初始可未出现或为 `None` | 综合判断和建议 |
| `supervisor_decision` | `SupervisorDecision \| None` | Supervisor | Graph 路由器、审计日志 | 每次决策后覆盖 | 初始可未出现或为 `None` | 下一节点和结束判断 |
| `open_questions` | `list[OpenQuestion]` | 任一 Agent、Supervisor | Supervisor、后续 Agent、报告 | 发现问题时追加 | 初始应为空列表 | 未解决问题，不覆盖删除历史 |
| `evidence` | `list[EvidenceItem]` | 数据工具、各 Agent | 所有 Agent、Supervisor、报告 | 获得证据时追加 | 初始应为空列表 | 可追踪的数据或内容证据 |
| `agent_call_counts` | `dict[str, int]` | Orchestrator / 调用控制器 | Supervisor、限额控制、审计 | 每次调用后覆盖 | 初始可为空字典 | 按 Agent 统计调用次数 |
| `total_agent_calls` | `int` | Orchestrator / 调用控制器 | Supervisor、限额控制、审计 | 每次调用后覆盖 | 初始为 `0` 或未出现 | 本轮总调用次数 |
| `route_trace` | `list[RouteTraceRecord]` | Supervisor / Graph 路由器 | Supervisor、审计、调试报告 | 每次有效决策后追加 | 初始应为空列表 | Supervisor 到下一目标的路由历史，包含 step、from、to、reason、task 和 timestamp |
| `tool_trace` | `list[ToolCallRecord]` | Tool wrapper / 调用器 | Agent、Supervisor、审计 | 工具完成或失败后追加 | 初始应为空列表 | 工具调用审计记录；包含 Agent、Tool、call_id、输入摘要、状态、耗时和返回摘要 |
| `agent_trace` | `list[AgentTraceRecord]` | Graph 编排边界 | Supervisor、审计、调试报告 | 每次 Supervisor / 专业 Agent 节点调用后追加 | 初始应为空列表 | Agent 调用审计记录；包含 run_id、输入 State 摘要、输出 Schema、状态、耗时、失败/retry 和可用的 token usage |
| `errors` | `list[str]` | 入口、Agent wrapper、Tool wrapper | Supervisor、报告、审计 | 捕获错误时追加 | 初始应为空列表 | 不得写入 API Key |
| `final_report` | `str \| None` | 报告渲染器 / Orchestrator | 推送器、人工复核 | 所需 Agent 完成后覆盖 | 生成前可未出现或为 `None` | 面向人的日报；基础渲染只格式化已有结构化结果，不新增业务判断 |
| `push_result` | `dict[str, Any] \| None` | 通知适配器 | Orchestrator、审计 | 推送尝试完成后覆盖 | 未推送前可未出现或为 `None` | 推送结果，不是广告执行结果 |

## 3. 更新语义

统一入口为 `agent/src/state.py:apply_state_update()`。

### 3.1 覆盖字段

```text
run_id
report_date
task
account_context
data_analysis_result
ads_diagnosis_result
content_review_result
strategy_result
supervisor_decision
agent_call_counts
total_agent_calls
final_report
push_result
```

覆盖只影响对应字段，不清空其他字段的历史 trace、证据或开放问题。

### 3.2 追加字段

```text
open_questions
evidence
route_trace
tool_trace
agent_trace
errors
```

`route_trace` 使用 `RouteTraceRecord`。它将 `from_agent="supervisor"` 序列化为
`from: "supervisor"`，将目标序列化为 `to`；`RouteRecord` 继续保留给 Agent
之间的 handoff，不把 Supervisor 加入业务 Agent 枚举。

`tool_trace` 使用 `ToolCallRecord`。运行时 Tool 可以继续把完整返回值放入
`ToolMessage` 供当前 Agent 使用，但写入 State / Checkpointer 的记录必须满足：

1. `agent`、`tool_name`、`call_id`、`status` 和 `duration_ms` 可定位一次调用。
2. `arguments` 只保存有界、递归脱敏后的输入摘要。
3. `result_summary`（兼容字段 `result`）只保存状态、来源、窗口、警告和数据结构摘要，不能复制整张报表或素材正文。
4. API Key、Token、Cookie、Authorization、密码、Secret、浏览器登录态等敏感字段统一写为 `[REDACTED]`；敏感字符串和超长列表也必须截断。
5. Tool 失败仍需追加一条 `status="error"` 记录，错误文本同样经过脱敏；业务 Agent 可以据此决定是否保留 `errors` 或 `open_questions`。

`agent_trace` 使用 `AgentTraceRecord`，由 Graph 编排边界生成，不接受 Agent
节点自行伪造。每条记录只保存 `summarize_agent_state()` 和输出 Schema 摘要；
输入 State 的指标值、素材正文和完整 Prompt 不会复制到 Trace。`retry_count`
只在运行时能够确认重试次数时填写；`token_usage` 仅在模型 SDK 暴露可用计数时
写入，否则保持 `None`。记录携带当前 `run_id`，可通过同一 run 的 Checkpointer
恢复完整 Trace，并可直接序列化为本地 JSON / JSONL。

追加规则：

1. 更新值必须是列表；非列表输入直接报错。
2. 保留已有元素顺序。
3. 完全相同的对象按稳定 JSON 表示去重，避免重试导致相同 trace 重复。
4. 不同内容的记录即使来自同一个 Agent，也必须保留。
5. 追加更新不会覆盖专业 Agent 的结果字段。

### 3.3 日报 Renderer 约束

`report_renderer` 是确定性的 Graph 节点，读取四个专业 Agent Result、`open_questions`、`route_trace` 和必要的运行元数据，写入 `final_report`。基础日报不调用 LLM，也不根据指标重新推断原因、阈值或执行动作。

当某个 Agent Result 尚未写入时，Renderer 必须保留显式状态：数据、投放诊断和策略分别标记为“未调用/暂无结果”；内容审核标记为“不适用 / 本次未调用”。缺失结果不能阻断其他章节和日报生成。`【分析路径】` 只展示已有 `route_trace`，不得由 Renderer 伪造新的路由记录。

可选的 `ReportPolisher` 只能接收已经生成的 `final_report` 文本，不接收原始 State、账户凭证或完整 Tool 返回值。它锁定【今日重点】、【策略建议】、【仍需观察】、【证据不足项】和【分析路径】，并要求其他章节保留原有事实行；模型失败、结构化输出失败或保护校验失败时，必须原样回退基础日报。润色层不新增 State 字段，也不修改任何 Agent Result。

## 4. Agent 读写摘要

```text
Data Analysis Agent
  写：data_analysis_result、evidence、open_questions、tool_trace、errors
  读：task、account_context、历史 evidence、历史 open_questions

Ads Diagnosis Agent
  写：ads_diagnosis_result、evidence、open_questions、tool_trace、errors
  读：data_analysis_result、account_context、历史 evidence

Content Review Agent
  写：content_review_result、evidence、open_questions、tool_trace、errors
  读：ads_diagnosis_result、data_analysis_result、素材内容证据

Strategy Agent
  写：strategy_result、evidence、open_questions、tool_trace、errors
  读：data_analysis_result、ads_diagnosis_result、content_review_result、account_context

Supervisor
  写：supervisor_decision、route_trace、agent_trace、open_questions、errors、调用计数
  读：完整 State、所有 Agent 结果、调用计数、route_trace、tool_trace、agent_trace、errors

Report Renderer
  写：final_report
  读：四个 Agent Result、open_questions、route_trace、run_id、report_date
```

以上是技术读写边界，不代表当前项目已经实现这些 Agent Node。

## 5. 不属于 State 的内容

以下内容不得通过新增普通字段写入 `MultiAgentState`：

```text
真实预算调整值
真实出价调整值
暂停 / 开启计划状态
扩量比例
后台执行参数
API Key、Access Token、浏览器登录态
```

如果后续需要执行层结果，必须先经过单独的执行 Contract、人工确认和安全校验，不能把执行状态混入本 State。
