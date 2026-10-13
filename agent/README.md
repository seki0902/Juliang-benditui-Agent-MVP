# Local Growth Agent

> **当前 V1 总览：** [本地推运营 Agent V1](docs/LOCAL_PUSH_AGENT_V1.md)。该文档说明当前技术方案、数据口径、已完成能力、真实执行边界和日常运行方式；旧开发文档保留作实现细节参考。

本目录是本地推运营 Agent 的开发目录。当前主路线是：浏览器自动下载报表、导入分析、生成建议、石墨同步与 Clawbot 推送；真实投放调整受逐条确认、实时值核验和页面校准开关保护。

## Current Contents

- `docs/`: 当前开发方案、数据口径、连接器验收、导入流程和交接记录。
- `rules/`: placeholder for anomaly rules, health scoring rules, and strategy rules.
- `prompts/`: placeholder for report and strategy prompts.
- `src/`: 数据接入、配置检查、回放和存储代码。

浏览器自动化使用 WorkBuddy 自带的 Node 运行时，入口是 `scripts/workbuddy_node.ps1` 和 `scripts/playwright_cli.ps1`；不依赖系统 PATH。详见 `docs/WORKBUDDY_NODE_RUNTIME.md`。
- `requirements-p1.txt`: 当前 Python 依赖；包括文件处理、浏览器自动化、HTTP 客户端和 PostgreSQL 驱动。
- `config.example.env`: 非敏感配置模板；真实密钥不要提交到仓库或发送到聊天中。
- `docs/P1_READONLY_IMPLEMENTATION.md`: 浏览器下载与文件导入实施说明。

## MVP Direction

当前开发顺序：

```text
浏览器自动下载 -> 保存原始文件 -> 标准化指标 -> 账户/计划/素材日报 -> 石墨/企微输出
```

第一版不自动调预算、自动启停计划或扩展平台写操作。预算调整与暂停只支持“候选 → 准备具体动作 → 用户逐条确认 → 单次执行 → 刷新复核”的受控流程；真实提交尚未完成验收。

## 每日任务运行

正式运行使用 `daily_job.py`：浏览器下载、文件校验、导入分析、状态写入和建议生成共用一条链路。

先复制 `browser_download_config.example.json` 为 `browser_download_config.json`，填写已登录浏览器配置目录和页面选择器，然后运行：

```powershell
.\scripts\run_daily.ps1
```

首次配置或页面结构变化时，可以先使用 `--InputDir` 走已有文件验收；确认链路稳定后，用 `scripts/register_daily_task.ps1` 注册每天 09:00 的 Windows 任务。

运营人员执行建议后，可记录反馈：

```powershell
& "python" ".\src\record_feedback.py" `
  --state-db ".\state\local_growth_agent.sqlite3" `
  --recommendation-id "rec_xxx" --status executed --actual-action "降预算" --actual-value "20%"
```

## 文件导入本地运行

使用项目自带的 Python 环境，并把 `agent/src` 加入 `PYTHONPATH`：

```powershell
$env:PYTHONPATH = (Resolve-Path ".\agent\src").Path
python ".\agent\src\p1_readonly_check.py" --refresh
```

实际路径为 `Juliang-benditui-Agent-MVP\agent` 时，将上面的 `.\agent` 替换为该目录前缀。

将后台下载的单元/计划报表、视频/素材报表放入 `agent/inputs` 后，运行：

```powershell
& "python" ".\agent\src\run_import.py"
```

项目会按表头自动识别文件、计算指标并生成日报，不会暂停计划、改预算或改出价。

浏览器自动下载脚本将在此导入入口前运行；脚本只读取和下载报表，不执行投放修改。

## 浏览器自动下载路线

主路线是使用已登录的本地推浏览器页面自动下载文件，再交给 `run_import.py`：

```powershell
$env:PYTHONPATH = (Resolve-Path ".\agent\src").Path
& "python" ".\agent\src\run_import.py"
```

详细说明见 `docs/IMPORT_WORKFLOW.md`。用户不需要手工改文件名或清洗列名；如果没有石墨反馈，质量指标会明确标记为缺失。

## 调整边界

系统负责分析并提出计划、预算和素材观察建议。真实预算调整或暂停必须经过受控执行层，并由用户对具体动作逐条确认；开启计划、修改出价、素材和删除均未实现、未授权。详见 [本地推运营 Agent V1](docs/LOCAL_PUSH_AGENT_V1.md)。
