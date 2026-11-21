from __future__ import annotations

import re
from dataclasses import dataclass

from aiogram.enums import MessageEntityType
from aiogram.types import Message, MessageEntity

FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})([^\n]*)$")
TYPST_LANGS = {"typst", "typ"}


@dataclass(frozen=True, slots=True)
class TypstFenceSpan:
    start: int
    end: int
    code: str


def find_typst_fences(markdown_text: str) -> list[TypstFenceSpan]:
    spans: list[TypstFenceSpan] = []
    lines = markdown_text.splitlines(keepends=True)

    offset = 0
    fence_char = ""
    fence_len = 0
    in_typst_fence = False
    block_start = 0
    code_lines: list[str] = []

    for line in lines:
        line_len = len(line)
        stripped = line.rstrip("\r\n")

        if not fence_char:
            match = FENCE_OPEN_RE.match(stripped)
            if match:
                marks, info = match.groups()
                fence_char = marks[0]
                fence_len = len(marks)
                lang = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""
                in_typst_fence = lang in TYPST_LANGS
                block_start = offset
                code_lines = []
        else:
            close_re = re.compile(rf"^[ \t]*{re.escape(fence_char)}{{{fence_len},}}[ \t]*$")
            if close_re.match(stripped):
                if in_typst_fence:
                    spans.append(
                        TypstFenceSpan(
                            start=block_start,
                            end=offset + line_len,
                            code="".join(code_lines).strip("\n"),
                        )
                    )
                fence_char = ""
                fence_len = 0
                in_typst_fence = False
                code_lines = []
            elif in_typst_fence:
                code_lines.append(line)

        offset += line_len

    return spans


def _extract_utf16_slice(text: str, offset: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    chunk = encoded[offset * 2 : (offset + length) * 2]
    return chunk.decode("utf-16-le", errors="replace")


def _extract_from_entities(
    text: str,
    entities: list[MessageEntity] | None,
) -> list[str]:
    if not text or not entities:
        return []
    blocks: list[str] = []
    for entity in entities:
        if entity.type != MessageEntityType.PRE:
            continue
        lang = (entity.language or "").strip().lower()
        if lang not in TYPST_LANGS:
            continue
        code = _extract_utf16_slice(text, entity.offset, entity.length).strip("\n")
        if code.strip():
            blocks.append(code)
    return blocks


def extract_typst_blocks_from_message(message: Message) -> list[str]:
    text = message.text or message.caption or ""
    entities = message.entities if message.text else message.caption_entities

    blocks = _extract_from_entities(text, entities)
    if blocks:
        return blocks

    return [span.code for span in find_typst_fences(text) if span.code.strip()]


def has_typst_blocks(message: Message) -> bool:
    return bool(extract_typst_blocks_from_message(message))


def no_typst_blocks(message: Message) -> bool:
    return not has_typst_blocks(message)


def strip_code_fences(text: str) -> str:
    fences = find_typst_fences(text)
    if fences:
        return fences[0].code.strip()

    cleaned = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\n([\s\S]*?)\n```$", cleaned)
    if match:
        return match.group(1).strip()
    return cleaned