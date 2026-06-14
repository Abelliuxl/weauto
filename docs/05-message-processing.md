# 消息接收与回复流程

## 1. Detached 主循环

推荐运行路径是 `WeChatGuiRpaBot.run_detached_window_forever()`：

```text
枚举窗口
  -> 按 window_id 截图
  -> Vision 或 OCR 解析
  -> 规范化发送者和消息
  -> 合并聊天历史
  -> 增量检测新消息
  -> 选择本轮处理消息
  -> 会话队列串行处理
  -> 回复或桥接
  -> GUI 发送
  -> 保存状态
```

不同窗口可以并发处理，同一窗口通过队列保持顺序。

## 2. 窗口枚举与捕获

`detached_window_receiver.py` 使用 macOS Quartz 枚举微信独立窗口。窗口必须：

- 属于 `app_name` 对应的应用
- 有有效 window ID 和尺寸
- 标题通过 `detached_window_title_filter`

截图后端：

- `screencapture`：在短生命周期子进程中捕获，默认路径
- `quartz`：进程内捕获，更快但长期运行可能增长 Mach 内存

## 3. OCR 解析

`VisibleMessageParser` 根据气泡位置、OCR 文本块和图像区域生成消息：

```json
{
  "side": "other",
  "sender": "联系人",
  "content_type": "text",
  "text": "消息内容",
  "bbox": [0, 0, 100, 40]
}
```

文本消息和图片消息都会进入可见消息快照。图片消息可以包含裁剪后的 `image_path` 和
`image_hash`。

## 4. Vision 解析

启用 Vision 后，Bot 只在聊天区图像发生变化时调用视觉模型。视觉输出会被规范化成与
本地 OCR 相同的消息结构。

处理策略：

1. 计算聊天区稳定 hash
2. hash 未变化时跳过模型请求
3. 调用 Vision 输出消息 JSON
4. 规范化 `self` / `other`、sender 和 content type
5. 失败且 `fail_open=true` 时回退 OCR
6. 失败且 `fail_open=false` 时跳过该窗口本轮处理

## 5. 增量检测与恢复

`VisibleMessageStateStore` 为每个 window ID 保存上一轮可见消息。它会：

- 返回上一轮尾部之后的新消息
- 忽略滚动后出现在旧尾部之前的历史消息
- 在尾部锚点消失时重新同步
- 保留锚点丢失时新出现的 incoming 尾消息

状态会写入 `data/runtime_state.json`。watchdog 重启后，Bot 可以恢复窗口消息基线，减少
重启造成的重复处理。

## 6. 启动基线

默认：

```toml
detached_process_existing_on_start = false
```

第一次扫描只建立窗口基线，不处理已经显示的消息。之后出现的新消息才进入处理队列。

如果启用该字段，启动时的可见 incoming 消息也可能被处理，风险更高。

## 7. 批处理与冷却

同一轮出现多条消息时：

- mention 消息会保留
- 普通群消息通常只选择最新一条
- 冷却期间的普通群消息可被丢弃
- 同一窗口正在处理时，新消息进入该窗口队列

`action_cooldown_sec` 控制同一会话动作间隔，
`normal_reply_interval_sec` 控制普通消息的全局节流。

## 8. 群聊判断

群聊规则综合使用：

- 会话标题前缀
- sender 前缀
- mention 标记
- `mention_keywords`
- `group_reply_keywords`
- `group_only_reply_when_mentioned`
- `group_require_sender_prefix_for_new_message`

群聊中可允许 LLM 使用 `[NO_REPLY]` 主动跳过。私聊和明确 mention 通常视为更高优先级事件。

## 9. Native 回复 pipeline

`native` 模式的典型顺序：

1. 构造会话、环境、人格、技能和记忆上下文
2. 可选执行 `llm_decision`
3. 调用 planner 规划工具和回复策略
4. 按预算执行 Agent tools
5. 将 observation 回传 planner，最多多轮
6. 调用 `llm_reply` 生成最终文本
7. 执行防复读检查
8. 识别 `[NO_REPLY]`
9. 发送文本或工具生成的文件
10. 更新会话历史、摘要和 runtime state

Planner 失败时是否继续，由 `agent_actions_fail_open` 决定。

## 10. Bridge 回复 pipeline

`bridge` 和 `long_bridge` 模式在消息接收后优先把事件交给外部系统。外部系统可以：

- 返回文本
- 指示不发送
- 长桥接中返回一个或多个附件

桥接失败时，`bridge_fail_open` 或 `long_bridge_fail_open` 决定是否回到 native pipeline。

## 11. 图片消息与图片工具

图片相关行为分三类：

- 收到图片：解析为图片消息，可选 Vision 描述
- 生成图片：`generate_image` 创建文件并尝试发送
- 编辑图片：`edit_image` 使用指定图片或当前会话最近图片

`detached_reply_on_image=false` 时，纯图片消息不会自动触发普通回复。

## 12. GUI 发送

发送层使用：

- 激活微信
- 聚焦目标独立窗口
- 剪贴板写入文本或文件
- AppleScript 触发粘贴
- `pyautogui` 控制鼠标和按键

`dry_run=true` 时保留检测、决策和生成过程，但跳过实际发送。GUI 发送无法提供微信内部的
可靠送达确认，因此日志中的“发送”表示自动化动作已执行。
