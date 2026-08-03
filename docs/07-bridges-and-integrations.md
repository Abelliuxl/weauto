# 桥接与外部集成

## 1. 模式选择

| 目标 | 模式 |
|---|---|
| 全部在 WeAuto 内处理 | `native` |
| 每条消息请求一个 HTTP 服务 | `bridge` + `http` |
| 调用现有 OpenClaw Gateway agent | `bridge` + `openclaw` |
| 将 WeAuto 作为 OpenClaw 持久 channel | `long_bridge` |

桥接模式只替换回复生成位置，不替换本地窗口接收和 GUI 发送。

## 2. HTTP bridge

```toml
processing_mode = "bridge"
bridge_backend = "http"
bridge_url = "http://127.0.0.1:8766/weauto/reply"
bridge_api_key_env = "WEAUTO_BRIDGE_API_KEY"
bridge_timeout_sec = 120.0
bridge_fail_open = true
```

WeAuto 使用 POST 发送标准化 JSON，内容包括：

- event schema 和 event ID
- 会话标题、类型和 session key
- 最新消息、发送者和 content type
- 当前聊天、环境、会话、workspace 和记忆上下文
- 图片路径与 image hash 元数据，不传图片二进制

需要把图片内容直接交给远端 agent 时，应使用 `long_bridge`；它支持 base64 附件。

HTTP 服务可以返回：

```json
{
  "reply": "最终回复",
  "send": true
}
```

也兼容 `message`、`text`、`content`、`answer`、`replies` 和 OpenAI-style choices。
`send=false` 表示不发送。

设置 API key 后，请求使用：

```text
Authorization: Bearer <token>
```

## 3. OpenClaw 短桥接

```toml
processing_mode = "bridge"
bridge_backend = "openclaw"
bridge_openclaw_gateway_url = "ws://127.0.0.1:18789"
bridge_openclaw_gateway_token_env = "OPENCLAW_GATEWAY_TOKEN"
bridge_openclaw_agent_id = "main"
bridge_openclaw_session_prefix = "agent:main:weauto"
bridge_openclaw_thinking = "off"
```

WeAuto 调用：

```text
openclaw gateway call agent --json ...
```

每次消息是一次 CLI / Gateway 请求。session key 根据 WeAuto 会话派生，使 OpenClaw 能维持
对应会话上下文。

`bridge_openclaw_strip_proxy_env=true` 会在子进程中移除代理变量，并补充本地网段到
`NO_PROXY`。

## 4. OpenClaw 持久 channel

### WeAuto 侧

```toml
processing_mode = "long_bridge"
long_bridge_url = "ws://REMOTE_HOST:18789/weauto/channel"
long_bridge_token_env = "WEAUTO_LONG_BRIDGE_TOKEN"
long_bridge_account_id = "default"
long_bridge_timeout_sec = 180.0
long_bridge_attachment_max_mb = 20
long_bridge_fail_open = true
```

客户端特性：

- Bearer token 认证
- account ID query 和 header
- hello / ready 握手
- ping / pong heartbeat
- 指数退避重连
- request ID 关联
- 文本和 base64 附件
- 远端主动下行消息
- detached 会话目录同步（稳定 ID + 精确窗口标题）
- 主动发送的最终投递回执；窗口未确认时 fail-closed

主动发送分为两级确认：`message.received` 只表示帧已到达 WeAuto，
`message.delivered` 才表示目标窗口已经确认且 GUI 发送动作已经执行。远端插件必须等待
后者；写入 OpenClaw session 或获得 message ID 不能视为微信投递成功。

收到的远端附件保存到：

```text
data/long_bridge/received/<request-id>/
```

### OpenClaw 侧

插件目录：

```text
openclaw-weauto-channel/
```

安装：

```bash
cd openclaw-weauto-channel
npm install --omit=dev
openclaw plugins install .
openclaw gateway restart
```

OpenClaw 配置：

```json5
{
  channels: {
    weauto: {
      enabled: true,
      token: "${WEAUTO_CHANNEL_TOKEN}",
      attachmentMaxMb: 20
    }
  }
}
```

两端 token 必须一致。插件支持多 account 配置。

## 5. Fail-open

桥接失败行为：

- `*_fail_open=true`：记录错误并回退本地 native pipeline
- `*_fail_open=false`：跳过本地回复

如果外部系统是唯一权威回复源，应使用 fail-closed；如果可接受本地兜底，可使用 fail-open。

## 6. 聚合网页搜索

`web_search` 同时调用已启用的：

- Tavily
- Brave
- Volcengine Ark

各 provider 并行执行，失败的一路保留空分区，不阻断其他结果。Planner 只看到统一的
`web_search` 工具。

`fetch_url` 用标准 HTTP 抓取，`browse_url` 用 Playwright 处理动态页面。

仓库根目录的 `mcporter.json` 和 `agent_reach_*` 实现保留了 Exa MCP / Agent Reach
兼容路径。当前 planner 暴露的聚合搜索不调用这条路径；除非自行接入，不应把它视为
默认搜索后端。

## 7. QWeather

`query_weather` 支持：

- 地点搜索
- 实况天气
- 多日预报
- 指定日期或 day offset
- JWT 或 API key 认证

目标日期超过 30 天或位于过去时会拒绝查询。

## 8. 图片服务

### DashScope

内置图像生成和编辑客户端：

- Z-Image 生成
- Qwen Image Edit
- 自动下载或解码图片
- 输出历史 JSONL
- 生成后通过微信文件粘贴发送

### ComfyUI bridge

项目还包含独立的 OpenAI-compatible ComfyUI bridge：

```bash
export COMFY_WORKFLOW_PATH=/absolute/path/to/workflow_api.json
./start_comfy_bridge.sh
```

常用环境变量：

- `COMFYUI_BASE_URL`
- `BRIDGE_HOST`
- `BRIDGE_PORT`
- `BRIDGE_API_KEY`
- `BRIDGE_OUTPUT_DIR`
- `COMFY_WORKFLOW_PATH`
- `COMFY_TIMEOUT_SEC`
- `COMFY_MAX_WAIT_SEC`
- `COMFY_*_NODE_IDS`

它是独立服务，不会随 `WeAuto.app` 自动启动。
