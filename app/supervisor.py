"""Supervise the bot subprocess (``run.py``).

Responsibilities (all inherited from ``start_rpa.sh``, reimplemented in-process
so we no longer need a terminal):

* ``Popen`` ``python -u run.py`` and stream its stdout/stderr.
* Strip ANSI colors and append a plain-text ``logs/rpa_<timestamp>.log``.
* Filter ``[warn|error|skip-]`` lines into ``logs/issues.log`` (timestamped,
  plain text — cleaner than the shell version which kept ANSI codes).
* Keep a bounded in-memory ring buffer for the web UI / SSE stream.
* Restart on crash, with a minimum interval guard to avoid restart storms.
* Aggregate a ``status`` snapshot: process liveness, uptime, runtime_state.json
  activity/heartbeat timestamps, and the bridge mode parsed from the log.

The bot core is treated as a black box: we only spawn it, read its output, and
watch a couple of files it already writes. Nothing in ``wechat_rpa`` is
imported.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import WebUIConfig

# Matches ``\e[...m`` ANSI escapes (colors) and a few other control sequences.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]")

# Lines carrying one of these log tags go to issues.log (mirrors start_rpa.sh).
_ISSUES_RE = re.compile(r"\[(?:warn|error|skip-)\]")

# Bridge / processing mode announcement line, e.g.
# ``[start] processing: mode=long_bridge target=... status=connecting ...``
# (older builds emitted ``processing: long_bridge`` without the ``mode=`` prefix).
_PROCESSING_RE = re.compile(r"\[start\]\s+processing:\s*(.+)$")

# Long-bridge status transitions in the running log.
_LONG_BRIDGE_RE = re.compile(
    r"\[long-?bridge\]\s+(.+)|long bridge (failed|reconnect|handshake|connected|disconnected)",
    re.IGNORECASE,
)


@dataclass
class ProcessStatus:
    """Point-in-time snapshot of the supervised bot + dashboard state."""

    running: bool
    pid: int | None
    exit_code: int | None
    started_at: float | None  # epoch of current process start
    uptime_sec: float
    restart_count: int
    auto_restart: bool
    last_exit_at: float | None
    last_exit_code: int | None
    # Parsed from runtime_state.json (epoch seconds, may be 0/None).
    runtime_saved_at: float | None
    last_activity_at: float | None
    last_heartbeat_at: float | None
    last_normal_reply_at: float | None
    bridge_mode: str  # native / bridge / long_bridge / unknown
    bridge_state: str  # connected / connecting / disabled / ...
    log_file: str
    issues_file: str
    config_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "uptime_sec": round(self.uptime_sec, 1),
            "restart_count": self.restart_count,
            "auto_restart": self.auto_restart,
            "last_exit_at": self.last_exit_at,
            "last_exit_code": self.last_exit_code,
            "runtime_saved_at": self.runtime_saved_at,
            "last_activity_at": self.last_activity_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_normal_reply_at": self.last_normal_reply_at,
            "bridge_mode": self.bridge_mode,
            "bridge_state": self.bridge_state,
            "log_file": self.log_file,
            "issues_file": self.issues_file,
            "config_path": self.config_path,
            "now": time.time(),
        }


class BotSupervisor:
    """Owns the bot subprocess, the log pipes, and the ring buffer."""

    def __init__(
        self,
        cfg: WebUIConfig,
        *,
        project_root: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.project_root = (project_root or cfg.project_root or Path(".")).resolve()
        self.config_path = (config_path or cfg.config_path or Path("config.toml")).resolve()

        self._log_dir = self.project_root / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._runtime_state_path = self.project_root / "data" / "runtime_state.json"

        # Log file for THIS supervisor session (new timestamp per start).
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.log_file = self._log_dir / f"rpa_{ts}.log"
        self.issues_file = self._log_dir / "issues.log"

        # Ring buffer of recent (clean) log lines, plus a condition to wake SSE
        # subscribers and a per-line monotonic id so clients can resume.
        self._ring: deque[tuple[int, str]] = deque(maxlen=max(200, int(cfg.log_ring_lines)))
        self._ring_lock = threading.Lock()
        self._ring_cond = threading.Condition(self._ring_lock)
        self._next_seq = 1

        # Process bookkeeping (guarded by _proc_lock).
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._started_at: float | None = None
        self._last_exit_at: float | None = None
        self._last_exit_code: int | None = None
        self._restart_count = 0

        # Supervision control.
        self._stop = threading.Event()
        self._auto_restart_enabled = bool(cfg.auto_restart)
        self._intentional_stop = False  # set when user stops; suppress auto-restart
        self._last_restart_at = 0.0

        # Bridge info parsed from the live log stream.
        self._bridge_mode = "unknown"
        self._bridge_state = "unknown"

        # Lazily opened log handles; created on first write.
        self._log_fh = None
        self._issues_fh = None

        self._reader_thread: threading.Thread | None = None
        self._supervisor_thread: threading.Thread | None = None
        # Optional callback invoked on every status-affecting change (used by
        # the menu bar to refresh its icon without polling).
        self.on_change: Callable[[], None] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self, *, spawn: bool = True) -> None:
        """Launch the supervisor loop (background) and (optionally) the bot."""
        if self._supervisor_thread and self._supervisor_thread.is_alive():
            return
        self._stop.clear()
        if spawn:
            self._spawn_bot()
        else:
            # Stay idle; user can start the bot via the menu bar / API later.
            self._intentional_stop = True
        self._supervisor_thread = threading.Thread(
            target=self._supervise_loop,
            name="weauto-supervisor",
            daemon=True,
        )
        self._supervisor_thread.start()

    def stop_bot(self) -> None:
        """Stop the bot and stay stopped (no auto-restart)."""
        self._intentional_stop = True
        self._terminate_bot()

    def start_bot(self) -> None:
        """(Re)start the bot after an explicit stop."""
        self._intentional_stop = False
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                return  # already running
        self._spawn_bot()

    def restart_bot(self) -> None:
        """Restart the bot now (user-initiated; bypasses the min-interval guard)."""
        self._intentional_stop = False
        self._terminate_bot(timeout=4.0)
        self._last_restart_at = 0.0  # allow immediate respawn
        self._spawn_bot()

    def set_auto_restart(self, enabled: bool) -> None:
        self._auto_restart_enabled = bool(enabled)
        self._notify_change()

    def shutdown(self) -> None:
        """Tear everything down (app quit)."""
        self._stop.set()
        self._intentional_stop = True
        self._terminate_bot(timeout=4.0)
        self._close_log_handles()
        if self._supervisor_thread and self._supervisor_thread.is_alive():
            self._supervisor_thread.join(timeout=2.0)

    # ------------------------------------------------------------------ #
    # Process spawning / supervision
    # ------------------------------------------------------------------ #
    def _spawn_bot(self) -> None:
        """Start one bot subprocess + its reader thread."""
        env = self._build_env()
        cmd = self._build_command()
        try:
            proc = subprocess.Popen(  # noqa: S603 - command is built from our own args
                cmd,
                cwd=str(self.project_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self._emit_local(f"[supervisor] failed to spawn bot: {exc}")
            return
        with self._proc_lock:
            self._proc = proc
            self._started_at = time.time()
        self._restart_count += 1
        self._emit_local(
            f"[supervisor] bot started pid={proc.pid} log={self.log_file.name}"
        )
        self._notify_change()
        # One reader thread per process; it exits when the pipe closes.
        reader = threading.Thread(
            target=self._read_loop,
            args=(proc,),
            name=f"weauto-log-reader-{proc.pid}",
            daemon=True,
        )
        reader.start()

    def _build_command(self) -> list[str]:
        python = sys.executable or "python3"
        config_arg = str(self.config_path)
        # Try to keep the path relative to project root for parity with the
        # shell launcher, which runs from the repo root.
        try:
            config_arg = str(self.config_path.relative_to(self.project_root))
        except ValueError:
            config_arg = str(self.config_path)
        return [python, "-u", "run.py", "--config", config_arg]

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["WEAUTO_LOG_FILE"] = str(self.log_file)
        env.setdefault("WEAUTO_SCREENSHOT_HIGH_RES", "0")
        # Preserve terminal width if the parent had one; otherwise pick a sane
        # default so column-aligned logs stay readable.
        env.setdefault("WEAUTO_LOG_WIDTH", os.environ.get("WEAUTO_LOG_WIDTH", "140"))
        # MakeFORCE_COLOR behave like a terminal only if the parent was a tty;
        # when launched from the menu bar there is no tty, so we strip color
        # upstream in _ANSI_RE regardless.
        return env

    def _read_loop(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._ingest_line(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            # Record exit info; the supervise loop will decide on respawn.
            rc = proc.wait()
            with self._proc_lock:
                self._last_exit_at = time.time()
                self._last_exit_code = rc
            self._emit_local(f"[supervisor] bot exited code={rc} pid={proc.pid}")
            self._notify_change()

    def _supervise_loop(self) -> None:
        """Watch for a dead process and respawn if auto-restart is on."""
        while not self._stop.is_set():
            with self._proc_lock:
                proc = self._proc
            if proc is not None and proc.poll() is not None:
                # Process is gone.
                if self._intentional_stop:
                    # User asked to stop; do not respawn.
                    with self._proc_lock:
                        self._proc = None
                elif self._auto_restart_enabled and self.cfg.supervisor_enabled:
                    self._maybe_respawn()
                else:
                    with self._proc_lock:
                        self._proc = None
            self._stop.wait(1.0)

    def _maybe_respawn(self) -> None:
        elapsed = time.time() - (self._last_restart_at or 0.0)
        min_interval = max(1.0, float(self.cfg.restart_min_interval_sec))
        if elapsed < min_interval:
            wait = min_interval - elapsed
            self._emit_local(
                f"[supervisor] crash-restart throttled, waiting {wait:.1f}s"
            )
            if self._stop.wait(wait):
                return
        self._last_restart_at = time.time()
        self._spawn_bot()

    def _terminate_bot(self, *, timeout: float = 6.0) -> None:
        with self._proc_lock:
            proc = self._proc
            self._proc = None
        if proc is None or proc.poll() is not None:
            return
        # Try SIGINT (KeyboardInterrupt path the bot already handles for clean
        # memory flush), then escalate to SIGTERM / SIGKILL.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.2)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
        self._notify_change()

    # ------------------------------------------------------------------ #
    # Log ingestion
    # ------------------------------------------------------------------ #
    def _ingest_line(self, line: str) -> None:
        clean = _ANSI_RE.sub("", line)
        ts = time.strftime("%H:%M:%S")
        # Write full clean line to rpa log; prefix with a wall-clock time so the
        # file is self-describing when the bot's own lines lack one.
        self._write_log(f"{clean}\n")
        # Issues side-channel (plain text, timestamped) — fixes the ANSI leak
        # present in the shell version.
        if _ISSUES_RE.search(clean):
            self._write_issues(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {clean}\n")
        # Ring buffer + notify SSE subscribers.
        self._update_bridge_state(clean)
        with self._ring_cond:
            seq = self._next_seq
            self._next_seq += 1
            self._ring.append((seq, f"[{ts}] {clean}"))
            self._ring_cond.notify_all()

    def _update_bridge_state(self, line: str) -> None:
        m = _PROCESSING_RE.search(line)
        if m:
            text = m.group(1).strip()
            mode, status = _parse_processing_line(text)
            self._bridge_mode = mode
            self._bridge_state = status
            return
        if "long bridge failed" in line.lower() or "long-bridge failed" in line.lower():
            self._bridge_state = "failed"
            return
        if "long bridge" in line.lower() and "connected" in line.lower():
            self._bridge_state = "connected"

    def _write_log(self, text: str) -> None:
        try:
            if self._log_fh is None:
                self._log_fh = open(self.log_file, "a", encoding="utf-8")
            self._log_fh.write(text)
            self._log_fh.flush()
        except OSError:
            pass

    def _write_issues(self, text: str) -> None:
        try:
            if self._issues_fh is None:
                self._issues_fh = open(self.issues_file, "a", encoding="utf-8")
            self._issues_fh.write(text)
        except OSError:
            pass

    def _close_log_handles(self) -> None:
        for fh in (self._log_fh, self._issues_fh):
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
        self._log_fh = None
        self._issues_fh = None

    def _emit_local(self, text: str) -> None:
        """A supervisor-generated (not from the bot) line."""
        ts = time.strftime("%H:%M:%S")
        clean = f"[{ts}] {text}"
        print(clean, file=sys.stderr, flush=True)
        with self._ring_cond:
            seq = self._next_seq
            self._next_seq += 1
            self._ring.append((seq, clean))
            self._ring_cond.notify_all()

    # ------------------------------------------------------------------ #
    # Ring buffer access (web layer)
    # ------------------------------------------------------------------ #
    def recent_lines(self, limit: int = 500) -> list[tuple[int, str]]:
        with self._ring_lock:
            items = list(self._ring)
        if limit > 0:
            items = items[-limit:]
        return items

    def wait_for_lines(self, after_seq: int, timeout: float = 15.0) -> list[tuple[int, str]]:
        """Block until new lines appear after ``after_seq`` or timeout.

        Used by the SSE handler to avoid busy-polling.
        """
        deadline = time.time() + timeout
        with self._ring_cond:
            while True:
                fresh = [(s, t) for (s, t) in self._ring if s > after_seq]
                if fresh:
                    return fresh
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._ring_cond.wait(timeout=remaining)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def status(self) -> ProcessStatus:
        with self._proc_lock:
            proc = self._proc
            started_at = self._started_at
            last_exit_at = self._last_exit_at
            last_exit_code = self._last_exit_code
            pid = proc.pid if proc is not None else None
            exit_code = proc.poll() if proc is not None else last_exit_code
            running = proc is not None and exit_code is None

        uptime = (time.time() - started_at) if (running and started_at) else 0.0
        rt = self._read_runtime_state()
        return ProcessStatus(
            running=running,
            pid=pid,
            exit_code=exit_code,
            started_at=started_at,
            uptime_sec=uptime,
            restart_count=max(0, self._restart_count),
            auto_restart=self._auto_restart_enabled,
            last_exit_at=last_exit_at,
            last_exit_code=last_exit_code,
            runtime_saved_at=_as_float(rt.get("saved_at")),
            last_activity_at=_as_float(rt.get("last_activity_at")),
            last_heartbeat_at=_as_float(rt.get("last_heartbeat_at")),
            last_normal_reply_at=_as_float(rt.get("last_normal_reply_at")),
            bridge_mode=self._bridge_mode,
            bridge_state=self._bridge_state,
            log_file=str(self.log_file.relative_to(self.project_root))
            if self.log_file.is_relative_to(self.project_root)
            else str(self.log_file),
            issues_file=str(self.issues_file.relative_to(self.project_root))
            if self.issues_file.is_relative_to(self.project_root)
            else str(self.issues_file),
            config_path=str(self.config_path.relative_to(self.project_root))
            if self.config_path.is_relative_to(self.project_root)
            else str(self.config_path),
        )

    def _read_runtime_state(self) -> dict[str, Any]:
        if not self._runtime_state_path.is_file():
            return {}
        try:
            import json

            return json.loads(self._runtime_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ #
    def _notify_change(self) -> None:
        cb = self.on_change
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass


def _as_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _parse_processing_line(text: str) -> tuple[str, str]:
    """Parse a ``[start] processing: ...`` payload into (mode, status).

    Handles two formats emitted by the bot:
      * ``mode=long_bridge target=... status=connecting ...`` (current)
      * ``long_bridge (connected)`` / ``native`` / ``bridge (disabled)`` (legacy)
    Returns ``("unknown", "unknown")`` if nothing recognizable is found.
    """
    text = (text or "").strip()
    if not text:
        return "unknown", "unknown"
    # key=value form
    kv: dict[str, str] = {}
    for token in text.split():
        if "=" in token:
            k, _, v = token.partition("=")
            kv[k.strip().lower()] = v.strip().lower()
    if "mode" in kv:
        mode = kv.get("mode", "unknown")
        status = kv.get("status") or "started"
        return mode, status
    # legacy paren form or bare mode
    first = text.split()[0].lower()
    if "(" in text and ")" in text:
        status = text[text.index("(") + 1 : text.index(")")].strip().lower()
        return first, status
    return first, "started"
