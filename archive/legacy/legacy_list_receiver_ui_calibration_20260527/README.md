# Legacy List-Rceiver UI Calibration Tools

These files are the calibration tools for the **old unified-list chat receiver mode** (legacy_list).

In the old mode, the bot scanned a single WeChat window's entire list view to detect
chat rows, unread badges, preview text, etc. Each UI element needed pixel-level calibration
for the user's screen resolution and WeChat theme.

The current mode (`receiver_mode = "detached_windows"`) captures each chat as its own
macOS window, so list-level calibration is no longer needed. These tools are archived
for reference in case someone needs to revive the legacy receiver mode.

## Contents

- `carlibrate_*.py` / `carlibrate_*.sh` — interactive calibration UIs for:
  - Chat row detection (`rows`, `row_title`)
  - Preview text region (`preview`, `preview_region`)
  - Unread badge region (`unread`, `unread_badge`)
  - Title group detection (`title_group`, `title_private`)
  - Auto-recover positioning (`recover_auto`)
  - Chat context extraction (`chat_context`)
- `debug_click*.py` / `debug_click.sh` — click debugging tools
- `debug_preview.sh` — preview region debug
- `debug_unread.sh` — unread badge debug

All depend on the old `detector.py` + `window.py` coordinate-based GUI automation path.
