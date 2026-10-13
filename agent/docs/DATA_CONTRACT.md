# DATA_CONTRACT.md

> 版本：V1.0
> 状态：当前开发基线
> 更新时间：2026-09-15

## 0. 总原则

- 数据库是唯一事实来源。
- 石墨是展示和人工协作界面，不是业务数据库。
- LangGraph checkpoint 只保存图执行状态，不承担业务历史存储。
- 所有指标必须能追溯到原始数据、统计日期和计算口径。
- 数据不完整时必须显式标记，不能生成“看起来正常”的日报。
- 第一版通过浏览器下载报表，生成投放建议，不自动修改预算、出价或计划状态。

## 1. 数据层级

```text
账户
 └── 项目
      └── 计划/单元
           └── 广告
                └── 素材
```

由于不同接口可能使用不同命名，系统内部统一使用：

- `account`：账户
- `project`：项目
- `unit`：计划/单元
- `ad`：广告
- `material`：素材/视频

## 2. 数据粒度

### account_daily_metrics

一行 = 一个账户 + 一个统计日。

| 字段 | 类型 | 说明 |
|---|---|---|
| data_date | date | 统计日期，默认是昨天 |
| account_id | string | 账户 ID |
| spend | decimal | 消耗 |
| impressions | int | 曝光 |
| clicks | int | 点击 |
| leads | int | 留资量：平台后台当天转化数 |
| valid_leads | int | 有效留资量：当天新增留资中经人工筛选后的有效数 |
| private_messages | int | 私信咨询数 |
| contacted_leads | int | 对接数量：石墨线索表中 `是否对接=是` 的留资数 |
| fetched_at | timestamp | 拉取时间 |
| data_completeness | enum | `complete` / `partial` / `missing` |
| late_update_flag | boolean | 是否存在延迟更新 |
| source_run_id | string | 产生该行数据的运行 ID |

唯一键：`account_id + data_date + metric_version`。

### unit_daily_metrics

一行 = 一个单元/计划 + 一个统计日。

| 字段 | 类型 | 说明 |
|---|---|---|
| data_date | date | 统计日期 |
| account_id | string | 账户 ID |
| project_id | string | 项目 ID |
| unit_id | string | 计划/单元 ID |
| plan_type | enum | `shallow` / `deep` / `unknown` |
| lifecycle_stage | enum | `cold_start` / `mature` / `declining` / `unknown` |
| observed_status | string | 从报表观察到的状态 |
| actual_status | string | 接口实时返回的状态，无法获取时为空 |
| budget | decimal | 预算 |
| bid | decimal | 出价 |
| spend | decimal | 消耗 |
| impressions | int | 曝光 |
| clicks | int | 点击 |
| leads | int | 留资量：平台后台当天转化数 |
| valid_leads | int | 有效留资量：当天新增留资中经人工筛选后的有效数 |
| private_messages | int | 私信咨询数 |
| contacted_leads | int | 对接数量：石墨线索表中 `是否对接=是` 的留资数 |
| fetched_at | timestamp | 拉取时间 |
| data_completeness | enum | `complete` / `partial` / `missing` |
| source_run_id | string | 产生该行数据的运行 ID |

唯一键：`account_id + unit_id + data_date + metric_version`。

### material_daily_metrics

一行 = 一个素材 + 一个统计日。

| 字段 | 类型 | 说明 |
|---|---|---|
| data_date | date | 统计日期 |
| material_id | string | 素材 ID |
| account_id | string | 账户 ID |
| spend | decimal | 消耗 |
| impressions | int | 曝光 |
| clicks | int | 点击 |
| leads | int | 留资量：平台后台当天转化数 |
| valid_leads | int | 有效留资量：当天新增留资中经人工筛选后的有效数 |
| private_messages | int | 私信咨询数；素材维度无法取得时为 NULL |
| contacted_leads | int | 对接数量；按石墨表 `是否对接=是` 汇总，素材维度无法取得时为 NULL |
| fetched_at | timestamp | 拉取时间 |
| data_completeness | enum | `complete` / `partial` / `missing` |
| source_run_id | string | 产生该行数据的运行 ID |

唯一键：`account_id + material_id + data_date + metric_version`。

### plan_material_relation

一行 = 一个计划和一个素材在一段有效期内的关联。

| 字段 | 类型 | 说明 |
|---|---|---|
| account_id | string | 账户 ID |
| unit_id | string | 计划/单元 ID |
| material_id | string | 素材 ID |
| relation_start_date | date | 关联开始日期 |
| relation_end_date | date | 关联结束日期，NULL 表示当前仍关联 |
| source | enum | `browser_download` / `api` / `manual` / `inferred` |
| confidence | decimal | 关联置信度；推断关联必须填写 |

如果计划明细与素材明细无法直接关联，禁止强行拼接；应保留未关联状态，并在诊断中提示“无法完成计划-素材归因”。

### plan_state_history

该表同时保存状态快照和可确认的状态变化。每日一次的报表只能证明“某日观察到消耗/状态”，不能证明准确的开关时刻。

| 字段 | 类型 | 说明 |
|---|---|---|
| account_id | string | 账户 ID |
| unit_id | string | 计划/单元 ID |
| observed_status | string | 从报表观察到的状态 |
| actual_status | string | 接口实时返回状态 |
| status_changed_at | timestamp | 能确认时填写，无法确认时为空 |
| change_source | enum | `api` / `manual` / `agent` / `inferred` |
| snapshot_at | timestamp | 快照时间 |
| evidence | jsonb | 证明状态变化的证据 |

日报应使用“9月9日未观察到消耗”这类表述，除非存在实时状态或操作日志证据，否则不要写“9月9日已关闭”。

## 3. 计划-素材关联和状态边界

系统必须区分以下三种事实：

| 类型 | 示例 | 是否可由单次日报确认 |
|---|---|---|
| 投放表现 | 9月8日消耗 344.93 元 | 可以 |
| 观察到的活跃 | 9月8日有消耗 | 可以 |
| 精确操作事件 | 9月8日 23:15 关闭计划 | 不可以，除非有事件或高频快照 |

## 4. 业务漏斗口径

本项目采用以下业务漏斗：

```text
消耗 → 平台留资 → 人工筛选有效留资 → 对接
```

字段定义必须固定：

| 字段 | 业务含义 | 数据来源 |
|---|---|---|
| leads | 平台后台当天转化数，即留资量 | 浏览器下载的本地推报表 |
| valid_leads | 当天新增留资中，经人工筛选后的有效数 | 人工筛选/业务表 |
| private_messages | 私信咨询数 | 浏览器下载的本地推报表 |
| contacted_leads | 石墨线索表中 `是否对接=是` 的数量 | 石墨文档人工反馈表 |

派生指标：

| 指标 | 公式 | 说明 |
|---|---|---|
| lead_cost | spend / leads | 单个留资成本；平台当天留资成本 |
| valid_lead_cost | spend / valid_leads | 有效留资成本；按投放日监控 |
| CTR | clicks / impressions | 点击率 |
| CPC | spend / clicks | 点击成本 |
| lead_conversion_rate | leads / clicks | 留资转化率 |
| contact_cost | spend / contacted_leads | 对接成本 |

有效留资和对接可能不是留资产生当天完成。系统需要保留线索产生时间、筛选时间、对接时间和归因时间：

| 字段 | 说明 |
|---|---|
| lead_created_at | 线索产生时间 |
| screened_at | 人工筛选时间 |
| contacted_at | 对接时间 |
| is_contacted | 石墨表“是否对接”标准化后的布尔值 |
| attribution_date | 用于投放归因的日期 |
| attribution_window | 归因窗口，如 1d / 3d / 7d |

同时输出两个口径：

| 口径 | 公式 | 用途 |
|---|---|---|
| 投放日有效留资成本 | 当日消耗 / 当日新增有效留资数 | 日报监控 |
| 线索 cohort 有效留资成本 | 按线索产生日期归因的消耗 / 后续筛选有效数 | 判断计划和素材真实质量 |
| 线索 cohort 对接成本 | 按线索产生日期归因的消耗 / 石墨表中 `是否对接=是` 的数量 | 判断投放带来的留资是否进入对接 |

如果人工筛选或对接数据存在延迟，必须标记 `late_update_flag`，并区分“当日未产生”和“尚未完成筛选”。Agent 不得仅根据未完成的有效留资数据生成高风险预算建议。

### 对接数量的计算规则

第一版按线索产生日期归属：

```text
contacted_leads(data_date)
  = COUNT(石墨线索表中 lead_date = data_date 且 是否对接 = 是)

contact_cost(data_date)
  = 当日消耗 / contacted_leads(data_date)
```

如果石墨表中的“是否对接”后来才被人工补填，历史日期的对接数量和对接成本允许回填更新，并必须记录 `feedback_updated_at`。不能把“当天刚被补填的历史对接”误认为“当天新增对接”。

## 5. 指标计算口径

```text
CTR = clicks / impressions
lead_conversion_rate = leads / clicks
lead_cost = spend / leads
valid_lead_cost = spend / valid_leads
CPC = spend / clicks
lead_conversion_rate = leads / clicks
contact_cost = spend / contacted_leads
```

所有比率必须保留分子、分母和样本量。分母为 0 时返回 NULL，不返回 0。

素材疲劳的窗口定义：

```text
current_window = 最近 3 个完整自然日
baseline_window = current_window 之前的 7 个完整自然日
weighted_ctr = SUM(clicks) / SUM(impressions)
```

两个窗口不能重叠。窗口内样本不足时，返回 `insufficient_data`，不生成疲劳结论。

## 6. 数据获取口径

| 字段 | 值 |
|---|---|
| 统计日期 | 昨天 |
| 初次获取 | 今天 09:00 |
| 时区 | Asia/Shanghai |
| 获取方式 | 已登录浏览器自动下载报表 |
| 补偿校准 | 下午或次日重新下载前一日延迟数据 |

每次运行必须产生唯一的 `run_id`，并记录下载时间、浏览器页面、原始文件路径和文件哈希。推荐使用：

```text
run_key = account_id + data_date + pipeline_version
```

重复运行时使用 upsert 或版本替换，不得重复累计消耗、曝光、点击和线索。

## 7. 本月累计口径

每日 09:00 日报必须同时展示“昨日数据”和“本月累计数据”。本月累计截止到昨日，不包含当天 09:00 前的未完整数据。

```text
mtd_start_date = 当月 1 日
mtd_end_date = data_date（昨日）
```

| 指标 | 公式 | 说明 |
|---|---|---|
| MTD 留资量 | SUM(leads) | 当月 1 日至昨日的平台当天转化数 |
| MTD 留资成本 | SUM(spend) / SUM(leads) | 不能对每日留资成本做简单平均 |
| MTD 对接量 | SUM(contacted_leads) | 当月线索中石墨表 `是否对接=是` 的数量 |
| MTD 对接成本 | SUM(spend) / SUM(contacted_leads) | 不能对每日对接成本做简单平均 |

本月累计应从已落库的日明细重新聚合，不单独维护一个容易重复累加的累计值。必须保留：

```text
mtd_start_date
mtd_end_date
mtd_fetched_at
```

石墨表后续补填“是否对接=是”时，应回填对应线索日期的对接量和本月累计对接成本，并记录反馈更新时间。

## 8. 数据不完整和对账

数据拉取失败时不得生成正常日报。报告必须展示：

```text
日报状态：数据不完整
失败维度：人工筛选或石墨对接数据
影响：有效留资成本、对接成本和预算动作不生成
```

每次运行至少执行：

1. 账户汇总与单元汇总对账。
2. 单元汇总与素材汇总对账；无法关联时明确列出未关联金额。
3. 拉取记录、接口响应摘要和字段版本留档。

## 9. 业务表清单

```text
daily_runs
raw_metrics
normalized_metrics
account_daily_metrics
unit_daily_metrics
material_daily_metrics
plan_material_relation
plan_state_history
baseline_snapshots
recommendations
recommendation_execution
```

LangGraph checkpoint 单独管理，不替代以上业务表。

## 10. 隐私和安全

- 手机号、微信号等敏感字段脱敏后再进入日志或模型输入。
- LLM 只接收诊断所需的聚合数据和证据，不接收完整原始线索。
- 密钥、App Secret、Access Token、Refresh Token 不写入代码、Markdown 或普通 n8n 文本节点。
- 数据库启用访问控制和传输加密，并设置数据保留期限。
