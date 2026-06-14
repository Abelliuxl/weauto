"""WeAuto control panel: a menu-bar daemon + web server front-end.

This package wraps the existing ``run.py`` bot as a supervised subprocess and
exposes a read-only status dashboard (macOS menu bar + remote web UI). The bot
core (``wechat_rpa/*``) is never imported or modified.
"""
