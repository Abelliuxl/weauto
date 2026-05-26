from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot


def _msg(text: str, side: str = "other"):
    return {
        "side": side,
        "content_type": "text",
        "text": text,
        "fingerprint": f"{side}|text|{text}",
    }


def test_detached_batch_selects_latest_normal_group_message():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(detached_reply_on_image=False, normal_reply_interval_sec=300.0)
    bot._last_normal_reply_at = 0.0
    bot.visible_message_state = SimpleNamespace(
        is_incoming=lambda message: message.get("side") == "other"
    )
    bot._detached_row_for_message = lambda **kwargs: SimpleNamespace(
        title=kwargs["title"],
        preview=kwargs["message"].get("text", ""),
        has_mention=False,
    )
    bot._is_ignored_title = lambda row: False
    bot._is_admin_session = lambda row: False
    bot._is_row_muted = lambda row: False
    bot._is_group_chat = lambda row: True
    bot._should_reply_group = lambda row, reason: True
    bot._is_normal_reply_event = lambda row, reason: reason == "new_message"
    bot._normal_reply_interval_active = lambda: True

    selected = bot._select_detached_messages_to_handle(
        window_id=1,
        title="群-临沧",
        now=1000.0,
        new_messages=[
            _msg("有没有钥匙"),
            _msg("不吃压力大发顺"),
            _msg("我测试一下"),
        ],
    )

    assert [message["text"] for message in selected] == ["我测试一下"]


def test_detached_batch_keeps_all_mentions_and_latest_normal_message():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(detached_reply_on_image=False, normal_reply_interval_sec=300.0)
    bot._last_normal_reply_at = 0.0
    bot.visible_message_state = SimpleNamespace(
        is_incoming=lambda message: message.get("side") == "other"
    )

    def row_for_message(**kwargs):
        text = kwargs["message"].get("text", "")
        return SimpleNamespace(
            title=kwargs["title"],
            preview=text,
            has_mention="@萨比" in text,
        )

    bot._detached_row_for_message = row_for_message
    bot._is_ignored_title = lambda row: False
    bot._is_admin_session = lambda row: False
    bot._is_row_muted = lambda row: False
    bot._is_group_chat = lambda row: True
    bot._should_reply_group = lambda row, reason: True
    bot._is_normal_reply_event = lambda row, reason: reason == "new_message"
    bot._normal_reply_interval_active = lambda: True

    selected = bot._select_detached_messages_to_handle(
        window_id=1,
        title="群-临沧",
        now=1000.0,
        new_messages=[
            _msg("@萨比 第一条"),
            _msg("普通闲聊一"),
            _msg("@萨比 第二条"),
            _msg("普通闲聊二"),
        ],
    )

    assert [message["text"] for message in selected] == [
        "@萨比 第一条",
        "@萨比 第二条",
        "普通闲聊二",
    ]


def test_detached_batch_drops_normal_group_message_during_cooldown():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(detached_reply_on_image=False, normal_reply_interval_sec=300.0)
    bot._last_normal_reply_at = 900.0
    bot.visible_message_state = SimpleNamespace(
        is_incoming=lambda message: message.get("side") == "other"
    )
    bot._detached_row_for_message = lambda **kwargs: SimpleNamespace(
        title=kwargs["title"],
        preview=kwargs["message"].get("text", ""),
        has_mention=False,
    )
    bot._is_ignored_title = lambda row: False
    bot._is_admin_session = lambda row: False
    bot._is_row_muted = lambda row: False
    bot._is_group_chat = lambda row: True
    bot._should_reply_group = lambda row, reason: True
    bot._is_normal_reply_event = lambda row, reason: reason == "new_message"
    bot._normal_reply_interval_active = lambda: True

    selected = bot._select_detached_messages_to_handle(
        window_id=1,
        title="群-临沧",
        now=1000.0,
        new_messages=[_msg("普通闲聊")],
    )

    assert selected == []
