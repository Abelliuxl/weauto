# 调试与故障排查

## 1. 调试命令总表

### 1.1 会话行点击调试

```bash
./debug_click.sh config.toml --dry-run --cycles 1
```

主要参数：

- `--cycles`：循环次数（默认 1）
- `--top-n`：只测前 N 行
- `--scan-retries`：空扫描重试次数
- `--click-delay-sec`：每次点击间隔
- `--dry-run`：只打印不点击

### 1.2 preview 区域点击调试

```bash
./debug_preview.sh config.toml --dry-run --cycles 1
```

主要参数与 `debug_click.sh` 一致。

### 1.3 未读红点调试

```bash
./debug_unread.sh config.toml --dry-run --cycles 1
```

附加参数：

- `--do-click`：实际点击红点坐标
- 默认不加 `--do-click` 时只移动鼠标（更安全）

输出会包含：

- `red_ratio=...`
- `threshold=0.333`
- `hit=True/False`

## 2. 常见日志标签说明

| 日志标签 | 含义 |
|---|---|
| `[scan-empty]` | 本轮没有识别到任何会话行 |
| `[skip-title]` | 会话标题命中忽略关键词 |
| `[skip-group-prefix]` | 群聊新消息未满足发送者前缀要求 |
| `[skip-muted]` | 会话被静音 |
| `[skip-self-preview]` | 预览变化被判定为自己消息回显 |
| `[skip-self-latest]` | 聊天最后一条是自己消息，触发跳过 |
| `[llm-repeat]` | 生成回复与近期回复过于相似，触发重试 |
| `[warn] vision parse failed, fallback ocr` | Vision 失败，已回退 OCR |
| `[warn] llm decision failed, skip reply` | LLM 分流失败，本次不回复（受 `decision_fail_open` 影响） |

## 3. 问题定位手册

### 3.1 报错“窗口找不到”

现象：`WindowNotFoundError`。

排查：

1. 微信窗口必须在前台且可见
2. `app_name` 是否匹配（可用 `WeChat|微信`）
3. 系统权限是否授予（屏幕录制/辅助功能）

### 3.2 总是扫描不到行（`rows=0`）

先做：

1. 重新跑 `./carlibrate_rows.sh config.toml`
2. 打开 `debug_scan = true`
3. 运行 `./debug_click.sh config.toml --dry-run --scan-retries 10`

重点检查：

- `use_manual_row_boxes` 是否为 `true`
- `manual_row_boxes_path` 是否正确
- 微信窗口布局是否与校准时一致

### 3.3 点击位置偏移

现象：点击到错误会话或错点空白。

处理：

1. 重跑行框校准
2. 用 `./debug_click.sh ... --dry-run` 看每行坐标
3. 若输入框偏移，调 `[input_point]`

### 3.4 标题匹配失败导致不发送

现象：日志出现 `skip-focus`。

处理：

1. 重跑 `./carlibrate_title_group.sh config.toml` 与 `./carlibrate_title_private.sh config.toml`
2. 调大 `focus_verify_max_clicks`
3. 适当增加 `focus_verify_wait_sec` 和 `post_select_wait_sec`

### 3.5 未读识别不稳定

处理顺序：

1. 重跑 `./carlibrate_unread.sh config.toml`
2. 用 `./debug_unread.sh ... --dry-run` 观察 `red_ratio`
3. 若关闭圆形模式，则调整 `[unread_badge].min_blob_pixels`

### 3.6 preview 变化误触发或漏触发

处理：

1. 重跑 `./carlibrate_preview.sh config.toml`
2. 需要时关闭 `trigger_on_preview_change`
3. 观察日志里 `skip-self-preview`/`self_echo` 相关信息

### 3.7 群聊不回复

依次确认：

1. 是否命中 `ignore_title_keywords`
2. 是否命中 `group_only_reply_when_mentioned`
3. `group_require_sender_prefix_for_new_message` 是否过严
4. LLM 分流是否返回 `skip`

### 3.8 LLM 不工作

检查项：

1. `[llm].enabled = true`
2. `api_key` 或 `api_key_env` 是否可用
3. `base_url` 是否可访问（OpenAI 兼容 `/chat/completions`）
4. 查看日志是否有 `http error` / `network error`

### 3.9 Vision 导致流程中断

说明：

- 当 `vision.fail_open = true`：Vision 失败会自动回退 OCR
- 当 `vision.fail_open = false`：Vision 异常会抛出，可能中断主流程

建议：

- 先保持 `vision.fail_open = true`
- Vision 稳定后再考虑改为严格模式

## 4. 安全调试建议

1. 永远先 `dry_run=true`
2. 调试脚本先加 `--dry-run`
3. 只在确认坐标准确后再去掉 `dry_run`
4. 保持微信窗口大小稳定，减少重新校准频率
