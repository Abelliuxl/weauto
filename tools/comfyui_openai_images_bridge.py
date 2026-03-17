#!/usr/bin/env python3
from __future__ import annotations

import base64
import copy
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_ids(name: str) -> list[str]:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return []
    out: list[str] = []
    for item in raw.split(","):
        text = item.strip()
        if text:
            out.append(text)
    return out


@dataclass
class Settings:
    host: str
    port: int
    comfy_base_url: str
    api_key: str
    workflow_path: Path
    output_dir: Path
    timeout_sec: float
    poll_interval_sec: float
    max_wait_sec: float
    positive_node_ids: list[str]
    negative_node_ids: list[str]
    latent_node_ids: list[str]
    sampler_node_ids: list[str]


def load_settings() -> Settings:
    base = str(os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")).strip().rstrip("/")
    return Settings(
        host=str(os.getenv("BRIDGE_HOST", "0.0.0.0")).strip() or "0.0.0.0",
        port=_env_int("BRIDGE_PORT", 8787),
        comfy_base_url=base,
        api_key=str(os.getenv("BRIDGE_API_KEY", "")).strip(),
        workflow_path=Path(
            str(os.getenv("COMFY_WORKFLOW_PATH", "./workflow_api.json")).strip()
        ).expanduser(),
        output_dir=Path(str(os.getenv("BRIDGE_OUTPUT_DIR", "/tmp/comfy_bridge")).strip()).expanduser(),
        timeout_sec=max(3.0, _env_float("COMFY_TIMEOUT_SEC", 60.0)),
        poll_interval_sec=max(0.2, _env_float("COMFY_POLL_INTERVAL_SEC", 1.0)),
        max_wait_sec=max(5.0, _env_float("COMFY_MAX_WAIT_SEC", 180.0)),
        positive_node_ids=_env_ids("COMFY_POSITIVE_NODE_IDS"),
        negative_node_ids=_env_ids("COMFY_NEGATIVE_NODE_IDS"),
        latent_node_ids=_env_ids("COMFY_LATENT_NODE_IDS"),
        sampler_node_ids=_env_ids("COMFY_SAMPLER_NODE_IDS"),
    )


SETTINGS = load_settings()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        # Client closed the socket before response flush (e.g. upstream timeout/cancel).
        return


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_len = str(handler.headers.get("Content-Length", "0")).strip()
    try:
        length = max(0, int(raw_len))
    except Exception:
        length = 0
    data = handler.rfile.read(length) if length > 0 else b"{}"
    if not data:
        return {}
    parsed = json.loads(data.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise RuntimeError("request body must be json object")
    return parsed


def _parse_size(raw: str, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    text = str(raw or "").strip().lower()
    m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", text)
    if not m:
        return default
    w = int(m.group(1))
    h = int(m.group(2))
    if w < 64 or h < 64 or w > 4096 or h > 4096:
        return default
    return w, h


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=SETTINGS.timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"comfy http {exc.code}: {detail[:320]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"comfy network error: {exc}") from exc
    obj = json.loads(raw or "{}")
    if not isinstance(obj, dict):
        raise RuntimeError("comfy response is not json object")
    return obj


def _http_bytes(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=SETTINGS.timeout_sec) as resp:
        data = resp.read()
        content_type = str(resp.headers.get("Content-Type", "")).lower()
    if not data:
        raise RuntimeError("empty image bytes")
    return data, content_type


def _node_items(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for node_id, node in workflow.items():
        if isinstance(node_id, str) and isinstance(node, dict):
            out.append((node_id, node))
    return out


def _pick_node_ids(
    workflow: dict[str, Any],
    *,
    class_type: str,
    explicit_ids: list[str],
) -> list[str]:
    if explicit_ids:
        return [x for x in explicit_ids if x in workflow]
    return [
        node_id
        for node_id, node in _node_items(workflow)
        if str(node.get("class_type", "")).strip() == class_type
    ]


def _patch_workflow(
    workflow_template: dict[str, Any],
    *,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    seed: int,
) -> dict[str, Any]:
    wf = copy.deepcopy(workflow_template)

    clip_ids = _pick_node_ids(
        wf, class_type="CLIPTextEncode", explicit_ids=SETTINGS.positive_node_ids + SETTINGS.negative_node_ids
    )
    if not clip_ids:
        clip_ids = [node_id for node_id, node in _node_items(wf) if "text" in (node.get("inputs") or {})]
    if clip_ids:
        first = clip_ids[0]
        wf[first].setdefault("inputs", {})["text"] = prompt
        if len(clip_ids) > 1:
            wf[clip_ids[1]].setdefault("inputs", {})["text"] = negative_prompt

    latent_ids = _pick_node_ids(wf, class_type="EmptyLatentImage", explicit_ids=SETTINGS.latent_node_ids)
    for node_id in latent_ids:
        inputs = wf[node_id].setdefault("inputs", {})
        if "width" in inputs:
            inputs["width"] = int(width)
        if "height" in inputs:
            inputs["height"] = int(height)

    sampler_ids = _pick_node_ids(wf, class_type="KSampler", explicit_ids=SETTINGS.sampler_node_ids)
    for node_id in sampler_ids:
        inputs = wf[node_id].setdefault("inputs", {})
        if "seed" in inputs:
            inputs["seed"] = int(seed)

    return wf


def _extract_first_image(history_item: dict[str, Any]) -> dict[str, str]:
    outputs = history_item.get("outputs")
    if not isinstance(outputs, dict):
        raise RuntimeError("comfy history missing outputs")
    for node_data in outputs.values():
        if not isinstance(node_data, dict):
            continue
        images = node_data.get("images")
        if not isinstance(images, list) or not images:
            continue
        first = images[0]
        if not isinstance(first, dict):
            continue
        filename = str(first.get("filename", "")).strip()
        if not filename:
            continue
        return {
            "filename": filename,
            "subfolder": str(first.get("subfolder", "")).strip(),
            "type": str(first.get("type", "output")).strip() or "output",
        }
    raise RuntimeError("comfy history has no image output")


def _queue_and_wait(workflow: dict[str, Any]) -> dict[str, str]:
    payload = {"prompt": workflow, "client_id": str(uuid4())}
    queued = _http_json("POST", f"{SETTINGS.comfy_base_url}/prompt", payload=payload)
    prompt_id = str(queued.get("prompt_id", "")).strip()
    if not prompt_id:
        raise RuntimeError("comfy /prompt missing prompt_id")
    started = time.time()
    while time.time() - started <= SETTINGS.max_wait_sec:
        history = _http_json("GET", f"{SETTINGS.comfy_base_url}/history/{prompt_id}")
        item = history.get(prompt_id) if isinstance(history.get(prompt_id), dict) else None
        if item and isinstance(item.get("outputs"), dict) and item.get("outputs"):
            return _extract_first_image(item)
        time.sleep(SETTINGS.poll_interval_sec)
    raise RuntimeError(f"comfy generation timeout after {SETTINGS.max_wait_sec:.1f}s")


def _save_image(data: bytes, filename_hint: str) -> Path:
    SETTINGS.output_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename_hint).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        ext = ".png"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    nonce = uuid4().hex[:8]
    out = SETTINGS.output_dir / f"comfy_{stamp}_{nonce}{ext}"
    out.write_bytes(data)
    return out


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "ComfyOpenAIBridge/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            body = json.dumps(
                {
                    "ok": True,
                    "comfy_base_url": SETTINGS.comfy_base_url,
                    "workflow_path": str(SETTINGS.workflow_path),
                    "output_dir": str(SETTINGS.output_dir),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        if parsed.path.startswith("/files/"):
            name = parsed.path[len("/files/") :].strip()
            safe = os.path.basename(name)
            target = (SETTINGS.output_dir / safe).resolve()
            if (not target.exists()) or (not target.is_file()):
                _json_response(self, 404, {"error": {"message": "file not found"}})
                return
            try:
                size = int(target.stat().st_size)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        _json_response(self, 404, {"error": {"message": "not found"}})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "comfy_base_url": SETTINGS.comfy_base_url,
                    "workflow_path": str(SETTINGS.workflow_path),
                    "output_dir": str(SETTINGS.output_dir),
                },
            )
            return

        if parsed.path.startswith("/files/"):
            name = parsed.path[len("/files/") :].strip()
            safe = os.path.basename(name)
            target = (SETTINGS.output_dir / safe).resolve()
            if (not target.exists()) or (not target.is_file()):
                _json_response(self, 404, {"error": {"message": "file not found"}})
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        _json_response(self, 404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/v1/images/generations":
            _json_response(self, 404, {"error": {"message": "not found"}})
            return

        try:
            if SETTINGS.api_key:
                auth = str(self.headers.get("Authorization", "")).strip()
                if auth != f"Bearer {SETTINGS.api_key}":
                    _json_response(
                        self,
                        401,
                        {"error": {"message": "invalid api key", "type": "authentication_error"}},
                    )
                    return

            if not SETTINGS.workflow_path.exists():
                raise RuntimeError(f"workflow not found: {SETTINGS.workflow_path}")
            workflow_template = json.loads(SETTINGS.workflow_path.read_text(encoding="utf-8"))
            if not isinstance(workflow_template, dict):
                raise RuntimeError("workflow_api.json must be json object")

            req = _read_json_body(self)
            prompt = str(req.get("prompt", "")).strip()
            if not prompt:
                raise RuntimeError("prompt is required")
            negative_prompt = str(req.get("negative_prompt", "")).strip()
            width, height = _parse_size(str(req.get("size", "1024x1024")))
            count = max(1, min(4, int(req.get("n", 1) or 1)))
            response_format = str(req.get("response_format", "url")).strip().lower() or "url"
            fixed_seed = req.get("seed")

            data_items: list[dict[str, str]] = []
            for idx in range(count):
                if fixed_seed is None:
                    seed = random.randint(1, 2_147_483_647)
                else:
                    seed = int(fixed_seed) + idx
                workflow = _patch_workflow(
                    workflow_template,
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    seed=seed,
                )
                image_ref = _queue_and_wait(workflow)
                params = urllib.parse.urlencode(image_ref)
                image_bytes, _ = _http_bytes(f"{SETTINGS.comfy_base_url}/view?{params}")
                out = _save_image(image_bytes, image_ref.get("filename", "image.png"))

                if response_format == "b64_json":
                    data_items.append(
                        {"b64_json": base64.b64encode(image_bytes).decode("ascii")}
                    )
                else:
                    host = str(self.headers.get("Host", f"127.0.0.1:{SETTINGS.port}")).strip()
                    data_items.append({"url": f"http://{host}/files/{out.name}"})

            _json_response(
                self,
                200,
                {
                    "created": int(time.time()),
                    "data": data_items,
                },
            )
        except Exception as exc:
            _json_response(
                self,
                500,
                {"error": {"message": str(exc), "type": "server_error"}},
            )


def main() -> None:
    print(
        f"[bridge] listen=http://{SETTINGS.host}:{SETTINGS.port} "
        f"comfy={SETTINGS.comfy_base_url} workflow={SETTINGS.workflow_path}"
    )
    server = ThreadingHTTPServer((SETTINGS.host, SETTINGS.port), BridgeHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
