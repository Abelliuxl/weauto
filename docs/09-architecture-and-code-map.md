# 架构与代码地图

## 1. 顶层结构

```text
WeAuto.app/                  macOS App bundle
app/                         控制面板和 supervisor
wechat_rpa/                  Bot 核心
openclaw-weauto-channel/     OpenClaw channel 插件
data/                        本地运行数据
docs/                        项目文档
tests/                       pytest 测试
scripts/                     数据维护和迁移脚本
tools/                       独立辅助服务
archive/legacy/              历史实现
```

根目录还包含以下构建和运行支撑文件：

| 文件 | 作用 |
|---|---|
| `config.toml.example` | 主配置模板 |
| `.env.example` | 密钥和环境变量模板 |
| `pyproject.toml`、`uv.lock` | Python 项目与锁定依赖 |
| `requirements.txt` | pip 兼容依赖入口 |
| `mcporter.json` | Exa MCP / Agent Reach 兼容配置；当前聚合搜索不自动使用 |

## 2. 启动层

| 文件 | 责任 |
|---|---|
| `WeAuto.app/.../launcher` | 从 Finder 启动项目内 Python |
| `start_app.sh` | 准备环境并启动 `app.main` |
| `start_rpa.sh` | 准备环境并直接启动 `run.py` |
| `start_rpa_watchdog.sh` | 固定周期重启 `start_rpa.sh` |
| `run.py` | 解析 run / recover 命令并构造 Bot |

## 3. 控制层 `app/`

| 模块 | 责任 |
|---|---|
| `app/main.py` | 组合 supervisor、Web server 和菜单栏 |
| `app/config.py` | 独立读取 `[webui]` |
| `app/supervisor.py` | 子进程、日志、状态、自动重启 |
| `app/bar/menu_app.py` | rumps 菜单栏 |
| `app/web/server.py` | ThreadingHTTPServer 和 SSE |
| `app/web/handlers.py` | 只读 API 和静态资源 |
| `app/static/` | Web UI HTML、CSS、JavaScript |

Supervisor 与 Bot 通过进程、stdout 和文件快照通信，没有内部 Python 对象耦合。

## 4. 接收与识别

| 模块 | 责任 |
|---|---|
| `window.py` | 主窗口边界和区域截图 |
| `detached_window_receiver.py` | detached 窗口枚举与按 ID 捕获 |
| `ocr.py` | RapidOCR / PaddleOCR / CnOCR 统一接口 |
| `visible_message_parser.py` | 从窗口图解析消息气泡 |
| `visible_message_state.py` | 增量消息状态 |
| `detector.py` | legacy list 模式检测 |

## 5. 核心编排

| 模块 | 责任 |
|---|---|
| `bot.py` | Bot 生命周期、状态机、pipeline 和恢复模式 |
| `message_handler.py` | 单次事件的通用处理顺序 |
| `sender.py` | 文本、@ 和文件 GUI 发送 |
| `config.py` | AppConfig、profiles 和 TOML 加载 |

`bot.py` 仍是最大的编排模块。新功能应优先放入独立模块，由 Bot 组合，而不是继续扩大底层
平台模块的职责。

## 6. LLM 与 Agent

| 模块 | 责任 |
|---|---|
| `llm.py` | 文本、Vision、tool calls、JSON repair、reasoning 控制 |
| `action_processor.py` | 执行 planner actions |
| `agent_store.py` | MemoryStore、SkillStore、PeopleStore、ChatHistoryStore |
| `prompt_context.py` | 人格和技能选择 |
| `people_aliases.py` | 人物别名和 mention 名称 |
| `python_sandbox.py` | 受限 Python 计算 |

`data/config/` 中的 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`USER.md`、`TOOLS.md` 和
`SKILLS.md` 会进入运行时提示词链路。`PEOPLE_ALIASES.md` 由联系人归一化逻辑读取；
`MENTION_ALIASES.md` 当前没有被 Python 运行链路加载，应视为人工参考或兼容遗留文件。

## 7. 外部能力

| 模块 | 责任 |
|---|---|
| `bridge.py` | HTTP / OpenClaw 短桥接 |
| `long_bridge.py` | 持久 WebSocket channel |
| `qweather.py` | QWeather lookup、JWT 和格式化 |
| `image_generation.py` | 图片生成 provider |
| `image_editing.py` | 图片编辑 provider |
| `tools/comfyui_openai_images_bridge.py` | 独立 ComfyUI 服务 |

## 8. 核心数据流

```text
macOS window
  -> screenshot
  -> OCR / Vision messages
  -> visible state diff
  -> ChatHistoryStore
  -> event policy
  -> native planner or bridge
  -> sender
  -> session state + runtime state + logs
```

## 9. 并发模型

- detached 主循环负责扫描窗口
- 每个会话使用队列避免同一窗口并发发送
- 消息任务在线程池中执行
- long bridge 使用后台线程维护连接
- supervisor 使用独立线程监控子进程和读取 stdout
- Web server 每个请求使用线程
- SSE 连接持有自己的长连接处理

任何新增共享状态都需要考虑锁、进程边界和退出清理。

## 10. 关键设计边界

- 微信自动化只依赖可见 GUI
- 控制面板不导入或实例化 Bot
- Web API 只读
- provider 工具在运行时做 capability check
- Markdown 人格、技能和记忆是可热更新文件
- 聊天正文使用简单文本，索引和状态使用 JSON
- legacy list 和恢复模式与 detached 主路径保持分离
