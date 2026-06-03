from types import SimpleNamespace

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
