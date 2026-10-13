# Local Growth Agent 开发文档

> 版本：V1.0  
> 状态：开发基线  
> 更新时间：2026-09-15

## 1. 开发目标

每天 09:00 自动启动已登录的本地推浏览器，下载昨日完整投放报表和石墨人工反馈文件，生成并推送运营日报。日报同时包含昨日数据、本月累计数据、异常诊断和可执行建议。

第一版只生成建议，不自动修改预算、出价、计划状态或素材。

当前取数方式不是平台 API，而是使用已登录的本地推浏览器页面自动下载报表。脚本只进行页面读取、日期选择和文件下载；登录失效、验证码或短信验证时停止并等待人工处理。

## 2. 核心边界

```text
LangGraph 不是历史数据库
LLM 不是异常检测器
石墨不是业务数据库
MCP 不是天然可靠的数据源
```

系统分工：

| 层 | 组件 | 职责 |
|---|---|---|
| 调度 | Windows Task Scheduler 或 n8n | 09:00 触发、重试、告警、流程编排 |
| 数据 Skill | Python | 浏览器下载、Excel/CSV 读取、标准化、对账、指标计算 |
| 业务存储 | PostgreSQL | 原始数据、历史指标、基线、建议和反馈 |
| 规则层 | Python Rule Engine | 样本门槛、异常检测、动作边界 |
| 决策层 | LangGraph + LLM | 根因比较、证据解释、建议生成 |
| 协作层 | 石墨 | 日报展示、人工填写有效和对接状态 |
| 推送层 | 企业微信 | 推送摘要和日报链接 |

## 3. 产品范围

系统每日输出以下内容：

### 3.1 昨日数据

```text
日期、消耗、留资量、单个留资成本、有效留资量、有效留资成本
曝光量、点击量、点击率、点击成本、私信咨询数、留资转化率
对接数量、对接成本
```

### 3.2 本月累计

```text
本月累计留资量
本月累计留资成本
本月累计对接量
本月累计对接成本
```

统计范围为当月 1 日至昨日。例如 9 月 14 日 09:00 的日报，昨日数据是 9 月 13 日，本月累计是 9 月 1 日至 9 月 13 日。

### 3.3 诊断和建议

系统需要给出：

- 异常发生在哪个账户、计划、单元或素材；
- 异常是真异常还是样本不足；
- 候选根因是素材、人群、出价、预算、投放环境、承接还是数据问题；
- 哪个计划需要暂停、降预算、观察或放量；
- 哪个素材需要替换；
- 建议调整幅度和观察周期；
- 当前还缺少哪些证据。

### 3.4 人工执行

系统可以建议暂停/保留计划、调整预算或出价、替换素材以及检查承接和回传，但不在后台执行这些动作。运营人员手动完成后，需要反馈是否执行、实际调整值、执行时间和 1 日/3 日结果。

## 4. 业务指标口径

业务漏斗：

```text
消耗 → 平台留资 → 人工筛选有效留资 → 石墨“是否对接=是”的对接量
```

| 中文名称 | 内部字段 | 定义 | 来源 |
|---|---|---|---|
| 日期 | `data_date` | 统计日期，日报默认是昨日 | 平台报表 |
| 消耗 | `spend` | 平台实际消耗 | 平台报表 |
| 留资量 | `leads` | 平台后台当天转化数 | 浏览器下载的报表 |
| 单个留资成本 | `lead_cost` | `spend / leads` | 计算 |
| 有效留资量 | `valid_leads` | 当天新增留资中经人工筛选后的有效数 | 石墨人工表 |
| 有效留资成本 | `valid_lead_cost` | `spend / valid_leads` | 计算 |
| 曝光量 | `impressions` | 平台曝光数 | 平台报表 |
| 点击量 | `clicks` | 平台点击数 | 平台报表 |
| 点击率 | `ctr` | `clicks / impressions` | 计算 |
| 点击成本 | `cpc` | `spend / clicks` | 计算 |
| 私信咨询数 | `private_messages` | 平台私信咨询数量 | 浏览器下载的报表 |
| 留资转化率 | `lead_conversion_rate` | `leads / clicks` | 计算 |
| 对接数量 | `contacted_leads` | 石墨表中“是否对接=是”的线索数量 | 石墨人工表 |
| 对接成本 | `contact_cost` | `spend / contacted_leads` | 计算 |

分母为 0 时返回 `NULL`，不得返回 0。所有比率保存分子、分母和样本状态。

## 5. 数据处理总流程

```text
创建 run_id / 获取运行锁
        ↓
启动浏览器下载昨日平台报表
        ↓
同步石墨人工反馈
        ↓
保存原始数据
        ↓
字段标准化与数据对账
        ↓
计算昨日指标和本月累计
        ↓
读取历史基线和建议反馈
        ↓
Rule Engine 检测异常
        ↓
生成诊断证据包
        ↓
Agent 诊断和建议
        ↓
保存建议、日报和运行结果
        ↓
写入石墨并推送企业微信摘要
```

## 6. 日报和本月累计计算

```text
lead_cost = spend / leads
valid_lead_cost = spend / valid_leads
ctr = clicks / impressions
cpc = spend / clicks
lead_conversion_rate = leads / clicks
contact_cost = spend / contacted_leads
```

本月累计：

```text
mtd_start_date = 当月1日
mtd_end_date = data_date
mtd_spend = SUM(spend)
mtd_leads = SUM(leads)
mtd_lead_cost = mtd_spend / mtd_leads
mtd_contacted_leads = SUM(contacted_leads)
mtd_contact_cost = mtd_spend / mtd_contacted_leads
```

本月成本使用累计消耗除以累计数量，不能简单平均每日成本。本月累计从已落库的日明细重新聚合，避免重试造成重复累计。

## 7. 石墨人工反馈

石墨不是业务数据库，但第一版作为人工协作入口。至少需要读取：

```text
lead_id
lead_date
account_id
project_id
unit_id
material_id
是否有效
是否对接
screened_at
contacted_at（如有）
feedback_updated_at
```

第一版按线索产生日期归属：

```text
valid_leads(data_date)
  = COUNT(lead_date = data_date AND 是否有效 = 是)

contacted_leads(data_date)
  = COUNT(lead_date = data_date AND 是否对接 = 是)
```

石墨表后续补填“是否对接=是”时，允许回算历史日期和本月累计，但不能把反馈更新时间当成实际对接日期。

## 8. 数据模型

### 8.1 投放层级

```text
账户 → 项目 → 计划/单元 → 广告 → 素材
```

### 8.2 核心业务表

```text
daily_runs                  每次日报运行记录
raw_metrics                 平台和石墨原始数据
normalized_metrics          标准化指标
account_daily_metrics       账户日指标
unit_daily_metrics          计划/单元日指标
material_daily_metrics      素材日指标
plan_material_relation      计划和素材历史关联
plan_state_history          计划状态快照和状态变化
lead_feedback               石墨人工筛选和对接反馈
baseline_snapshots          历史基线
anomalies                   规则异常记录
recommendations              Agent 建议
recommendation_execution    执行和观察结果
```

### 8.3 每日数据唯一键

```text
run_key = account_id + data_date + pipeline_version

account_daily_metrics = account_id + data_date + metric_version
unit_daily_metrics = account_id + unit_id + data_date + metric_version
material_daily_metrics = account_id + material_id + data_date + metric_version
```

重复运行使用 upsert 或版本替换，不得重复累计消耗、曝光、点击、留资和对接量。

计划和素材无法直接关联时，保留未关联记录和金额，不得强行拼接。

## 9. 基线和规则

### 9.1 基线窗口

```text
当前窗口：最近3个完整自然日
基准窗口：当前窗口之前的7个完整自然日
weighted_ratio = SUM(分子) / SUM(分母)
```

窗口不能重叠。样本不足、数据不完整或只包含部分日期时，输出 `insufficient_data`。

### 9.2 用户指定的第一版诊断信号

所有高低判断都相对于账户自身或同类型计划基线：

```text
CTR低
→ 候选根因：素材问题 / 受众问题

CTR高但留资转化率低
→ 候选根因：页面、承接话术或线索筛选问题

CTR低且CPC高
→ 候选根因：素材弱 / 竞争弱势 / 出价问题

CTR高、CPC低、留资转化率高
→ 放量候选
```

这些是候选根因，不是绝对因果结论。没有频次、定向包或受众重叠数据时，必须列出缺失证据。

### 9.3 动作边界

```text
样本不足、数据延迟、人工反馈缺失 → HOLD / CHECK_DATA
有效留资成本持续偏高 → 降预算或替换素材候选
达到有效留资成本止损线且持续无有效留资 → 暂停候选
CTR高、CPC低、留资转化率高且有效质量稳定 → 放量候选
```

第一版建议调整幅度默认不超过 20%–25%，暂停、降预算、放量和出价调整都需要人工确认。

## 10. Agent 设计

### 10.1 Agent 输入

Agent 只接收结构化证据包，不直接读取完整 Excel 或完整线索内容：

```text
对象信息
当前指标
历史基线
趋势变化
样本量
规则命中的异常
缺失证据
昨日建议及执行状态
```

### 10.2 Agent 输出

```json
{
  "recommendation_id": "rec-20260914-001",
  "object_type": "unit",
  "object_id": "unit-828",
  "priority": "P0",
  "action": "pause",
  "reason": "valid_lead_cost_increased",
  "cause": "creative_fatigue",
  "diagnosis_status": "probable",
  "evidence": [],
  "missing_evidence": [],
  "confidence": 0.78,
  "suggested_value": null,
  "observation_window": "24h",
  "requires_human_approval": true,
  "status": "pending_approval"
}
```

诊断状态支持：`confirmed`、`probable`、`possible`、`insufficient_data`。

建议状态：

```text
generated → pending_approval → approved / rejected
approved → executed → observing → evaluated → closed
```

同一对象、同一动作在没有新证据时不重复升级。

## 11. 日报模板

```markdown
# 本地推运营日报｜{report_date}

## 1. 数据状态
统计日期：{data_date}
拉取时间：{fetched_at}
数据完整度：{data_completeness}

## 2. 昨日数据
| 指标 | 数值 |
|---|---:|
| 消耗 | |
| 留资量 | |
| 单个留资成本 | |
| 有效留资量 | |
| 有效留资成本 | |
| 曝光量 | |
| 点击量 | |
| CTR | |
| CPC | |
| 私信咨询数 | |
| 留资转化率 | |
| 对接数量 | |
| 对接成本 | |

## 3. 本月累计（当月1日至昨日）
| 指标 | 本月累计 |
|---|---:|
| 留资量 | |
| 留资成本 | |
| 对接数量 | |
| 对接成本 | |

## 4. 异常和诊断
| 优先级 | 对象 | 异常 | 根因候选 | 置信度 | 动作 |
|---|---|---|---|---:|---|

## 5. 计划和素材建议
| 对象 | 阶段 | 主要证据 | 建议动作 | 幅度 | 观察窗口 |
|---|---|---|---|---|---|

## 6. 昨日建议执行情况
| 建议 ID | 是否执行 | 实际动作 | 执行时间 | 结果 |
|---|---|---|---|---|
```

## 12. 模块结构

```text
agent/src/
├── config.py
├── models.py
├── storage/
│   ├── db.py
│   ├── repositories.py
│   └── migrations/
├── skills/
│   ├── excel_loader.py
│   ├── oceanengine_client.py
│   ├── shimo_client.py
│   ├── metrics_calculator.py
│   ├── data_reconciler.py
│   ├── report_renderer.py
│   └── wecom_pusher.py
├── rules/
│   ├── engine.py
│   ├── baseline.py
│   └── rules_loader.py
├── agent/
│   ├── graph.py
│   ├── state.py
│   ├── evidence_builder.py
│   ├── diagnose.py
│   └── suggest.py
└── replay/
    ├── run_replay.py
    └── fixtures/
```

## 13. 分阶段开发

### P0：本地回放

使用现有 9 月 Excel，不接真实 API、石墨或企业微信。

验收目标：

- 复现 `8-28深转` 前期有效、后期衰退；
- 识别 `9.3素材6-10浅层` 留资和有效留资质量差异；
- 识别 9 月 9 日后低消耗、零新增留资；
- 计算昨日数据和本月累计数据；
- 计划-素材无法关联时明确提示；
- 小样本和人工字段缺失时不强行归因；
- 同一输入重复运行不会重复累计。

### P1：数据核心

实现原始数据保存、标准化、数据库表、指标计算、账户/计划/素材对账、本月累计、基线和幂等运行。

### P2：浏览器自动下载

实现已登录浏览器会话复用、昨日日期选择、单元/计划和视频/素材报表下载、下载完成校验、登录失效停止和延迟数据补偿。巨量 API 只作为未来可选路线，不作为当前主链路。

### P3：石墨和企业微信

实现石墨日报写入、石墨人工反馈读取、有效留资和对接同步、企业微信摘要推送以及失败降级。

### P4：诊断 Agent

实现 Rule Engine、证据包、LangGraph 流程、根因候选、建议 JSON、置信度和重复建议抑制。

### P5：反馈闭环和人工审批

实现建议状态机、执行结果、观察窗口和效果评估。计划、预算、出价和素材的实际调整由人工执行，不接平台写操作。

## 14. 本地回放验收标准

### 14.1 指标

- [ ] CTR = 点击 / 曝光；
- [ ] CPC = 消耗 / 点击；
- [ ] 留资转化率 = 留资 / 点击；
- [ ] 单个留资成本 = 消耗 / 留资；
- [ ] 有效留资成本 = 消耗 / 有效留资；
- [ ] 对接成本 = 消耗 / 石墨“是否对接=是”的数量；
- [ ] 分母为 0 时返回 NULL；
- [ ] 本月成本 = 本月累计消耗 / 本月累计数量；
- [ ] 本月累计范围为当月 1 日至昨日。

### 14.2 诊断

- [ ] 8.28 深转后期衰退能被识别；
- [ ] CTR 下降不会直接被判定为素材疲劳；
- [ ] CTR、CPC、留资转化率能够生成候选根因；
- [ ] 有效留资字段缺失时不生成质量类高风险建议；
- [ ] 没有频次或受众重叠数据时列出缺失证据；
- [ ] 样本不足时输出观察，而不是暂停；
- [ ] 高 CTR、低 CPC、高留资转化率只标记为放量候选。

### 14.3 数据和幂等

- [ ] 原始文件、工作表和原始行号可追溯；
- [ ] 账户和计划汇总能够对账；
- [ ] 计划和素材无法关联的金额能够单独展示；
- [ ] 数据不完整时不会生成假日报；
- [ ] 同一输入重复运行不会重复累计；
- [ ] 重跑不会重复生成日报或无限重复建议。

## 15. 失败处理

### 平台数据失败

标记 `data_completeness = partial/missing`，保存失败记录，禁止生成高风险预算动作，并触发重试和告警。浏览器下载失败或登录失效时同样处理。

### 石墨读取失败

平台基础日报可以生成，但有效留资量、对接数量、有效留资成本和对接成本标记为缺失，不生成质量类高风险动作。

### LLM 失败

保留规则异常和证据，日报显示“诊断生成失败”，输出 `CHECK_DATA`，不生成未经解释的高风险动作。

### 推送失败

先保存数据库和石墨结果，只重试推送，不重新拉数和生成建议。

## 16. 安全要求

- App Secret、Access Token、Refresh Token 不写入代码或普通 n8n 文本节点；
- Refresh Token 轮换后必须加密持久化；
- 手机号、微信号等敏感字段不得进入普通日志；
- LLM 只接收聚合指标和诊断证据，不接收完整线索内容；
- 石墨和数据库按最小权限配置；
- 建议执行必须记录操作人、时间和实际参数；
- 第一版不自动执行暂停、预算、出价和素材发布动作。

## 17. 当前不做的事情

第一版明确不做：

- 自动暂停计划；
- 自动修改预算或出价；
- 自动发布或替换素材；
- 自动给抖音来客线索打标签；
- 用行业固定 CTR/CPC 作为自动告警标准；
- 在计划和素材无法关联时强行归因；
- 把其他质量字段自动等同于有效留资；
- 把石墨全量覆盖写入作为默认写入方式。

## 18. 开发完成定义

当以下条件全部满足，才认为 V1 开发完成：

1. 使用 9 月历史文件可以完成本地回放。
2. 指标和本月累计口径经过人工核对。
3. 计划和素材数据能够追溯到原始记录。
4. 数据不完整时不会生成假日报或高风险动作。
5. Agent 输出包含对象、动作、证据、置信度和观察窗口。
6. 石墨“是否有效”和“是否对接”能够同步到业务数据库。
7. 日报包含昨日数据和本月累计数据。
8. 建议执行结果可以在下一次运行时被读取。
9. 同一日期重复运行不会重复累计或重复制造建议。
10. 浏览器下载、文件导入和人工执行反馈都通过 [CONNECTOR_ACCEPTANCE.md](./CONNECTOR_ACCEPTANCE.md) 验收。

## 19. 关联文档

- [DATA_CONTRACT.md](./DATA_CONTRACT.md)：数据字段、粒度和本月累计口径；
- [RULES_V1.yaml](./RULES_V1.yaml)：规则层配置；
- [CONNECTOR_ACCEPTANCE.md](./CONNECTOR_ACCEPTANCE.md)：连接器验收；
- [LOCAL_REPLAY_PLAN.md](./LOCAL_REPLAY_PLAN.md)：本地回放执行方案。
