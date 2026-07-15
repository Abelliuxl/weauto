#!/bin/bash
set -Eeuo pipefail

WEAUTO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
WEAUTO_ENV="$WEAUTO_ROOT/.env.weauto"
SSH_HOST="${1:-debian-claw}"
TARGET_URL="ws://127.0.0.1:38789/weauto/channel"
PROBE_URL="http://192.168.5.104:18789/weauto/channel"

[[ -f "$WEAUTO_ENV" ]] || {
  echo "Missing WeAuto environment file: $WEAUTO_ENV" >&2
  exit 1
}

TOKEN="$(
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$SSH_HOST" \
    'node -e '\''const fs=require("fs"); const p=process.env.HOME+"/.openclaw/openclaw.json"; const c=JSON.parse(fs.readFileSync(p,"utf8")); const token=c?.channels?.weauto?.token; if(typeof token!=="string" || token.length<32 || token.includes("${")) process.exit(2); process.stdout.write(token)'\'''
)"
if (( ${#TOKEN} < 32 || ${#TOKEN} > 512 )) || \
   [[ "$TOKEN" == *$'\n'* || "$TOKEN" == *$'\r'* ]]; then
  echo "Remote channel token is empty or malformed; refusing to copy it." >&2
  exit 1
fi

WS_ARGS=(--noproxy '*' --http1.1 --max-time 3 -s -o /dev/null -w '%{http_code}'
  -H 'Connection: Upgrade' -H 'Upgrade: websocket'
  -H 'Sec-WebSocket-Version: 13'
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==')
UNAUTH="$(curl "${WS_ARGS[@]}" "$PROBE_URL" 2>/dev/null || true)"
AUTH="$(curl "${WS_ARGS[@]}" -H "Authorization: Bearer $TOKEN" "$PROBE_URL" 2>/dev/null || true)"
if [[ "$UNAUTH" != 401 || "$AUTH" != 101 ]]; then
  echo "debian-claw channel authentication failed ($UNAUTH/$AUTH)." >&2
  exit 1
fi

TMP="$(mktemp "$WEAUTO_ROOT/.env.weauto.debian.XXXXXX")"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

awk '
  !/^[[:space:]]*WEAUTO_LONG_BRIDGE_URL[[:space:]]*=/ &&
  !/^[[:space:]]*WEAUTO_LONG_BRIDGE_TOKEN[[:space:]]*=/
' "$WEAUTO_ENV" > "$TMP"
printf '\n# OpenClaw long bridge target (debian-claw)\n' >> "$TMP"
printf 'WEAUTO_LONG_BRIDGE_URL="%s"\n' "$TARGET_URL" >> "$TMP"
printf 'WEAUTO_LONG_BRIDGE_TOKEN=%q\n' "$TOKEN" >> "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" "$WEAUTO_ENV"
trap - EXIT

echo "WeAuto link synchronized and authenticated successfully."
echo "Host: $SSH_HOST"
echo "URL: $TARGET_URL"
echo "Token: stored in .env.weauto (not displayed)"
