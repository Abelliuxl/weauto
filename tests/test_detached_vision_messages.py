from types import SimpleNamespace

from PIL import Image
from PIL import ImageDraw

from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.llm import LlmReplyGenerator
from wechat_rpa.people_aliases import PersonAliasResolver


def test_vision_messages_to_visible_messages_preserves_order_and_self_sender():
    messages = WeChatGuiRpaBot._vision_messages_to_visible_messages(
        [
            {"sender": "饕餮cong", "content": "所以经常黑屏"},
            {"sender": "我", "content": "我看一下"},
            {"sender": "饕餮cong", "content": "[图片] 显卡报错截图"},
            {"sender": "饕餮cong", "content": "所以经常黑屏"},
        ]
    )

    assert [item["side"] for item in messages] == ["other", "self", "other", "other"]
    assert messages[1]["sender"] == "self"
    assert messages[2]["content_type"] == "image"
    assert messages[2]["vision_text"] == "显卡报错截图"
    assert messages[0]["fingerprint"] != messages[3]["fingerprint"]


def test_vision_fingerprint_ignores_ocr_whitespace_but_keeps_text_and_position():
    compact = WeChatGuiRpaBot._vision_messages_to_visible_messages(
        [{"sender": "刘晓亮", "content": "叫我帅亮"}]
    )[0]
    spaced = WeChatGuiRpaBot._vision_messages_to_visible_messages(
        [{"sender": "刘晓亮", "content": "叫 我帅亮"}]
    )[0]

    assert compact["fingerprint"] == spaced["fingerprint"]
    assert compact["text"] == "叫我帅亮"
    assert spaced["text"] == "叫 我帅亮"
    assert compact["vision_index"] == spaced["vision_index"] == 0


def test_detached_chat_body_hash_is_stable():
    image = Image.new("RGB", (720, 1800), (247, 247, 247))
    try:
        first = WeChatGuiRpaBot._detached_chat_body_hash(image)
        second = WeChatGuiRpaBot._detached_chat_body_hash(image)
    finally:
        image.close()

    assert first
    assert first == second


def test_detached_stable_chat_body_hash_ignores_media_frame_changes():
    first = Image.new("RGB", (720, 1800), (247, 247, 247))
    second = Image.new("RGB", (720, 1800), (247, 247, 247))
    try:
        for image, color in ((first, (180, 40, 40)), (second, (40, 80, 190))):
            draw = ImageDraw.Draw(image)
            draw.rectangle((190, 320, 460, 540), fill=color)

        assert WeChatGuiRpaBot._detached_chat_body_hash(first) != WeChatGuiRpaBot._detached_chat_body_hash(second)
        assert (
            WeChatGuiRpaBot._detached_stable_chat_body_hash(first)
            == WeChatGuiRpaBot._detached_stable_chat_body_hash(second)
        )

        changed_layout = Image.new("RGB", (720, 1800), (247, 247, 247))
        ImageDraw.Draw(changed_layout).rectangle((190, 380, 460, 600), fill=(180, 40, 40))
        try:
            assert (
                WeChatGuiRpaBot._detached_stable_chat_body_hash(first)
                != WeChatGuiRpaBot._detached_stable_chat_body_hash(changed_layout)
            )
        finally:
            changed_layout.close()
    finally:
        first.close()
        second.close()


def test_detached_stable_chat_body_hash_masks_small_left_stickers():
    first = Image.new("RGB", (1826, 1940), (247, 247, 247))
    second = Image.new("RGB", (1826, 1940), (247, 247, 247))
    try:
        for image, color in ((first, (15, 15, 15)), (second, (220, 220, 220))):
            draw = ImageDraw.Draw(image)
            draw.rectangle((137, 1605, 230, 1712), fill=color)

        assert WeChatGuiRpaBot._detached_chat_body_hash(first) != WeChatGuiRpaBot._detached_chat_body_hash(second)
        assert (
            WeChatGuiRpaBot._detached_stable_chat_body_hash(first)
            == WeChatGuiRpaBot._detached_stable_chat_body_hash(second)
        )
    finally:
        first.close()
        second.close()


def test_detached_text_anchor_shift_detects_structural_scroll():
    previous = [
        {"key": "动图会导致截图的hash一直变", "y": 400},
        {"key": "一直提示有新信息", "y": 520},
        {"key": "不过还好doubao够便宜", "y": 650},
        {"key": "测试看看", "y": 900},
    ]
    current = [{**anchor, "y": anchor["y"] - 80} for anchor in previous]

    assert WeChatGuiRpaBot._detached_text_anchor_changed(previous, current) is True


def test_detached_text_anchor_shift_ignores_jitter_and_animation():
    previous = [
        {"key": "动图会导致截图的hash一直变", "y": 400},
        {"key": "一直提示有新信息", "y": 520},
        {"key": "不过还好doubao够便宜", "y": 650},
        {"key": "测试看看", "y": 900},
    ]
    current = [
        {"key": "动图会导致截图的hash一直变", "y": 404},
        {"key": "一直提示有新信息", "y": 517},
        {"key": "不过还好doubao够便宜", "y": 652},
        {"key": "测试看看", "y": 902},
    ]

    assert WeChatGuiRpaBot._detached_text_anchor_changed(previous, current) is False


def test_detached_text_anchor_shift_detects_new_tail_text():
    previous = [
        {"key": "动图会导致截图的hash一直变", "y": 400},
        {"key": "一直提示有新信息", "y": 520},
        {"key": "不过还好doubao够便宜", "y": 650},
    ]
    current = [
        *previous,
        {"key": "这是一条新的文字消息", "y": 760},
    ]

    assert WeChatGuiRpaBot._detached_text_anchor_changed(previous, current) is True


def test_detached_text_anchor_shift_returns_unknown_when_too_few_anchors():
    assert (
        WeChatGuiRpaBot._detached_text_anchor_changed(
            [{"key": "只有一个文本", "y": 100}],
            [{"key": "只有一个文本", "y": 40}],
        )
        is None
    )


def test_vision_sender_is_canonicalized_with_people_aliases(tmp_path):
    aliases = tmp_path / "PEOPLE_ALIASES.md"
    aliases.write_text("- 游戏王者陈冠东 -> 游戳王者陳冠東\n", encoding="utf-8")

    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(people_aliases_enabled=True)
    bot.people_alias_resolver = PersonAliasResolver(str(aliases))

    messages = WeChatGuiRpaBot._vision_messages_to_visible_messages(
        [{"sender": "游戳王者陳冠東", "content": "30系自己出问题的可能性很高"}]
    )
    normalized = bot._canonicalize_visible_messages(messages)

    assert normalized[0]["sender"] == "游戏王者陈冠东"
    assert normalized[0]["sender_raw"] == "游戳王者陳冠東"


def test_llm_visible_message_normalizer_accepts_legacy_shapes():
    messages = LlmReplyGenerator._normalize_visible_message_items(
        {
            "recent_messages": [
                {"role": "assistant", "text": "收到"},
                {"sender": "张三", "text": None},
            ]
        }
    )

    assert messages == [
        {"sender": "我", "content": "收到"},
        {"sender": "张三", "content": None},
    ]
