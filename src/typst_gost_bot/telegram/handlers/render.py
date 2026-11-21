from __future__ import annotations

import io
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.rag.guard import FIX_OFF_TOPIC_REPLY, TopicGuard
from typst_gost_bot.rag.service import RagService
from typst_gost_bot.telegram.formatters import to_rich_messages
from typst_gost_bot.telegram.keyboards import build_fix_keyboard
from typst_gost_bot.telegram.rate_limit import CooldownLimiter
from typst_gost_bot.telegram.render_state import EditTracker, FixSessionStore
from typst_gost_bot.telegram.sender import (
    SentMessageResult,
    build_slot_markdown,
    delete_messages_safe,
    format_compile_error_blockquote,
    send_rich_chunks,
    send_rich_markdown,
)
from typst_gost_bot.typst_runner.compiler import TypstCompiler
from typst_gost_bot.typst_runner.extractor import (
    extract_typst_blocks_from_message,
    has_typst_blocks,
)

router = Router(name="render")

MAX_TYP_FILE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class _BatchCompileOutput:
    slot_images: dict[str, list[bytes]]
    all_images: list[bytes]
    notes: list[str]
    limit_exceeded: bool
    first_error: tuple[str, str] | None


async def _compile_user_blocks(
    blocks: list[str],
    compiler: TypstCompiler,
    max_attachments: int,
) -> _BatchCompileOutput:
    remaining = max_attachments
    slot_images: dict[str, list[bytes]] = {}
    all_images: list[bytes] = []
    notes: list[str] = []
    limit_exceeded = False
    first_error: tuple[str, str] | None = None

    for idx, code in enumerate(blocks):
        probe_budget = remaining if remaining > 0 else 1
        result = await compiler.compile_source(code, max_pages=probe_budget)

        if result.error:
            if first_error is None:
                first_error = (code, result.error)
            notes.append(
                format_compile_error_blockquote(
                    result.error,
                    block_index=idx + 1 if len(blocks) > 1 else None,
                )
            )
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

    return _BatchCompileOutput(
        slot_images=slot_images,
        all_images=all_images,
        notes=notes,
        limit_exceeded=limit_exceeded,
        first_error=first_error,
    )


async def _extract_blocks_or_file(message: Message, settings: Settings) -> list[str]:
    if message.document is not None:
        filename = (message.document.file_name or "").lower()
        if not filename.endswith(".typ"):
            return []
        if (message.document.file_size or 0) > MAX_TYP_FILE_BYTES:
            raise ValueError("Файл .typ превышает допустимый размер (256 КБ).")

        buf = io.BytesIO()
        await message.bot.download(message.document, destination=buf)
        try:
            content = buf.getvalue().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Файл .typ должен быть в кодировке UTF-8.") from exc
        if len(content) > settings.max_source_chars:
            raise ValueError(
                f"Файл слишком длинный (лимит {settings.max_source_chars} символов)."
            )
        return [content]

    return extract_typst_blocks_from_message(message)


def _build_render_rich_payload(
    compiled: _BatchCompileOutput,
    settings: Settings,
    fix_store: FixSessionStore,
) -> tuple[list, str, list[bytes], InlineKeyboardMarkup | None]:
    markdown_with_slots = build_slot_markdown(
        compiled.slot_images,
        limit_exceeded=compiled.limit_exceeded,
        max_attachments=settings.max_render_attachments,
        extra_notes=compiled.notes,
    )
    clean_notes = build_slot_markdown(
        {},
        limit_exceeded=compiled.limit_exceeded,
        max_attachments=settings.max_render_attachments,
        extra_notes=compiled.notes,
    )

    keyboard: InlineKeyboardMarkup | None = None
    if compiled.first_error is not None:
        source_code, error_text = compiled.first_error
        fix_token = fix_store.save(source_code, error_text)
        keyboard = build_fix_keyboard(fix_token)

    rich_chunks = to_rich_messages(
        markdown_with_slots,
        slot_images=compiled.slot_images or None,
    )
    return rich_chunks, clean_notes, compiled.all_images, keyboard


async def _render_and_reply(
    message: Message,
    blocks: list[str],
    compiler: TypstCompiler,
    settings: Settings,
    fix_store: FixSessionStore,
    edit_tracker: EditTracker,
) -> None:
    thread_id = message.message_thread_id if message.is_topic_message else None
    async with ChatActionSender(
        bot=message.bot,
        chat_id=message.chat.id,
        action=ChatAction.UPLOAD_PHOTO,
        message_thread_id=thread_id,
    ):
        compiled = await _compile_user_blocks(
            blocks,
            compiler,
            settings.max_render_attachments,
        )
        rich_chunks, clean_notes, all_images, keyboard = _build_render_rich_payload(
            compiled,
            settings,
            fix_store,
        )
        sent = await send_rich_chunks(
            message,
            rich_chunks,
            fallback_markdown=clean_notes,
            fallback_images=all_images,
            reply_markup=keyboard,
            reply_to_sender=True,
        )
        edit_tracker.set(
            message.chat.id,
            message.message_id,
            sent.message_ids,
            is_rich_single=sent.is_rich_single,
        )


@router.message(F.document)
async def handle_typ_document(
    message: Message,
    compiler: TypstCompiler,
    settings: Settings,
    fix_store: FixSessionStore,
    edit_tracker: EditTracker,
    rate_limiter: CooldownLimiter,
) -> None:
    filename = (message.document.file_name or "").lower()
    if not filename.endswith(".typ"):
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    wait_seconds = rate_limiter.check(user_id)
    if wait_seconds > 0:
        await send_rich_markdown(
            message,
            f"Подождите {wait_seconds:.1f} с перед следующей компиляцией.",
            render_mode="off",
            reply_to_sender=True,
        )
        return

    try:
        blocks = await _extract_blocks_or_file(message, settings)
        if blocks:
            await _render_and_reply(
                message,
                blocks,
                compiler,
                settings,
                fix_store,
                edit_tracker,
            )
    except ValueError as exc:
        await send_rich_markdown(
            message,
            f"> **Примечание:** {exc}",
            render_mode="off",
            reply_to_sender=True,
        )
    except Exception:
        logger.exception(f"Failed to compile .typ file in chat {message.chat.id}")
        await send_rich_markdown(
            message,
            "> **Примечание:** не удалось скомпилировать файл.",
            render_mode="off",
            reply_to_sender=True,
        )


@router.message(F.text | F.caption, has_typst_blocks)
async def handle_typst_code_message(
    message: Message,
    compiler: TypstCompiler,
    settings: Settings,
    fix_store: FixSessionStore,
    edit_tracker: EditTracker,
    rate_limiter: CooldownLimiter,
) -> None:
    blocks = extract_typst_blocks_from_message(message)
    if not blocks:
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    wait_seconds = rate_limiter.check(user_id)
    if wait_seconds > 0:
        await send_rich_markdown(
            message,
            f"Подождите {wait_seconds:.1f} с перед следующей компиляцией.",
            render_mode="off",
            reply_to_sender=True,
        )
        return

    try:
        await _render_and_reply(
            message,
            blocks,
            compiler,
            settings,
            fix_store,
            edit_tracker,
        )
    except Exception:
        logger.exception(f"Failed to render typst blocks in chat {message.chat.id}")
        await send_rich_markdown(
            message,
            "> **Примечание:** не удалось отрендерить код Typst.",
            render_mode="off",
            reply_to_sender=True,
        )


@router.edited_message()
async def handle_edited_typst_message(
    message: Message,
    compiler: TypstCompiler,
    settings: Settings,
    fix_store: FixSessionStore,
    edit_tracker: EditTracker,
) -> None:
    tracked = edit_tracker.get(message.chat.id, message.message_id)
    try:
        blocks = await _extract_blocks_or_file(message, settings)
    except ValueError as exc:
        await send_rich_markdown(
            message,
            f"> **Примечание:** {exc}",
            render_mode="off",
            reply_to_sender=True,
        )
        return

    if not blocks:
        if tracked is not None:
            edit_tracker.pop(message.chat.id, message.message_id)
            await delete_messages_safe(message.bot, message.chat.id, tracked.bot_message_ids)
        return

    compiled = await _compile_user_blocks(
        blocks,
        compiler,
        settings.max_render_attachments,
    )
    rich_chunks, clean_notes, all_images, keyboard = _build_render_rich_payload(
        compiled,
        settings,
        fix_store,
    )

    if tracked is not None and tracked.is_rich_single and len(rich_chunks) == 1:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=tracked.bot_message_ids[0],
                rich_message=rich_chunks[0],
                reply_markup=keyboard,
            )
            edit_tracker.set(
                message.chat.id,
                message.message_id,
                tracked.bot_message_ids,
                is_rich_single=True,
            )
            return
        except TelegramBadRequest as exc:
            logger.warning(f"In-place rich edit failed, replacing message: {exc}")

    if tracked is not None:
        await delete_messages_safe(message.bot, message.chat.id, tracked.bot_message_ids)

    sent: SentMessageResult = await send_rich_chunks(
        message,
        rich_chunks,
        fallback_markdown=clean_notes,
        fallback_images=all_images,
        reply_markup=keyboard,
        reply_to_sender=True,
    )
    edit_tracker.set(
        message.chat.id,
        message.message_id,
        sent.message_ids,
        is_rich_single=sent.is_rich_single,
    )


@router.callback_query(F.data.startswith("fix:"))
async def handle_fix_callback(
    callback: CallbackQuery,
    rag_service: RagService,
    topic_guard: TopicGuard,
    compiler: TypstCompiler,
    settings: Settings,
    fix_store: FixSessionStore,
    rate_limiter: CooldownLimiter,
) -> None:
    token = (callback.data or "").split(":", 1)[1]
    payload = fix_store.get(token)
    if payload is None:
        await callback.answer(
            "Сессия исправления устарела. Отправьте код повторно.",
            show_alert=True,
        )
        return

    wait_seconds = rate_limiter.check(callback.from_user.id)
    if wait_seconds > 0:
        await callback.answer(
            f"Подождите {wait_seconds:.1f} с перед следующим запросом.",
            show_alert=True,
        )
        return

    await callback.answer("Проверяю и исправляю код...")
    fix_store.delete(token)

    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

    target_message = callback.message
    if target_message is None:
        return

    verdict = await topic_guard.check_code_fix(payload.source_code, payload.error_text)
    debug_footer = verdict.format_debug_blockquote() if settings.guard_debug else None

    if not verdict.allowed:
        refusal_md = (
            f"{FIX_OFF_TOPIC_REPLY}\n\n{debug_footer}" if debug_footer else FIX_OFF_TOPIC_REPLY
        )
        await send_rich_markdown(
            target_message,
            refusal_md,
            render_mode="off",
            reply_to_sender=True,
        )
        return

    thread_id = target_message.message_thread_id if target_message.is_topic_message else None
    try:
        async with ChatActionSender.typing(
            bot=target_message.bot,
            chat_id=target_message.chat.id,
            message_thread_id=thread_id,
        ):
            fixed_code = await rag_service.fix_code(
                payload.source_code,
                payload.error_text,
            )
            fixed_markdown = f"```typst\n{fixed_code}\n```"
            if debug_footer:
                fixed_markdown = f"{fixed_markdown}\n\n{debug_footer}"
            await send_rich_markdown(
                target_message,
                fixed_markdown,
                compiler=compiler,
                render_mode="strict",
                max_attachments=settings.max_render_attachments,
                reply_to_sender=True,
            )
    except Exception:
        logger.exception("Failed to fix Typst code via RAG LLM")
        await send_rich_markdown(
            target_message,
            "> **Примечание:** не удалось исправить код. Попробуйте позже.",
            render_mode="off",
            reply_to_sender=True,
        )