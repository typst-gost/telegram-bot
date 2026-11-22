from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.gateway.models import (
    DocSource,
    FixResult,
    RagAnswer,
    RagStreamEvent,
    RetrievalStats,
)

TelegramChatChannel = Literal["telegram_private", "telegram_group"]


class GatewayError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class GatewayRateLimitError(GatewayError):
    pass


class GatewayDisabledError(GatewayError):
    pass


class GatewayUnavailableError(GatewayError):
    pass


def _extract_error_detail(raw_body: bytes, fallback: str) -> str:
    try:
        parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
        if isinstance(parsed, dict) and parsed.get("detail"):
            return str(parsed["detail"])
    except Exception:
        pass
    text = raw_body.decode("utf-8", errors="replace").strip()
    return text[:300] if text else fallback


def _raise_for_gateway_status(status_code: int, raw_body: bytes) -> None:
    if status_code < 400:
        return
    detail = _extract_error_detail(raw_body, f"Gateway HTTP {status_code}")
    if status_code == 429:
        raise GatewayRateLimitError(detail, status_code=status_code)
    if status_code == 403:
        raise GatewayDisabledError(detail, status_code=status_code)
    if status_code == 503:
        raise GatewayUnavailableError(detail, status_code=status_code)
    raise GatewayError(detail, status_code=status_code)


class GatewayClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.ai_gateway_url
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=10.0,
                read=settings.ai_gateway_timeout,
                write=30.0,
                pool=10.0,
            ),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        *,
        channel: TelegramChatChannel,
        query: str,
        conversation_id: str,
        available_assets: list[str] | None = None,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RagAnswer:
        payload: dict[str, Any] = {
            "channel": channel,
            "conversation_id": conversation_id,
            "query": query,
            "stream": False,
            "save_history": True,
        }
        if available_assets is not None:
            payload["available_assets"] = available_assets
        if external_user_id is not None:
            payload["external_user_id"] = external_user_id
        if external_chat_id is not None:
            payload["external_chat_id"] = external_chat_id
        if metadata:
            payload["metadata"] = metadata

        resp = await self._http.post("/api/v1/assistant/chat", json=payload)
        if resp.status_code >= 400:
            _raise_for_gateway_status(resp.status_code, resp.content)

        data = resp.json()
        sources = [DocSource.from_dict(s) for s in data.get("sources") or []]
        stats = RetrievalStats.from_dict(data.get("stats"))
        return RagAnswer(
            answer=str(data.get("answer", "")),
            raw_answer=str(data.get("raw_answer", "")),
            sources=sources,
            stats=stats,
            blocked_by_guard=bool(data.get("blocked_by_guard", False)),
            no_docs_found=bool(data.get("no_docs_found", False)),
            debug_markdown=data.get("debug_markdown"),
            already_renumbered=True,
        )

    async def stream_chat(
        self,
        *,
        channel: TelegramChatChannel,
        query: str,
        conversation_id: str,
        available_assets: list[str] | None = None,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[RagStreamEvent]:
        payload: dict[str, Any] = {
            "channel": channel,
            "conversation_id": conversation_id,
            "query": query,
            "stream": True,
            "save_history": True,
        }
        if available_assets is not None:
            payload["available_assets"] = available_assets
        if external_user_id is not None:
            payload["external_user_id"] = external_user_id
        if external_chat_id is not None:
            payload["external_chat_id"] = external_chat_id
        if metadata:
            payload["metadata"] = metadata

        async with self._http.stream("POST", "/api/v1/assistant/chat", json=payload) as resp:
            if resp.status_code >= 400:
                raw_body = await resp.aread()
                _raise_for_gateway_status(resp.status_code, raw_body)

            event_name = ""
            data_lines: list[str] = []

            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        parsed = json.loads("\n".join(data_lines))
                        event = self._parse_sse_event(event_name, parsed)
                        if event is not None:
                            yield event
                    event_name = ""
                    data_lines = []
                    continue

                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

            if data_lines:
                parsed = json.loads("\n".join(data_lines))
                event = self._parse_sse_event(event_name, parsed)
                if event is not None:
                    yield event

    def _parse_sse_event(
        self,
        event_name: str,
        data: dict[str, Any],
    ) -> RagStreamEvent | None:
        if event_name == "meta":
            return RagStreamEvent(
                kind="meta",
                sources=[DocSource.from_dict(s) for s in data.get("sources") or []],
                stats=RetrievalStats.from_dict(data.get("stats")),
                blocked_by_guard=bool(data.get("blocked_by_guard", False)),
                no_docs_found=bool(data.get("no_docs_found", False)),
            )
        if event_name == "delta":
            return RagStreamEvent(kind="delta", text=str(data.get("text", "")))
        if event_name == "done":
            return RagStreamEvent(
                kind="done",
                sources=[DocSource.from_dict(s) for s in data.get("sources") or []],
                text=str(data.get("answer", "")),
                raw_text=str(data.get("raw_answer", "")),
                stats=RetrievalStats.from_dict(data.get("stats")),
                blocked_by_guard=bool(data.get("blocked_by_guard", False)),
                no_docs_found=bool(data.get("no_docs_found", False)),
                debug_markdown=data.get("debug_markdown"),
            )
        if event_name == "error":
            raise GatewayError(str(data.get("detail") or "Gateway stream error"))
        return None

    async def fix_code(
        self,
        *,
        source_code: str,
        error_text: str,
        conversation_id: str,
        available_assets: list[str] | None = None,
        external_user_id: str | None = None,
        external_chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FixResult:
        payload: dict[str, Any] = {
            "source_code": source_code,
            "error_text": error_text,
            "conversation_id": conversation_id,
            "save_history": True,
        }
        if available_assets is not None:
            payload["available_assets"] = available_assets
        if external_user_id is not None:
            payload["external_user_id"] = external_user_id
        if external_chat_id is not None:
            payload["external_chat_id"] = external_chat_id
        if metadata:
            payload["metadata"] = metadata

        resp = await self._http.post("/api/v1/assistant/fix", json=payload)
        if resp.status_code >= 400:
            _raise_for_gateway_status(resp.status_code, resp.content)

        data = resp.json()
        return FixResult(
            fixed_code=str(data.get("fixed_code") or source_code),
            blocked_by_guard=bool(data.get("blocked_by_guard", False)),
            refusal_message=data.get("refusal_message"),
            debug_markdown=data.get("debug_markdown"),
        )

    async def clear_conversation(self, conversation_id: str) -> None:
        try:
            resp = await self._http.post(
                "/api/v1/assistant/conversations/clear",
                json={"conversation_id": conversation_id},
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(f"Failed to clear conversation {conversation_id} on gateway: {exc}")