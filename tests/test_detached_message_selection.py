from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot


def _msg(text: str, side: str = "other"):
    return {
        "side": side,
        "content_type": "text",
        "text": text,
        "fingerprint": f"{side}|text|{text}",
    }


def _selected_texts(selected):
    return [message["text"] for message, _allow_pass in selected]


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

    assert _selected_texts(selected) == ["我测试一下"]
    assert [allow_pass for _message, allow_pass in selected] == [True]


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

    assert _selected_texts(selected) == [
        "@萨比 第一条",
        "@萨比 第二条",
        "普通闲聊二",
    ]
    assert [allow_pass for _message, allow_pass in selected] == [False, False, True]


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


def test_detached_batch_logs_cooldown_reason(capsys):
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_reply_on_image=False,
        normal_reply_interval_sec=300.0,
        log_verbose=True,
    )
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
    out = capsys.readouterr().out
    assert "[cooldown]" in out
    assert "reason=normal_reply_interval" in out
    assert "drop=1" in out
    assert "[batch-result]" in out


def test_detached_batch_logs_latest_normal_selection(capsys):
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_reply_on_image=False,
        normal_reply_interval_sec=300.0,
        log_verbose=True,
    )
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
        new_messages=[_msg("普通闲聊一"), _msg("普通闲聊二")],
    )

    assert _selected_texts(selected) == ["普通闲聊二"]
    assert [allow_pass for _message, allow_pass in selected] == [True]
    out = capsys.readouterr().out
    assert "[batch-select]" in out
    assert "policy=latest_normal" in out
    assert "selected=1" in out


def test_detached_handler_allows_selected_normal_message_through_reserved_cooldown():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_reply_on_image=False,
        log_verbose=False,
        processing_mode="long_bridge",
        normal_reply_interval_sec=300.0,
    )
    bot._last_normal_reply_at = 1000.0
    bot.visible_message_state = SimpleNamespace(is_incoming=lambda message: True)
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
    bot._image_followup_context_for_text = lambda row, text: ""
    bot._detached_context_text = lambda messages: ""
    bot._build_session_context = lambda row: ""
    bot._workspace_context_for_row = lambda row, **kwargs: ""
    bot._workspace_memory_recall_for_row = lambda row, query, is_admin=False: ""
    bot._save_persistent_memory = lambda: None

    calls = []

    def long_bridge_reply(row, **kwargs):
        calls.append(kwargs["latest_message"])
        return True, None

    bot._long_bridge_reply = long_bridge_reply

    bot._handle_detached_new_message(
        window_id=1,
        title="群-临沧",
        messages=[_msg("普通闲聊")],
        message=_msg("普通闲聊"),
        now=1000.0,
        allow_normal_cooldown_pass=True,
    )

    assert calls == ["普通闲聊"]


def test_long_bridge_forwards_admin_slash_command_without_local_interception():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_reply_on_image=False,
        log_verbose=False,
        processing_mode="long_bridge",
        normal_reply_interval_sec=300.0,
    )
    bot._last_normal_reply_at = 0.0
    bot.visible_message_state = SimpleNamespace(is_incoming=lambda message: True)
    bot._detached_row_for_message = lambda **kwargs: SimpleNamespace(
        title=kwargs["title"],
        preview=kwargs["message"].get("text", ""),
        has_mention=False,
    )
    bot._is_ignored_title = lambda row: False
    bot._is_admin_session = lambda row: True
    bot._is_row_muted = lambda row: False
    bot._is_group_chat = lambda row: False
    bot._should_reply_group = lambda row, reason: True
    bot._is_normal_reply_event = lambda row, reason: False
    bot._normal_reply_interval_active = lambda: True
    bot._image_followup_context_for_text = lambda row, text: ""
    bot._detached_context_text = lambda messages: ""
    bot._build_session_context = lambda row: ""
    bot._workspace_context_for_row = lambda row, **kwargs: ""
    bot._workspace_memory_recall_for_row = lambda row, query, is_admin=False: ""
    bot._save_persistent_memory = lambda: None
    bot._handle_admin_command = lambda cmd: (_ for _ in ()).throw(
        AssertionError(f"slash command was intercepted locally: {cmd}")
    )

    calls = []

    def long_bridge_reply(row, **kwargs):
        calls.append(kwargs)
        return True, None

    bot._long_bridge_reply = long_bridge_reply

    bot._handle_detached_new_message(
        window_id=1,
        title="real刘晓亮",
        messages=[_msg("/new")],
        message=_msg("/new"),
        now=1000.0,
    )

    assert len(calls) == 1
    assert calls[0]["latest_message"] == "/new"
    assert calls[0]["source_message"]["text"] == "/new"
    assert calls[0]["is_admin"] is True


def test_native_mode_still_handles_admin_slash_commands_locally():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(processing_mode="native")

    assert bot._should_handle_admin_command_locally(True) is True
    assert bot._should_handle_admin_command_locally(False) is False
    bot.cfg.processing_mode = "long_bridge"
    assert bot._should_handle_admin_command_locally(True) is False
