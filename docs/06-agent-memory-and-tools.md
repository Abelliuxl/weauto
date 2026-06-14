# Agent、记忆、人物与技能

## 1. Prompt 上下文

`prompt_context.py` 从 `data/config/` 读取：

- `AGENTS.md`
- `SOUL.md`
- `IDENTITY.md`
- `USER.md`
- `TOOLS.md`
- `SKILLS.md`

这些文件共同定义 Agent 的身份、语气、用户背景、工具原则和技能使用规则。它们会被裁剪后
注入 LLM 上下文。

`data/identity.md` 还会作为 heartbeat 身份上下文的一部分读取。

## 2. 技能选择

技能位于：

```text
data/skills/<skill-name>/SKILL.md
```

系统也兼容 `data/skills/*.md`。技能选择会根据：

- 标题
- 摘要
- 关键词 / 触发词
- 内容中的 token
- 当前消息与技能的粗略相似度

默认只注入少量最相关技能，避免所有技能同时占用上下文。

## 3. 记忆层次

### 会话状态

每个会话维护：

- 短期记忆
- 完整 history
- 长期摘要
- muted 状态
- 最近发送和 Agent 任务状态

索引位于 `data/chat_history/_index.json`，部分状态写入同目录下的会话状态文件。

### 聊天正文

消息正文按日期写入：

```text
data/chat_history/<session>/YYYY-MM-DD.txt
```

格式：

```text
[14:03:12] U(发送者): 用户消息
[14:03:20] A: 助手回复
```

### 长期记忆

```text
data/memory/core.md
data/memory/timeline.md
```

- `core` 保存相对稳定的事实和偏好
- `timeline` 保存带日期的事件

追加工具会去重、合并近似内容并限制追加区长度。

### 人物印象

```text
data/people/<name>.md
```

人物名称先经过 `PersonAliasResolver` 规范化。别名来源为
`data/config/PEOPLE_ALIASES.md`。

仓库中还保留了 `data/config/MENTION_ALIASES.md`，但当前 Python 运行链路没有加载它。
提及名称的归一化仍以 `PEOPLE_ALIASES.md` 为准；前者只能视为人工参考或兼容遗留文件。

## 4. Planner 可用工具

实际暴露给 planner 的工具会根据配置和 provider 可用性动态变化。

### 记忆

- `read_memory`
- `recall_memory`
- `remember_fact`
- `write_memory`

优先使用追加式 `remember_fact`。`write_memory` 会完整覆盖文件，适合明确的重写任务。

### 人物

- `read_impression`
- `update_impression`
- `write_impression`

优先使用 `update_impression`，它会保留已有记录并合并新观察。

### 技能

- `list_skills`
- `read_skill`
- `update_skill`
- `write_skill`
- `delete_skill`

技能写入前会备份现有 `SKILL.md`。

### 聊天历史

- `read_chat_history`
- `read_chat_history_by_date`
- `summarize_chat_history`
- `search_chat_history`

按日读取适合复盘指定日期，搜索适合定位关键词，summary 工具适合提取时间段和发送者分布。

### 计算和网络

- `run_python`
- `fetch_url`
- `browse_url`
- `web_search`

`fetch_url` 遇到部分 403 或特定动态站点时会回退 `browse_url`。`browse_url` 使用 Playwright。

### 可选能力

- `query_weather`
- `generate_image`
- `edit_image`
- `build_wow_character_url`

只有对应服务或本地 skill 可用时才向 planner 暴露。

### 管理员能力

- `mute_session`
- `unmute_session`

只有 `admin_session_titles` 中的会话可执行。

## 5. Planner 循环

Agent planner 支持多轮执行：

```text
规划
  -> 执行工具
  -> 收集 observation
  -> 再规划
  -> 最终回复
```

限制由以下字段控制：

- 每轮动作数
- 最大规划轮数
- 总动作预算
- 相同 tool + args 重复上限
- observation 注入长度
- 单次最多发送消息数

这些限制用于防止工具循环、重复调用和过长上下文。

## 6. Heartbeat

Heartbeat 在达到间隔且系统空闲时运行。当前可用工具主要包括：

- 读取、检索和追加长期记忆
- 读取和更新人物印象
- 读取、按日读取、总结和搜索聊天历史

它会从最近聊天中选择有限数量的人物进行维护。`heartbeat_max_actions` 是单次内部动作总预算。

Heartbeat 不等于定时发消息；它的主要责任是维护本地知识。

## 7. 管理员命令

管理员会话可发送：

```text
/help
/?
/sessions
/mute 会话名
/unmute 会话名
/reset 会话名
/merge 源会话 -> 目标会话
```

命令由 `admin_command_prefix` 和 `admin_session_titles` 控制。

当前代码仍会识别 `/remember`，但处理器只返回确认文本，并没有把内容写入长期记忆。
因此不要把它当作有效的持久化命令；需要写入记忆时，应让 agent 调用记忆工具，或直接
维护对应的 Markdown 文件。

## 8. 数据编辑原则

- 不要同时手工编辑正在被 Bot 写入的状态 JSON
- 手工编辑 Markdown 人格、技能和记忆后无需重启，HotFile 会按 mtime 重新读取
- 批量改人物名称前先更新 `PEOPLE_ALIASES.md`
- 完整覆盖工具的风险高于追加工具
- 对个人数据做迁移或清理前先停止 Bot 并备份 `data/`
