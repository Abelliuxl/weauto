# 配置指南

## 1. 配置来源与优先级

Bot 配置由 `wechat_rpa.config.load_config()` 读取，控制面板的 `[webui]` 由
`app.config.load_webui_config()` 独立读取。

配置来源：

1. `AppConfig` / `WebUIConfig` 代码默认值
2. `config.toml` 显式值
3. 部分连接字段在 TOML 为空时读取对应环境变量

`config.toml.example` 是当前推荐配置模板。模板值不一定等于 dataclass 的代码默认值，
实际部署应以自己的 `config.toml` 为准。

## 2. 顶层运行配置

### 接收和处理

| 字段 | 用途 |
|---|---|
| `processing_mode` | `native`、`bridge` 或 `long_bridge` |
| `receiver_mode` | 推荐 `detached_windows`；`legacy_list` 仅用于旧布局 |
| `detached_window_title_filter` | 空列表处理全部，非空时按标题过滤 |
| `detached_window_output_dir` | 截图、消息图片和调试输出目录 |
| `detached_window_capture_backend` | `screencapture` 或 `quartz` |
| `detached_debug_save` | 保存最新窗口图和解析 JSON |
| `detached_reply_on_image` | 是否处理纯图片消息 |
| `detached_process_existing_on_start` | 启动时是否把当前可见消息当作新消息 |
| `detached_vision_parse_enabled` | 是否优先使用 Vision 解析窗口消息 |

### 时序和安全

| 字段 | 用途 |
|---|---|
| `poll_interval_sec` | 窗口轮询间隔 |
| `action_cooldown_sec` | 同一会话动作冷却 |
| `normal_reply_interval_sec` | 普通消息全局节流 |
| `dry_run` | 检测和生成但不实际发送 |
| `activate_wait_sec` | 激活微信后的等待 |
| `click_move_duration_sec` | 鼠标移动耗时 |
| `mouse_down_hold_sec` | 鼠标按下保持时间 |
| `post_select_wait_sec` | 选中窗口后的等待 |
| `send_after_paste_delay_sec` | 粘贴后按回车前的等待 |
| `skip_first_action_on_start` | 首次触发仅建立状态，不执行回复 |
| `log_verbose` | 是否输出更详细的运行日志 |
| `debug_scan` | 是否输出窗口扫描调试信息 |
| `memory_gc_interval_sec` | 定期触发 Python GC 的间隔；`0` 表示关闭 |
| `memory_watchdog_max_rss_mb` | RSS 超过阈值且队列空闲时自重启；`0` 表示关闭 |

代码还支持 `focus_verify_*`、`trigger_on_preview_change` 和 `log_snapshot_rows` 等高级字段；
推荐模式通常不需要手工设置。

## 3. 会话、人物和管理员

| 字段 | 用途 |
|---|---|
| `memory_enabled` | 会话状态和摘要总开关 |
| `memory_store_path` | 会话索引路径，当前模板为 `data/chat_history/_index.json` |
| `memory_short_max_items` | 每会话短期项上限 |
| `memory_short_context_items` | 注入回复上下文的短期项数 |
| `memory_summary_update_every` | 摘要刷新间隔 |
| `memory_summary_recent_items` | 摘要参考的最近项数 |
| `memory_summary_max_chars` | 摘要长度上限 |
| `memory_history_context_items` | 回复时读取的历史项数 |
| `memory_history_max_items` | 会话状态历史上限，`0` 表示不限 |
| `memory_append_max_items` | 记忆追加区保留项数 |
| `impression_append_max_items` | 人物印象追加区保留项数 |
| `people_aliases_enabled` | 是否启用人物别名 |
| `people_aliases_path` | 人物别名文件 |
| `admin_commands_enabled` | 是否处理管理员命令 |
| `admin_session_titles` | 管理员会话白名单 |
| `admin_command_prefix` | 管理命令前缀 |

`mention_send_*` 控制在群聊中发送带 @ 的消息时，触发昵称输入、退格、确认和暂停的时序。

## 4. 回复与群聊策略

| 字段 | 用途 |
|---|---|
| `reply_on_new_message` | LLM 不可用时的普通兜底回复 |
| `reply_on_mention` | mention 兜底回复 |
| `mention_keywords` | mention 识别词 |
| `mention_any_at` | 任意 `@` 是否视为 mention |
| `group_title_prefixes` | 群聊标题识别前缀 |
| `group_detect_sender_prefix` | 是否从群消息文本识别发送者前缀 |
| `group_require_sender_prefix_for_new_message` | 新群消息是否必须包含发送者 |
| `group_only_reply_when_mentioned` | 群聊是否仅在 mention / 关键词时回复 |
| `group_allow_llm_no_reply` | 是否允许 LLM 返回 `[NO_REPLY]` |
| `group_reply_keywords` | 群聊触发关键词 |
| `ignore_title_keywords` | 忽略的会话标题关键词 |

`config.toml.example` 为具体部署提供一组示例值。群聊策略应根据风险自行收紧。

## 5. LLM 与 profiles

`[llm]` 是基础配置，以下 profile 从它继承未覆盖的字段：

- `[llm_reply]`
- `[llm_decision]`
- `[llm_planner]`
- `[llm_summary]`
- `[llm_heartbeat]`

通用字段：

| 字段 | 用途 |
|---|---|
| `enabled` | 是否启用 |
| `base_url` / `base_url_env` | API 地址或环境变量名 |
| `api_key` / `api_key_env` | 密钥或环境变量名 |
| `model` | 模型名 |
| `api_format` | `openai` 或 `anthropic` 兼容格式 |
| `temperature` | 采样温度 |
| `max_tokens` | 输出 token 上限；`<=0` 时不发送 |
| `timeout_sec` | 请求超时 |
| `openai_compat_think_mode` | `default`、`on`、`off` |
| `reasoning_effort` / `reasoning_budget` | provider reasoning 控制 |
| `debug_log_payload` / `debug_log_response` | 调试请求与响应 |

代码包含 NVIDIA、MiMo、SiliconFlow、OpenRouter 等 OpenAI-compatible provider 的特定
reasoning 参数适配。provider 拒绝控制字段时，部分路径会自动用原始 payload 重试。

`[llm]` 中的 `decision_*`、`summary_*` 和 `anti_repeat_*` 控制分流、摘要和防复读。

## 6. Vision 与 OCR

### `[ocr]`

| 字段 | 用途 |
|---|---|
| `backend` | `rapidocr`、`paddleocr` 或 `cnocr` |
| `enhance` | 是否增强输入图 |
| `target_short_side` | 图像短边目标尺寸 |
| `max_upscale` | 最大放大倍数 |
| `paddle_lang` | Paddle / CnOCR 语言 |
| `ab_compare_backend` | 可选 A/B OCR 后端 |
| `ab_compare_sample_rate` | A/B 抽样比例 |
| `ab_compare_max_text_len` | A/B 日志文本上限 |

环境变量 `WEAUTO_OCR_BACKEND` 和 `WEAUTO_OCR_ENHANCE` 可以覆盖部分 OCR 设置。

### `[vision]`

Vision 支持独立的连接、模型、thinking、超时、JSON response format 和 fail-open 设置。
`base_url`、`api_key` 或 `model` 为空时会回退 `[llm]`。

只有同时满足以下条件才会用于 detached 消息解析：

```toml
detached_vision_parse_enabled = true

[vision]
enabled = true
```

## 7. Agent 与 Heartbeat

Agent 预算字段：

- `agent_actions_enabled`
- `agent_actions_max_per_turn`
- `agent_reply_max_messages_per_turn`
- `agent_actions_fail_open`
- `agent_plan_loop_enabled`
- `agent_plan_max_rounds`
- `agent_plan_max_total_actions`
- `agent_plan_repeat_limit`
- `agent_plan_observation_max_chars`

Heartbeat 字段：

- `heartbeat_enabled`
- `heartbeat_interval_sec`
- `heartbeat_min_idle_sec`
- `heartbeat_max_actions`
- `heartbeat_max_people`
- `heartbeat_people_history_records`

Heartbeat 只在空闲条件满足时运行，使用受限的记忆、人物和聊天历史工具集。

## 8. 搜索、网页和 Python

`web_search` 当前并行聚合：

- Tavily
- Brave Search
- Volcengine Ark

每个 provider 有独立的 `enabled`、endpoint、密钥、结果数和超时字段。至少一路可用时，
planner 才会看到 `web_search`。

`web_search_provider` 与 `agent_reach_*` 仍由代码加载，用于旧 provider 路径兼容；当前
聚合执行不依赖 `web_search_provider`。

`python_sandbox_restricted = true` 时，`run_python` 限制导入和 builtins。只有可信环境才应
关闭限制。

## 9. 天气和图片

### QWeather

启用条件：

```toml
weather_enabled = true
weather_provider = "qweather"
qweather_api_host = "你的专属 API Host"
```

认证支持：

- JWT：Credential ID、Project ID、Ed25519 私钥
- API key：`qweather_api_key` 或 `qweather_api_key_env`

JWT 签名依赖系统 `openssl`。

### `[image_generation]`

支持 DashScope Z-Image 和 OpenAI-compatible images endpoint。需要 `enabled`、provider、
endpoint、密钥和模型全部可用。

### `[image_editing]`

当前实现面向 DashScope Qwen Image Edit，支持本地路径或 URL 输入、大小、watermark、
prompt extend 和最大输入文件限制。

## 10. Bridge 配置

短桥接和长桥接的完整配置见
[桥接与外部集成](07-bridges-and-integrations.md)。

## 11. `[webui]`

| 字段 | 用途 |
|---|---|
| `host` | Web UI 监听地址 |
| `port` | 监听端口 |
| `log_ring_lines` | SSE 内存日志行数 |
| `auto_restart` | Bot 异常退出后是否重启 |
| `restart_min_interval_sec` | 自动重启最小间隔 |
| `supervisor_enabled` | 控制面板启动时是否自动拉起 Bot |

`[webui]` 只由 `app/` 读取。

## 12. Legacy list 与恢复几何

`config.toml.example` 中的以下区域主要用于 `legacy_list` 和恢复模式：

- `[list_region]`
- `[chat_context_region]`
- `[chat_title_region]`
- `[chat_title_region_group]`

`process_existing_unread_on_start` 也主要服务于 `legacy_list`，用于决定启动时是否处理已经
存在的未读消息。分离窗口模式使用自己的启动扫描和去重状态。
- `[chat_title_region_private]`
- `[input_point]`

代码还支持 `row_title_region`、`preview_text_region`、`recover_auto_click_point`、
`unread_badge` 和 `unread_badge_circle`。除非正在维护旧模式或恢复历史，否则不要调整。
