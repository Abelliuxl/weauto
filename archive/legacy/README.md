# Legacy WeChat List Receiver

This directory keeps copies of the old whole-app chat-list based receiver and
sender code.

- `detector_list_receiver.py` is the previous left-list detector that used
  unread badges and preview changes.
- `sender_gui_legacy.py` is the sender implementation before detached-window
  sender activation support.

The active code path is moving toward detached chat windows captured by
macOS window id, with UI-block parsing and OCR over visible message bubbles.
