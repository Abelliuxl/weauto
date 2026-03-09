# 架构与代码地图

## 1. 模块分工

- `run.py`
  - 启动入口
  - 读取配置并实例化 `WeChatGuiRpaBot`

- `wechat_rpa/config.py`
  - dataclass 默认值定义
  - `load_config()` TOML 读取与类型转换

- `wechat_rpa/window.py`
  - 基于 Quartz 获取微信窗口边界
  - `screenshot_region()` 区域截图

- `wechat_rpa/ocr.py`
  - `RapidOCR` 封装
  - 可选图像增强（`WEAUTO_OCR_ENHANCE=1`）

- `wechat_rpa/detector.py`
  - 聊天列表行检测（手工行框模式 / 自动行模式）
  - 标题、preview、未读、@、点击坐标提取

- `wechat_rpa/llm.py`
  - OpenAI 兼容接口请求封装
  - 文本 LLM 分流决策、回复生成、摘要更新
  - Vision 图片解析（仅输出 context + environment JSON）与 JSON 纠错

- `wechat_rpa/workspace_context.py`
  - 文件工作区初始化
  - `AGENTS.md` / `SOUL.md` / `MEMORY.md` 等 bootstrap 文件维护
  - 长期记忆、每日记忆、会话记忆写入
  - 简单本地检索，把相关记忆片段注入 Vision/LLM

- `wechat_rpa/bot.py`
  - 主循环状态机
  - 事件挑选、过滤、聊天区截图、Vision 感知执行、session 记忆管理
  - 工作区规则和记忆检索注入

## 2. 运行时数据流

1. `run.py` 加载 `config.toml`
2. `bot.run_forever()` 轮询窗口截图
3. `detector.detect_chat_rows()` 产出 `ChatRowState[]`
4. `bot._pick_event()` 挑选当前要处理的一条事件
5. 必要时聚焦目标会话并截图 `[chat_context_region]`
6. Vision 输出 `context + environment` JSON（不产出 reply，不做 should_reply 判断）
7. 文本 LLM 基于 `context + environment + workspace/context/memory` 做 reply/skip 决策并生成回复
8. GUI 点击输入并发送
9. 更新 baseline、会话完整历史、摘要、每日记忆和持久化文件
10. 无消息事件时可选进入 heartbeat 分支，执行固定间隔的自驱工具任务

## 3. 两种行检测模式

### 3.1 手工行框模式（推荐）

启用条件：`use_manual_row_boxes = true`。

- 读取 `manual_row_boxes_path`
- 每个框独立 OCR 和未读判定
- 结果更稳定，受窗口微调影响更小

### 3.2 自动行模式

启用条件：`use_manual_row_boxes = false`。

- 按 `list_region + row_height_ratio + rows_max` 估算行
- 适合快速起步，不如手工框稳

## 4. 触发判定优先级

`_pick_event()` 中优先级固定：

1. mention 触发
2. unread/new_message 触发
3. preview 变化触发（仅当 `trigger_on_preview_change=true`）

随后再经过过滤：

- 标题忽略
- 静音会话
- 群聊前缀规则
- 冷却窗口
- 自回声抑制

## 5. 回复策略分层

1. 规则层：群聊/私聊、关键词、@、冷却、忽略
2. 解析层：`llm.analyze_chat_image()` 输出标准化 `context + environment`
3. 分流层：统一使用 `llm.should_reply()`（Vision 不参与是否回复判断）
4. 生成层：统一使用 `llm.generate()`（失败时回退固定模板）
5. 防复读层：相似度比对 + 重试
6. 发送层：点击输入框 + 粘贴发送

其中第 3、4 层都会额外带入两类工作区信息：

- `workspace_context`：人格、规则、身份、工具、长期记忆文件内容
- `memory_recall`：根据当前消息从 `MEMORY.md`、每日记忆、会话记忆中检索出的相关片段

## 5.1 Heartbeat 自驱任务

- 入口：`run_forever()` 的 idle 分支
- 条件：`heartbeat_enabled=true` 且达到 `heartbeat_interval_sec`，并满足 `heartbeat_min_idle_sec`
- 输入：`HEARTBEAT.md` + `heartbeat_prompt` + 工作区上下文
- 规划：优先解析 `HEARTBEAT.md` 的显式工具指令；未命中时回退 `llm.plan_actions()`
- 执行：复用 `_execute_agent_actions()` 白名单工具，不依赖新消息触发
- 可执行维护动作：`maintain_memory`（整理 `MEMORY.md`）和 `refine_persona_files`（整理人格设定文件）

## 6. 记忆结构

记忆文件 `data/session_memory.json` 主要字段：

- `sessions.<key>.short`：短期记忆
- `sessions.<key>.history`：完整历史消息记录
- `sessions.<key>.summary`：长期摘要
- `sessions.<key>.muted`：静音状态
- `aliases`：会话 key 别名映射

此外，`workspace_dir` 下还有一套更接近 OpenClaw 的 Markdown 记忆：

- `MEMORY.md`：稳定长期记忆
- `memory/YYYY-MM-DD.md`：每日流水
- `memory/sessions/<session>.md`：按聊天窗口切分的会话记忆

管理员命令通过同一套记忆结构直接修改会话状态，也可以通过 `/remember` 写入长期记忆。

## 7. 与平台耦合点

当前实现依赖 macOS：

- Quartz：窗口枚举
- AppleScript：激活应用、粘贴发送
- pyautogui：鼠标键盘自动化
