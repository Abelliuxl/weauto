"""Threaded HTTP server for the control panel (stdlib only).

A single ``ThreadingHTTPServer`` dispatches to :class:`Handlers`. SSE responses
are upgraded to streaming writes and held open per-thread.
"""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import WebUIConfig
from ..supervisor import BotSupervisor
from .handlers import Handlers, _SSE_SENTINEL, parse_request_target, run_sse_stream


def make_server(
    supervisor: BotSupervisor,
    cfg: WebUIConfig,
    static_dir: Path,
) -> ThreadingHTTPServer:
    """Build (but do not start) the HTTP server bound to ``cfg.host:cfg.port``."""
    handlers = Handlers(supervisor=supervisor, static_dir=static_dir)

    class _Handler(BaseHTTPRequestHandler):
        # Quiet the default noisy logging; we surface our own line on start.
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path, query = parse_request_target(self.path)
            try:
                status, ctype, _, body = handlers.handle(
                    self.command, path, query, dict(self.headers)
                )
            except Exception as exc:  # noqa: BLE001
                self._send(500, "text/plain; charset=utf-8", f"server error: {exc}".encode())
                return
            if body is _SSE_SENTINEL:
                self._handle_sse()
                return
            self._send(status, ctype, body or b"")

        def _send(self, status: int, ctype: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _handle_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            stop_flag = threading.Event()

            def write(data: bytes) -> None:
                if stop_flag.is_set():
                    return
                try:
                    self.wfile.write(data)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    stop_flag.set()

            # Detect client disconnect via socket readability (recv returns b"").
            def _watcher() -> None:
                try:
                    while not stop_flag.is_set():
                        try:
                            data = self.connection.recv(1, socket.MSG_PEEK)
                        except OSError:
                            break
                        if data == b"":
                            break
                        stop_flag.wait(0.5)
                finally:
                    stop_flag.set()

            watcher = threading.Thread(target=_watcher, daemon=True)
            watcher.start()
            try:
                run_sse_stream(supervisor, write, stop_check=stop_flag.is_set)
            finally:
                stop_flag.set()

        def do_HEAD(self) -> None:  # noqa: N802 - http.server API
            path, query = parse_request_target(self.path)
            status, ctype, _, body = handlers.handle(
                self.command, path, query, dict(self.headers)
            )
            self._send(status, ctype, b"")

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            # Read-only dashboard: no POST surface. 405 keeps it explicit.
            self._send(405, "text/plain; charset=utf-8", b"Method Not Allowed")

    class _Server(ThreadingHTTPServer):
        # Set as a class attribute so it takes effect before server_bind().
        allow_reuse_address = True
        daemon_threads = True

    httpd = _Server((cfg.host, cfg.port), _Handler)
    return httpd


def start_in_thread(httpd: ThreadingHTTPServer) -> threading.Thread:
    """Serve forever in a background daemon thread."""
    thread = threading.Thread(
        target=httpd.serve_forever,
        name="weauto-web",
        daemon=True,
    )
    thread.start()
    return thread
