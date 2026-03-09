# 运行与日常操作

## 1. 启动方式

### 1.1 推荐：一键脚本

```bash
./start_rpa.sh config.toml
```

脚本行为：

1. 进入项目目录
2. 确保 `.venv312` 存在
3. 安装依赖（`requirements.txt` 变化时会重新安装）
4. 若配置不存在，自动由 `config.toml.example` 复制
5. 运行 `python -u run.py --config ...`
6. 同时写日志到 `logs/rpa_YYYYmmdd_HHMMSS.log`

### 1.2 直接执行

```bash
python run.py --config config.toml
```

`run.py` 在 Python >= 3.13 时，会优先重启到项目的 `.venv312/bin/python`（若存在）。

## 2. 首次上线建议

1. `dry_run = true`
2. 启动并观察日志 10~20 分钟
3. 确认触发逻辑和目标会话正确
4. 切换 `dry_run = false` 再真实发送

## 3. 主循环真实行为

以下流程来自 `wechat_rpa/bot.py` 的 `run_forever()`：

1. 定位微信窗口并截图
2. 调 `detect_chat_rows()` 提取每行状态（标题、preview、未读、@、点击坐标）
3. 首轮建立 baseline（不立即动作）
4. 后续每轮用 `_pick_event()` 挑选事件
5. 事件优先级：`mention` > `new_message`（未读） > `preview变化`
6. 命中后按规则过滤：
- 忽略标题关键词
- 会话是否静音
- 群聊发送者前缀要求
- 冷却时间
- 自回声抑制
7. 需要时先聚焦会话并截图右侧聊天记录区域
8. Vision 输出 `context + environment` JSON（只做感知，不做 reply 生成）
9. 管理员命令会单独处理
10. 若启用 LLM 分流，会结合聊天 context、environment、会话记忆和工作区检索结果决策
11. 回复发送（`dry_run` 仅打印；真实模式会点击输入框并发送）
12. 更新会话完整历史、摘要、每日记忆和持久化文件
13. 若当前轮没有消息事件，且 `heartbeat_enabled=true`，会按固定间隔执行一次心跳任务规划与工具动作

## 4. 群聊与私聊判定

群聊判定条件（满足其一）：

- 标题以前缀命中 `group_title_prefixes`
- `group_detect_sender_prefix = true` 且 preview 形如 `发送者:内容`

群聊是否回复还会受以下约束：

- `group_only_reply_when_mentioned`
- `group_reply_keywords`
- `group_require_sender_prefix_for_new_message`
- 可选 LLM 分流（`decision_on_group`）

## 5. 聊天上下文读取策略

在以下情况会主动读取右侧聊天区内容：

- `reason == new_message` 且 `skip_if_latest_chat_from_self = true`
- 当前为管理员会话
- 启用了 LLM 分流且 `decision_read_chat_context = true`
- Vision 感知链路已启用

当前策略：

1. OCR 只负责左侧会话列表的新消息检测
2. 右侧聊天区按 `[chat_context_region]` 截图
3. Vision 输出标准化 `context + environment` JSON
4. Vision 失败且 `vision.fail_open=true` 时，不再回退聊天区 OCR，只回退到模板或纯文本 LLM 回复

## 6. 心跳模式（非消息驱动）

启用条件：

- `heartbeat_enabled = true`
- 距离上次心跳超过 `heartbeat_interval_sec`
- 空闲时长超过 `heartbeat_min_idle_sec`

执行行为：

1. 读取 `agent_workspace/HEARTBEAT.md`（忽略注释/空行）
2. 先尝试解析 `HEARTBEAT.md` 里的显式工具指令（如 `maintain_memory` / `refine_persona_files`）
3. 若未解析到显式指令，再将 `heartbeat_prompt + HEARTBEAT.md + 当前时间 + 工作区上下文` 喂给 LLM 规划
4. 执行白名单工具（记忆更新、检索、可选 `web_search` 等）

默认支持的心跳维护动作包括：

- `maintain_memory`：整理近期每日记忆到 `MEMORY.md`（托管块）
- `refine_persona_files`：整理 `SOUL.md` / `IDENTITY.md` / `USER.md` / `TOOLS.md`（托管块）

注意：

- 心跳不会直接代替正常消息回复链路
- `HEARTBEAT.md` 为空时会跳过，不会消耗 LLM 调用

## 7. 消息发送链路

真实发送（`dry_run=false`）时：

1. 点击会话行，必要时重试并校验右侧标题
2. 点击输入框两次
3. 复制消息到剪贴板
4. 通过 AppleScript 执行 `Cmd+V` + `Enter`
5. AppleScript 失败时回退 `pyautogui` 键盘输入

## 8. 管理员命令

启用条件：

- `admin_commands_enabled = true`
- 当前会话标题在 `admin_session_titles` 中
- 消息以 `admin_command_prefix` 开头（默认 `/`）

支持命令：

- `/help`
- `/sessions`
- `/mute 会话名`
- `/unmute 会话名`
- `/reset 会话名`
- `/merge 源会话 -> 目标会话`
- `/remember 需要长期记住的内容`

示例：

```text
/mute 群-上海
/unmute 群-上海
/reset 群-上海
/merge 群-上海 -> 上海群
```

## 9. 记忆机制

记忆分两层：

1. `data/session_memory.json`

- 短期消息缓存（`short`）
- 完整历史（`history`）
- 长期摘要（`summary`）
- 会话静音状态（`muted`）
- 标题别名映射（`aliases`）

2. `agent_workspace/`（或 `workspace_dir` 指定目录）

- `AGENTS.md` / `SOUL.md` / `IDENTITY.md` / `USER.md` / `TOOLS.md`
- `MEMORY.md` 长期记忆
- `memory/YYYY-MM-DD.md` 每日记忆
- `memory/sessions/*.md` 每个聊天窗口对应的会话记忆

记忆在以下时机写盘：

- 每轮空闲时
- 命令执行后
- 事件处理后

管理员可以通过 `/remember ...` 直接把稳定事实写入 `MEMORY.md`。

## 9. 日志建议

- 主运行日志：`logs/rpa_*.log`
- 校准日志：`logs/calibrate_*.log`
- 调试日志：`logs/debug_*.log`

常用查看：

```bash
tail -f logs/*.log
```
