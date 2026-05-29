from wechat_rpa.bridge import BridgeClient


def test_bridge_extracts_common_reply_fields():
    assert BridgeClient._extract_reply({"reply": "你好"}) == "你好"
    assert BridgeClient._extract_reply({"message": "收到"}) == "收到"
    assert BridgeClient._extract_reply({"replies": ["第一句", "第二句"]}) == "第一句\n第二句"


def test_bridge_extracts_openai_style_reply():
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "外部"},
                        {"type": "text", "text": "回复"},
                    ]
                }
            }
        ]
    }
    assert BridgeClient._extract_reply(payload) == "外部\n回复"


def test_bridge_send_flag_defaults_true():
    assert BridgeClient._extract_send({}) is True
    assert BridgeClient._extract_send({"send_reply": False}) is False


def test_bridge_extracts_openclaw_payload_text():
    payload = {
        "result": {
            "payloads": [
                {"text": "第一句"},
                {"text": "第二句"},
            ]
        }
    }
    assert BridgeClient._extract_openclaw_text(payload) == "第一句\n第二句"


def test_bridge_extracts_openclaw_final_text_fallback():
    payload = {
        "result": {
            "payloads": [],
            "meta": {"finalAssistantVisibleText": "收到"},
        }
    }
    assert BridgeClient._extract_openclaw_text(payload) == "收到"
