# 配置文件参考（`config.toml`）

## 1. 读取规则

- 默认值来自各 `AppConfig` dataclass
- `config.toml` 只覆盖你显式填写的项
- `config.toml.example` 是一份"可运行示例"
- LLM profiles（`llm_reply/llm_decision/llm_planner/llm_summary/llm_heartbeat`）未填写字段时继承 `[llm]`

## 2. 接收模式

| Key | 默认值 | 说明 |
|---|---|---|
| `receiver_mode` | `detached_windows` | 当前仅 `detached_windows`（旧 `legacy_list` 已归档） |
| `detached_window_title_filter` | `[]` | 只处理匹配标题的窗口（空=全部） |
| `detached_debug_save` | `false` | 保存窗口截图到 `data/detached_window_images/` |
| `detached_reply_on_image` | `false` | 是否回复纯图片消息 |
| `detached_process_existing_on_start` | `false` | 启动时是否处理窗口已有消息 |

## 3. 主循环与交互

| Key | 默认值 | 说明 |
|---|---|---|
| `app_name` | `"WeChat"` | 应用名 |
| `poll_interval_sec` | `2.0` | 主循环间隔 |
| `action_cooldown_sec` | `8.0` | 同一窗口冷却时间 |
| `normal_reply_interval_sec` | `60.0` | 普通消息全局节流 |
| `dry_run` | `true` | 只检测不发送 |
| `activate_wait_sec` | `0.6` | 激活微信后等待 |
| `click_move_duration_sec` | `0.18` | 鼠标移动耗时 |
| `mouse_down_hold_sec` | `0.03` | 鼠标按下保持 |
| `send_after_paste_delay_sec` | `0.5` | 粘贴后延迟回车 |
| `debug_scan` | `false` | 扫描细节日志 |
| `log_verbose` | `false` | 详细日志 |
| `process_existing_unread_on_start` | `true` | 启动时已有消息是否处理 |
| `skip_first_action_on_start` | `true` | 首次事件只记录不执行 |

## 4. 会话记忆

| Key | 默认值 | 说明 |
|---|---|---|
| `memory_enabled` | `true` | 会话记忆总开关 |
| `memory_store_path` | `data/session_memory.json` | 主记忆文件 |
| `memory_short_max_items` | `12` | 每会话短期记忆条数上限 |
| `memory_short_context_items` | `8` | 注入回复上下文的短期条数 |
| `memory_summary_update_every` | `4` | 每 N 个用户回合刷新长期摘要 |
| `memory_summary_recent_items` | `10` | 摘要引用 recent short 条数 |
| `memory_summary_max_chars` | `500` | 摘要最大长度 |
| `memory_history_context_items` | `24` | 构造历史上下文使用条数 |
| `memory_history_max_items` | `0` | history 持久化上限（0=不限） |
| `memory_append_max_items` | `200` | core/timeline 追加区 bullet 保留上限 |
| `impression_append_max_items` | `80` | 单个人物印象追加观察 bullet 保留上限 |
| `people_aliases_enabled` | `true` | 人物别名硬映射开关 |
| `people_aliases_path` | `data/config/PEOPLE_ALIASES.md` | 别名文件 |

## 5. 管理命令与 Agent 动作

| Key | 默认值 | 说明 |
|---|---|---|
| `admin_commands_enabled` | `true` | 管理命令开关 |
| `admin_session_titles` | `["example_admin"]` | 管理员会话白名单 |
| `admin_command_prefix` | `"/"` | 命令前缀 |
| `agent_actions_enabled` | `true` | 工具动作执行开关 |
| `agent_actions_max_per_turn` | `2` | 单轮最多动作数 |
| `agent_reply_max_messages_per_turn` | `3` | 单次触发最多连续发送条数 |
| `agent_actions_fail_open` | `true` | 动作规划失败是否继续主流程 |
| `agent_plan_loop_enabled` | `true` | 多轮规划循环开关 |
| `agent_plan_max_rounds` | `3` | 最多规划轮数 |
| `agent_plan_max_total_actions` | `6` | 单次触发总动作预算 |
| `agent_plan_repeat_limit` | `2` | 相同 tool+args 重复上限 |
| `agent_plan_observation_max_chars` | `5200` | 规划可见 observation 长度上限 |

### 可用工具

- **记忆类：** `write_memory`（core/timeline）、`read_impression`、`write_impression`、`read_chat_history`
- **技能类：** `write_skill`、`delete_skill`
- **计算类：** `run_python`
- **联网：** `web_search`、`web_search_volc`、`search_web`、`search_web_brave`、`fetch_url`、`browse_url`
- **图像：** `generate_image`、`edit_image`
- **会话管理：** `mute_session`、`unmute_session`（管理员）
- **其他：** `build_wow_character_url`

### 管理员命令

- `/help`、`/?`
- `/sessions`
- `/mute 会话名`
- `/unmute 会话名`
- `/reset 会话名`
- `/merge 源 -> 目标`

## 6. Web Search provider

| Key | 默认值 | 说明 |
|---|---|---|
| `web_search_provider` | `tavily` | `tavily` / `brave` / `agent_reach` / `volc_ark` |
| `tavily_enabled` / `brave_enabled` / `volc_ark_enabled` / `agent_reach_enabled` | `false` | 各 provider 开关 |
| `*_api_key` / `*_api_key_env` | `""` | 密钥与环境变量 |
| `*_base_url` | 各 provider 默认 | API 地址 |
| `*_max_results` | 3~8 | 返回条数上限 |
| `*_timeout_sec` | 8~20 | 超时 |

## 7. Heartbeat

| Key | 默认值 | 说明 |
|---|---|---|
| `heartbeat_enabled` | `false` | 心跳开关 |
| `heartbeat_interval_sec` | `300.0` | 间隔 |
| `heartbeat_min_idle_sec` | `20.0` | 最小空闲时间 |
| `heartbeat_max_actions` | `4` | 单次最多内部动作数 |
| `heartbeat_fail_open` | `true` | 心跳失败不中断主循环 |

可用工具：`read_impression`、`write_impression`、`write_memory`

## 8. 回复策略

| Key | 默认值 | 说明 |
|---|---|---|
| `reply_on_new_message` | 兜底文本 | 普通消息兜底回复 |
| `reply_on_mention` | 兜底文本 | @ 兜底回复 |
| `mention_keywords` | `["@我", ...]` | mention 识别词 |
| `mention_any_at` | `false` | 任意 `@` 视为 mention |
| `group_title_prefixes` | `["群"]` | 群聊标题前缀 |
| `group_only_reply_when_mentioned` | `true` | 群聊仅 @/关键词 时回复 |
| `group_allow_llm_no_reply` | `true` | 允许群聊 `[NO_REPLY]` |
| `group_reply_keywords` | `["@助手", ...]` | 群聊关键词白名单 |
| `ignore_title_keywords` | `["折叠的聊天"]` | 标题命中则跳过 |
| `skip_if_latest_chat_from_self` | `true` | 最新消息是自己时跳过 |
| `skip_if_latest_chat_from_self_private` | `false` | 私聊是否启用该规则 |

## 9. `[llm]` 与 profiles

通用字段：
- `enabled/base_url/api_key/model`
- `temperature/presence_penalty/frequency_penalty/max_tokens/timeout_sec`
- `system_prompt/interest_hint/sarcasm_level`
- `decision_*` 分流参数
- `anti_repeat_*` 防复读参数

Profiles（继承 `[llm]`）：
- `[llm_reply]` — 回复生成
- `[llm_decision]` — 是否回复判断
- `[llm_planner]` — 工具规划
- `[llm_summary]` — 会话摘要
- `[llm_heartbeat]` — 心跳规划

## 10. `[vision]` 配置

| Key | 默认值 | 说明 |
|---|---|---|
| `enabled` | `false` | Vision 总开关 |
| `base_url/api_key/model` | 回退到 `[llm]` | 连接信息 |
| `timeout_sec` | `20.0` | 超时 |
| `max_tokens` | `0` | `<=0` 不传 |
| `fail_open` | `true` | 失败继续主流程 |

## 11. `[image_generation]` / `[image_editing]`

| Key | 默认值 | 说明 |
|---|---|---|
| `enabled` | `false` | 开关 |
| `provider` | `dashscope_z_image` / `dashscope_qwen_image_edit` | 服务商 |
| `base_url/api_key/model` | 百炼 AIGC | 连接信息 |
| `output_dir` | `data/generated_images` / `data/edited_images` | 输出目录 |

## 12. 常用环境变量

- `OPENAI_API_KEY`、`OPENAI_BASE_URL`
- `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`
- `TAVILY_API_KEY`、`BRAVE_SEARCH_API_KEY`、`ARK_API_KEY`
- `WEAUTO_OCR_BACKEND`、`WEAUTO_OCR_ENHANCE`
