from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.rag.service import RagService
from typst_gost_bot.telegram.history import ChatHistoryStore
from typst_gost_bot.telegram.rate_limit import CooldownLimiter
from typst_gost_bot.telegram.sender import send_rag_reply, send_rich_markdown
from typst_gost_bot.typst_runner.compiler import TypstCompiler
from typst_gost_bot.typst_runner.extractor import no_typst_blocks

router = Router(name="commands")

MAX_QUERY_CHARS = 1500

WELCOME_MD = """# Помощник по Typst и modern-g7-32

Отвечаю на вопросы по языку **Typst** и оформлению документов по **ГОСТ 7.32-2017** (пакет `modern-g7-32`), а также компилирую код Typst в изображения.

### Как пользоваться

- **В личных сообщениях:** пишите вопрос обычным сообщением. Бот помнит контекст предыдущих сообщений диалога.
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


async def _execute_rag_flow(
    message: Message,
    query: str,
    rag_service: RagService,
    compiler: TypstCompiler,
    settings: Settings,
    rate_limiter: CooldownLimiter,
    history_store: ChatHistoryStore,
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
    history = history_store.get_messages(message.chat.id) if is_private else None

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
            rag_answer = await rag_service.answer(query, history=history)
            if is_private:
                history_store.add_user_message(message.chat.id, query)
                history_store.add_assistant_message(message.chat.id, rag_answer.answer)

            await send_rag_reply(
                message,
                rag_answer,
                compiler=compiler,
                max_attachments=settings.max_render_attachments,
            )
    except Exception:
        logger.exception(f"Failed to process query in chat {message.chat.id}")
        await send_rich_markdown(
            message,
            "Не удалось обработать запрос. Попробуйте повторить позже.",
            render_mode="off",
            reply_to_sender=True,
        )
    finally:
        try:
            await status_message.delete()
        except Exception:
            pass


@router.message(CommandStart())
async def handle_start(
    message: Message,
    settings: Settings,
    history_store: ChatHistoryStore,
) -> None:
    history_store.clear(message.chat.id)
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
    rag_service: RagService,
    compiler: TypstCompiler,
    settings: Settings,
    rate_limiter: CooldownLimiter,
    history_store: ChatHistoryStore,
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
        rag_service=rag_service,
        compiler=compiler,
        settings=settings,
        rate_limiter=rate_limiter,
        history_store=history_store,
    )


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.text,
    ~F.text.startswith("/"),
    no_typst_blocks,
)
async def handle_direct_pm_message(
    message: Message,
    rag_service: RagService,
    compiler: TypstCompiler,
    settings: Settings,
    rate_limiter: CooldownLimiter,
    history_store: ChatHistoryStore,
) -> None:
    query = (message.text or "").strip()
    if not query:
        return

    await _execute_rag_flow(
        message=message,
        query=query,
        rag_service=rag_service,
        compiler=compiler,
        settings=settings,
        rate_limiter=rate_limiter,
        history_store=history_store,
    )