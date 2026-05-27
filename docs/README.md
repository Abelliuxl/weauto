# 文档总览

## 推荐阅读顺序

1. [配置文件参考](02-configuration-reference.md)
2. [运行与运维](03-run-and-operations.md)
3. [调试与排障](04-debug-and-troubleshooting.md)
4. [架构与代码地图](05-architecture.md)

## 快速上手

1. 复制配置：`cp config.toml.example config.toml`
2. 编辑 `config.toml` 配置 LLM / Vision / 搜索等
3. 确保微信聊天窗口处于 detach 状态
4. 先 `dry_run=true` 跑一段时间观察日志
5. 稳定后再切 `dry_run=false`
