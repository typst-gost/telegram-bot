from __future__ import annotations

import re
from dataclasses import replace

from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    InputRichMessage,
    InputRichMessageMedia,
    MessageEntity,
)
from loguru import logger
from telegramify_markdown import convert, split_entities, telegramify_rich

from typst_gost_bot.rag.models import DocSource

CODE_SEGMENT_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")
CITATION_GROUP_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\](?!\()")


def filter_and_renumber_sources(
    markdown_text: str,
    sources: list[DocSource],
) -> tuple[str, list[DocSource]]:
    if not sources or not markdown_text.strip():
        return markdown_text, []

    source_by_label = {src.label: src for src in sources}
    new_label_by_old: dict[int, int] = {}

    segments = CODE_SEGMENT_RE.split(markdown_text)

    # 1. Собираем фактически процитированные номера в порядке первого появления в тексте
    for idx in range(0, len(segments), 2):
        for match in CITATION_GROUP_RE.finditer(segments[idx]):
            for part in match.group(1).split(","):
                num = int(part.strip())
                if num in source_by_label and num not in new_label_by_old:
                    new_label_by_old[num] = len(new_label_by_old) + 1

    if not new_label_by_old:
        return markdown_text, []

    # 2. Перенумеровываем ссылки в прозе (код внутри ```...``` и `...` не трогаем)
    def _remap(match: re.Match[str]) -> str:
        raw_nums = [int(part.strip()) for part in match.group(1).split(",")]
        remapped: list[str] = []
        for num in raw_nums:
            new_num = new_label_by_old.get(num)
            remapped.append(f"[{new_num if new_num is not None else num}]")
        return ", ".join(remapped) if len(remapped) > 1 else remapped[0]

    for idx in range(0, len(segments), 2):
        segments[idx] = CITATION_GROUP_RE.sub(_remap, segments[idx])

    used_sources = [
        replace(source_by_label[old_label], label=new_label)
        for old_label, new_label in new_label_by_old.items()
    ]
    return "".join(segments), used_sources


def _replace_citations_in_prose(
    text: str,
    url_by_label: dict[int, str],
) -> str:
    def _replace(match: re.Match[str]) -> str:
        raw_nums = [part.strip() for part in match.group(1).split(",")]
        linked: list[str] = []
        for raw_num in raw_nums:
            num = int(raw_num)
            url = url_by_label.get(num)
            if url:
                linked.append(f"[[{num}]]({url})")
            else:
                linked.append(f"[{num}]")
        return " ".join(linked)

    return CITATION_GROUP_RE.sub(_replace, text)


def inject_source_links(
    markdown_text: str,
    sources: list[DocSource],
) -> str:
    if not sources:
        return markdown_text

    url_by_label = {src.label: src.url for src in sources}
    segments = CODE_SEGMENT_RE.split(markdown_text)
    for idx in range(0, len(segments), 2):
        segments[idx] = _replace_citations_in_prose(segments[idx], url_by_label)
    return "".join(segments)


def _model_to_dict(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        return item
    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    raise TypeError(f"Unsupported object type: {type(item)!r}")


def _build_slot_html(media_ids: list[str]) -> str:
    if len(media_ids) == 1:
        return f'<figure><img src="tg://photo?id={media_ids[0]}"/></figure>'
    images = "".join(f'<img src="tg://photo?id={mid}"/>' for mid in media_ids)
    return f"<tg-collage>{images}</tg-collage>"


def to_rich_messages(
    markdown_text: str,
    slot_images: dict[str, list[bytes]] | None = None,
) -> list[InputRichMessage]:
    try:
        raw_chunks = telegramify_rich(markdown_text, mode="html")
        payloads = [_model_to_dict(chunk) for chunk in raw_chunks] if raw_chunks else []
    except Exception as exc:
        logger.warning(f"telegramify_rich failed, falling back to native markdown: {exc}")
        payloads = []

    if not payloads:
        return [InputRichMessage(markdown=markdown_text[:32000])]

    if not slot_images:
        return [InputRichMessage.model_validate(payload) for payload in payloads]

    result: list[InputRichMessage] = []
    for chunk_idx, payload in enumerate(payloads):
        html = str(payload.get("html") or "")
        media_items: list[InputRichMessageMedia] = []

        for slot_token, png_pages in slot_images.items():
            if slot_token not in html or not png_pages:
                continue

            media_ids: list[str] = []
            for page_idx, png_bytes in enumerate(png_pages):
                media_id = f"r_{chunk_idx}_{len(media_items)}_{page_idx}"
                media_ids.append(media_id)
                media_items.append(
                    InputRichMessageMedia(
                        id=media_id,
                        media=InputMediaPhoto(
                            media=BufferedInputFile(
                                png_bytes,
                                filename=f"{media_id}.png",
                            )
                        ),
                    )
                )

            slot_re = re.compile(rf"(?:<p>\s*)?{re.escape(slot_token)}(?:\s*</p>)?")
            html = slot_re.sub(_build_slot_html(media_ids), html)

        payload["html"] = html
        if media_items:
            payload["media"] = media_items
        result.append(InputRichMessage.model_validate(payload))

    return result


def to_fallback_entities(markdown_text: str) -> list[tuple[str, list[MessageEntity]]]:
    plain_text, raw_entities = convert(markdown_text)
    chunks = split_entities(plain_text, raw_entities, 4096)
    result: list[tuple[str, list[MessageEntity]]] = []
    for text_part, ent_part in chunks:
        entities = [MessageEntity.model_validate(_model_to_dict(e)) for e in ent_part]
        result.append((text_part, entities))
    return result