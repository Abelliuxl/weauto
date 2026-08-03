# OpenClaw weauto channel

This plugin exposes `/weauto/channel` on the OpenClaw Gateway and accepts a
persistent, authenticated WebSocket from a remote weauto instance.

## Source of truth

The canonical source is this `openclaw-weauto-channel/` directory in the
[`Abelliuxl/weauto`](https://github.com/Abelliuxl/weauto) repository. A copy
under an OpenClaw host's `~/.openclaw/extensions/weauto` is a deployed runtime
artifact, not a separate Git checkout. Make changes and run tests here first,
then deploy the tested files to the OpenClaw host.

Keeping the plugin with weauto lets protocol changes to the Python client and
the JavaScript channel be reviewed, tested, and released atomically.

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

## Proactive delivery safety

After the WebSocket handshake, weauto synchronizes the currently available
detached conversations as stable IDs plus exact window titles. Proactive sends
are rejected when the target is absent from that directory; target text is
never used as an unverified window title.

`sendText` and `sendMedia` wait for a `message.delivered` receipt from weauto.
Writing the frame to the WebSocket or mirroring it into an OpenClaw session is
not treated as successful delivery. A missing target, GUI focus failure,
disconnect, or receipt timeout is returned to the OpenClaw message tool as an
error.
