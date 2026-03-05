# 微信 macOS GUI RPA（无 Hook/注入）

这个项目实现了一个**仅基于 GUI** 的微信桌面版自动回复工具：
- 识别聊天列表的新增消息（红点/红色未读角标）
- 识别聊天列表中的 `@`（通过 OCR 文本关键词）
- 满足条件后自动点击会话、聚焦输入框、发送预设回复

## 安全边界（按你的要求）

- 不使用 Hook
- 不做进程注入/线程注入
- 不访问微信数据库
- 只做 GUI 截图识别 + 鼠标键盘自动化

## 目录

- `run.py`：启动入口
- `config.toml.example`：配置模板（基于你给的微信界面布局）
- `wechat_rpa/window.py`：微信窗口定位 + 截图
- `wechat_rpa/ocr.py`：OCR 识别
- `wechat_rpa/detector.py`：新消息/`@` 检测
- `wechat_rpa/bot.py`：自动点击与自动回复
- `calibrate_rows_ui.py`：会话行框可视化校准工具（拖拽/缩放）

## 依赖安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

```bash
cp config.toml.example config.toml
```

核心参数（`config.toml`）：
- `dry_run = true`：先只检测不发送
- `list_region`：聊天列表区域比例（已按你截图初始调好）
- `row_height_ratio`：聊天行高比例
- `use_manual_row_boxes / manual_row_boxes_path`：启用手工会话行框（推荐）
- `input_point`：输入框点击点
- `mention_keywords`：`@` 判定关键词
- `activate_wait_sec / click_move_duration_sec`：降低点击过快导致的误拖拽
- `focus_verify_enabled / focus_verify_max_clicks`：点击会话后用 OCR 校验右侧标题，未命中自动重试点击
- `trigger_on_preview_change = true`：会话预览文本变化也触发新消息回复
- `debug_scan = true`：输出每轮识别变化，排查“无反应”问题
- `log_verbose = true`：每轮打印会话快照（row/group/unread/mention）和事件路径
- `group_title_prefixes = ["群"]`：把群名改为 `群***` 可稳定识别群聊
- `group_only_reply_when_mentioned = true`：群里默认只在被@/命中关键词时回复
- `memory_enabled = true`：开启会话记忆持久化（短期+长期摘要）
- `memory_store_path = "data/session_memory.json"`：记忆文件路径
- `admin_session_titles = ["example_admin"]`：管理员会话标题（支持多项）
- `admin_command_prefix = "/"`：管理员命令前缀
- `vision.enabled = true`：开启聊天截图多模态解析（图片消息可读）
- `vision.model`：截图解析模型，可与 `llm.model` 不同

### 可视化校准（推荐）

当自动行高/起点有偏移时，用这个工具一次校准：

```bash
./start_calibrator.sh config.toml
```

流程：
1. 自动截图微信窗口并预放 `0..N-1` 会话框；
2. 你在弹窗里拖拽/缩放每个框（框内拖动，右下角缩放）；
3. 点“保存并启用”。

保存后会：
- 写入 `data/manual_row_boxes.json`
- 自动更新 `config.toml`：
  - `use_manual_row_boxes = true`
  - `manual_row_boxes_path = "data/manual_row_boxes.json"`

### 标题栏 OCR 区域校准（用于点击后二次确认）

```bash
bash start_title_calibrator.sh config.toml
```

用途：
- 点击会话后，程序会 OCR 右侧标题栏判断是否真的进入目标会话。
- 这个工具用于校准标题 OCR 区域，避免“点第二次把会话收起/状态反转”。

## 运行

```bash
python run.py --config config.toml
```

或一键脚本：

```bash
./start_rpa.sh
```

仅调试“从上到下点击每个会话”：

```bash
./start_click_debug.sh config.toml --cycles 1 --click-delay-sec 1.0
```

查看调试日志：

```bash
tail -f logs/*.log
```

## 首次运行前的 macOS 权限

需要在“系统设置 -> 隐私与安全性”里给 Python/终端授予：
- 辅助功能（Accessibility）
- 屏幕录制（Screen Recording）

否则无法点击/输入/截图。

## 调参建议（结合你给的截图）

1. 先保持 `dry_run = true`，观察控制台日志是否能稳定打印 `mention` 或 `new_message` 事件。
2. 若误检：
   - 调小 `list_region.h`
   - 调整 `row_height_ratio`
   - 调大 `unread_badge.min_blob_pixels`
3. 若点击输入框不准：调整 `input_point`。
4. 确认稳定后，将 `dry_run` 改为 `false` 开启真实发送。

## 对接大模型接口（OpenAI 兼容）

在 `config.toml` 里打开：

```toml
[llm]
enabled = true
base_url = "https://api.openai.com/v1"
api_key = ""
api_key_env = "OPENAI_API_KEY"
model = "gpt-4o-mini"
```

二选一提供 API Key：

```bash
export OPENAI_API_KEY="你的key"
```

说明：
- 接口使用 OpenAI 兼容的 `POST /chat/completions`
- 优先使用 `llm.api_key`，为空时回退读取 `llm.api_key_env` 对应的环境变量
- `llm.decision_enabled = true` 时，先由 LLM 判断 `reply/skip`，再决定是否发送
- `llm.decision_read_chat_context = true` 时，会先点开会话并读取最近聊天内容后再判断
- `llm.interest_hint`：用大白话写你的关注偏好，LLM会据此调整`reply/skip`判断
- `llm.summary_enabled = true`：周期性更新“长期摘要”，增强跨轮记忆
- `llm.reasoning_exclude = true`：尽量关闭推理输出（OpenRouter）
- `llm.reasoning_effort = "low"`：若模型不支持完全关闭，可将推理强度降到 low
- 建议群聊开 `decision_on_group = true`，私聊按需开 `decision_on_private = true`
- LLM 调用失败会自动回退到本地固定回复文案，不会中断主流程

## 多模态截图解析（图片消息）

- 触发新消息后，机器人会截图右侧聊天区，调用 `[vision]` 模型解析。
- 模型输出结构化 JSON（最后消息、最后发送方、最近消息列表），再进入决策/回复/记忆。
- 如果 `vision` 调用失败且 `vision.fail_open = true`，会自动回退 OCR，不中断流程。

### Vision JSON 协议（wechat_vision_v1）

```json
{
  "schema": "wechat_vision_v1",
  "conversation": {"title": "群-上海", "is_group": true},
  "last_message": {
    "role": "user|assistant|unknown",
    "content_type": "text|image|mixed|unknown",
    "text": "最新消息内容",
    "sender": "昵称",
    "is_mention_me": false
  },
  "recent_messages": [
    {
      "role": "user|assistant|unknown",
      "content_type": "text|image|mixed|unknown",
      "text": "消息内容",
      "sender": "昵称",
      "is_mention_me": false
    }
  ],
  "confidence": 0.0
}
```

- `assistant` 表示右侧绿色（我方），`user` 表示左侧白色（他方）。
- 图片消息建议 `content_type=image`，`text` 可写 `[图片]`。
- 机器人内部会自动兼容旧版 vision 输出（`last_speaker/last_message/...`）。

### 与会话记忆兼容

- 当前持久化记忆格式在 `data/session_memory.json`：
  - `sessions.<key>.short`：`["U:...","A:..."]`
  - `sessions.<key>.summary`：长期摘要
- vision 的 `recent_messages` 会映射到上述 `U:/A:` 形式，保持兼容旧数据。

## 记忆机制（短期 + 长期）

- 短期记忆：每个会话保留最近 `memory_short_max_items` 条 `U:/A:` 片段。
- 长期记忆：每隔 `memory_summary_update_every` 条用户消息，用 LLM 更新一次摘要。
- 持久化：重启后从 `memory_store_path` 自动加载，不会丢会话上下文。

## 管理员命令

在 `admin_session_titles` 指定的会话里发送命令：

- `/sessions`：查看已缓存会话与静音状态
- `/mute 会话名`：停止该会话自动发言
- `/unmute 会话名`：恢复该会话自动发言
- `/reset 会话名`：清空该会话短期与长期记忆
- `/merge 源会话 -> 目标会话`：合并会话记忆（含别名映射）

## 注意

- 这是 GUI RPA，微信窗口布局变化会影响识别，需要重新调参。
- 建议先使用测试号和测试群验证逻辑，避免误发。
