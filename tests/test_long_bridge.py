from __future__ import annotations

import base64
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.config import load_config
from wechat_rpa.long_bridge import LongBridgeClient, _PendingTurn


def _cfg(tmp_path: Path):
    return SimpleNamespace(
        processing_mode="long_bridge",
        long_bridge_url="ws://192.168.5.104:18789/weauto/channel",
        long_bridge_token="secret",
        long_bridge_account_id="default",
        long_bridge_timeout_sec=10.0,
        long_bridge_reconnect_min_sec=0.2,
        long_bridge_reconnect_max_sec=1.0,
        long_bridge_heartbeat_sec=20.0,
        long_bridge_attachment_max_mb=2,
        long_bridge_inbound_dedupe_sec=120.0,
    )


def test_connection_url_adds_account_id(tmp_path: Path) -> None:
    client = LongBridgeClient(_cfg(tmp_path))
    assert client._connection_url() == (
        "ws://192.168.5.104:18789/weauto/channel?account_id=default"
    )


def test_build_attachment_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "photo.bin"
    source.write_bytes(b"hello")
    client = LongBridgeClient(_cfg(tmp_path))

    attachment = client.build_attachment(str(source))

    assert attachment is not None
    assert attachment["name"] == "photo.bin"
    assert base64.b64decode(attachment["data_base64"]) == b"hello"


def test_handle_remote_reply_and_completion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = LongBridgeClient(_cfg(tmp_path))
    pending = _PendingTurn(request_id="req-1", frame={})
    client._pending["req-1"] = pending

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, value: str) -> None:
            self.sent.append(value)

    socket = Socket()
    client._handle_frame(
        socket,
        """{
          "type": "message.send",
          "request_id": "req-1",
          "message_id": "msg-1",
          "text": "remote reply",
          "attachments": [{
            "name": "result.txt",
            "data_base64": "aGVsbG8="
          }]
        }""",
    )
    client._handle_frame(
        socket,
        '{"type":"turn.complete","request_id":"req-1","status":"ok"}',
    )

    assert pending.done.is_set()
    assert pending.replies == ["remote reply"]
    assert pending.attachments[0].read_bytes() == b"hello"
    assert '"type": "message.received"' in socket.sent[0]


def test_pending_frames_are_sent_once_per_connection(tmp_path: Path) -> None:
    client = LongBridgeClient(_cfg(tmp_path))
    client._pending["req-1"] = _PendingTurn(
        request_id="req-1",
        frame={"type": "message.create", "event_id": "req-1"},
    )

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, value: str) -> None:
            self.sent.append(value)

    socket = Socket()
    sent_request_ids: set[str] = set()
    client._send_pending(socket, sent_request_ids)
    client._send_pending(socket, sent_request_ids)

    assert len(socket.sent) == 1
    assert json.loads(socket.sent[0])["event_id"] == "req-1"


def test_proactive_message_is_dispatched(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = LongBridgeClient(_cfg(tmp_path))
    delivered = []
    delivered_event = threading.Event()

    def handle(outbound) -> None:
        delivered.append(outbound)
        delivered_event.set()

    client.set_outbound_handler(handle)

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send(self, value: str) -> None:
            self.sent.append(value)

    socket = Socket()
    client._handle_frame(
        socket,
        json.dumps(
            {
                "type": "message.send",
                "message_id": "proactive-1",
                "conversation": {
                    "id": "chat-1",
                    "title": "测试会话",
                },
                "text": "scheduled reply",
            }
        ),
    )

    assert delivered_event.wait(timeout=1.0)
    assert delivered[0].conversation_id == "chat-1"
    assert delivered[0].conversation_title == "测试会话"
    assert delivered[0].result.reply == "scheduled reply"
    assert '"type": "message.received"' in socket.sent[0]


def test_load_config_accepts_long_bridge_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
processing_mode = "long_bridge"
long_bridge_url = "ws://remote:18789/weauto/channel"
long_bridge_token = "token"
long_bridge_account_id = "work"
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.processing_mode == "long_bridge"
    assert cfg.long_bridge_url == "ws://remote:18789/weauto/channel"
    assert cfg.long_bridge_token == "token"
    assert cfg.long_bridge_account_id == "work"


def test_completed_request_is_suppressed_during_dedupe_window(tmp_path: Path) -> None:
    client = LongBridgeClient(_cfg(tmp_path))
    client.start = lambda: None
    client._recent_completed["same-event"] = 1.0e12

    result = client.request_reply(
        {"event_id": "same-event", "message": {"text": "duplicate"}}
    )

    assert result.send is False
    assert client._pending == {}


def test_inflight_request_is_not_replaced_by_duplicate(tmp_path: Path) -> None:
    client = LongBridgeClient(_cfg(tmp_path))
    client.start = lambda: None
    original = _PendingTurn(request_id="same-event", frame={})
    client._pending["same-event"] = original

    result = client.request_reply(
        {"event_id": "same-event", "message": {"text": "duplicate"}}
    )

    assert result.send is False
    assert client._pending["same-event"] is original


def test_long_bridge_event_id_is_stable_for_the_same_position_fingerprint() -> None:
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(receiver_mode="detached_windows")
    bot.long_bridge_client = SimpleNamespace(build_attachment=lambda _path: None)
    bot._session_key_for_row = lambda _row: "wechat:group:测试群"
    bot._sender_from_prefixed_text = lambda _text: ""
    row = SimpleNamespace(title="测试群", preview="叫我帅亮", text="叫我帅亮")
    source = {
        "sender": "刘晓亮",
        "text": "叫我帅亮",
        "content_type": "text",
        "fingerprint": "vision|other|text|刘晓亮|叫我帅亮|0",
    }

    first = bot._long_bridge_event_payload(
        row,
        reason="mention",
        is_group=True,
        is_admin=False,
        latest_message="叫我帅亮",
        source_message=source,
    )
    second = bot._long_bridge_event_payload(
        row,
        reason="mention",
        is_group=True,
        is_admin=False,
        latest_message="叫我帅亮",
        source_message=source,
    )

    assert first["event_id"] == second["event_id"]
    assert first["message"]["text"] == "叫我帅亮"
