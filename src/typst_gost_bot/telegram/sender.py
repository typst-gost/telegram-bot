from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions, Message, ReplyParameters
from loguru import logger

from typst_gost_bot.rag.models import RagAnswer
from typst_gost_bot.telegram.formatters import (
    inject_source_links,
    to_fallback_entities,
    to_rich_messages,
)
from typst_gost_bot.telegram.keyboards import build_sources_keyboard


async def send_rich_markdown(
    message: Message,
    markdown_text: str,
    *,
    reply_markup=None,
    reply_to_sender: bool = True,
) -> None:
    reply_params = (
        ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        )
        if reply_to_sender
        else None
    )
    rich_chunks = to_rich_messages(markdown_text)

    try:
        for idx, rich_chunk in enumerate(rich_chunks):
            is_first = idx == 0
            is_last = idx == len(rich_chunks) - 1
            await message.answer_rich(
                rich_message=rich_chunk,
                reply_parameters=reply_params if is_first else None,
                reply_markup=reply_markup if is_last else None,
            )
        return
    except TelegramBadRequest as exc:
        logger.warning(f"send_rich_message rejected, using entity fallback: {exc}")

    fallback_chunks = to_fallback_entities(markdown_text)
    for idx, (text_part, entities) in enumerate(fallback_chunks):
        is_first = idx == 0
        is_last = idx == len(fallback_chunks) - 1
        await message.answer(
            text=text_part,
            entities=entities,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_parameters=reply_params if is_first else None,
            reply_markup=reply_markup if is_last else None,
        )


async def send_rag_reply(message: Message, rag_answer: RagAnswer) -> None:
    linked_markdown = inject_source_links(rag_answer.answer, rag_answer.sources)
    keyboard = build_sources_keyboard(rag_answer.sources)
    await send_rich_markdown(
        message,
        linked_markdown,
        reply_markup=keyboard,
        reply_to_sender=True,
    )