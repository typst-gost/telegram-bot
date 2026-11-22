from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand
from loguru import logger

from typst_gost_bot.config import load_settings
from typst_gost_bot.gateway import GatewayClient
from typst_gost_bot.logging_setup import setup_logging
from typst_gost_bot.telegram.handlers import root_router
from typst_gost_bot.telegram.rate_limit import CooldownLimiter
from typst_gost_bot.telegram.render_state import (
    DraftStopRegistry,
    EditTracker,
    FixSessionStore,
)
from typst_gost_bot.typst_runner import TypstCompiler


async def start_bot() -> None:
    settings = load_settings()
    setup_logging(level=settings.log_level, log_file=settings.log_file)

    gateway_client = GatewayClient(settings=settings)
    compiler = TypstCompiler(settings=settings)
    rate_limiter = CooldownLimiter(settings.rate_limit_seconds)
    fix_store = FixSessionStore()
    edit_tracker = EditTracker()
    draft_stop_registry = DraftStopRegistry()

    session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else None
    bot = Bot(token=settings.bot_token, session=session)
    dp = Dispatcher()
    dp["settings"] = settings
    dp["gateway_client"] = gateway_client
    dp["compiler"] = compiler
    dp["rate_limiter"] = rate_limiter
    dp["fix_store"] = fix_store
    dp["edit_tracker"] = edit_tracker
    dp["draft_stop_registry"] = draft_stop_registry
    dp.include_router(root_router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="О боте и примеры использования"),
            BotCommand(command="help", description="Задать вопрос по Typst / ГОСТ 7.32"),
        ]
    )

    me = await bot.get_me()
    logger.info(
        f"Starting bot @{me.username} "
        f"(gateway: {settings.ai_gateway_url}, assets: {settings.assets_dir})"
    )

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await gateway_client.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(start_bot())


if __name__ == "__main__":
    run()