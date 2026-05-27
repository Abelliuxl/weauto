# WeAuto：微信 macOS GUI RPA

基于 macOS 原生窗口的微信自动化（不 Hook、不注入、不读微信数据库）。

## 核心能力

- **Detached Window 接收模式** — 每个聊天窗口独立捕获 + OCR 解析
- **文本 LLM + Vision** 双模回复（decision / reply / summary）
- **Agent 工具规划** — 记忆读写、网页搜索、图片生成/编辑、Python 计算
- **Skill 技能系统** — 可扩展的本地策略模板
- **人物印象** — 自动维护每个联系人的印象笔记
- **Heartbeat 心跳** — 空闲时自驱维护记忆

## 快速开始

1. `cp config.toml.example config.toml`
2. 配置 LLM 连接和 API key
3. 确保微信聊天窗口已 detach
4. `./start_rpa.sh config.toml`（先 `dry_run=true` 观察）
5. 稳定后切 `dry_run=false`

## 目录结构

```
wechat_rpa/              核心代码
  bot.py                 主状态机
  message_handler.py     事件处理
  action_processor.py    工具执行
  agent_store.py         存储层
  prompt_context.py      人格/技能上下文
  llm.py                 LLM 请求封装
  detached_window_receiver.py  窗口接收
  visible_message_parser.py    消息解析
data/                    运行时数据
  config/                人格配置
  memory/                记忆（core.md, timeline.md）
  people/                人物印象
  skills/                技能模板
  session_memory.json    会话状态
docs/                    文档
archive/legacy/          旧版归档
```

## 启动方式

```bash
# 推荐
./start_rpa.sh config.toml

# 定期重启守护
./start_rpa_watchdog.sh config.toml

# 直接
python run.py --config config.toml
```

## 前置条件

- macOS（依赖 Quartz、AppleScript）
- 微信桌面版已登录，窗口处于 detach 状态
- 辅助功能 + 屏幕录制权限已授予

## 文档

- [配置参考](docs/02-configuration-reference.md)
- [运行与运维](docs/03-run-and-operations.md)
- [调试与排障](docs/04-debug-and-troubleshooting.md)
- [架构与代码地图](docs/05-architecture.md)
