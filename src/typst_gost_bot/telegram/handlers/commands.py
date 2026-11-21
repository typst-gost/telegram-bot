from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.rag.service import RagService
from typst_gost_bot.telegram.rate_limit import CooldownLimiter
from typst_gost_bot.telegram.sender import send_rag_reply, send_rich_markdown

router = Router(name="commands")

MAX_QUERY_CHARS = 1500

WELCOME_MD = """# Помощник по Typst и modern-g7-32

Отвечаю на вопросы по языку **Typst** и оформлению документов по **ГОСТ 7.32-2017** (пакет `modern-g7-32`) на основе официальной документации.

### Как пользоваться

Используйте команду:
`/help <ваш вопрос>`

**Примеры:**
- `/help какие параметры есть у реферата`
- `/help как скрыть стандартный титульный лист`
- `/help как указать соисполнителя из другой организации`
- `/help как сделать кириллическую нумерацию перечислений`"""

HELP_USAGE_MD = """Укажите вопрос после команды `/help`.

**Примеры:**
- `/help как настроить титульный лист в modern-g7-32`
- `/help как оформить приложения с буквой и статусом`
- `/help как экспортировать документ в word docx`"""


@router.message(CommandStart())
async def handle_start(message: Message, settings: Settings) -> None:
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
        reply_markup=keyboard,
        reply_to_sender=False,
    )


@router.message(Command("help"))
async def handle_help(
    message: Message,
    command: CommandObject,
    rag_service: RagService,
    rate_limiter: CooldownLimiter,
) -> None:
    query = (command.args or "").strip()
    if not query:
        await send_rich_markdown(message, HELP_USAGE_MD, reply_to_sender=True)
        return

    if len(query) > MAX_QUERY_CHARS:
        await send_rich_markdown(
            message,
            f"Запрос слишком длинный. Максимум — {MAX_QUERY_CHARS} символов.",
            reply_to_sender=True,
        )
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    wait_seconds = rate_limiter.check(user_id)
    if wait_seconds > 0:
        await send_rich_markdown(
            message,
            f"Подождите {wait_seconds:.1f} с перед следующим запросом.",
            reply_to_sender=True,
        )
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
            rag_answer = await rag_service.answer(query)
    except Exception:
        logger.exception(f"Failed to process /help query in chat {message.chat.id}")
        try:
            await status_message.delete()
        except Exception:
            pass

        await send_rich_markdown(
            message,
            "Не удалось обработать запрос. Попробуйте повторить позже.",
            reply_to_sender=True,
        )
        return

    try:
        await status_message.delete()
    except Exception:
        pass

    await send_rag_reply(message, rag_answer)