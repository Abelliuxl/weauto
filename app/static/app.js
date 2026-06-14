"use strict";

// ============================================================
// Tab switching
// ============================================================
const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
tabs.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    tabs.forEach((b) => b.classList.toggle("active", b === btn));
    panels.forEach((p) => p.classList.toggle("active", p.id === `tab-${target}`));
    if (target === "logs") maybeStartLogStream();
  });
});

// ============================================================
// Helpers
// ============================================================
function fmtAgo(sec) {
  if (sec == null || isNaN(sec)) return "—";
  if (sec < 5) return "刚刚";
  if (sec < 60) return `${Math.floor(sec)}秒前`;
  if (sec < 3600) return `${Math.floor(sec / 60)}分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}小时前`;
  return `${Math.floor(sec / 86400)}天前`;
}
function fmtDuration(sec) {
  if (sec == null || isNaN(sec) || sec < 0) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
function setText(id, txt) {
  const el = document.getElementById(id);
  if (el) el.textContent = txt;
}
function setCardState(cardId, level) {
  const card = document.getElementById(cardId);
  if (!card) return;
  card.classList.remove("ok", "warn", "err", "info");
  if (level) card.classList.add(level);
}
function setDot(level) {
  const dot = document.getElementById("status-dot");
  if (!dot) return;
  dot.classList.remove("dot-on", "dot-warn", "dot-off", "dot-think");
  dot.classList.add(level);
}

// ============================================================
// Status polling
// ============================================================
let lastStatus = null;
async function pollStatus() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    if (!res.ok) throw new Error(`status ${res.status}`);
    const s = await res.json();
    lastStatus = s;
    renderStatus(s);
  } catch (e) {
    setText("status-text", "连接失败");
    setDot("dot-off");
  }
}
function renderStatus(s) {
  const now = s.now || Date.now() / 1000;
  // Process card
  if (s.running) {
    setText("ov-process", "运行中");
    setText("ov-process-sub", `exit=${s.exit_code == null ? "—" : s.exit_code}`);
    setCardState("card-process", "ok");
  } else {
    setText("ov-process", "已停止");
    const sub = s.last_exit_code != null ? `上次退出码 ${s.last_exit_code}` : "未启动";
    setText("ov-process-sub", sub);
    setCardState("card-process", "err");
  }
  // Uptime card
  setText("ov-uptime", s.running ? fmtDuration(s.uptime_sec) : "—");
  setText("ov-restarts", `重启次数 ${s.restart_count}`);
  // Bridge card
  const bm = s.bridge_mode || "unknown";
  const bs = s.bridge_state || "unknown";
  setText("ov-bridge", bm === "unknown" ? "未知" : bm);
  setText("ov-bridge-sub", bs);
  if (bm === "long_bridge") {
    setCardState("card-bridge", bs === "connected" ? "ok" : "warn");
  } else if (bm === "native" || bm === "bridge") {
    setCardState("card-bridge", "info");
  } else {
    setCardState("card-bridge", "warn");
  }
  // Activity / heartbeat / reply
  const actAgo = s.last_activity_at ? now - s.last_activity_at : null;
  const hbAgo = s.last_heartbeat_at ? now - s.last_heartbeat_at : null;
  const rpAgo = s.last_normal_reply_at ? now - s.last_normal_reply_at : null;
  setText("ov-activity", fmtAgo(actAgo));
  setText("ov-activity-sub", s.last_activity_at ? new Date(s.last_activity_at * 1000).toLocaleTimeString() : "无");
  setText("ov-heartbeat", fmtAgo(hbAgo));
  setText("ov-heartbeat-sub", s.last_heartbeat_at ? new Date(s.last_heartbeat_at * 1000).toLocaleTimeString() : "无");
  setText("ov-reply", fmtAgo(rpAgo));
  setText("ov-reply-sub", s.last_normal_reply_at ? new Date(s.last_normal_reply_at * 1000).toLocaleTimeString() : "无");
  // Meta
  setText("ov-pid", s.pid != null ? s.pid : "—");
  setText("ov-auto-restart", s.auto_restart ? "开" : "关");
  setText("ov-config", s.config_path || "—");
  setText("ov-logfile", s.log_file || "—");
  setText("ov-issuesfile", s.issues_file || "—");
  setText("ov-saved", s.runtime_saved_at ? fmtAgo(now - s.runtime_saved_at) : "—");

  // Top status text + dot
  if (s.running) {
    setDot("dot-on");
    setText("status-text", `运行中 · ${fmtDuration(s.uptime_sec)} · ${bm}/${bs}`);
  } else {
    setDot("dot-off");
    setText("status-text", `已停止 · 退出码 ${s.last_exit_code ?? "—"}`);
  }
}
setInterval(pollStatus, 2000);
pollStatus();

// Clock
function tickClock() {
  setText("clock", new Date().toLocaleTimeString());
}
setInterval(tickClock, 1000);
tickClock();

// ============================================================
// Sessions
// ============================================================
let allSessions = [];
let activeSession = null;

async function loadSessions() {
  try {
    const res = await fetch("/api/sessions", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    allSessions = data.sessions || [];
    renderSessionList();
  } catch (e) {
    /* ignore */
  }
}
function renderSessionList() {
  const ul = document.getElementById("session-list-ul");
  const filter = (document.getElementById("session-filter").value || "").trim().toLowerCase();
  ul.innerHTML = "";
  const items = allSessions.filter((s) => {
    if (!filter) return true;
    return (
      (s.title || "").toLowerCase().includes(filter) ||
      (s.key || "").toLowerCase().includes(filter)
    );
  });
  for (const s of items) {
    const li = document.createElement("li");
    if (activeSession === s.key) li.classList.add("active");
    if (s.muted) li.classList.add("muted");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.title || s.key;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = `${s.message_count}条 · ${fmtAgo(s.updated_ago_sec)}`;
    li.appendChild(name);
    li.appendChild(meta);
    li.addEventListener("click", () => selectSession(s.key));
    ul.appendChild(li);
  }
  if (items.length === 0) {
    const li = document.createElement("li");
    li.style.color = "var(--text-mute)";
    li.textContent = filter ? "无匹配会话" : "暂无会话";
    ul.appendChild(li);
  }
}
document.getElementById("session-filter").addEventListener("input", renderSessionList);

async function selectSession(key) {
  activeSession = key;
  renderSessionList();
  setText("msg-title", "加载中…");
  setText("msg-count", "");
  const box = document.getElementById("messages");
  box.innerHTML = `<p class="placeholder">加载中…</p>`;
  lastMsgTimestamp = 0;
  try {
    const res = await fetch(`/api/messages?session=${encodeURIComponent(key)}&limit=100`);
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    renderMessages(data);
    const msgs = data.messages || [];
    lastMsgTimestamp = msgs.length ? msgs[msgs.length - 1].observed_at || 0 : 0;
  } catch (e) {
    box.innerHTML = `<p class="placeholder">加载失败：${e.message}</p>`;
  }
}
function renderMessages(data) {
  setText("msg-title", data.title || data.session || "未知会话");
  setText("msg-count", `${data.count} 条`);
  const box = document.getElementById("messages");
  box.innerHTML = "";
  const msgs = data.messages || [];
  if (msgs.length === 0) {
    box.innerHTML = `<p class="placeholder">该会话无消息记录。</p>`;
    return;
  }
  const now = new Date();
  for (const m of msgs) {
    const div = document.createElement("div");
    div.className = `msg ${m.role || "user"}`;
    const time = document.createElement("span");
    time.className = "time";
    const t = m.observed_at ? new Date(m.observed_at * 1000) : null;
    time.textContent = t ? fmtMsgTime(t, now) : "";
    const who = document.createElement("span");
    who.className = "who";
    if (m.role === "assistant") {
      who.textContent = "A";
    } else if (m.sender) {
      who.textContent = m.sender;
    } else {
      who.textContent = "U";
    }
    const txt = document.createElement("span");
    txt.textContent = m.text || "";
    div.appendChild(time);
    div.appendChild(who);
    div.appendChild(txt);
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

// Show the date when the message is not from today, so cross-day history is
// unambiguous. Today's messages show only HH:MM:SS.
function fmtMsgTime(t, now) {
  const sameDay =
    t.getFullYear() === now.getFullYear() &&
    t.getMonth() === now.getMonth() &&
    t.getDate() === now.getDate();
  if (sameDay) {
    return t.toLocaleTimeString([], { hour12: false });
  }
  const mm = String(t.getMonth() + 1).padStart(2, "0");
  const dd = String(t.getDate()).padStart(2, "0");
  return `${mm}-${dd} ` + t.toLocaleTimeString([], { hour12: false });
}

// Refresh the currently-open session's messages periodically so new ones show
// up without re-clicking. We track the last message's observed_at timestamp
// (rather than just count) because when history exceeds the limit a new
// message pushes an old one out without changing the count.
let lastMsgTimestamp = 0;
async function refreshActiveSession() {
  if (!activeSession) return;
  try {
    const res = await fetch(
      `/api/messages?session=${encodeURIComponent(activeSession)}&limit=100`,
      { cache: "no-store" }
    );
    if (!res.ok) return;
    const data = await res.json();
    const msgs = data.messages || [];
    const newest = msgs.length ? msgs[msgs.length - 1].observed_at || 0 : 0;
    if (newest > lastMsgTimestamp) {
      lastMsgTimestamp = newest;
      renderMessages(data);
    }
  } catch (e) {
    /* ignore */
  }
}
setInterval(refreshActiveSession, 5000);
loadSessions();
setInterval(loadSessions, 15000);

// ============================================================
// Logs (SSE)
// ============================================================
let logEs = null;
let logPaused = false;
let logBuffer = [];      // accumulated lines (capped)
const LOG_CAP = 4000;
let logTagFilter = "";
let logHistoryLoaded = false;

function tagOf(line) {
  const m = line.match(/\]\s*(\[[a-z][a-z0-9_-]*)\]/i) || line.match(/(\[[a-z][a-z0-9_-]*\])/i);
  if (!m) return "";
  return m[1].replace(/[\[\]]/g, "").toLowerCase();
}
function classForTag(t) {
  if (t.startsWith("warn")) return "warn";
  if (t.startsWith("error")) return "error";
  if (t.startsWith("skip")) return "skip";
  if (t.startsWith("cycle")) return "cycle";
  if (t.startsWith("event")) return "event";
  if (t.startsWith("supervisor")) return "supervisor";
  return "";
}
function appendLogLine(text) {
  logBuffer.push(text);
  if (logBuffer.length > LOG_CAP) {
    logBuffer.splice(0, logBuffer.length - LOG_CAP);
  }
  const t = tagOf(text);
  if (logTagFilter && t !== logTagFilter && !t.startsWith(logTagFilter)) return;
  const box = document.getElementById("log-box");
  const span = document.createElement("span");
  span.className = `log-line ${classForTag(t)}`;
  span.textContent = text + "\n";
  box.appendChild(span);
  // Trim DOM nodes to avoid runaway growth
  while (box.childNodes.length > LOG_CAP) {
    box.removeChild(box.firstChild);
  }
  if (!logPaused && document.getElementById("log-autoscroll").checked) {
    box.scrollTop = box.scrollHeight;
  }
}
async function loadLogHistory() {
  const n = parseInt(document.getElementById("log-load-history").value || "0", 10);
  if (n <= 0) return;
  try {
    const res = await fetch(`/api/logs/recent?lines=${n}`);
    if (!res.ok) return;
    const data = await res.json();
    const box = document.getElementById("log-box");
    box.innerHTML = "";
    logBuffer = [];
    for (const item of data.lines || []) {
      appendLogLine(item.text);
    }
  } catch (e) {
    /* ignore */
  }
  logHistoryLoaded = true;
}
function maybeStartLogStream() {
  if (logEs) return;
  if (!logHistoryLoaded) {
    loadLogHistory();
  }
  logEs = new EventSource("/api/logs/stream");
  logEs.onopen = () => setText("log-status", "已连接");
  logEs.onerror = () => setText("log-status", "重连中…");
  logEs.onmessage = (ev) => {
    try {
      const obj = JSON.parse(ev.data);
      if (obj.text) appendLogLine(obj.text);
    } catch (e) {
      /* ignore */
    }
  };
}
// controls
document.getElementById("log-pause").addEventListener("change", (e) => {
  logPaused = e.target.checked;
});
document.getElementById("log-tag").addEventListener("input", (e) => {
  logTagFilter = (e.target.value || "").trim().toLowerCase();
  // re-render from buffer with the new filter
  const box = document.getElementById("log-box");
  box.innerHTML = "";
  for (const line of logBuffer) {
    const t = tagOf(line);
    if (logTagFilter && t !== logTagFilter && !t.startsWith(logTagFilter)) continue;
    const span = document.createElement("span");
    span.className = `log-line ${classForTag(t)}`;
    span.textContent = line + "\n";
    box.appendChild(span);
  }
  box.scrollTop = box.scrollHeight;
});
document.getElementById("log-clear").addEventListener("click", () => {
  document.getElementById("log-box").innerHTML = "";
  logBuffer = [];
});
document.getElementById("log-load-history").addEventListener("change", () => {
  logHistoryLoaded = false;
  if (document.getElementById("tab-logs").classList.contains("active")) loadLogHistory();
});
