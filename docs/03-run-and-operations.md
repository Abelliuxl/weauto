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
7. 需要时先聚焦会话并提取聊天上下文
8. 管理员命令会单独处理
9. 若启用 LLM 分流，先判定 `reply/skip`
10. 回复（`dry_run` 仅打印；真实模式会点击输入框并发送）
11. 更新会话记忆并持久化

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

读取顺序：

1. OCR 上下文（永远可用）
2. Vision 解析（`vision.enabled=true` 时尝试）
3. Vision 失败且 `vision.fail_open=true` 时回退 OCR

## 6. 消息发送链路

真实发送（`dry_run=false`）时：

1. 点击会话行，必要时重试并校验右侧标题
2. 点击输入框两次
3. 复制消息到剪贴板
4. 通过 AppleScript 执行 `Cmd+V` + `Enter`
5. AppleScript 失败时回退 `pyautogui` 键盘输入

## 7. 管理员命令

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

示例：

```text
/mute 群-上海
/unmute 群-上海
/reset 群-上海
/merge 群-上海 -> 上海群
```

## 8. 记忆机制

记忆文件默认在 `data/session_memory.json`，包含：

- 短期消息缓存（`short`）
- 长期摘要（`summary`）
- 会话静音状态（`muted`）
- 标题别名映射（`aliases`）

记忆在以下时机写盘：

- 每轮空闲时
- 命令执行后
- 事件处理后

## 9. 日志建议

- 主运行日志：`logs/rpa_*.log`
- 校准日志：`logs/calibrate_*.log`
- 调试日志：`logs/debug_*.log`

常用查看：

```bash
tail -f logs/*.log
```

