# OpenClaw weauto channel

This plugin exposes `/weauto/channel` on the OpenClaw Gateway and accepts a
persistent, authenticated WebSocket from a remote weauto instance.

Remote OpenClaw configuration:

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

Install and restart the remote Gateway:

```bash
npm install --omit=dev ./openclaw-weauto-channel
openclaw plugins install ./openclaw-weauto-channel
openclaw gateway restart
```

weauto configuration:

```toml
processing_mode = "long_bridge"
long_bridge_url = "ws://REMOTE_OPENCLAW_HOST:18789/weauto/channel"
long_bridge_token_env = "WEAUTO_LONG_BRIDGE_TOKEN"
long_bridge_account_id = "default"
```

The token must match on both sides. Keep it in each host's secret environment
or encrypted configuration rather than committing it.
