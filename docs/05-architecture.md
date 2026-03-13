# 架构与代码地图

## 1. 目录与模块

- `run.py`：启动入口；解析命令 `run/recover/recover-auto`
- `start_rpa.sh`：一键运行脚本（venv、依赖、env、日志）
- `wechat_rpa/config.py`：dataclass 默认值与 `config.toml` 加载/回退逻辑
- `wechat_rpa/window.py`：微信窗口定位、区域截图
- `wechat_rpa/ocr.py`：OCR 引擎封装（`rapidocr/paddleocr/cnocr`），支持增强与 A/B 对比日志
- `wechat_rpa/detector.py`：左侧会话列表识别与事件特征提取，支持手工行框与自动行模式
- `wechat_rpa/llm.py`：OpenAI-compatible / Ollama-native 请求封装，Vision 解析与文本 LLM 能力
- `wechat_rpa/workspace_context.py`：`agent_workspace` bootstrap、记忆写入/检索、SQLite/embedding/rerank
- `wechat_rpa/bot.py`：主状态机与消息处理链路（focus、session memory、agent actions、heartbeat）

## 2. 主数据流（普通模式）

1. `run.py` 加载配置并构造 `WeChatGuiRpaBot`
2. `bot.run_forever()` 循环截图微信窗口
3. `detector.detect_chat_rows()` 输出 `ChatRowState[]`
4. `_pick_event()` 选定待处理会话与触发原因
5. 规则过滤（静音、群聊前缀、节流、自回声等）
6. `_focus_chat()` 聚焦目标会话并做标题校验
7. `_extract_chat_context()` 截图右侧聊天区并走 Vision
8. 整合上下文：session summary/history + workspace context + memory recall
9. 管理员命令优先执行（若命中）
10. 可选 `_run_agent_planner_loop()` 执行工具规划循环
11. `_reply_text()` 生成回复（含防复读与 `[NO_REPLY]` 处理）
12. `_reply()` 真实发送或 dry-run 输出
13. 更新 `session_memory.json` 与 `agent_workspace` 记忆文件

## 3. 检测层

### 3.1 手工行框模式

- 条件：`use_manual_row_boxes=true`
- 数据：`manual_row_boxes.json`
- 特点：稳定性高，推荐生产使用

### 3.2 自动行模式

- 条件：`use_manual_row_boxes=false`
- 依赖：`list_region + row_height_ratio + rows_max`
- 特点：快速起步，但对窗口变化更敏感

### 3.3 触发优先级

1. mention
2. unread/new_message
3. preview change（若 `trigger_on_preview_change=true`）

## 4. 回复层分工

- Vision：只做“观察层”解析，输出 `context + environment`
- Decision：是否回复判断
- Reply：最终回复生成
- Planner：工具动作规划
- Summary：会话摘要更新

回复兜底：

- LLM 不可用或失败 -> `reply_on_new_message/reply_on_mention`
- 群聊可配置允许 `[NO_REPLY]` 静默

## 5. 记忆层

### 5.1 会话记忆（JSON）

`data/session_memory.json` + `data/session_memory.sessions/*.json`：

- `short`
- `history`
- `summary`
- `muted`
- `aliases`

### 5.2 工作区记忆（Markdown/JSON）

`agent_workspace/`：

- `AGENTS.md` / `SOUL.md` / `IDENTITY.md` / `USER.md` / `TOOLS.md`
- `MEMORY.md`
- `memory/YYYY-MM-DD.md`
- `memory/sessions/*.md`
- `memory/session_state/*.json`

### 5.3 recall 增强

可选链路：

- SQLite FTS 索引召回
- embedding 相似度加权
- rerank 精排

## 6. Agent 工具执行

普通消息可用工具（按配置与权限）：

- `remember_session_fact`
- `remember_session_event`
- `set_session_summary`
- `search_memory`
- `web_search`
- 管理员额外：`remember_long_term`、`mute_session`、`unmute_session`

Heartbeat 额外支持：

- `maintain_memory`
- `refine_persona_files`

## 7. Heartbeat 支线

入口：主循环 idle 分支 `_maybe_run_heartbeat()`。

触发条件：

- 心跳开关开启
- 达到间隔
- 达到最小空闲时长

执行顺序：

1. 读取 `HEARTBEAT.md`
2. 优先解析直接动作
3. 否则 LLM 规划动作
4. 复用同一工具执行器落盘记忆

## 8. 平台耦合

当前实现依赖 macOS：

- Quartz：窗口枚举/坐标
- AppleScript：应用激活、粘贴发送
- `pyautogui`：鼠标键盘动作
