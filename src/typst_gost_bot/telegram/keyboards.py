from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from typst_gost_bot.rag.models import DocSource


def _truncate_label(text: str, limit: int = 44) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def build_sources_keyboard(sources: list[DocSource]) -> InlineKeyboardMarkup | None:
    if not sources:
        return None

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=_truncate_label(f"[{src.label}] {src.title}"),
                url=src.url,
            )
        ]
        for src in sources
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_fix_keyboard(fix_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Исправить",
                    callback_data=f"fix:{fix_token}",
                )
            ]
        ]
    )