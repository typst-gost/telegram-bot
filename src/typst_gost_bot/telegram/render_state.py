from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FixPayload:
    source_code: str
    error_text: str
    created_at: float


@dataclass(frozen=True, slots=True)
class TrackedReply:
    bot_message_ids: list[int]
    is_rich_single: bool
    updated_at: float


class FixSessionStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl = ttl_seconds
        self._items: dict[str, FixPayload] = {}

    def save(self, source_code: str, error_text: str) -> str:
        now = time.monotonic()
        if len(self._items) > 512:
            self._items = {
                k: v for k, v in self._items.items() if now - v.created_at <= self._ttl
            }
        token = uuid.uuid4().hex[:16]
        self._items[token] = FixPayload(
            source_code=source_code,
            error_text=error_text,
            created_at=now,
        )
        return token

    def get(self, token: str) -> FixPayload | None:
        item = self._items.get(token)
        if item is None:
            return None
        if time.monotonic() - item.created_at > self._ttl:
            self._items.pop(token, None)
            return None
        return item

    def delete(self, token: str) -> None:
        self._items.pop(token, None)


class EditTracker:
    def __init__(self, ttl_seconds: int = 86400) -> None:
        self._ttl = ttl_seconds
        self._replies: dict[tuple[int, int], TrackedReply] = {}

    def set(
        self,
        chat_id: int,
        user_message_id: int,
        bot_message_ids: list[int],
        *,
        is_rich_single: bool,
    ) -> None:
        now = time.monotonic()
        if len(self._replies) > 1024:
            self._replies = {
                k: v for k, v in self._replies.items() if now - v.updated_at <= self._ttl
            }
        self._replies[(chat_id, user_message_id)] = TrackedReply(
            bot_message_ids=bot_message_ids,
            is_rich_single=is_rich_single,
            updated_at=now,
        )

    def get(self, chat_id: int, user_message_id: int) -> TrackedReply | None:
        item = self._replies.get((chat_id, user_message_id))
        if item is None:
            return None
        if time.monotonic() - item.updated_at > self._ttl:
            self._replies.pop((chat_id, user_message_id), None)
            return None
        return item

    def pop(self, chat_id: int, user_message_id: int) -> TrackedReply | None:
        return self._replies.pop((chat_id, user_message_id), None)