# 配置文件参考（`config.toml`）

本文按当前代码（`wechat_rpa/config.py` + `wechat_rpa/bot.py`）整理。

## 1. 读取规则

- 默认值来自 `AppConfig` / `LlmConfig` / `VisionConfig` / `EmbeddingConfig` / `RerankConfig`。
- `config.toml` 只覆盖你显式填写的项。
- `config.toml.example` 是一份“可运行示例”，其值不等于代码默认值。

关键回退规则：

- `llm/vision/embedding/rerank` 的 `base_url` 为空时，会尝试读取 `base_url_env` 指向的环境变量。
- `vision.base_url/api_key/model` 为空时，会回退到 `llm` 对应字段。
- `llm_reply/llm_decision/llm_planner/llm_summary/llm_heartbeat` 会在未填写字段时继承 `[llm]`。

## 2. 坐标规则

两类坐标：

1. 窗口全局比例（相对微信窗口）
- 用于：`list_region`、`chat_context_region`、`chat_title_region*`、`input_point`、`recover_auto_click_point`

2. 单行局部比例（相对单条会话行，左下角为锚点）
- 用于：`row_title_region`、`preview_text_region`、`unread_badge_circle`

## 3. 顶层参数

### 3.1 主循环与交互

| Key | 默认值 | 说明 |
|---|---:|---|
| `app_name` | `"WeChat"` | 应用名，支持 `WeChat|微信` 这种别名串 |
| `poll_interval_sec` | `2.0` | 主循环间隔 |
| `action_cooldown_sec` | `8.0` | 同一行冷却时间 |
| `normal_reply_interval_sec` | `60.0` | 普通消息全局节流（私聊/@ 不受限） |
| `dry_run` | `true` | 只检测不发送 |
| `activate_wait_sec` | `0.6` | 激活微信后等待 |
| `click_move_duration_sec` | `0.18` | 鼠标移动耗时 |
| `mouse_down_hold_sec` | `0.03` | 鼠标按下保持 |
| `post_select_wait_sec` | `0.35` | 点击会话后等待 UI 稳定 |
| `focus_verify_enabled` | `true` | 是否做标题校验 |
| `focus_verify_max_clicks` | `3` | 校验重试次数 |
| `focus_verify_wait_sec` | `0.20` | 校验重试间隔 |
| `trigger_on_preview_change` | `true` | 允许 preview 变化触发 |
| `debug_scan` | `false` | 扫描细节日志 |
| `log_verbose` | `false` | 详细日志 |
| `log_snapshot_rows` | `6` | 每轮打印行数上限 |
| `process_existing_unread_on_start` | `true` | 启动时已有未读是否纳入待处理 |
| `skip_first_action_on_start` | `true` | 首次事件只记录不执行 |

### 3.2 记忆与工作区

| Key | 默认值 | 说明 |
|---|---:|---|
| `memory_enabled` | `true` | 会话记忆总开关 |
| `memory_store_path` | `data/session_memory.json` | 主记忆文件 |
| `memory_short_max_items` | `12` | 每会话短期记忆条数上限 |
| `memory_short_context_items` | `8` | 注入回复上下文的短期条数 |
| `memory_summary_update_every` | `4` | 每 N 个用户回合刷新长期摘要 |
| `memory_summary_recent_items` | `10` | 摘要时引用的 recent short 条数 |
| `memory_summary_max_chars` | `500` | 摘要最大长度 |
| `memory_history_context_items` | `24` | 构造历史上下文使用条数 |
| `memory_history_max_items` | `0` | history 持久化上限（0=不限） |
| `workspace_enabled` | `true` | `agent_workspace` 体系开关 |
| `workspace_dir` | `agent_workspace` | 工作区目录 |
| `workspace_memory_main_only` | `true` | 非管理员是否禁止读取全局长期记忆 |
| `workspace_memory_search_limit` | `3` | 每次 recall 输出条数 |
| `workspace_memory_rerank_enabled` | `false` | recall 重排开关 |
| `workspace_memory_rerank_shortlist` | `24` | 重排候选池 |
| `workspace_memory_rerank_weight` | `2.5` | 重排加权系数 |
| `workspace_memory_sqlite_enabled` | `false` | SQLite 记忆索引开关 |
| `workspace_memory_sqlite_path` | `data/workspace_memory.sqlite3` | SQLite 路径 |
| `workspace_memory_sqlite_sync_interval_sec` | `20.0` | 索引增量同步最小间隔 |
| `workspace_memory_sqlite_fts_limit` | `64` | FTS 召回上限 |
| `workspace_memory_sqlite_vector_limit` | `24` | embedding 加权候选上限 |
| `workspace_memory_sqlite_chunk_chars` | `320` | 索引切块长度 |

### 3.3 管理命令与 Agent 动作

| Key | 默认值 | 说明 |
|---|---:|---|
| `admin_commands_enabled` | `true` | 管理命令开关 |
| `admin_session_titles` | `["example_admin"]` | 管理员会话白名单 |
| `admin_command_prefix` | `"/"` | 命令前缀 |
| `agent_actions_enabled` | `true` | 工具动作执行开关 |
| `agent_actions_max_per_turn` | `2` | 单轮最多动作数 |
| `agent_actions_fail_open` | `true` | 动作规划失败是否继续主流程 |
| `agent_plan_loop_enabled` | `true` | 多轮规划循环开关 |
| `agent_plan_max_rounds` | `3` | 最多规划轮数 |
| `agent_plan_max_total_actions` | `6` | 单次触发总动作预算 |
| `agent_plan_repeat_limit` | `2` | 相同 tool+args 重复上限 |
| `agent_plan_observation_max_chars` | `5200` | 规划可见 observation 长度上限 |

管理员命令（当前实现）：

- `/help`、`/?`
- `/sessions`
- `/mute 会话名`
- `/unmute 会话名`
- `/reset 会话名`
- `/merge 源 -> 目标`
- `/remember 内容`

### 3.4 Web Search provider

| Key | 默认值 | 说明 |
|---|---:|---|
| `web_search_provider` | `tavily` | `tavily` / `brave` / `agent_reach` |
| `tavily_enabled` | `false` | Tavily 开关 |
| `tavily_base_url` | `https://api.tavily.com` | Tavily 地址 |
| `tavily_api_key` | `""` | Tavily key |
| `tavily_api_key_env` | `TAVILY_API_KEY` | Tavily 环境变量名 |
| `tavily_max_results` | `3` | 返回条数上限 |
| `tavily_timeout_sec` | `8.0` | 超时 |
| `brave_enabled` | `false` | Brave 开关 |
| `brave_base_url` | `https://api.search.brave.com/res/v1/web` | Brave 地址 |
| `brave_api_key` | `""` | Brave key |
| `brave_api_key_env` | `BRAVE_SEARCH_API_KEY` | Brave 环境变量名 |
| `brave_max_results` | `3` | 返回条数上限 |
| `brave_timeout_sec` | `8.0` | 超时 |
| `agent_reach_enabled` | `false` | Agent Reach 开关 |
| `agent_reach_mcporter_cmd` | `mcporter` | 外部命令名（需在 PATH） |
| `agent_reach_max_results` | `5` | 返回条数上限 |
| `agent_reach_timeout_sec` | `12.0` | 超时 |

### 3.5 Heartbeat

| Key | 默认值 | 说明 |
|---|---:|---|
| `heartbeat_enabled` | `false` | 心跳开关 |
| `heartbeat_interval_sec` | `1800.0` | 心跳间隔 |
| `heartbeat_min_idle_sec` | `20.0` | 最小空闲时间 |
| `heartbeat_max_actions` | `2` | 单次最多动作数 |
| `heartbeat_fail_open` | `true` | 心跳失败是否不中断主循环 |
| `heartbeat_prompt` | 内置提示词 | 心跳规划提示词 |

`HEARTBEAT.md` 支持两类执行：

- 直接指令解析：`maintain_memory` / `refine_persona_files`
- 未命中直接指令时：LLM 规划动作

### 3.6 回复策略与检测

| Key | 默认值 | 说明 |
|---|---:|---|
| `reply_on_new_message` | `已收到消息，我稍后回复你。` | 兜底回复（普通消息） |
| `reply_on_mention` | `收到@，我看到了，稍后处理。` | 兜底回复（@） |
| `mention_keywords` | `[@我,有人@我,[有人@我]]` | mention 识别词 |
| `mention_any_at` | `false` | 任意 `@` 视为 mention |
| `group_title_prefixes` | `["群"]` | 群聊标题前缀 |
| `group_detect_sender_prefix` | `true` | 用 `发送者:内容` 识别群聊 |
| `group_require_sender_prefix_for_new_message` | `true` | 群聊 `new_message` 要求 sender 前缀 |
| `group_only_reply_when_mentioned` | `true` | 群聊仅 @/关键词 时回复 |
| `group_allow_llm_no_reply` | `true` | 允许群聊 `[NO_REPLY]` 静默 |
| `group_reply_keywords` | `[@助手,@机器人,机器人,bot,小助手]` | 群聊关键词白名单 |
| `ignore_title_keywords` | `["折叠的聊天"]` | 标题命中则跳过 |
| `skip_if_latest_chat_from_self` | `true` | 最新消息是自己时跳过 |
| `skip_if_latest_chat_from_self_private` | `false` | 私聊是否也启用该规则 |

### 3.7 列表检测、区域与恢复

| Key | 默认值 | 说明 |
|---|---:|---|
| `use_manual_row_boxes` | `false` | 手工行框模式 |
| `manual_row_boxes_path` | `data/manual_row_boxes.json` | 行框文件 |
| `row_title_region_enabled` | `false` | 行标题局部 OCR 区域 |
| `preview_region_enabled` | `false` | 行预览局部 OCR 区域 |
| `rows_max` | `8` | 自动行模式扫描上限 |
| `row_height_ratio` | `0.145` | 自动行高比例 |
| `chat_context_max_lines` | `14` | 兼容字段（当前主要由 Vision 解析） |
| `chat_self_x_ratio` | `0.62` | 兼容字段 |
| `recover_auto_scroll_amount` | `900` | recover-auto 每次滚动 |
| `recover_auto_scroll_pause_sec` | `0.7` | recover-auto 每次滚动后等待 |

## 4. 区域 section

### 4.1 窗口全局区域

- `[list_region]`：左侧会话列表
- `[chat_context_region]`：右侧聊天消息截图区域（给 Vision）
- `[chat_title_region]`：右侧标题区域（focus 校验）
- `[chat_title_region_group]`：群聊独立标题区域
- `[chat_title_region_private]`：私聊独立标题区域
- `[input_point]`：输入框点击点
- `[recover_auto_click_point]`：recover-auto 安全点击点（`-1,-1` 表示自动推导）

开关：

- `chat_title_region_group_enabled`
- `chat_title_region_private_enabled`

### 4.2 单行局部区域（左下锚点）

- `[row_title_region]`
- `[preview_text_region]`
- `[unread_badge_circle]`

### 4.3 未读检测

- `[unread_badge_circle].enabled=true` 时优先使用圆形红色占比判定
- 关闭圆形模式时，回退到 `[unread_badge].min_blob_pixels` + OCR 数字兜底

## 5. `[ocr]` 配置

| Key | 默认值 | 说明 |
|---|---:|---|
| `backend` | `rapidocr` | 主 OCR 后端：`rapidocr/paddleocr/cnocr` |
| `ab_compare_backend` | `""` | A/B 对比后端 |
| `ab_compare_sample_rate` | `0.0` | A/B 采样率（0~1） |
| `ab_compare_max_text_len` | `120` | A/B 日志截断长度 |
| `enhance` | `false` | OCR 前图像增强 |
| `target_short_side` | `900` | 增强目标短边 |
| `max_upscale` | `3.2` | 增强最大放大倍数 |
| `paddle_lang` | `ch` | Paddle/CnOCR 语言 |
| `paddle_use_angle_cls` | `false` | Paddle 参数 |

环境变量可覆盖：

- `WEAUTO_OCR_BACKEND`
- `WEAUTO_OCR_ENHANCE=1`

## 6. `[llm]` 与 profiles

### 6.1 通用字段

`[llm]` 支持：

- 连接与模型：`enabled/base_url/base_url_env/api_key/api_key_env/model`
- 兼容选项：`ollama_native/ollama_think/openai_compat_think_mode`
- 采样：`temperature/presence_penalty/frequency_penalty/max_tokens/timeout_sec`
- 风格与提示：`interest_hint/sarcasm_level/system_prompt`
- 分流：`decision_enabled/decision_on_group/decision_on_private/decision_fail_open/decision_max_tokens/decision_read_chat_context/decision_system_prompt`
- 摘要：`summary_enabled/summary_max_tokens/summary_system_prompt`
- 防复读：`anti_repeat_enabled/anti_repeat_window/anti_repeat_similarity/anti_repeat_retry`
- 推理控制与调试：`reasoning_exclude/reasoning_effort/debug_log_payload/debug_log_response`

### 6.2 profiles

可配置 section：

- `[llm_reply]`
- `[llm_decision]`
- `[llm_planner]`
- `[llm_summary]`
- `[llm_heartbeat]`

这些 profile 未填写字段会继承 `[llm]`。

说明：当前代码里 decision 开关字段读取 `decision_*`（`[llm]` 语义）；`llm_decision.decision_read_chat_context` 会影响是否预取聊天上下文；实际 `should_reply` 请求与回复链路共用同一生成器（`llm_reply` backend）。

## 7. `[vision]` 配置

| Key | 默认值 | 说明 |
|---|---:|---|
| `enabled` | `false` | Vision 总开关 |
| `base_url/base_url_env` | `""` / `OPENAI_BASE_URL` | 为空时可从 env 读取 |
| `api_key/api_key_env` | `""` / `OPENAI_API_KEY` | 认证 |
| `model` | `""` | 为空会回退到 `llm.model` |
| `ollama_native/ollama_think/openai_compat_think_mode` |  | 传输模式控制 |
| `timeout_sec` | `20.0` | 超时 |
| `max_tokens` | `0` | `<=0` 表示不传 |
| `response_format_json_object` | `true` | 是否附带 `response_format` |
| `fail_open` | `true` | Vision 失败是否继续主流程 |
| `reasoning_exclude/reasoning_effort` |  | 推理控制 |
| `debug_log_payload/debug_log_response` | `false` | 调试日志 |
| `system_prompt` | 内置 | 要求输出 schema JSON |

注意：

- 当前链路中 Vision 失败（`fail_open=true`）时，会返回空 context 并继续；不会回退“聊天区 OCR 解析”。

## 8. `[embedding]` 与 `[rerank]`

两者字段结构一致：

- `enabled`
- `base_url/base_url_env`
- `api_key/api_key_env`
- `model`
- `timeout_sec`

用途：

- `embedding`：记忆召回相似度加权
- `rerank`：候选记忆精排

## 9. 常用环境变量

- `OPENAI_API_KEY`、`OPENAI_BASE_URL`
- `SILICONFLOW_API_KEY`、`SILICONFLOW_BASE_URL`
- `TAVILY_API_KEY`
- `BRAVE_SEARCH_API_KEY`
- `WEAUTO_OCR_BACKEND`
- `WEAUTO_OCR_ENHANCE`
- `WEAUTO_LOG_WIDTH`（日志宽度）
- `WEAUTO_LOG_FILE`（由启动脚本注入，用于 debug 附加日志）
- `FORCE_COLOR` / `NO_COLOR`

## 10. 建议流程

1. 从 `config.toml.example` 复制并跑完校准
2. 先 `dry_run=true` 观察日志
3. 再调整 `group_*`、`decision_*`、`agent_*`
4. 最后按需要开启 `workspace_memory_sqlite_*`、`embedding/rerank`
