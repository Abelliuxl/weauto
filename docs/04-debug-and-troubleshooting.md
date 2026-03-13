# 调试与故障排查

## 1. 常用调试命令

### 1.1 行点击

```bash
./debug_click.sh config.toml --dry-run --cycles 1
```

常用参数：`--top-n`、`--scan-retries`、`--scan-retry-wait-sec`、`--click-delay-sec`。

### 1.2 preview 区域点击

```bash
./debug_preview.sh config.toml --dry-run --cycles 1
```

### 1.3 未读圆点

```bash
./debug_unread.sh config.toml --dry-run --cycles 1
```

可选：`--do-click`（默认只移动鼠标，不点击）。

输出重点：`red_ratio`、`threshold=0.333`、`hit=True/False`。

### 1.4 planner 调试

```bash
./debug_planner.sh config.toml "帮我查一下今天魔兽更新"
```

用于单独检查 `plan_actions` 输出是否包含合理 `actions/reply_hint`。

### 1.5 记忆召回调试

```bash
./debug_rerank.sh config.toml "查询词"
./debug_memory_sqlite.sh config.toml "查询词" "session_key"
```

### 1.6 heartbeat 调试

```bash
./debug_heartbeat.sh config.toml --show-tasks
```

可加：`--safe`（只探测，不执行维护动作）。

## 2. 关键日志标签

| 标签 | 含义 |
|---|---|
| `[skip-title]` | 标题命中忽略关键词 |
| `[skip-group-prefix]` | 群聊 `new_message` 未满足 sender 前缀约束 |
| `[skip-muted]` | 会话静音中 |
| `[skip-self-preview]` | 预览变化被判定为自己消息回显 |
| `[skip-self-latest]` | 最新聊天消息是自己，触发跳过 |
| `[skip-normal-interval]` | 命中全局 normal 回复节流 |
| `[skip-focus]` | 会话 focus 标题校验失败 |
| `[focus-ok]/[focus-retry]/[focus-fail]` | 会话 focus 过程状态 |
| `[reply-repeat]` | 防复读命中，触发重试 |
| `[warn] vision parse failed: ...` | Vision 解析失败（`fail_open=true` 时继续流程） |
| `[warn] llm decision failed, skip reply` | 分流失败且 fail-close，跳过回复 |
| `[memory-sqlite] synced ...` | SQLite 记忆索引完成增量同步 |
| `[memory-rerank] backend=...` | 记忆重排链路运行状态 |

说明：`[scan-empty]` 主要出现在 `debug_click_*` 脚本，不是主循环固定日志标签。

## 3. 常见问题

### 3.1 窗口找不到

现象：`WindowNotFoundError`。

排查：

1. 微信窗口必须可见
2. `app_name` 是否匹配（可配 `WeChat|微信`）
3. 权限是否授予（辅助功能 + 屏幕录制）

### 3.2 rows=0 或识别不稳定

建议：

1. 重跑 `./carlibrate_rows.sh config.toml`
2. 打开 `debug_scan=true`
3. 用 `./debug_click.sh ... --scan-retries 10` 验证
4. 确认窗口大小与校准时一致

### 3.3 点击偏移

建议：

1. 重跑行框校准
2. 用 `debug_click.sh --dry-run` 校对每行点击坐标
3. 若发送点偏移，重新调 `[input_point]`

### 3.4 focus 一直失败

现象：大量 `[skip-focus]`。

建议：

1. 重跑 `carlibrate_title_group/private`
2. 增大 `focus_verify_max_clicks`
3. 增大 `post_select_wait_sec` 和 `focus_verify_wait_sec`

### 3.5 未读识别不准

建议顺序：

1. 重跑 `./carlibrate_unread.sh config.toml`
2. 用 `debug_unread.sh` 观察 `red_ratio`
3. 若不用圆形模式，再调 `[unread_badge].min_blob_pixels`

### 3.6 群聊不回/乱回

依次检查：

1. `group_only_reply_when_mentioned`
2. `group_require_sender_prefix_for_new_message`
3. `group_reply_keywords`
4. `ignore_title_keywords`
5. `decision_*` 开关和日志

### 3.7 LLM 或 Vision 不工作

检查：

1. 对应 `enabled=true`
2. `base_url/api_key/model` 是否有效
3. 若走环境变量，`*_env` 指向变量是否存在
4. 查看日志中 `http error/network error/timeout`

### 3.8 heartbeat 不触发

检查：

1. `heartbeat_enabled=true`
2. 空闲是否达到 `heartbeat_min_idle_sec`
3. `HEARTBEAT.md` 是否有可执行内容
4. 先用 `./debug_heartbeat.sh ... --show-tasks` 验证

## 4. 安全调试建议

1. 所有调试先加 `--dry-run`
2. 主程序先用 `dry_run=true`
3. 坐标确认后再开启真实发送
4. 尽量固定微信窗口尺寸和位置
