from wechat_rpa.visible_message_state import VisibleMessageStateStore


def _msg(fingerprint: str, text: str = "", side: str = "other"):
    return {
        "fingerprint": fingerprint,
        "text": text or fingerprint,
        "side": side,
        "content_type": "text",
    }


def test_visible_message_state_ignores_old_messages_that_appear_before_tail():
    state = VisibleMessageStateStore()
    first = state.update(window_id=1, messages=[_msg("a"), _msg("b")])
    assert [msg["fingerprint"] for msg in first] == ["a", "b"]

    second = state.update(window_id=1, messages=[_msg("x-old"), _msg("a"), _msg("b")])
    assert second == []


def test_visible_message_state_returns_only_messages_after_previous_tail():
    state = VisibleMessageStateStore()
    state.update(window_id=1, messages=[_msg("a"), _msg("b")])

    second = state.update(window_id=1, messages=[_msg("a"), _msg("b"), _msg("c")])
    assert [msg["fingerprint"] for msg in second] == ["c"]


def test_visible_message_state_resyncs_when_tail_anchor_is_lost():
    state = VisibleMessageStateStore()
    state.update(window_id=1, messages=[_msg("a"), _msg("b")])

    second = state.update(
        window_id=1,
        messages=[_msg("x"), _msg("y", side="self")],
    )
    assert second == []


def test_visible_message_state_preserves_new_incoming_tail_when_anchor_is_lost():
    state = VisibleMessageStateStore()
    state.update(window_id=1, messages=[_msg("old-question"), _msg("old-answer", side="self")])

    second = state.update(
        window_id=1,
        messages=[
            _msg("long-answer", side="self"),
            _msg("follow-up", text="他们之前有推出过什么模型吗？"),
        ],
    )

    assert [msg["fingerprint"] for msg in second] == ["follow-up"]
