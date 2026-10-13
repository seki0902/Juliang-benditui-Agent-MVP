# WorkBuddy Node 运行时

本项目不依赖系统 PATH 中的 Node/npm，也不在每次运行时扫描 WorkBuddy 目录。Node/npm/npx 的固定项目副本位于：

```text
D:\投放skill\Juliang-benditui-Agent-MVP\agent\runtime\workbuddy-node\node.exe
```

该副本来自 WorkBuddy 的 Node 22.22.2 运行时。WorkBuddy 原目录保持不动，避免影响其自身运行；项目脚本只引用上方固定目录。

```powershell
# 查看 WorkBuddy Node 版本
.\scripts\workbuddy_node.ps1 node --version

# 使用 WorkBuddy 的 npm/npx
.\scripts\workbuddy_node.ps1 npm --version
.\scripts\workbuddy_node.ps1 npx --version

# 运行 Playwright CLI（首次会由 npx 下载并缓存 @playwright/cli）
.\scripts\playwright_cli.ps1 --help
```

浏览器自动化统一调用 `scripts/playwright_cli.ps1`，不使用系统的 `node`、`npm` 或 `npx`。
