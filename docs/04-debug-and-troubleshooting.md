# 调试与故障排查

## 1. 关键日志标签

| 标签 | 含义 |
|---|---|
| `[skip-muted]` | 会话静音中 |
| `[skip-normal-interval]` | 命中全局 normal 回复节流 |
| `[reply-repeat]` | 防复读命中，触发重试 |
| `[warn] vision parse failed` | Vision 解析失败（`fail_open=true` 时继续） |
| `[warn] llm decision failed, skip reply` | 分流失败且 fail-close，跳过回复 |
| `[warn] agent action planner failed` | 工具规划 JSON 解析失败（fail-open） |

## 2. 常见问题

### 2.1 检测不到窗口

现象：无任何事件触发。

排查：
1. 微信聊天窗口必须处于 detach 状态（拖出主窗口成为独立窗口）
2. `app_name` 是否匹配微信进程名
3. 权限是否授予（辅助功能 + 屏幕录制）
4. 确认 `receiver_mode = "detached_windows"`

### 2.2 消息漏读或重复

建议：
1. 检查 `visible_message_state` 去重逻辑是否正常
2. 适当降低 `poll_interval_sec` 提高捕获频率
3. 查看 `detached_debug_save` 输出的截图

### 2.3 群聊不回/乱回

依次检查：
1. `group_only_reply_when_mentioned`
2. `group_reply_keywords`
3. `ignore_title_keywords`
4. `decision_*` 开关和日志

### 2.4 LLM 或 Vision 不工作

检查：
1. 对应 `enabled=true`
2. `base_url/api_key/model` 是否有效
3. 若走环境变量，`*_env` 指向变量是否存在
4. 查看日志中 `http error/network error/timeout`

### 2.5 Heartbeat 不触发

检查：
1. `heartbeat_enabled=true`
2. 空闲是否达到 `heartbeat_min_idle_sec`
3. `llm_heartbeat` 是否配置正确

### 2.6 图片生成/编辑失败

检查：
1. `[image_generation]` / `[image_editing]` 的 `enabled=true`
2. `base_url/api_key/model` 是否有效
3. `output_dir` 是否可写入

## 3. 调试建议

1. 先用 `dry_run=true` 观察行为
2. 开启 `log_verbose=true` 获取详细日志
3. 确认后再切 `dry_run=false`
