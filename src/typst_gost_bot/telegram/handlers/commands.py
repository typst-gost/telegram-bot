from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageGenerationStopped,
    ReplyParameters,
)
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.gateway.client import (
    GatewayClient,
    GatewayDisabledError,
    GatewayRateLimitError,
    GatewayUnavailableError,
)
from typst_gost_bot.telegram.rate_limit import CooldownLimiter
from typst_gost_bot.telegram.render_state import DraftStopRegistry
from typst_gost_bot.telegram.sender import (
    send_rag_reply,
    send_rich_markdown,
    stream_rag_reply,
)
from typst_gost_bot.typst_runner.assets import list_compile_assets
from typst_gost_bot.typst_runner.compiler import TypstCompiler
from typst_gost_bot.typst_runner.extractor import no_typst_blocks

router = Router(name="commands")

MAX_QUERY_CHARS = 1500

WELCOME_MD = """# Помощник по Typst и modern-g7-32

Отвечаю на вопросы по языку **Typst** и оформлению документов по **ГОСТ 7.32-2017** (пакет `modern-g7-32`), а также компилирую код Typst в изображения.

### Как пользоваться

- **В личных сообщениях:** пишите вопрос обычным сообщением. Бот отвечает потоково и помнит контекст диалога.
- **В группах:** используйте команду `/help <ваш вопрос>` (каждый вопрос обрабатывается независимо).
- **Рендер кода:** отправьте блок кода `typst` или файл `.typ` в любой чат.

**Примеры вопросов:**
- `какие параметры есть у реферата`
- `как скрыть стандартный титульный лист`
- `как сделать кириллическую нумерацию перечислений`"""

HELP_USAGE_MD = """Укажите вопрос после команды `/help`.

**Примеры:**
- `/help как настроить титульный лист в modern-g7-32`
- `/help как оформить приложения с буквой и статусом`
- `/help как экспортировать документ в word docx`"""


def _build_user_metadata(message: Message) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if message.from_user:
        if message.from_user.username:
            meta["username"] = message.from_user.username
        if message.from_user.full_name:
            meta["full_name"] = message.from_user.full_name
    if message.chat.title:
        meta["chat_title"] = message.chat.title
    return meta


async def _handle_gateway_exception(message: Message, exc: Exception) -> None:
    if isinstance(exc, GatewayRateLimitError):
        await send_rich_markdown(
            message,
            "Слишком много запросов. Подождите немного перед следующим вопросом.",
            render_mode="off",
            reply_to_sender=True,
        )
        return
    if isinstance(exc, GatewayDisabledError):
        await send_rich_markdown(
            message,
            "Этот режим ассистента временно отключён.",
            render_mode="off",
            reply_to_sender=True,
        )
        return
    if isinstance(exc, GatewayUnavailableError):
        await send_rich_markdown(
            message,
            "База знаний сейчас обновляется. Попробуйте повторить запрос чуть позже.",
            render_mode="off",
            reply_to_sender=True,
        )
        return
    logger.exception(f"Gateway request failed in chat {message.chat.id}")
    await send_rich_markdown(
        message,
        "Не удалось обработать запрос. Попробуйте повторить позже.",
        render_mode="off",
        reply_to_sender=True,
    )


async def _execute_rag_flow(
    message: Message,
    query: str,
    gateway_client: GatewayClient,
    compiler: TypstCompiler,
    settings: Settings,
    rate_limiter: CooldownLimiter,
    draft_stop_registry: DraftStopRegistry,
) -> None:
    if len(query) > MAX_QUERY_CHARS:
        await send_rich_markdown(
            message,
            f"Запрос слишком длинный. Максимум — {MAX_QUERY_CHARS} символов.",
            render_mode="off",
            reply_to_sender=True,
        )
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    wait_seconds = rate_limiter.check(user_id)
    if wait_seconds > 0:
        await send_rich_markdown(
            message,
            f"Подождите {wait_seconds:.1f} с перед следующим запросом.",
            render_mode="off",
            reply_to_sender=True,
        )
        return

    is_private = message.chat.type == ChatType.PRIVATE
    ext_user_id = str(user_id)
    ext_chat_id = str(message.chat.id)
    metadata = _build_user_metadata(message)

    if is_private:
        try:
            await stream_rag_reply(
                message,
                query,
                conversation_id=f"tg_private:{message.chat.id}",
                external_user_id=ext_user_id,
                external_chat_id=ext_chat_id,
                metadata=metadata,
                gateway_client=gateway_client,
                compiler=compiler,
                settings=settings,
                draft_stop_registry=draft_stop_registry,
            )
        except Exception as exc:
            await _handle_gateway_exception(message, exc)
        return

    status_message = await message.answer(
        "Думаю...",
        reply_parameters=ReplyParameters(
            message_id=message.message_id,
            allow_sending_without_reply=True,
        ),
    )

    thread_id = message.message_thread_id if message.is_topic_message else None
    try:
        async with ChatActionSender.typing(
            bot=message.bot,
            chat_id=message.chat.id,
            message_thread_id=thread_id,
        ):
            rag_answer = await gateway_client.chat(
                channel="telegram_group",
                query=query,
                conversation_id=f"tg_group:{message.chat.id}:{message.message_id}",
                available_assets=list_compile_assets(settings.assets_dir),
                external_user_id=ext_user_id,
                external_chat_id=ext_chat_id,
                metadata=metadata,
            )
            await send_rag_reply(
                message,
                rag_answer,
                compiler=compiler,
                max_attachments=settings.max_render_attachments,
            )
    except Exception as exc:
        await _handle_gateway_exception(message, exc)
    finally:
        try:
            await status_message.delete()
        except Exception:
            pass


@router.stopped_message_generation()
async def handle_stopped_generation(
    event: MessageGenerationStopped,
    draft_stop_registry: DraftStopRegistry,
) -> None:
    draft_stop_registry.request_stop(event.chat.id, event.draft_id)


@router.message(CommandStart())
async def handle_start(
    message: Message,
    settings: Settings,
    gateway_client: GatewayClient,
) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await gateway_client.clear_conversation(f"tg_private:{message.chat.id}")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть документацию",
                    url=settings.docs_base_url,
                )
            ]
        ]
    )
    await send_rich_markdown(
        message,
        WELCOME_MD,
        render_mode="off",
        reply_markup=keyboard,
        reply_to_sender=False,
    )


@router.message(Command("help"))
async def handle_help(
    message: Message,
    command: CommandObject,
    gateway_client: GatewayClient,
    compiler: TypstCompiler,
    settings: Settings,
    rate_limiter: CooldownLimiter,
    draft_stop_registry: DraftStopRegistry,
) -> None:
    query = (command.args or "").strip()
    if not query:
        await send_rich_markdown(
            message,
            HELP_USAGE_MD,
            render_mode="off",
            reply_to_sender=True,
        )
        return

    await _execute_rag_flow(
        message=message,
        query=query,
        gateway_client=gateway_client,
        compiler=compiler,
        settings=settings,
        rate_limiter=rate_limiter,
        draft_stop_registry=draft_stop_registry,
    )


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.text,
    ~F.text.startswith("/"),
    no_typst_blocks,
)
async def handle_direct_pm_message(
    message: Message,
    gateway_client: GatewayClient,
    compiler: TypstCompiler,
    settings: Settings,
    rate_limiter: CooldownLimiter,
    draft_stop_registry: DraftStopRegistry,
) -> None:
    query = (message.text or "").strip()
    if not query:
        return

    await _execute_rag_flow(
        message=message,
        query=query,
        gateway_client=gateway_client,
        compiler=compiler,
        settings=settings,
        rate_limiter=rate_limiter,
        draft_stop_registry=draft_stop_registry,
    )