# 文档总览

本目录是 WeAuto 的完整使用文档，按“先校准、后运行、再优化”的顺序组织。

## 推荐阅读顺序

1. [01 校准流程](01-calibration-workflow.md)
2. [02 配置文件参考](02-configuration-reference.md)
3. [03 运行与日常操作](03-run-and-operations.md)
4. [04 调试与故障排查](04-debug-and-troubleshooting.md)
5. [05 架构与代码地图](05-architecture.md)

## 你最常会用到的 3 个页面

- 上线前必看：[校准流程](01-calibration-workflow.md)
- 改参数时查：[配置参考](02-configuration-reference.md)
- 出问题时查：[故障排查](04-debug-and-troubleshooting.md)

## 上线前最小 Checklist

1. 完成 [校准流程](01-calibration-workflow.md) 的 2.1~2.6（如用 recover-auto 再做 2.7）。
2. 运行三项 dry-run 校验：
- `./debug_click.sh config.toml --dry-run --cycles 1`
- `./debug_preview.sh config.toml --dry-run --cycles 1`
- `./debug_unread.sh config.toml --dry-run --cycles 1`
3. 主程序保持 `dry_run=true` 观察 10~20 分钟：
- `./start_rpa.sh config.toml`
4. 确认 `skip-focus`、错点、未读误判都在可接受范围后，再切 `dry_run=false`。

## 事实边界说明

本文档仅基于仓库当前代码行为整理，不包含未实现功能，不包含对未来计划的推测。
