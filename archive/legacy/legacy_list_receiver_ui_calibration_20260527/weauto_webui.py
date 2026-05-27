#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
ACTION_LOG = LOG_DIR / "weauto-webui-actions.log"
HOST = "127.0.0.1"
PORT = 8765

SCRIPTS = {
    "rows": "./carlibrate_rows.sh",
    "chat_context": "./carlibrate_chat_context.sh",
    "title_group": "./carlibrate_title_group.sh",
    "title_private": "./carlibrate_title_private.sh",
    "preview": "./carlibrate_preview.sh",
    "unread": "./carlibrate_unread.sh",
    "debug_click": "./debug_click.sh",
    "debug_preview": "./debug_preview.sh",
    "debug_unread": "./debug_unread.sh",
    "debug_heartbeat": "./debug_heartbeat.sh",
}


def run_cmd(cmd: str, timeout: int = 180) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        shell=True,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    out = proc.stdout or ""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with ACTION_LOG.open("a", encoding="utf-8", errors="ignore") as f:
        f.write(f"$ {cmd}\n")
        f.write(out)
        if not out.endswith("\n"):
            f.write("\n")
        f.write(f"[exit={proc.returncode}]\n")
    return proc.returncode, out


def status_text() -> str:
    return "ready"


def read_log_chunk(path: Path, pos: int) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    size = path.stat().st_size
    if pos < 0 or pos > size:
        pos = 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(pos)
        chunk = f.read()
        pos = f.tell()
    return pos, chunk


HTML = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>WeAuto Web UI</title>
  <style>
    body { font-family: Menlo, ui-monospace, monospace; margin: 0; padding: 16px; background:#f6f8fb; }
    .row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    button { padding:8px 12px; border:1px solid #c9d2e0; background:white; border-radius:8px; cursor:pointer; }
    .panel { background:white; border:1px solid #d9e1ee; border-radius:10px; padding:10px; margin-top:10px; }
    .logs { display:grid; grid-template-columns:1fr; gap:10px; }
    pre { margin:0; height:420px; overflow:auto; background:#0f172a; color:#dbeafe; border-radius:8px; padding:10px; white-space:pre-wrap; }
    .status { margin-bottom:8px; color:#334155; }
  </style>
</head>
<body>
  <h3>WeAuto Control</h3>
  <div class=\"status\" id=\"status\">status: __STATUS__</div>

  <div class=\"row\">
    <button onclick=\"refreshStatus()\">Refresh Status</button>
    <button onclick=\"clearActionLog()\">Clear Action Log</button>
  </div>

  <div class=\"panel\">
    <div class=\"row\">
      <button onclick=\"runScript('rows')\">Rows</button>
      <button onclick=\"runScript('chat_context')\">Chat Context</button>
      <button onclick=\"runScript('title_group')\">Title Group</button>
      <button onclick=\"runScript('title_private')\">Title Private</button>
      <button onclick=\"runScript('preview')\">Preview</button>
      <button onclick=\"runScript('unread')\">Unread</button>
      <button onclick=\"runScript('debug_click')\">Debug Click</button>
      <button onclick=\"runScript('debug_preview')\">Debug Preview</button>
      <button onclick=\"runScript('debug_unread')\">Debug Unread</button>
      <button onclick=\"runScript('debug_heartbeat')\">Debug Heartbeat</button>
    </div>
  </div>

  <div class=\"logs\">
    <div class=\"panel\"><div>Action Output</div><pre id=\"act\"></pre></div>
  </div>

<script>
let actPos = 0;

async function post(url, body = {}) {
  try {
    const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const data = await res.json();
    if (data.output) appendAct('\\n' + data.output);
    await refreshStatus();
  } catch (e) {
    appendAct('\\n[webui-error] ' + e);
    document.getElementById('status').textContent = 'status: offline/error';
  }
}

async function runScript(name) {
  await post('/api/script', {name});
}

async function refreshStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('status').textContent = 'status: ' + data.status;
  } catch (e) {
    document.getElementById('status').textContent = 'status: offline/error';
  }
}

async function pollLogs() {
  try {
    const a = await fetch('/api/log?kind=action&pos=' + actPos).then(r => r.json());
    actPos = a.pos;
    if (a.chunk) appendAct(a.chunk);
  } catch (e) {
    document.getElementById('status').textContent = 'status: offline/error';
  }
}

function appendAct(s) {
  const el = document.getElementById('act');
  el.textContent += s;
  el.scrollTop = el.scrollHeight;
}

async function clearActionLog() {
  await post('/api/clear_action_log');
  document.getElementById('act').textContent = '';
  actPos = 0;
}

refreshStatus();
setInterval(refreshStatus, 5000);
setInterval(pollLogs, 800);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, code: int, text: str) -> None:
        raw = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/":
            self._html(200, HTML.replace("__STATUS__", status_text()))
            return
        if u.path == "/api/status":
            self._json(200, {"status": status_text()})
            return
        if u.path == "/api/log":
            q = parse_qs(u.query)
            pos_raw = (q.get("pos") or ["0"])[0]
            try:
                pos = int(pos_raw)
            except ValueError:
                pos = 0
            next_pos, chunk = read_log_chunk(ACTION_LOG, pos)
            self._json(200, {"pos": next_pos, "chunk": chunk})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        size = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(size) if size > 0 else b"{}"
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            data = {}

        if u.path == "/api/script":
            name = str(data.get("name", "")).strip()
            cmd = SCRIPTS.get(name)
            if not cmd:
                self._json(400, {"ok": False, "output": f"unknown script: {name}"})
                return
            code, out = run_cmd(cmd)
            self._json(200, {"ok": code == 0, "output": out})
            return
        if u.path == "/api/clear_action_log":
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            ACTION_LOG.write_text("", encoding="utf-8")
            self._json(200, {"ok": True, "output": "action log cleared"})
            return
        self._json(404, {"error": "not found"})


def main() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not ACTION_LOG.exists():
        ACTION_LOG.write_text("", encoding="utf-8")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[webui] http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
