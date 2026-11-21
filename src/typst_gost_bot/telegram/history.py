from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    role: str
    content: str
    timestamp: float


class ChatHistoryStore:
    def __init__(self, ttl_seconds: int = 3600, max_messages: int = 10) -> None:
        self._ttl = ttl_seconds
        self._max_messages = max_messages
        self._sessions: dict[int, list[HistoryEntry]] = {}

    def get_messages(self, chat_id: int) -> list[dict[str, str]]:
        now = time.monotonic()
        entries = self._sessions.get(chat_id)
        if not entries:
            return []

        if now - entries[-1].timestamp > self._ttl:
            self._sessions.pop(chat_id, None)
            return []

        return [{"role": e.role, "content": e.content} for e in entries]

    def add_user_message(self, chat_id: int, content: str) -> None:
        self._append(chat_id, "user", content)

    def add_assistant_message(self, chat_id: int, content: str) -> None:
        self._append(chat_id, "assistant", content)

    def _append(self, chat_id: int, role: str, content: str) -> None:
        now = time.monotonic()
        entries = self._sessions.setdefault(chat_id, [])
        if entries and (now - entries[-1].timestamp > self._ttl):
            entries.clear()

        entries.append(HistoryEntry(role=role, content=content, timestamp=now))
        if len(entries) > self._max_messages:
            self._sessions[chat_id] = entries[-self._max_messages :]

        if len(self._sessions) > 512:
            self._cleanup(now)

    def clear(self, chat_id: int) -> None:
        self._sessions.pop(chat_id, None)

    def _cleanup(self, now: float) -> None:
        expired = [
            cid
            for cid, items in self._sessions.items()
            if not items or (now - items[-1].timestamp > self._ttl)
        ]
        for cid in expired:
            self._sessions.pop(cid, None)