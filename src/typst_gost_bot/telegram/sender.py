from __future__ import annotations

import asyncio
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

from typst_gost_bot.rag.models import RagAnswer
from typst_gost_bot.telegram.formatters import (
    inject_source_links,
    to_fallback_entities,
    to_rich_messages,
)
from typst_gost_bot.telegram.keyboards import build_sources_keyboard
from typst_gost_bot.typst_runner.compiler import TypstCompiler
from typst_gost_bot.typst_runner.extractor import find_typst_fences

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
) -> SentMessageResult:
    linked_markdown = inject_source_links(rag_answer.answer, rag_answer.sources)
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