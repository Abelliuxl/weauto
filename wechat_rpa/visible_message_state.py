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
        if previous_order:
            current_order = [fingerprint for fingerprint, _ in indexed_messages]
            for anchor in reversed(previous_order):
                if anchor in current_order:
                    start_idx = current_order.index(anchor)
                    break
            # If every previous tail vanished, OCR/window layout is unstable.
            # Resync the visible page instead of treating old visible bubbles as new.
            if start_idx < 0:
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
            if (not previous_order) or (idx > start_idx and fingerprint not in previous_seen):
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
