from aiogram import Router

from typst_gost_bot.telegram.handlers.commands import router as commands_router

root_router = Router()
root_router.include_router(commands_router)

__all__ = ["root_router"]