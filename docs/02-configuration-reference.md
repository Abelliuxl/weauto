# 配置文件完整参考（`config.toml`）

本文按代码中的 `wechat_rpa/config.py` 实际加载逻辑整理。

## 1. 基本规则

- 默认配置来源：`AppConfig` / `LlmConfig` / `VisionConfig` dataclass
- 自定义配置来源：`config.toml`
- 未填写项会回退默认值
- `run.py` 默认读取 `config.toml`

建议流程：

1. 从 `config.toml.example` 复制
2. 先完成校准文档中的区域参数
3. 再调触发和 LLM 行为参数

## 2. 坐标与比例规则

项目中有两类区域坐标：

1. 窗口全局比例坐标（相对微信窗口）
- 用于：`list_region`、`chat_context_region`、`chat_title_region`、`input_point`
- 范围通常为 `0.0 ~ 1.0`

2. 单行局部比例坐标（相对会话单行）
- 用于：`row_title_region`、`preview_text_region`、`unread_badge_circle`
- 锚点是**左下角**：`x` 从左到右，`y` 从下到上

## 3. 顶层参数

### 3.1 运行节奏与交互

| Key | 默认值 | 说明 |
|---|---:|---|
| `app_name` | `"WeChat"` | 微信应用名，可用 `WeChat|微信` 形式配置别名 |
| `poll_interval_sec` | `2.0` | 主循环扫描间隔（秒） |
| `action_cooldown_sec` | `8.0` | 同一行触发后冷却时间（秒） |
| `dry_run` | `true` | `true` 只打印不发送；`false` 才会真实点击和发消息 |
| `activate_wait_sec` | `0.6` | 激活微信后等待时间 |
| `click_move_duration_sec` | `0.18` | 鼠标移动动画时间 |
| `mouse_down_hold_sec` | `0.03` | 按下鼠标保持时长 |
| `post_select_wait_sec` | `0.35` | 点击会话后等待 UI 稳定时间 |
| `focus_verify_enabled` | `true` | 点击会话后是否做“右侧标题匹配”校验 |
| `focus_verify_max_clicks` | `3` | 进入会话重试点击次数 |
| `focus_verify_wait_sec` | `0.20` | 标题校验重试间隔 |
| `trigger_on_preview_change` | `true` | 允许“预览文本变化”触发事件 |
| `debug_scan` | `false` | 打印扫描细节日志 |
| `log_verbose` | `false` | 打印详细运行日志 |
| `log_snapshot_rows` | `6` | 每轮最多打印多少行快照 |
| `process_existing_unread_on_start` | `true` | 启动时已存在未读是否进入待处理集合 |
| `skip_first_action_on_start` | `true` | 首次触发事件是否跳过执行（仅记录） |
| `skip_if_latest_chat_from_self` | `true` | 若聊天区最后一条是我方消息则跳过回复 |

### 3.2 回复与群聊策略

| Key | 默认值 | 说明 |
|---|---:|---|
| `reply_on_new_message` | `"已收到消息，我稍后回复你。"` | LLM 不可用或失败时的新消息兜底文案 |
| `reply_on_mention` | `"收到@，我看到了，稍后处理。"` | 被 @ 时兜底文案 |
| `mention_keywords` | `[@我, 有人@我, [有人@我]]` | 列表文本中识别 @ 的关键词 |
| `mention_any_at` | `false` | 是否把任意 `@` 字符当成 mention |
| `group_title_prefixes` | `["群"]` | 通过标题前缀识别群聊 |
| `group_detect_sender_prefix` | `true` | 通过 `发送者:内容` 形式识别群聊 |
| `group_require_sender_prefix_for_new_message` | `true` | 群聊 `new_message` 触发时，要求 preview 带发送者前缀 |
| `group_only_reply_when_mentioned` | `true` | 群聊是否仅在被 @ 或命中关键词时回复 |
| `group_reply_keywords` | `[@助手,@机器人,机器人,bot,小助手]` | 群聊关键词命中可允许回复 |
| `ignore_title_keywords` | `["折叠的聊天"]` | 标题包含这些词则跳过 |

### 3.3 记忆与管理员命令

| Key | 默认值 | 说明 |
|---|---:|---|
| `memory_enabled` | `true` | 是否启用会话记忆 |
| `memory_store_path` | `"data/session_memory.json"` | 记忆持久化路径 |
| `memory_short_max_items` | `12` | 每会话短期记忆上限 |
| `memory_short_context_items` | `8` | 构造回复上下文时带入短期条数 |
| `memory_summary_update_every` | `4` | 每多少个用户回合触发长期摘要更新 |
| `memory_summary_recent_items` | `10` | 摘要更新时引用最近短期条数 |
| `memory_summary_max_chars` | `500` | 长期摘要最大长度 |
| `admin_commands_enabled` | `true` | 是否启用管理员命令 |
| `admin_session_titles` | `["example_admin"]` | 管理员会话标题白名单 |
| `admin_command_prefix` | `"/"` | 管理命令前缀 |

管理员命令（实际实现）：

- `/help` 或 `/?`
- `/sessions`
- `/mute 会话名`
- `/unmute 会话名`
- `/reset 会话名`
- `/merge 源会话 -> 目标会话`

### 3.4 列表检测与区域开关

| Key | 默认值 | 说明 |
|---|---:|---|
| `use_manual_row_boxes` | `false` | 是否启用手工行框 JSON |
| `manual_row_boxes_path` | `"data/manual_row_boxes.json"` | 手工行框路径 |
| `row_title_region_enabled` | `false` | 是否启用行标题独立 OCR 区域 |
| `preview_region_enabled` | `false` | 是否启用 preview 独立 OCR 区域 |
| `rows_max` | `8` | 自动行模式最多扫描行数 |
| `row_height_ratio` | `0.145` | 自动行模式单行高度比例 |
| `chat_context_max_lines` | `14` | 聊天上下文 OCR 最大行数 |
| `chat_self_x_ratio` | `0.62` | OCR 行左右归属阈值（>= 判定为我方） |

## 4. 区域 section 参数

### 4.1 窗口全局区域

| Section | 默认值 | 说明 |
|---|---|---|
| `[list_region]` | `x=0.065 y=0.12 w=0.325 h=0.82` | 左侧会话列表区域 |
| `[chat_context_region]` | `x=0.40 y=0.16 w=0.57 h=0.63` | 右侧聊天内容截图区域 |
| `[chat_title_region]` | `x=0.40 y=0.01 w=0.57 h=0.10` | 右侧顶部标题区域 |
| `[input_point]` | `x=0.73 y=0.92` | 输入框点击点 |

### 4.2 单行局部区域（左下锚点）

| Section | 默认值 | 说明 |
|---|---|---|
| `[row_title_region]` | `x=0.24 y=0.52 w=0.58 h=0.42` | 行标题 OCR 区域 |
| `[preview_text_region]` | `x=0.24 y=0.10 w=0.72 h=0.52` | 行预览 OCR 区域 |
| `[unread_badge_circle]` | `enabled=false x=0.86 y=0.66 r=0.18` | 未读红点圆形区域 |

### 4.3 未读颜色检测（备用）

| Section | Key | 默认值 | 说明 |
|---|---|---:|---|
| `[unread_badge]` | `min_blob_pixels` | `70` | 红色连通域面积阈值 |

说明：

- 当 `unread_badge_circle.enabled=true` 时，优先用圆形区域红色占比判定
- 关闭圆形模式时，回退到红色 blob + OCR 数字兜底

## 5. LLM 参数（`[llm]`）

| Key | 默认值 | 说明 |
|---|---:|---|
| `enabled` | `false` | 是否启用 LLM |
| `base_url` | `https://api.openai.com/v1` | OpenAI 兼容接口地址（程序会拼 `/chat/completions`） |
| `api_key` | `""` | 明文 key（为空则尝试环境变量） |
| `api_key_env` | `OPENAI_API_KEY` | 环境变量名 |
| `model` | `gpt-4o-mini` | 回复模型 |
| `temperature` | `0.3` | 采样温度 |
| `presence_penalty` | `0.2` | presence_penalty |
| `frequency_penalty` | `0.2` | frequency_penalty |
| `max_tokens` | `180` | 回复最大 token |
| `timeout_sec` | `20.0` | 请求超时 |
| `interest_hint` | `""` | 决策时附加关注偏好 |
| `system_prompt` | 内置中文提示词 | 生成回复系统提示词 |
| `decision_enabled` | `true` | 是否启用 reply/skip 分流 |
| `decision_on_group` | `true` | 群聊启用分流 |
| `decision_on_private` | `false` | 私聊启用分流 |
| `decision_fail_open` | `false` | 分流失败时是否默认放行回复 |
| `decision_max_tokens` | `32` | 分流输出 token 上限 |
| `decision_read_chat_context` | `true` | 分流前是否读取聊天上下文 |
| `decision_system_prompt` | 内置提示词 | 分流器系统提示词 |
| `summary_enabled` | `true` | 是否启用长期摘要更新 |
| `summary_max_tokens` | `160` | 摘要 token 上限 |
| `summary_system_prompt` | 内置提示词 | 摘要器系统提示词 |
| `anti_repeat_enabled` | `true` | 是否启用防复读 |
| `anti_repeat_window` | `4` | 对比最近回复窗口 |
| `anti_repeat_similarity` | `0.82` | 相似度阈值 |
| `anti_repeat_retry` | `1` | 复读时重试次数 |
| `reasoning_exclude` | `true` | OpenRouter 场景下尝试关闭 reasoning 输出 |
| `reasoning_effort` | `low` | reasoning 强度（low/medium/high） |

## 6. Vision 参数（`[vision]`）

| Key | 默认值 | 说明 |
|---|---:|---|
| `enabled` | `false` | 是否启用截图多模态解析 |
| `base_url` | `""` | 为空时自动继承 `llm.base_url` |
| `api_key` | `""` | 为空时自动继承 `llm.api_key` |
| `api_key_env` | `OPENAI_API_KEY` | 环境变量名 |
| `model` | `""` | 为空时自动继承 `llm.model` |
| `timeout_sec` | `20.0` | 超时 |
| `max_tokens` | `512` | 输出 token 上限 |
| `fail_open` | `true` | Vision 失败时是否回退 OCR |
| `reasoning_exclude` | `true` | OpenRouter 场景关闭 reasoning |
| `reasoning_effort` | `low` | reasoning 强度 |
| `system_prompt` | 内置提示词 | 要求输出 `wechat_vision_v1` JSON |

## 7. 手工行框文件格式（`manual_row_boxes.json`）

```json
{
  "schema": "manual_row_boxes_v1",
  "window_width": 624,
  "window_height": 968,
  "boxes": [
    {"idx": 0, "x": 0.11, "y": 0.07, "w": 0.29, "h": 0.04}
  ],
  "updated_at": 1772727829
}
```

`x/y/w/h` 是相对窗口比例。

## 8. 环境变量（非 `config.toml`）

- `OPENAI_API_KEY`（或你在 `api_key_env` 里配置的名字）
- `WEAUTO_OCR_ENHANCE=1`：开启 OCR 预处理增强
- `WEAUTO_LOG_WIDTH`：覆盖日志排版宽度

