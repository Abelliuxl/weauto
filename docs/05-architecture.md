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
  - 分流决策、回复生成、摘要更新
  - Vision 图片解析与 JSON 纠错

- `wechat_rpa/bot.py`
  - 主循环状态机
  - 事件挑选、过滤、上下文提取、回复执行、记忆管理

## 2. 运行时数据流

1. `run.py` 加载 `config.toml`
2. `bot.run_forever()` 轮询窗口截图
3. `detector.detect_chat_rows()` 产出 `ChatRowState[]`
4. `bot._pick_event()` 挑选当前要处理的一条事件
5. 必要时读取聊天上下文（OCR/Vision）
6. 可选 LLM 分流 `reply/skip`
7. 回复生成（LLM 或模板）
8. GUI 点击输入并发送
9. 更新 baseline、会话记忆和持久化文件

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
2. 分流层：`llm.should_reply()`（可选）
3. 生成层：`llm.generate()` 或固定模板
4. 防复读层：相似度比对 + 重试
5. 发送层：点击输入框 + 粘贴发送

## 6. 记忆结构

记忆文件 `data/session_memory.json` 主要字段：

- `sessions.<key>.short`：短期记忆
- `sessions.<key>.summary`：长期摘要
- `sessions.<key>.muted`：静音状态
- `aliases`：会话 key 别名映射

管理员命令通过同一套记忆结构直接修改会话状态。

## 7. 与平台耦合点

当前实现依赖 macOS：

- Quartz：窗口枚举
- AppleScript：激活应用、粘贴发送
- pyautogui：鼠标键盘自动化

