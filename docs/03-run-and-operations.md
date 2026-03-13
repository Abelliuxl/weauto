# 运行与日常操作

## 1. 启动入口

### 1.1 推荐：`start_rpa.sh`

```bash
./start_rpa.sh config.toml
```

脚本行为（当前实现）：

1. 创建/复用 `.venv312`
2. 安装依赖（`requirements.txt` 变化时会重装）
3. 若 `config.toml` 不存在则从 `config.toml.example` 复制
4. 自动加载 `.env.weauto`、`.env`（若存在）
5. 启动 `python -u run.py --config ...`
6. 输出日志到 `logs/rpa_*.log`
7. 启动前按策略清理旧日志：
- `LOG_KEEP_MAX_FILES`（默认 40）
- `LOG_KEEP_MAX_TOTAL_MB`（默认 512）

### 1.2 直接运行

```bash
python run.py --config config.toml
```

说明：`run.py` 在 Python >= 3.13 且项目存在 `.venv312/bin/python` 时，会优先重启到该解释器。

## 2. 运行模式

普通模式：

```bash
./start_rpa.sh config.toml
```

恢复模式（手动翻页）：

```bash
./start_rpa.sh config.toml recover
```

恢复模式（自动滚动）：

```bash
./start_rpa.sh config.toml recoverauto
# 或 recover-auto
```

`recover/recover-auto` 启动时会先让你选择会话类型（群聊/私聊）并确认目标标题。

## 3. 主循环真实链路（`run_forever()`）

1. 定位微信窗口并截图
2. `detect_chat_rows()` 产出会话行状态
3. 若 baseline 为空则初始化 baseline
4. `_pick_event()` 选事件（优先 mention，其次 unread/new_message，再次 preview 变化）
5. 规则过滤：忽略标题、静音、群聊 sender 前缀、冷却、全局 normal 间隔、自回声等
6. 需要上下文时执行 focus 校验并截图右侧聊天区
7. Vision 解析聊天截图为 `wechat_context_v2`
8. 注入会话记忆 + 工作区上下文 + recall 记忆片段
9. 管理员命令优先分流执行（`/mute`、`/remember` 等）
10. 可选 agent 规划循环（tool -> observation -> re-plan）
11. 文本 LLM 生成回复（或模板兜底）
12. 真实发送（`dry_run=false`）或仅日志输出（`dry_run=true`）
13. 更新 session/history/summary/workspace memory 并落盘
14. 本轮无事件时，按 heartbeat 条件触发自驱任务

## 4. 何时读取右侧聊天上下文

满足任一条件会读取右侧聊天区：

- Vision 已启用
- `new_message` 且开启 `skip_if_latest_chat_from_self` 规则
- 当前会话是管理员会话
- 启用 decision 且 `decision_read_chat_context=true`

注意：

- OCR 负责左侧列表检测
- 右侧正文由 Vision 解析
- `vision.fail_open=true` 时，Vision 失败后继续流程（空 context），不回退聊天区 OCR

## 5. 群聊/私聊策略

群聊识别：

- 标题命中 `group_title_prefixes`
- 或 `group_detect_sender_prefix=true` 且 preview 呈现 `发送者:内容`

群聊回复还受：

- `group_only_reply_when_mentioned`
- `group_reply_keywords`
- `group_require_sender_prefix_for_new_message`
- `group_allow_llm_no_reply`
- `decision_*` 分流参数

## 6. 心跳（Heartbeat）

触发条件：

- `heartbeat_enabled=true`
- 距离上次心跳超过 `heartbeat_interval_sec`
- 空闲时长超过 `heartbeat_min_idle_sec`

执行顺序：

1. 读取 `workspace_dir/HEARTBEAT.md`（忽略空行和 markdown 标题）
2. 先尝试直接解析动作：`maintain_memory`、`refine_persona_files`
3. 若无直接动作，则走 LLM 规划动作
4. 复用 agent 工具执行链路

## 7. 发送链路

真实发送时（`dry_run=false`）：

1. 必要时 focus 到目标会话并校验标题
2. 点击输入框
3. 复制到剪贴板
4. AppleScript 执行 `Cmd+V + Enter`
5. AppleScript 失败时回退 `pyautogui` 键盘输入

## 8. 常用运维命令

```bash
# 主程序
./start_rpa.sh config.toml

# 快速看日志
tail -f logs/rpa_*.log

# 清理单会话记忆（先 dry-run）
./clear_session_data.sh config.toml "会话key" --dry-run
```

调试入口详见：`docs/04-debug-and-troubleshooting.md`。

## 9. 上线建议

1. 先校准再运行
2. 先 `dry_run=true` 观察 10-20 分钟
3. 确认 focus/title/context 正确
4. 再切 `dry_run=false`
