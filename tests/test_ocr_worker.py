"""Tests for the recycled capture+OCR worker proxy (DetachedOcrProxy).

These cover the IPC protocol, fail-open behavior, and RSS-driven recycling
without spawning a real subprocess: the proxy talks to a stub Popen whose
stdin/stdout streams are in-memory queues. The worker module's own loop
(``run_worker``) is exercised end-to-end against a fake capture+OCR.
"""

import json
from types import SimpleNamespace

import pytest

from wechat_rpa.bot import DetachedOcrProxy
from wechat_rpa.ocr_worker import run_worker
from wechat_rpa.visible_message_parser import VisibleChatSnapshot


def _cfg(**overrides):
    base = dict(
        ocr_worker_enabled=True,
        ocr_worker_max_rss_mb=2048,
        ocr_worker_request_timeout_sec=2.0,
        ocr_worker_ready_timeout_sec=2.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeStream:
    """A minimal bidirectional line stream with a preloaded output buffer."""

    def __init__(self):
        self.write_buffer = []
        self._read_buffer = []
        self.closed = False

    def write(self, text):
        self.write_buffer.append(text)

    def flush(self):
        pass

    def readline(self):
        if self._read_buffer:
            return self._read_buffer.pop(0)
        # Simulate EOF.
        return ""

    def push(self, line):
        self._read_buffer.append(line)

    def close(self):
        self.closed = True


class _FakePopen:
    """Stub subprocess.Popen: stdout is preloaded, poll() drives lifecycle.

    By default the first stdout readline() returns the ``{"ready": true}``
    handshake so ``_spawn`` succeeds; tests then ``push()`` further lines.
    Pass ``ready_first=False`` to simulate a failed warmup.
    """

    def __init__(self, *args, ready_first=True, **kwargs):
        self.stdin = _FakeStream()
        self.stdout = _FakeStream()
        if ready_first:
            self.stdout.push(json.dumps({"ready": True}) + "\n")
        self._returncode = None
        self.terminated = False
        self.killed = False
        self.env = kwargs.get("env", {})

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = -15

    def kill(self):
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        return self._returncode if self._returncode is not None else 0


def _make_proxy(monkeypatch, cfg=None, popener=None, **overrides):
    cfg = cfg or _cfg(**overrides)
    proxy = DetachedOcrProxy(cfg, log_fn=lambda *_a, **_k: None)

    fakes = []

    if popener is None:
        def popener(*args, **kwargs):
            return _FakePopen(**kwargs)

    def _fake_popen(*args, **kwargs):
        fake = popener(*args, **kwargs)
        fakes.append(fake)
        return fake

    monkeypatch.setattr("wechat_rpa.bot.subprocess.Popen", _fake_popen)
    return proxy, fakes


# ---------------------------------------------------------------------------
# Snapshot reconstruction
# ---------------------------------------------------------------------------


def test_snapshot_from_dict_roundtrips_fields():
    payload = {
        "schema": "weauto_visible_messages_v1",
        "window_id": 42,
        "title": "测试",
        "captured_at": 1700000000.5,
        "source": "ocr_worker",
        "image_size": {"width": 800, "height": 1600},
        "messages": [
            {"side": "self", "text": "hi", "content_type": "text", "fingerprint": "self|text|self|hi"},
            {"side": "other", "text": "yo", "content_type": "text", "fingerprint": "other|text|A|yo"},
        ],
        "latest_message": {"side": "other", "text": "yo"},
        "debug": {"ocr_elapsed_sec": 0.1},
    }
    snapshot = DetachedOcrProxy._snapshot_from_dict(payload)
    assert isinstance(snapshot, VisibleChatSnapshot)
    assert snapshot.window_id == 42
    assert snapshot.title == "测试"
    assert snapshot.image_size == {"width": 800, "height": 1600}
    assert len(snapshot.messages) == 2
    assert snapshot.latest_message == {"side": "other", "text": "yo"}
    assert snapshot.debug == {"ocr_elapsed_sec": 0.1}


def test_snapshot_from_dict_handles_missing_and_malformed_fields():
    # Barely-populated payloads must not raise.
    snapshot = DetachedOcrProxy._snapshot_from_dict({})
    assert snapshot.window_id == 0
    assert snapshot.title == ""
    assert snapshot.messages == []
    assert snapshot.latest_message is None
    assert snapshot.debug is None
    assert snapshot.image_size == {"width": 0, "height": 0}

    snapshot2 = DetachedOcrProxy._snapshot_from_dict({"messages": "not a list", "image_size": None})
    assert snapshot2.messages == []
    assert snapshot2.image_size == {"width": 0, "height": 0}


# ---------------------------------------------------------------------------
# Handshake + happy path
# ---------------------------------------------------------------------------


def test_parse_returns_snapshot_after_ready_handshake(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    assert proxy._ensure_worker()
    fake = fakes[0]
    # The handshake consumed the {"ready": true} line during _spawn.
    assert proxy._ready is True

    # Feed a snapshot response to the next read.
    snapshot_payload = {
        "window_id": 7,
        "title": "群",
        "messages": [{"side": "other", "text": "hello"}],
        "latest_message": {"side": "other", "text": "hello"},
        "image_size": {"width": 100, "height": 200},
        "schema": "weauto_visible_messages_v1",
        "captured_at": 1.0,
        "source": "ocr_worker",
    }
    fake.stdout.push(json.dumps({"snapshot": snapshot_payload}) + "\n")

    result = proxy.parse(7, title="群")
    assert result is not None
    assert result.window_id == 7
    assert result.messages == [{"side": "other", "text": "hello"}]
    # The request was written to the worker's stdin.
    request = json.loads(fake.stdin.write_buffer[0])
    assert request["window_id"] == 7
    assert request["title"] == "群"
    proxy.close()


def test_spawn_terminates_worker_when_ready_handshake_missing(monkeypatch):
    # Worker returns garbage instead of {"ready": true}.
    def popener(*args, **kwargs):
        fake = _FakePopen(ready_first=False, **kwargs)
        fake.stdout.push("garbage\n")
        return fake

    proxy, fakes = _make_proxy(monkeypatch, popener=popener)
    assert proxy._ensure_worker() is False
    assert proxy._ready is False
    # A subsequent parse fail-opens (returns None) rather than blocking.
    assert proxy.parse(1, title="x") is None


# ---------------------------------------------------------------------------
# Fail-open paths
# ---------------------------------------------------------------------------


def test_parse_failopens_on_error_payload(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fake = fakes[0]
    fake.stdout.push(json.dumps({"error": "boom"}) + "\n")
    assert proxy.parse(1, title="x") is None
    assert fake.terminated is True
    assert proxy._retry_count == 1
    assert proxy._skipped_request_count == 1


def test_parse_failopens_on_invalid_json(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push("not json\n")
    assert proxy.parse(1, title="x") is None


def test_parse_failopens_on_eof_and_recycles(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    # EOF: empty readline.
    assert proxy.parse(1, title="x") is None
    # The dead worker was recycled (terminated).
    assert fakes[0].terminated is True
    assert proxy._proc is None


# ---------------------------------------------------------------------------
# RSS-driven recycling via __exit__
# ---------------------------------------------------------------------------


def test_parse_retries_with_fresh_worker_after_rss_exit(monkeypatch):
    snapshot_payload = {
        "window_id": 1,
        "title": "x",
        "messages": [{"side": "other", "text": "retried"}],
        "latest_message": {"side": "other", "text": "retried"},
        "image_size": {"width": 100, "height": 200},
        "schema": "weauto_visible_messages_v1",
        "captured_at": 1.0,
        "source": "ocr_worker",
    }
    spawn_count = 0

    def popener(*args, **kwargs):
        nonlocal spawn_count
        fake = _FakePopen(**kwargs)
        spawn_count += 1
        if spawn_count == 2:
            fake.stdout.push(json.dumps({"snapshot": snapshot_payload}) + "\n")
        return fake

    proxy, fakes = _make_proxy(monkeypatch, popener=popener)
    proxy._ensure_worker()
    fake = fakes[0]
    fake.stdout.push(json.dumps({"__exit__": True, "reason": "rss_limit", "rss_mb": 2100}) + "\n")
    result = proxy.parse(1, title="x")
    assert result is not None
    assert result.messages == [{"side": "other", "text": "retried"}]
    assert proxy._recycle_count == 1
    assert proxy._retry_count == 1
    assert proxy._skipped_request_count == 0
    assert fake.terminated is True
    assert len(fakes) == 2
    proxy.close()


def test_parse_skips_after_retry_also_fails(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push(
        json.dumps({"__exit__": True, "reason": "rss_limit", "rss_mb": 2100}) + "\n"
    )

    assert proxy.parse(1, title="x") is None
    assert len(fakes) == 2
    assert proxy._retry_count == 1
    assert proxy._skipped_request_count == 1


# ---------------------------------------------------------------------------
# capture_only (vision-path capture isolation)
# ---------------------------------------------------------------------------


def test_capture_only_returns_image_path_and_hash(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fake = fakes[0]
    fake.stdout.push(json.dumps({
        "image_path": "/tmp/weauto-capture-xyz/win_42.png",
        "body_hash": "abc123def456",
        "stable_body_hash": "stable456",
        "text_anchors": [{"key": "hello", "x": 10, "y": 20}],
        "image_size": {"width": 1600, "height": 1900},
    }) + "\n")
    result = proxy.capture_only(42)
    assert result is not None
    image_path, body_hash, stable_body_hash, image_size, text_anchors = result
    assert str(image_path) == "/tmp/weauto-capture-xyz/win_42.png"
    assert body_hash == "abc123def456"
    assert stable_body_hash == "stable456"
    assert image_size == {"width": 1600, "height": 1900}
    assert text_anchors == [{"key": "hello", "x": 10, "y": 20}]
    # The request carried mode=capture_only.
    request = json.loads(fake.stdin.write_buffer[0])
    assert request["mode"] == "capture_only"
    assert request["window_id"] == 42
    proxy.close()


def test_capture_only_failopens_on_missing_fields(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push(json.dumps({"image_path": "/tmp/x.png"}) + "\n")  # no body_hash
    assert proxy.capture_only(1) is None
    fakes[0].stdout.push(json.dumps({"body_hash": "h"}) + "\n")  # no image_path
    assert proxy.capture_only(1) is None


def test_capture_only_failopens_on_error(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push(json.dumps({"error": "capture failed: boom"}) + "\n")
    assert proxy.capture_only(1) is None


# ---------------------------------------------------------------------------
# list_windows (window enumeration isolation)
# ---------------------------------------------------------------------------


def test_list_windows_returns_window_dicts(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push(json.dumps({"windows": [
        {"window_id": 101, "owner": "WeChat", "title": "real刘晓亮", "x": 0, "y": 0, "width": 800, "height": 900},
        {"window_id": 202, "owner": "WeChat", "title": "群-魔兽", "x": 820, "y": 0, "width": 800, "height": 900},
    ]}) + "\n")
    result = proxy.list_windows("WeChat")
    assert result is not None
    assert len(result) == 2
    assert result[0] == {"window_id": 101, "owner": "WeChat", "title": "real刘晓亮", "x": 0, "y": 0, "width": 800, "height": 900}
    request = json.loads(fakes[0].stdin.write_buffer[0])
    assert request["mode"] == "list_windows"
    assert request["app_name"] == "WeChat"
    proxy.close()


def test_list_windows_failopens_on_error(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push(json.dumps({"error": "list_windows failed: boom"}) + "\n")
    assert proxy.list_windows("WeChat") is None


def test_list_windows_failopens_on_malformed(monkeypatch):
    proxy, fakes = _make_proxy(monkeypatch)
    proxy._ensure_worker()
    fakes[0].stdout.push(json.dumps({"windows": "not a list"}) + "\n")
    assert proxy.list_windows("WeChat") is None


def test_enumerate_detached_windows_skips_on_worker_none(monkeypatch):
    from wechat_rpa.bot import WeChatGuiRpaBot

    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.ocr_proxy = SimpleNamespace(list_windows=lambda *a, **k: None)
    captured = {"called": False}

    def _fake_inproc(app_name):
        captured["called"] = True
        raise AssertionError("strict worker mode must not enumerate in-process")

    bot.cfg = SimpleNamespace(app_name="WeChat")
    monkeypatch.setattr("wechat_rpa.bot.list_detached_wechat_windows", _fake_inproc)
    result = bot._enumerate_detached_windows()
    assert captured["called"] is False
    assert result == []


def test_enumerate_detached_windows_uses_worker_when_available(monkeypatch):
    from wechat_rpa.bot import WeChatGuiRpaBot

    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.ocr_proxy = SimpleNamespace(list_windows=lambda app_name: [
        {"window_id": 55, "owner": "WeChat", "title": "via-worker", "x": 1, "y": 2, "width": 3, "height": 4},
    ])
    bot.cfg = SimpleNamespace(app_name="WeChat")
    # In-process must NOT be called when worker succeeds.
    def _should_not_call(app_name):
        raise AssertionError("in-process enumeration should not run when worker is available")
    monkeypatch.setattr("wechat_rpa.bot.list_detached_wechat_windows", _should_not_call)
    result = bot._enumerate_detached_windows()
    assert len(result) == 1
    assert result[0].window_id == 55
    assert result[0].title == "via-worker"


def test_capture_detached_image_skips_on_worker_none(monkeypatch):
    from wechat_rpa.bot import WeChatGuiRpaBot

    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.ocr_proxy = SimpleNamespace(capture_only=lambda *a, **k: None)
    bot.cfg = SimpleNamespace(detached_window_capture_backend="quartz")

    def _should_not_capture(*args, **kwargs):
        raise AssertionError("strict worker mode must not capture in-process")

    monkeypatch.setattr("wechat_rpa.bot.capture_window_by_id", _should_not_capture)
    assert bot._capture_detached_image(123) is None


# ---------------------------------------------------------------------------
# worker run_worker loop with a fake capture+parser
# ---------------------------------------------------------------------------


def test_run_worker_emits_ready_then_snapshots(monkeypatch):
    import wechat_rpa.ocr_worker as worker_mod

    written = []

    class _In:
        def __init__(self):
            self.lines = iter([
                json.dumps({"window_id": 5, "title": "t", "image_output_dir": None, "include_debug": False}),
                json.dumps({"window_id": 6, "title": "u", "image_output_dir": None, "include_debug": False}),
                "",  # EOF
            ])

        def readline(self):
            try:
                return next(self.lines)
            except StopIteration:
                return ""

    class _Out:
        def write(self, text):
            written.append(text)

        def flush(self):
            pass

    monkeypatch.setattr(worker_mod.sys, "stdin", _In())
    monkeypatch.setattr(worker_mod.sys, "stdout", _Out())
    monkeypatch.setattr(worker_mod, "_current_rss_mb", lambda: 100)

    captured_images = []

    def _fake_capture(window_id, *, backend="screencapture"):
        captured_images.append((window_id, backend))
        return _DummyImage()

    class _DummyParser:
        def parse(self, image, *, window_id, title, image_output_dir=None, include_debug=False):
            return VisibleChatSnapshot(
                schema="weauto_visible_messages_v1",
                window_id=window_id,
                title=title,
                captured_at=1.0,
                source="ocr_worker",
                image_size={"width": 10, "height": 20},
                messages=[{"side": "other", "text": title}],
                latest_message={"side": "other", "text": title},
                debug=None,
            )

    class _DummyImage:
        def close(self):
            pass

    import wechat_rpa.detached_window_receiver as dwr
    import wechat_rpa.visible_message_parser as vmp

    monkeypatch.setattr(dwr, "capture_window_by_id", _fake_capture)
    # run_worker imports capture_window_by_id locally from the module above, so
    # patching the module attribute is enough; no separate worker-mod patch.
    # Stub the heavy imports inside run_worker so we never load rapidocr.
    import wechat_rpa.ocr as ocr_mod

    class _DummyEngine:
        pass

    monkeypatch.setattr(ocr_mod, "OcrEngine", lambda *a, **k: _DummyEngine())
    # VisibleMessageParser is imported at the top of ocr_worker, so patch the
    # worker module's own attribute (not the source module).
    monkeypatch.setattr(worker_mod, "VisibleMessageParser", lambda engine: _DummyParser())

    run_worker(
        ocr_cfg=SimpleNamespace(),
        capture_backend="screencapture",
        max_rss_mb=0,  # never exit on RSS in this test
    )

    # First line is the ready handshake; then one snapshot per request.
    # _emit() writes payload and "\n" as separate writes, so join+split.
    full_output = "".join(written)
    payloads = [json.loads(line) for line in full_output.splitlines() if line.strip()]
    assert payloads[0] == {"ready": True}
    assert payloads[1]["snapshot"]["window_id"] == 5
    assert payloads[1]["snapshot"]["messages"] == [{"side": "other", "text": "t"}]
    assert payloads[2]["snapshot"]["window_id"] == 6
    # Both windows captured via the worker's capture path.
    assert captured_images == [(5, "screencapture"), (6, "screencapture")]


def test_run_worker_exits_on_rss_limit_before_capture(monkeypatch):
    import wechat_rpa.ocr_worker as worker_mod

    written = []

    class _In:
        def __init__(self):
            self.lines = iter([json.dumps({"window_id": 1, "title": "t"}), ""])

        def readline(self):
            try:
                return next(self.lines)
            except StopIteration:
                return ""

    class _Out:
        def write(self, text):
            written.append(text)

        def flush(self):
            pass

    monkeypatch.setattr(worker_mod.sys, "stdin", _In())
    monkeypatch.setattr(worker_mod.sys, "stdout", _Out())

    call_count = {"capture": 0}

    def _fake_capture(window_id, *, backend="screencapture"):
        call_count["capture"] += 1
        return _DummyImage()

    class _DummyImage:
        def close(self):
            pass

    class _DummyParser:
        def parse(self, image, **kwargs):
            return VisibleChatSnapshot(
                schema="x", window_id=0, title="", captured_at=0.0, source="x",
                image_size={}, messages=[], latest_message=None, debug=None,
            )

    import wechat_rpa.detached_window_receiver as dwr
    import wechat_rpa.visible_message_parser as vmp
    import wechat_rpa.ocr as ocr_mod

    monkeypatch.setattr(dwr, "capture_window_by_id", _fake_capture)
    monkeypatch.setattr(ocr_mod, "OcrEngine", lambda *a, **k: object())
    monkeypatch.setattr(worker_mod, "VisibleMessageParser", lambda engine: _DummyParser())

    # Force RSS to always exceed the cap so the worker exits before capturing.
    monkeypatch.setattr(worker_mod, "_current_rss_mb", lambda: 9999)

    run_worker(ocr_cfg=SimpleNamespace(), capture_backend="quartz", max_rss_mb=100)

    full_output = "".join(written)
    payloads = [json.loads(line) for line in full_output.splitlines() if line.strip()]
    assert payloads[0] == {"ready": True}
    assert payloads[1] == {"__exit__": True, "reason": "rss_limit", "rss_mb": 9999}
    # The capture+OCR work was skipped because RSS was over budget.
    assert call_count["capture"] == 0


def test_run_worker_capture_only_writes_png_and_returns_hash(monkeypatch, tmp_path):
    """capture_only mode persists the capture to a temp PNG and returns the
    path + body hash, without touching the OCR engine."""
    import wechat_rpa.ocr_worker as worker_mod
    from PIL import Image as _PILImage

    written = []
    capture_tmp = tmp_path / "captures"
    capture_tmp.mkdir()
    monkeypatch.setattr(worker_mod, "_capture_tmp_dir", lambda: capture_tmp)

    class _In:
        def __init__(self):
            self.lines = iter([
                json.dumps({"mode": "capture_only", "window_id": 99}),
                "",  # EOF
            ])

        def readline(self):
            try:
                return next(self.lines)
            except StopIteration:
                return ""

    class _Out:
        def write(self, text):
            written.append(text)

        def flush(self):
            pass

    monkeypatch.setattr(worker_mod.sys, "stdin", _In())
    monkeypatch.setattr(worker_mod.sys, "stdout", _Out())

    saved_image = _PILImage.new("RGB", (200, 400), (247, 247, 247))

    def _fake_capture(window_id, *, backend="screencapture"):
        return saved_image

    import wechat_rpa.detached_window_receiver as dwr
    import wechat_rpa.ocr as ocr_mod
    ocr_created = []

    # Hash helpers use VisibleMessageParser class methods, so patch with a real
    # class carrying those attributes, not a factory function.
    class _StubParser:
        _chat_body_bounds = staticmethod(lambda height: (190, min(height - 210, 1720)))
        chat_body_hash = staticmethod(lambda image, mask_media=False: "stable" if mask_media else "raw")

        def __init__(self, engine):
            pass

    monkeypatch.setattr(dwr, "capture_window_by_id", _fake_capture)
    monkeypatch.setattr(
        ocr_mod,
        "OcrEngine",
        lambda *a, **k: ocr_created.append(True) or object(),
    )
    monkeypatch.setattr(worker_mod, "VisibleMessageParser", _StubParser)

    run_worker(ocr_cfg=SimpleNamespace(), capture_backend="quartz", max_rss_mb=0)

    full_output = "".join(written)
    payloads = [json.loads(line) for line in full_output.splitlines() if line.strip()]
    assert payloads[0] == {"ready": True}
    assert payloads[1]["image_path"].endswith("win_99.png")
    assert payloads[1]["body_hash"] == "raw"
    assert payloads[1]["stable_body_hash"] == "stable"
    assert payloads[1]["image_size"] == {"width": 200, "height": 400}
    # The PNG was actually written to disk.
    assert (capture_tmp / "win_99.png").exists()
    assert ocr_created == []


def test_run_worker_capture_only_can_return_text_anchors(monkeypatch, tmp_path):
    import wechat_rpa.ocr_worker as worker_mod
    from PIL import Image as _PILImage

    written = []
    capture_tmp = tmp_path / "captures"
    capture_tmp.mkdir()
    monkeypatch.setattr(worker_mod, "_capture_tmp_dir", lambda: capture_tmp)

    class _In:
        def __init__(self):
            self.lines = iter([
                json.dumps({"mode": "capture_only", "window_id": 99, "include_text_anchors": True}),
                "",
            ])

        def readline(self):
            try:
                return next(self.lines)
            except StopIteration:
                return ""

    class _Out:
        def write(self, text):
            written.append(text)

        def flush(self):
            pass

    class _StubParser:
        chat_body_hash = staticmethod(lambda image, mask_media=False: "stable" if mask_media else "raw")
        text_anchors = staticmethod(lambda image, engine: [{"key": "anchor", "x": 1, "y": 2}])

        def __init__(self, engine):
            pass

    monkeypatch.setattr(worker_mod.sys, "stdin", _In())
    monkeypatch.setattr(worker_mod.sys, "stdout", _Out())

    import wechat_rpa.detached_window_receiver as dwr
    import wechat_rpa.ocr as ocr_mod

    monkeypatch.setattr(dwr, "capture_window_by_id", lambda *_a, **_k: _PILImage.new("RGB", (200, 400)))
    monkeypatch.setattr(ocr_mod, "OcrEngine", lambda *a, **k: object())
    monkeypatch.setattr(worker_mod, "VisibleMessageParser", _StubParser)

    run_worker(ocr_cfg=SimpleNamespace(), capture_backend="quartz", max_rss_mb=0)

    payloads = [json.loads(line) for line in "".join(written).splitlines() if line.strip()]
    assert payloads[1]["text_anchors"] == [{"key": "anchor", "x": 1, "y": 2}]


def test_run_worker_list_windows_returns_window_list(monkeypatch):
    """list_windows mode enumerates windows without capturing or touching OCR."""
    import wechat_rpa.ocr_worker as worker_mod

    written = []

    class _In:
        def __init__(self):
            self.lines = iter([
                json.dumps({"mode": "list_windows", "app_name": "WeChat"}),
                "",  # EOF
            ])

        def readline(self):
            try:
                return next(self.lines)
            except StopIteration:
                return ""

    class _Out:
        def write(self, text):
            written.append(text)

        def flush(self):
            pass

    monkeypatch.setattr(worker_mod.sys, "stdin", _In())
    monkeypatch.setattr(worker_mod.sys, "stdout", _Out())

    from wechat_rpa.detached_window_receiver import DetachedWindowInfo

    fake_windows = [
        DetachedWindowInfo(101, "WeChat", "群-A", 0, 0, 800, 900),
        DetachedWindowInfo(202, "WeChat", "real刘晓亮", 820, 0, 800, 900),
    ]

    import wechat_rpa.detached_window_receiver as dwr
    import wechat_rpa.ocr as ocr_mod

    monkeypatch.setattr(dwr, "list_detached_wechat_windows", lambda app_name: fake_windows)
    monkeypatch.setattr(dwr, "capture_window_by_id", lambda *a, **k: None)
    monkeypatch.setattr(ocr_mod, "OcrEngine", lambda *a, **k: object())
    monkeypatch.setattr(worker_mod, "VisibleMessageParser", lambda engine: object())

    run_worker(ocr_cfg=SimpleNamespace(), capture_backend="quartz", max_rss_mb=0)

    full_output = "".join(written)
    payloads = [json.loads(line) for line in full_output.splitlines() if line.strip()]
    assert payloads[0] == {"ready": True}
    assert len(payloads[1]["windows"]) == 2
    assert payloads[1]["windows"][0] == {
        "window_id": 101, "owner": "WeChat", "title": "群-A",
        "x": 0, "y": 0, "width": 800, "height": 900,
    }
    assert payloads[1]["windows"][1]["title"] == "real刘晓亮"


# ---------------------------------------------------------------------------
# Bot integration: worker miss never falls back to in-process OCR
# ---------------------------------------------------------------------------


def test_parse_detached_snapshot_ocr_skips_on_worker_none():
    from wechat_rpa.bot import WeChatGuiRpaBot

    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.ocr_proxy = SimpleNamespace(parse=lambda *a, **k: None)
    bot.visible_message_parser = SimpleNamespace(
        parse=lambda *a, **k: pytest.fail("strict worker mode must not parse in-process")
    )

    with pytest.raises(RuntimeError, match="ocr worker unavailable"):
        bot._parse_detached_snapshot_ocr(
            "image-placeholder",
            window_id=9,
            title="no-fallback-test",
        )
