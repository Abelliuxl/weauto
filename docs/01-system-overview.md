# 系统总览

## 1. 项目定位

WeAuto 是运行在 macOS 上的微信 GUI RPA 与 AI 助手。它通过系统窗口和可见界面完成：

1. 枚举独立微信聊天窗口
2. 截图并解析可见消息
3. 判断消息是否需要处理
4. 调用本地 LLM pipeline 或外部桥接
5. 通过剪贴板、AppleScript 和键鼠事件发送回复
6. 保存聊天记录、会话状态、记忆、人物印象和运行状态

它不 Hook 微信、不注入进程、不读取微信数据库。

## 2. 系统组成

```text
WeAuto.app / start_app.sh
        |
        v
控制层 app/
  +-- 菜单栏 App
  +-- BotSupervisor
  +-- 只读 Web UI
        |
        v
run.py 子进程
        |
        v
wechat_rpa/
  +-- 窗口捕获与 OCR / Vision
  +-- 消息增量检测
  +-- native / bridge / long_bridge
  +-- Agent、记忆和工具
  +-- GUI 发送
```

控制层不是 Bot 的一部分。`app/` 负责进程管理和观测，`wechat_rpa/` 负责微信自动化业务。

## 3. 三种处理模式

| 模式 | 回复生成位置 | WeAuto 负责 |
|---|---|---|
| `native` | 本地 LLM 与 Agent pipeline | 接收、判断、工具、记忆、生成、发送 |
| `bridge` | HTTP 服务或 OpenClaw Gateway | 接收、规则、上下文组装、发送 |
| `long_bridge` | 远程 OpenClaw channel | 接收、WebSocket 协议、附件、发送 |

三种模式共用窗口接收、冷却规则、会话存储和 GUI 发送能力。

## 4. 消息解析

推荐模式下，每个微信会话必须是一个独立窗口。WeAuto 按窗口 ID 截图：

- 默认使用 `screencapture` 子进程，长期运行更稳定
- 可选 Quartz 进程内捕获，速度更快
- 本地 OCR 负责基础解析
- 可选 Vision LLM 输出结构化消息
- Vision 失败时可按 `vision.fail_open` 回退 OCR

解析结果由 `VisibleMessageStateStore` 做增量去重，避免把旧消息重复当成新消息。

## 5. 本地智能层

`native` 模式由多个可独立配置的 LLM profile 协作：

| Profile | 责任 |
|---|---|
| `llm_reply` | 最终回复 |
| `llm_decision` | 是否回复 |
| `llm_planner` | 工具规划 |
| `llm_summary` | 会话摘要 |
| `llm_heartbeat` | 空闲维护 |

Agent 可读取或维护记忆、人物印象、技能和聊天历史，并按配置使用网页、天气、Python 和图像工具。

## 6. 运行与观测

日常运行由 `WeAuto.app` 或 `start_app.sh` 启动：

- 菜单栏显示状态并提供启动、停止、重启和自动重启开关
- supervisor 以子进程方式运行 `run.py`
- Web UI 显示进程、会话和实时日志
- Web API 全部只读，不提供远程启停接口

直接调试时可绕过控制层运行 `start_rpa.sh`。

## 7. 数据原则

WeAuto 的持久数据主要位于 `data/`：

- `chat_history/`：按会话、日期保存的消息
- `memory/`：核心记忆和时间线
- `people/`：人物印象
- `skills/`：本地技能
- `config/`：人格和行为上下文
- `runtime_state.json`：运行恢复状态

日志位于 `logs/`。生成或编辑的图片也保存在 `data/` 下。

## 8. 平台限制

GUI RPA 的正确性依赖：

- 微信窗口结构与标题
- macOS 辅助功能和屏幕录制权限
- 显示缩放、窗口尺寸与 OCR 质量
- 当前前台焦点和输入法状态

新机器、微信升级或显示设置改变后，应先使用 `dry_run = true` 验证。
