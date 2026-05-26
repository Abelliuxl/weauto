from types import SimpleNamespace

from wechat_rpa.message_handler import MessageHandler


def test_message_handler_startup_skip_keeps_mention_pending():
    class FakeBot:
        def __init__(self):
            self._skip_first_action_pending = True
            self._baseline = {
                1: SimpleNamespace(
                    last_replied_at=0,
                    pending_unread=True,
                    pending_normal=True,
                    pending_mention=False,
                )
            }

        def _fit_col(self, text, width):
            return str(text)[:width]

    bot = FakeBot()
    row = SimpleNamespace(row_idx=1, title="群", preview="", text="")
    MessageHandler(bot).handle_event(rows=[row], row=row, reason="mention", now=123)

    mem = bot._baseline[1]
    assert bot._skip_first_action_pending is False
    assert mem.pending_mention is True
    assert mem.pending_unread is True


def test_message_handler_ignored_title_marks_event_done():
    class FakeBot:
        cfg = SimpleNamespace(log_verbose=False, debug_scan=False)

        def __init__(self):
            self._skip_first_action_pending = False
            self._baseline = {
                1: SimpleNamespace(
                    last_replied_at=0,
                    pending_unread=True,
                    pending_normal=True,
                    pending_mention=True,
                )
            }

        def _is_ignored_title(self, row):
            return True

    bot = FakeBot()
    row = SimpleNamespace(row_idx=1, title="忽略", preview="", text="")
    MessageHandler(bot).handle_event(rows=[row], row=row, reason="new_message", now=456)

    mem = bot._baseline[1]
    assert mem.last_replied_at == 456
    assert mem.pending_unread is False
    assert mem.pending_normal is False
    assert mem.pending_mention is False
