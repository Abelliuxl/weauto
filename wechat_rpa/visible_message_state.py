from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any


@dataclass
class WindowMessageState:
    seen: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


class VisibleMessageStateStore:
    def __init__(self) -> None:
        self._windows: dict[int, WindowMessageState] = {}

    def export_state(self) -> dict[str, Any]:
        windows: dict[str, dict[str, Any]] = {}
        for window_id, state in self._windows.items():
            ordered_seen = {
                str(fingerprint): dict(state.seen[fingerprint])
                for fingerprint in state.order
                if fingerprint in state.seen
            }
            windows[str(int(window_id))] = {
                "seen": ordered_seen,
                "order": [str(key) for key in state.order if str(key) in ordered_seen],
            }
        return {"version": 1, "windows": windows}

    def load_state(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        windows_raw = payload.get("windows")
        if not isinstance(windows_raw, dict):
            return
        restored: dict[int, WindowMessageState] = {}
        for key, state_raw in windows_raw.items():
            try:
                window_id = int(key)
            except Exception:
                continue
            if not isinstance(state_raw, dict):
                continue
            seen_raw = state_raw.get("seen")
            order_raw = state_raw.get("order")
            if not isinstance(seen_raw, dict) or not isinstance(order_raw, list):
                continue
            seen = {
                str(fingerprint): dict(message)
                for fingerprint, message in seen_raw.items()
                if isinstance(message, dict)
            }
            order = [str(fingerprint) for fingerprint in order_raw if str(fingerprint) in seen]
            restored[window_id] = WindowMessageState(seen=seen, order=order)
        self._windows = restored

    def messages_for_window(self, window_id: int) -> list[dict[str, Any]]:
        state = self._windows.get(int(window_id))
        if state is None:
            return []
        return [dict(state.seen[fingerprint]) for fingerprint in state.order if fingerprint in state.seen]

    def has_window(self, window_id: int) -> bool:
        return int(window_id) in self._windows

    def update(self, *, window_id: int, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        state = self._windows.setdefault(int(window_id), WindowMessageState())
        now = time.time()
        previous_seen = set(state.seen)
        previous_order = list(state.order)
        indexed_messages: list[tuple[str, dict[str, Any]]] = []

        for message in messages:
            fingerprint = str(message.get("fingerprint", "")).strip()
            if fingerprint:
                indexed_messages.append((fingerprint, message))

        new_messages: list[dict[str, Any]] = []

        start_idx = -1
        tail_anchor_lost = False
        if previous_order:
            current_order = [fingerprint for fingerprint, _ in indexed_messages]
            for anchor in reversed(previous_order):
                if anchor in current_order:
                    start_idx = current_order.index(anchor)
                    break
            # If every previous tail vanished, OCR/window layout is unstable.
            # Resync the visible page, but preserve a new incoming tail message.
            if start_idx < 0:
                tail_anchor_lost = True
                start_idx = len(indexed_messages) - 1

        for idx, (fingerprint, message) in enumerate(indexed_messages):
            existing = state.seen.get(fingerprint)
            if existing is not None:
                existing["last_seen_at"] = now
                continue

            stored = dict(message)
            stored["first_seen_at"] = now
            stored["last_seen_at"] = now
            state.seen[fingerprint] = stored
            is_new_tail_after_resync = (
                tail_anchor_lost
                and idx == len(indexed_messages) - 1
                and self.is_incoming(stored)
            )
            if (
                (not previous_order)
                or (idx > start_idx and fingerprint not in previous_seen)
                or is_new_tail_after_resync
            ):
                new_messages.append(stored)

        state.order = [fingerprint for fingerprint, _ in indexed_messages]
        return new_messages

    @staticmethod
    def is_incoming(message: dict[str, Any]) -> bool:
        return str(message.get("side", "")).strip() == "other"

    @staticmethod
    def is_mention(message: dict[str, Any], aliases: list[str]) -> bool:
        text = str(message.get("text", "") or "")
        mentions = [str(x) for x in (message.get("mentions") or [])]
        haystack = "\n".join([text] + mentions)
        return any(alias and alias in haystack for alias in aliases)
