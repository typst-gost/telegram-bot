from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass
from typing import Literal

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichMessage,
    LinkPreviewOptions,
    Message,
    ReplyParameters,
)
from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.rag.models import DocSource, RagAnswer
from typst_gost_bot.rag.service import RagService
from typst_gost_bot.telegram.formatters import (
    inject_source_links,
    to_fallback_entities,
    to_rich_messages,
)
from typst_gost_bot.telegram.keyboards import build_sources_keyboard
from typst_gost_bot.telegram.render_state import DraftStopRegistry
from typst_gost_bot.typst_runner.compiler import TypstCompiler
from typst_gost_bot.typst_runner.extractor import close_open_fences, find_typst_fences

RenderMode = Literal["off", "attach_valid", "strict"]


@dataclass(frozen=True, slots=True)
class RenderedPayload:
    markdown_text: str
    clean_markdown: str
    slot_images: dict[str, list[bytes]]
    all_images: list[bytes]
    errors: list[tuple[int, str, str]]
    limit_exceeded: bool


@dataclass(frozen=True, slots=True)
class SentMessageResult:
    message_ids: list[int]
    is_rich_single: bool


async def _prepare_rendered_payload(
    markdown_text: str,
    compiler: TypstCompiler | None,
    *,
    render_mode: RenderMode,
    max_attachments: int,
) -> RenderedPayload:
    if render_mode == "off" or compiler is None:
        return RenderedPayload(
            markdown_text=markdown_text,
            clean_markdown=markdown_text,
            slot_images={},
            all_images=[],
            errors=[],
            limit_exceeded=False,
        )

    fences = find_typst_fences(markdown_text)
    if not fences:
        return RenderedPayload(
            markdown_text=markdown_text,
            clean_markdown=markdown_text,
            slot_images={},
            all_images=[],
            errors=[],
            limit_exceeded=False,
        )

    remaining = max_attachments
    slot_images: dict[str, list[bytes]] = {}
    all_images: list[bytes] = []
    errors: list[tuple[int, str, str]] = []
    limit_exceeded = False

    parts: list[str] = []
    clean_parts: list[str] = []
    cursor = 0

    for idx, fence in enumerate(fences):
        parts.append(markdown_text[cursor : fence.end])
        clean_parts.append(markdown_text[cursor : fence.end])
        cursor = fence.end

        probe_budget = remaining if remaining > 0 else 1
        result = await compiler.compile_source(fence.code, max_pages=probe_budget)

        if result.error:
            errors.append((idx + 1, fence.code, result.error))
            if render_mode == "strict":
                note = format_compile_error_blockquote(
                    result.error,
                    block_index=idx + 1 if len(fences) > 1 else None,
                )
                parts.append(f"\n\n{note}\n\n")
                clean_parts.append(f"\n\n{note}\n\n")
            continue

        if remaining <= 0:
            if result.pages:
                limit_exceeded = True
            continue

        attached = result.pages[:remaining]
        if result.truncated or len(result.pages) > remaining:
            limit_exceeded = True

        if attached:
            token = f"TYPST_RENDER_SLOT_{idx}"
            slot_images[token] = attached
            all_images.extend(attached)
            remaining -= len(attached)
            parts.append(f"\n\n{token}\n\n")

    parts.append(markdown_text[cursor:])
    clean_parts.append(markdown_text[cursor:])

    rendered_md = "".join(parts).strip()
    clean_md = "".join(clean_parts).strip()

    if limit_exceeded:
        warning = build_attachment_limit_warning(max_attachments)
        rendered_md = f"{rendered_md}\n\n{warning}"
        clean_md = f"{clean_md}\n\n{warning}"

    return RenderedPayload(
        markdown_text=rendered_md,
        clean_markdown=clean_md,
        slot_images=slot_images,
        all_images=all_images,
        errors=errors,
        limit_exceeded=limit_exceeded,
    )


def format_compile_error_blockquote(
    error_text: str,
    *,
    block_index: int | None = None,
) -> str:
    header = (
        f"**Примечание — ошибка компиляции (блок {block_index}):**"
        if block_index is not None
        else "**Примечание — ошибка компиляции:**"
    )
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in error_text.splitlines())
    return f"> {header}\n>\n{quoted}"


def build_attachment_limit_warning(max_attachments: int) -> str:
    return (
        f"> **Примечание:** превышен лимит вложений на сообщение (максимум {max_attachments}). "
        f"Прикреплены первые {max_attachments} страниц."
    )


def build_slot_markdown(
    slot_images: dict[str, list[bytes]],
    *,
    limit_exceeded: bool,
    max_attachments: int,
    extra_notes: list[str] | None = None,
) -> str:
    parts = list(slot_images.keys())
    if extra_notes:
        parts.extend(extra_notes)
    if limit_exceeded:
        parts.append(build_attachment_limit_warning(max_attachments))
    return "\n\n".join(parts)


async def _send_media_fallback(
    message: Message,
    images: list[bytes],
    *,
    reply_params: ReplyParameters | None,
) -> list[int]:
    if not images:
        return []
    if len(images) == 1:
        sent = await message.answer_photo(
            photo=BufferedInputFile(images[0], filename="render_1.png"),
            reply_parameters=reply_params,
        )
        return [sent.message_id]

    media_group = [
        InputMediaPhoto(media=BufferedInputFile(img, filename=f"render_{i + 1}.png"))
        for i, img in enumerate(images)
    ]
    sent_messages = await message.answer_media_group(
        media=media_group,
        reply_parameters=reply_params,
    )
    return [m.message_id for m in sent_messages]


async def send_rich_chunks(
    message: Message,
    rich_chunks: list[InputRichMessage],
    *,
    fallback_markdown: str,
    fallback_images: list[bytes],
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_to_sender: bool = True,
) -> SentMessageResult:
    reply_params = (
        ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        )
        if reply_to_sender
        else None
    )

    sent_ids: list[int] = []
    try:
        for idx, rich_chunk in enumerate(rich_chunks):
            is_first = idx == 0
            is_last = idx == len(rich_chunks) - 1
            sent = await message.answer_rich(
                rich_message=rich_chunk,
                reply_parameters=reply_params if is_first else None,
                reply_markup=reply_markup if is_last else None,
            )
            sent_ids.append(sent.message_id)
        return SentMessageResult(
            message_ids=sent_ids,
            is_rich_single=(len(sent_ids) == 1),
        )
    except TelegramBadRequest as exc:
        logger.warning(f"send_rich_message rejected, using fallback: {exc}")

    if fallback_images:
        media_ids = await _send_media_fallback(
            message,
            fallback_images,
            reply_params=reply_params,
        )
        sent_ids.extend(media_ids)

    if fallback_markdown.strip():
        fallback_chunks = to_fallback_entities(fallback_markdown)
        for idx, (text_part, entities) in enumerate(fallback_chunks):
            is_first = idx == 0 and not sent_ids
            is_last = idx == len(fallback_chunks) - 1
            sent = await message.answer(
                text=text_part,
                entities=entities,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_parameters=reply_params if is_first else None,
                reply_markup=reply_markup if is_last else None,
            )
            sent_ids.append(sent.message_id)
    elif reply_markup is not None and sent_ids:
        sent = await message.answer(
            text="Результат рендера:",
            reply_markup=reply_markup,
        )
        sent_ids.append(sent.message_id)

    return SentMessageResult(message_ids=sent_ids, is_rich_single=False)


async def send_rich_markdown(
    message: Message,
    markdown_text: str,
    *,
    compiler: TypstCompiler | None = None,
    render_mode: RenderMode = "attach_valid",
    max_attachments: int = 10,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_to_sender: bool = True,
) -> SentMessageResult:
    prepared = await _prepare_rendered_payload(
        markdown_text,
        compiler,
        render_mode=render_mode,
        max_attachments=max_attachments,
    )
    rich_chunks = to_rich_messages(
        prepared.markdown_text,
        slot_images=prepared.slot_images or None,
    )
    return await send_rich_chunks(
        message,
        rich_chunks,
        fallback_markdown=prepared.clean_markdown,
        fallback_images=prepared.all_images,
        reply_markup=reply_markup,
        reply_to_sender=reply_to_sender,
    )


async def send_rag_reply(
    message: Message,
    rag_answer: RagAnswer,
    *,
    compiler: TypstCompiler,
    max_attachments: int,
    debug_footer: str | None = None,
) -> SentMessageResult:
    linked_markdown = inject_source_links(rag_answer.answer, rag_answer.sources)
    if debug_footer:
        linked_markdown = f"{linked_markdown}\n\n{debug_footer}"
    keyboard = build_sources_keyboard(rag_answer.sources)
    return await send_rich_markdown(
        message,
        linked_markdown,
        compiler=compiler,
        render_mode="attach_valid",
        max_attachments=max_attachments,
        reply_markup=keyboard,
        reply_to_sender=True,
    )


async def _push_rich_draft(
    bot: Bot,
    *,
    chat_id: int,
    draft_id: int,
    thread_id: int | None,
    rich_message: InputRichMessage,
    can_stop: bool = True,
) -> bool:
    try:
        await bot.send_rich_message_draft(
            chat_id=chat_id,
            draft_id=draft_id,
            message_thread_id=thread_id,
            rich_message=rich_message,
            can_stop=can_stop,
            keep_on_stop=True,
        )
        return True
    except TelegramBadRequest as exc:
        logger.warning(f"sendRichMessageDraft failed: {exc}")
        return False


def _build_draft_preview(
    raw_markdown: str,
    sources: list[DocSource],
    *,
    thinking_status: str | None = None,
) -> InputRichMessage:
    closed_md = close_open_fences(raw_markdown)
    linked_md = inject_source_links(closed_md, sources)
    rich_msg = to_rich_messages(linked_md)[-1]
    if thinking_status:
        status_html = f"<tg-thinking>{html.escape(thinking_status)}</tg-thinking>"
        if rich_msg.html:
            return InputRichMessage(html=f"{rich_msg.html}{status_html}")
        return InputRichMessage(html=status_html)
    return rich_msg


async def stream_rag_reply(
    message: Message,
    query: str,
    *,
    history: list[dict[str, str]] | None,
    rag_service: RagService,
    compiler: TypstCompiler,
    settings: Settings,
    draft_stop_registry: DraftStopRegistry,
    debug_footer: str | None = None,
) -> RagAnswer | None:
    chat_id = message.chat.id
    thread_id = message.message_thread_id if message.is_topic_message else None
    draft_id, stop_event = draft_stop_registry.create(chat_id)

    drafts_enabled = await _push_rich_draft(
        message.bot,
        chat_id=chat_id,
        draft_id=draft_id,
        thread_id=thread_id,
        rich_message=InputRichMessage(html="<tg-thinking>Ищу в документации...</tg-thinking>"),
    )

    sources: list[DocSource] = []
    accumulated_parts: list[str] = []
    last_push_time = time.monotonic()
    last_pushed_len = 0
    stopped_by_user = False

    try:
        async for event in rag_service.stream_answer(query, history=history):
            if stop_event.is_set():
                stopped_by_user = True
                break

            if event.kind == "sources":
                sources = event.sources
                if sources and drafts_enabled:
                    drafts_enabled = await _push_rich_draft(
                        message.bot,
                        chat_id=chat_id,
                        draft_id=draft_id,
                        thread_id=thread_id,
                        rich_message=InputRichMessage(
                            html="<tg-thinking>Формулирую ответ...</tg-thinking>"
                        ),
                    )
                    last_push_time = time.monotonic()
                continue

            if event.kind == "delta":
                accumulated_parts.append(event.text)
                if not drafts_enabled:
                    continue

                now = time.monotonic()
                current_text = "".join(accumulated_parts)
                chars_delta = len(current_text) - last_pushed_len
                elapsed = now - last_push_time

                if (elapsed >= settings.stream_update_interval and chars_delta >= 12) or (
                    elapsed >= 18.0
                ):
                    preview = _build_draft_preview(current_text, sources)
                    drafts_enabled = await _push_rich_draft(
                        message.bot,
                        chat_id=chat_id,
                        draft_id=draft_id,
                        thread_id=thread_id,
                        rich_message=preview,
                    )
                    last_push_time = now
                    last_pushed_len = len(current_text)

                if stop_event.is_set():
                    stopped_by_user = True
                    break

            elif event.kind == "done":
                if not accumulated_parts and event.text:
                    accumulated_parts.append(event.text)

        raw_answer = "".join(accumulated_parts).strip()

        if stopped_by_user:
            if not raw_answer:
                stopped_md = (
                    f"Генерация остановлена.\n\n{debug_footer}"
                    if debug_footer
                    else "Генерация остановлена."
                )
                await send_rich_markdown(
                    message,
                    stopped_md,
                    render_mode="off",
                    reply_to_sender=True,
                )
                return None

            final_text = f"{close_open_fences(raw_answer)}\n\n> *Генерация остановлена.*"
            rag_answer = RagAnswer(answer=final_text, sources=sources)
            await send_rag_reply(
                message,
                rag_answer,
                compiler=compiler,
                max_attachments=settings.max_render_attachments,
                debug_footer=debug_footer,
            )
            return RagAnswer(answer=close_open_fences(raw_answer), sources=sources)

        if not raw_answer:
            raw_answer = "Не удалось сформировать ответ по найденной документации."

        if drafts_enabled:
            thinking = "Рендерю примеры кода..." if find_typst_fences(raw_answer) else None
            await _push_rich_draft(
                message.bot,
                chat_id=chat_id,
                draft_id=draft_id,
                thread_id=thread_id,
                rich_message=_build_draft_preview(
                    raw_answer,
                    sources,
                    thinking_status=thinking,
                ),
                can_stop=False,
            )

        rag_answer = RagAnswer(answer=raw_answer, sources=sources)
        await send_rag_reply(
            message,
            rag_answer,
            compiler=compiler,
            max_attachments=settings.max_render_attachments,
            debug_footer=debug_footer,
        )
        return rag_answer
    finally:
        draft_stop_registry.release(chat_id, draft_id)


async def delete_messages_safe(
    bot: Bot,
    chat_id: int,
    message_ids: list[int],
) -> None:
    if not message_ids:
        return
    await asyncio.gather(
        *(bot.delete_message(chat_id=chat_id, message_id=mid) for mid in message_ids),
        return_exceptions=True,
    )