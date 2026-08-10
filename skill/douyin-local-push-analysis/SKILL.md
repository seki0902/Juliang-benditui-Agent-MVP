---
name: douyin-local-push-analysis
description: Analyze Douyin Local Push / 抖音本地推 advertising data and campaign operations using the user's整理版投放规范. Use when reviewing 本地推投放数据, 账户/计划/素材/搜索广告效果, CTR/CVR/ECPM/CPL/ROI fluctuations, budget and bid changes, plan pausing decisions, lead follow-up, data回传, 素材审核风险, or when producing diagnostic reports and optimization recommendations from CSV/XLSX/manual tables.
---

# Douyin Local Push Analysis

## Core Workflow

1. Load the relevant references before giving operational judgments:
   - Read `references/analysis-framework.md` for data fields, formulas, segmentation, and report structure.
   - Read `references/douyin-local-push-rules.md` for the operating rules distilled from the user's TXT document.
   - Read `references/paid-ads-enhancement-playbook.md` when the user asks for platform logic, creative suitability, campaign architecture, budget scaling, kill/hold decisions, or beginner-friendly execution steps.
2. Identify the analysis objective: account health, plan diagnosis, material testing, search ads, budget/bid review, lead quality, or weekly/monthly复盘.
3. Normalize the data to the same grain before comparing: date, account, campaign/plan, material, ad type, bid strategy, budget, spend, impressions, clicks, conversions/leads, valid leads, revenue, follow-up timeliness.
4. Analyze in 3-day windows first, then inspect single-day anomalies. Treat one-day cost spikes or drops as insufficient evidence unless quality is clearly unacceptable or the account has already completed a stable observation cycle.
5. Segment before concluding:
   - 通投 vs 搜索广告
   - 新计划/新素材 vs 稳定计划
   - 素材类型: 口播、混剪、情景剧、前贴/搜索承接等
   - 出价策略: 稳定成本、最大转化、控制成本
   - 时段、地域/门店半径、年龄/性别/行为兴趣、关键词类别
   - 漏斗环节: Click / Convert / Close, i.e. 点击、留资、有效留资/成交承接.
6. Produce decisions as risk-graded actions:
   - `Hold`: continue observation; do not disturb system learning.
   - `Minor adjust`: small budget/bid/material/copy changes.
   - `Control risk`: cap budget, isolate tests, reduce new-plan share, or fix tracking/follow-up.
   - `Stop/replace`: only after minimum cycle or when quality/compliance risk is clear.
   - `Scale`: only when effective lead cost, lead quality, and trend stability all pass the thresholds; scale with small increments rather than budget jumps.

## Analysis Standards

- Prefer multi-day averages and trend direction over isolated daily metrics.
- Explain whether a metric issue is likely traffic-side, creative-side, bid/budget-side, targeting-side, sales follow-up-side, or platform-cycle-side.
- Tie every recommendation to a specific rule, metric pattern, and expected effect.
- Distinguish platform suggestions from actual optimization needs. Do not blindly execute backend prompts if the plan is still投放中 and business metrics are acceptable.
- Flag compliance and operational risks separately from performance issues.
- Diagnose upstream before downstream: if CTR is weak, fix the creative/traffic promise; if CTR is healthy but CVR is weak, fix landing/form/intent; if leads exist but valid leads are weak, fix screening and sales follow-up before scaling.
- State uncertainty when required fields are missing, and list the exact data needed to make the diagnosis reliable.

## Output Pattern

For data reports, use this concise structure:

1. `结论`: 3-5 bullets with the strongest findings.
2. `数据健康`: coverage, missing fields, sample size, and observation window.
3. `关键指标`: spend, leads/conversions, CPL/CPA, CTR, CVR, CPM/ECPM, valid lead rate, ROI if available.
4. `分层诊断`: account / plan / material / search keyword / time period / targeting / sales follow-up.
5. `规则映射`: cite the relevant rule from the reference, e.g. "3日观察周期", "30%测新预算", "CTR-CVR联动".
6. `行动建议`: list immediate actions, observation actions, and forbidden actions.
7. `下次复盘`: define the next observation window and metrics to check.

## Important Guardrails

- Treat the bundled rules as a user-provided operating playbook, not current official Douyin policy. If the user asks for latest platform rules, verify with current official sources.
- Do not promise deterministic起量, fixed ROI, guaranteed赔付, or guaranteed conversion outcomes.
- For legal/compliance risk, use cautious wording and recommend platform/法律/广告法 review when stakes are high.
- For spend decisions, provide analytical guidance rather than financial guarantees.
