"""Entry point for the control panel.

Wires together the three pieces:
  1. :class:`BotSupervisor` — owns the bot subprocess + log pipes.
  2. :mod:`app.web.server` — read-only HTTP API + SSE on ``cfg.host:cfg.port``.
  3. :mod:`app.bar.menu_app` — rumps menu-bar app (main thread).

The menu-bar app must run on the main thread (it owns the macOS CFRunLoop), so
the supervisor and web server are started as daemon threads first, then we hand
control to rumps.

When rumps is unavailable or a GUI session is not present (``--headless``), we
fall back to running only the supervisor + web server and block on the main
thread until interrupted. This is useful for SSH-only hosts and for testing.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from .config import load_webui_config
from .supervisor import BotSupervisor

LOG = logging.getLogger("weauto.app")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m app.main",
        description="WeAuto control panel (menu bar + web UI supervisor).",
    )
    p.add_argument(
        "--config",
        default="config.toml",
        help="Path to the project config.toml (default: ./config.toml)",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Do not start the menu-bar app; run supervisor + web only.",
    )
    p.add_argument(
        "--no-bot",
        action="store_true",
        help="Do not spawn the bot on startup (supervisor idle). "
        "Useful for inspecting state without starting the RPA loop.",
    )
    p.add_argument(
        "--host",
        default=None,
        help="Override [webui].host for the HTTP server.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override [webui].port for the HTTP server.",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose supervisor logging.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    cfg = load_webui_config(args.config)
    if args.host is not None:
        cfg = _with_overrides(cfg, host=args.host)
    if args.port is not None:
        cfg = _with_overrides(cfg, port=args.port)

    project_root = cfg.project_root
    config_path = cfg.config_path
    LOG.info("project_root=%s config=%s", project_root, config_path)
    LOG.info("webui host=%s port=%s url=%s", cfg.host, cfg.port, cfg.web_url())

    static_dir = (Path(__file__).resolve().parent / "static").resolve()

    supervisor = BotSupervisor(
        cfg=cfg,
        project_root=project_root,
        config_path=config_path,
    )

    # Start supervisor. Spawns the bot immediately unless --no-bot or the
    # config disabled supervision; in those cases the loop still runs so the
    # menu bar / API can start the bot on demand.
    spawn_bot = (not args.no_bot) and cfg.supervisor_enabled
    if not spawn_bot:
        LOG.info("bot spawn skipped (%s)", "--no-bot" if args.no_bot else "supervisor_enabled=false")
    supervisor.start(spawn=spawn_bot)

    # Start web server in a daemon thread.
    from .web.server import make_server, start_in_thread

    httpd = make_server(supervisor, cfg, static_dir)
    start_in_thread(httpd)
    LOG.info("web server listening on http://%s:%s", cfg.host, cfg.port)

    use_menu_bar = (not args.headless) and _can_run_menu_bar()

    def _shutdown(_signum=None, _frame=None) -> None:
        LOG.info("shutting down…")
        try:
            httpd.shutdown()
        except Exception:
            pass
        supervisor.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if use_menu_bar:
        try:
            from .bar.menu_app import run as run_bar
        except Exception as exc:  # noqa: BLE001
            LOG.warning("menu-bar import failed (%s); falling back to headless", exc)
            _run_headless()
        else:
            LOG.info("starting menu-bar app on main thread")
            run_bar(supervisor, cfg)
            # run_bar returns when the user quits the app.
            _shutdown()
            return 0
    else:
        LOG.info("running headless (no menu bar). Ctrl+C to quit.")
        _run_headless()
        return 0


def _run_headless() -> None:
    """Block the main thread until SIGINT/SIGTERM. Used in headless mode."""
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass


def _can_run_menu_bar() -> bool:
    """Heuristic: only attempt the menu-bar app in a macOS GUI session.

    On macOS without a WindowServer (e.g. SSH login), or when rumps/Cocoa are
    not installed, we fall back to headless. We also avoid the menu bar when
    there's no controlling tty AND no GUI session marker.
    """
    if sys.platform != "darwin":
        return False
    try:
        import rumps  # noqa: F401
    except Exception:
        return False
    # Presence of the macOS WindowServer is a decent proxy for "GUI session".
    # SSH-only sessions can still set SECURITYSESSIONID via reattach, so check
    # both common markers.
    import os

    if os.environ.get("SECURITYSESSIONID") or os.environ.get("XPC_SERVICE_NAME"):
        return True
    # Fall back: if there's a GUI login session, /var/run/WindowServer exists.
    return Path("/private/var/run/WindowServer").exists() or Path(
        "/var/run/WindowServer"
    ).exists()


def _with_overrides(cfg, **kwargs) -> "WebUIConfig":
    """Return a new WebUIConfig with the given fields replaced."""
    from dataclasses import replace

    return replace(cfg, **kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
