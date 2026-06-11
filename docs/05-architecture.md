# 架构与代码地图

## 1. 接收模式

当前唯一支持的接收模式是 **detached windows**（`receiver_mode = "detached_windows"`）。

每个微信聊天窗口被识别为一个独立 macOS 窗口，通过窗口 ID 捕获并 OCR：
- `detached_window_receiver.py` — 枚举 detach 窗口、截图
- `visible_message_parser.py` — OCR 解析聊天区内容
- `visible_message_state.py` — 去重和增量跟踪

旧版统一列表模式（`legacy_list`）已归档至 `archive/legacy/`。

## 2. 目录与模块

- `run.py`：启动入口
- `start_rpa.sh`：一键运行脚本（venv、依赖、env、日志、日志清理）
- `start_rpa_watchdog.sh`：定期重启守护
- `wechat_rpa/config.py`：dataclass 默认值与 `config.toml` 加载
- `wechat_rpa/window.py`：微信窗口定位、区域截图
- `wechat_rpa/ocr.py`：OCR 引擎封装（`rapidocr/paddleocr/cnocr`）
- `wechat_rpa/detector.py`：专用窗口检测器（detached window 模式）
- `wechat_rpa/llm.py`：LLM 请求封装（文本 + Vision + planner）
- `wechat_rpa/bot.py`：主状态机与消息处理链路
- `wechat_rpa/message_handler.py`：事件处理流程（focus、context、reply）
- `wechat_rpa/action_processor.py`：Agent 工具执行器
- `wechat_rpa/agent_store.py`：MemoryStore / SkillStore / PeopleStore / ChatHistoryStore
- `wechat_rpa/prompt_context.py`：人格配置（`data/config/`）+ 技能（`data/skills/`）上下文构建
- `wechat_rpa/sender.py`：消息发送（剪贴板 + AppleScript / pyautogui）
- `wechat_rpa/image_generation.py`：图片生成
- `wechat_rpa/image_editing.py`：图片编辑
- `wechat_rpa/people_aliases.py`：人物别名解析
- `wechat_rpa/python_sandbox.py`：安全 Python 沙盒（数学/统计/日期计算）

## 3. 主数据流

1. `run.py` 加载配置并构造 `WeChatGuiRpaBot`
2. `bot.run_forever()` 循环检测 detach 窗口
3. 默认由 `visible_message_parser` OCR 解析消息变化；启用 `detached_vision_parse_enabled` 时，窗口聊天区变化后先用 vision LLM 解析 messages JSON，失败再回退 OCR
4. `message_handler.handle_event()` 处理事件
5. `_reply()` 生成并发送回复

## 4. 存储层（System A）

所有运行时数据统一在 `data/` 下：

| 目录 | 存储 | 访问类 |
|---|---|---|
| `data/memory/` | core.md / timeline.md | `MemoryStore` |
| `data/people/` | 人物印象 `<name>.md` | `PeopleStore` |
| `data/skills/` | 技能 `<name>/SKILL.md` | `SkillStore` |
| `data/config/` | 人格配置（SOUL/IDENTITY/USER/TOOLS/AGENTS/SKILLS/PEOPLE_ALIASES） | `prompt_context.py` |
| `data/session_memory.json` | 会话状态（history/summary/muted） | bot 内部 |

## 5. Agent 工具

**当前可用工具（System A）：**

- `write_memory` — 写入 `data/memory/core.md` 或 `timeline.md`
- `read_impression` / `write_impression` — 读写 `data/people/` 人物印象
- `write_skill` / `delete_skill` — 管理 `data/skills/` 技能
- `read_chat_history` — 读取当前/指定会话历史
- `run_python` — 安全沙盒 Python 计算
- `web_search` — 并行聚合 Tavily、Brave、Volc Ark 的联网检索
- `fetch_url` / `browse_url` — 网页内容抓取
- `generate_image` / `edit_image` — 图片生成与编辑
- `mute_session` / `unmute_session` — 会话静音控制
- `build_wow_character_url` — 魔兽角色链接构建

## 6. Heartbeat

心跳空闲自驱任务。触发条件：
- `heartbeat_enabled=true`
- 达到 `heartbeat_interval_sec`
- 空闲超过 `heartbeat_min_idle_sec`

可用工具：`read_impression`、`write_impression`、`write_memory`

## 7. 数据目录

```
data/
  memory/              MemoryStore（core.md, timeline.md）
    core.md
    timeline.md
  people/              PeopleStore（人物印象）
    <name>.md
  skills/              SkillStore（技能）
    <name>/SKILL.md
  config/              人格配置
    SOUL.md, IDENTITY.md, USER.md, TOOLS.md
    AGENTS.md, SKILLS.md, PEOPLE_ALIASES.md
  session_memory.json  会话状态
  generated_images/    生图输出
  edited_images/       改图输出
```

## 8. 平台依赖

- macOS：
  - Quartz：窗口枚举/坐标
  - AppleScript：粘贴发送
  - `pyautogui`：鼠标键盘动作
