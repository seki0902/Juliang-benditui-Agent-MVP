# Principles V2 改造

业务依据：用户确认的 P01–P64 和后续补充。本版替代旧版业务规则，不改变五 Agent 和 Supervisor 动态调度架构。内部 `content_review` 名称保留，以兼容旧 State、Graph 和审计文件。

## 行为变化

- 昨日完整一天为默认报告日期，结合3天、5天、30天；显式历史日期保持不变。
- 新计划/调价约2–5天观察，每天分析。预算/出价建议调整10%–30%，人工执行。
- 四份原则独立注入。核心为跑量、浅层/深转、可比组、修改历史及单变量验证，不以ROI和完整销售流程作前置条件。
- 素材同时读取投放数据与正文。正文缺失可报告表现，但不能标记完整内容分析；标题或片段只支持部分判断。正文发现、表现解释和缺口保留到日报。
- 策略可以调用只读计算器取得具体预算/出价候选。`proposal` 需匹配计算器返回的目标计划和数值；不是广告执行参数，不连接原后台执行器。
- 默认日报五部分：账户总体、待处理计划、问题更可能在哪、今天具体做什么、下一次看什么。完整审计留在 State。旧长格式可显式使用 `ReportRenderer(compact=False, include_path=True)`。

## 数据入口

当前运行入口为相邻 Douyin-AdPulse 项目的 `scripts/model_shadow.py`。
可选参数：`--shimo-snapshot`、`--plan-context`、`--content-snapshot`，均为只读UTF-8 JSON；`--report-date` 默认上海时间昨天。

三个文件均使用 `{"account_id": "账户1", "rows": [...]}`。账户必须与 `--inputs` 的账户目录名一致，防止串账户。缺来源返回 unavailable，不会补造。

石墨 daily rows 示例：

```json
{"data_date":"2026-09-19","valid_leads":2,"invalid_leads":1,"spend":600}
```

有效=加微信，无效=没加微信。深转来自石墨，深转成本由同范围同窗口消耗/有效条数计算。缺日期或空值保持缺失，0保留为真实0。仅有账户汇总时不能分配到计划或素材；只有明确提供 `plan_id` / `material_id` 的独立日汇总才支持对应范围查询，不将账户与计划行相加。现有石墨网页读取产物可按账户归属包裹为该结构；没有验证账户归属时不自动合并。是否成功回传平台单独保留为未知，不从石墨记录推断。

计划日快照示例：

```json
{"data_date":"2026-09-19","plan_id":"p1","budget":600,"bid":150,"conversion_goal":"lead","traffic_type":"recommendation","bidding_strategy":"stable_cost","targeting":{"region":"上海"},"schedule":"全天","status":"active","stage":"stable","material_ids":["m1"],"changes":[]}
```

保留同计划历次快照，按报告日截断。设置、素材关联及修改记录为事实，不根据计划名称猜测。计划汇总不伪装为该素材在该计划的独立效果。

正文快照示例：

```json
{"data_date":"2026-09-19","material_id":"m1","title":"标题","script":"完整口播稿","transcript":"字幕或转写","visual_notes":"画面要点","source":"verified_content_export"}
```

按素材和日期选取已知版本，报告日以后版本不可见。只有标题时不视作完整正文。

## 验证范围

`tests/test_principles_v2_runtime.py` 覆盖默认日期、窗口、石墨缺失/零/范围、非法计数、重复记录、日期截断、正文、数值建议来源校验、五段输出及内容完整性。
`docs/golden_cases_v2/case-a.yaml` 至 `case-e.yaml` 是用户给定案例的合成验收样本，兼容 GoldenCaseRunner；旧10个历史样本保留。加载和FakeModel测试不能证明真实模型的业务判断正确，真实模型语义评估仍单独进行。

此次不自动读取在线石墨、不启用生产推送，不改 `.env` 或广告执行开关。真实深转/计划设置/正文接入情况由运行时提供的来源决定。
