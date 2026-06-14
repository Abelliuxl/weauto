# 数据、日志与维护

## 1. 数据目录

```text
data/
  config/                    Agent 人格与行为配置
  skills/                    本地技能
  memory/                    core.md、timeline.md
  people/                    人物印象
  chat_history/              会话索引、状态和按日消息
  detached_window_images/    调试截图和消息图片
  generated_images/          生成图片与 history.jsonl
  edited_images/             编辑图片与 history.jsonl
  long_bridge/received/      长桥接收到的附件
  secrets/                   私钥等本地秘密
  runtime_state.json         运行恢复状态
  identity.md                heartbeat 补充身份
```

部分目录按功能启用后才会创建。

## 2. 会话索引与聊天记录

`data/chat_history/_index.json` 保存：

- canonical session key
- 标题和别名
- 会话目录
- muted 状态
- 摘要和更新时间
- 会话状态文件引用

聊天正文位于：

```text
data/chat_history/<session>/YYYY-MM-DD.txt
```

Web UI 直接读取这套存储，不实例化 Bot。

## 3. Runtime state

`data/runtime_state.json` 用于保存：

- 最近启动、活动、回复和 heartbeat 时间
- detached 可见消息状态
- watchdog 恢复标记

它不是用户配置文件。手工修改可能破坏恢复基线。

## 4. 日志

控制面板和 `start_rpa.sh` 都生成：

```text
logs/rpa_YYYYmmdd_HHMMSS.log
logs/issues.log
```

- `rpa_*.log`：完整 stdout
- `issues.log`：warn、error、skip 类日志

`start_rpa.sh` 默认保留最多 40 个日志文件和 512 MB，可通过：

- `LOG_KEEP_MAX_FILES`
- `LOG_KEEP_MAX_TOTAL_MB`

控制面板还在内存中维护 SSE 环形缓冲，大小由 `webui.log_ring_lines` 控制。

## 5. 调试截图

启用：

```toml
detached_debug_save = true
```

会在 `detached_window_output_dir` 下保存窗口图和解析 JSON。长期启用会持续写磁盘，问题定位
完成后应关闭。

独立诊断工具：

```bash
python debug_detached_windows.py --config config.toml
python debug_detached_windows.py --watch --debug --title "会话标题"
```

默认输出到 `~/Downloads/weauto_detached_windows_<timestamp>/`。

## 6. 当前维护脚本

### 压缩记忆与人物追加区

```bash
python scripts/compact_memory_notes.py --dry-run
python scripts/compact_memory_notes.py
```

写入模式会先备份到 `data/.backup/compact-memory-<timestamp>/`。

### 聊天记录去重

```bash
python scripts/dedup_chat_history.py
```

它按 `(sender, text)` 删除同一天文件中的重复行。脚本直接改文件，运行前应停止 Bot 并备份。

### 修复错误发送者

```bash
python scripts/fix_corrupted_senders.py
```

它根据 `PEOPLE_ALIASES.md` 修复 OCR 把群名识别成发送者的部分记录。脚本会直接改按日文本。

## 7. 迁移与历史脚本

以下脚本面向旧数据格式，不属于日常维护：

- `scripts/migrate_to_date_files.py`
- `scripts/clean_tool_history.py`

`clean_tool_history.py` 仍指向旧的 `data/session_memory.json` 布局。当前日期文件存储不应直接
使用它。

旧实现和历史资料位于 `archive/legacy/`。

## 8. App 图标

图标生成脚本：

```bash
python scripts/generate_app_icon.py
```

它更新 `WeAuto.app/Contents/Resources/` 中的 PNG 和 ICNS。需要 macOS 的 `iconutil`。

## 9. 备份建议

至少备份：

- `config.toml`
- `.env.weauto`
- `data/config/`
- `data/memory/`
- `data/people/`
- `data/skills/`
- `data/chat_history/`
- `data/secrets/`

日志和生成图片是否备份取决于用途。

## 10. 隐私与版本控制

聊天历史、人物印象、密钥、私钥和生成内容可能包含敏感信息。提交前检查：

```bash
git status --short
git diff --cached
```

不要依赖文件名判断隐私，实际检查 staged 内容。
