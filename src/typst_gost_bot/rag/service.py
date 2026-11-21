from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from typst_gost_bot.config import Settings
from typst_gost_bot.rag.models import DocSource, RagAnswer, RagStreamEvent
from typst_gost_bot.rag.query_builder import expand_query
from typst_gost_bot.rag.retriever import DocsRetriever
from typst_gost_bot.typst_runner.assets import list_compile_assets
from typst_gost_bot.typst_runner.extractor import strip_code_fences

SYSTEM_PROMPT = """Ты — официальный AI-ассистент по системе вёрстки Typst и пакету modern-g7-32 (typst-gost, оформление по ГОСТ 7.32-2017).
Отвечай точно, структурированно и строго на основе переданного контекста документации.

УЧИТЫВАЙ СТРУКТУРУ ДОКУМЕНТАЦИИ:
- Вопросы про «реферат», «титульник», «исполнителей», «содержание», «приложения», «оформление по ГОСТ» относятся к пакету `modern-g7-32` (раздел typst-gost): например, реферату соответствует функция `#abstract`, титульнику — `gost.with(...)`, приложениям — `#show: appendixes`.
- Вопросы про синтаксис языка, `table`, `figure`, `set`/`show`-правила, функции стандартной библиотеки относятся к базовому `Typst`.
- Если вопрос про параметры функции, перечисли все найденные в контексте параметры, их типы, значения по умолчанию и покажи краткий пример кода.
- Не выдумывай параметры и функции, которых нет в контексте. Если в контексте есть только часть ответа, опиши то, что точно есть в источниках, и честно отметь, чего в них нет. Если ответа нет совсем — скажи: «В документации нет информации по этому вопросу».

{assets_section}

ПРАВИЛА ЦИТИРОВАНИЯ И ОФОРМЛЕНИЯ:
1. В тексте ответа ставь ссылки на источники строго как [1], [2].
2. Ставь каждую цифру отдельно: [1][2] или [1], [2], но не [1, 2].
3. НЕ пиши URL-адреса в тексте и НЕ добавляй список «Источники» в конце — ссылки и кнопки под сообщением добавляются автоматически.
4. Для форматирования используй стандартный Markdown (заголовки, списки, таблицы, цитаты, блоки кода ```typst ... ```).
5. Если приводишь готовый к запуску пример кода `modern-g7-32`, включай актуальный `#import` из контекста, чтобы пример можно было скомпилировать.

КОНТЕКСТ ИЗ ДОКУМЕНТАЦИИ:
{context}"""

FIX_SYSTEM_PROMPT = """Ты — эксперт по Typst и пакету modern-g7-32 (ГОСТ 7.32-2017).
Тебе дан исходный код Typst и сообщение об ошибке компилятора.
Исправь код так, чтобы он успешно компилировался и сохранял исходный замысел пользователя.

ПРАВИЛА ОТВЕТА:
1. Верни ТОЛЬКО исправленный код Typst без каких-либо пояснений, приветствий и списка источников.
2. Оберни результат ровно в один блок ```typst ... ```.
3. Используй точные имена параметров и импорты из документации и доступные файлы ассетов.

{assets_section}

КОНТЕКСТ ИЗ ДОКУМЕНТАЦИИ:
{context}"""


class RagService:
    def __init__(self, settings: Settings, retriever: DocsRetriever) -> None:
        self._settings = settings
        self._retriever = retriever
        self._client = AsyncOpenAI(
            base_url=settings.polza_base_url,
            api_key=settings.polza_api_key,
        )

    async def close(self) -> None:
        await self._client.close()

    def _build_assets_section(self) -> str:
        assets = list_compile_assets(self._settings.assets_dir)
        if not assets:
            return (
                "ДОСТУПНЫЕ АССЕТЫ ДЛЯ КОМПИЛЯЦИИ:\n"
                "Локальные файлы изображений отсутствуют. В примерах кода не указывай вымышленные пути в `#image(...)` или `#read(...)` — вместо этого используй встроенную графику Typst (`rect`, `circle`, `polygon`, `table`)."
            )
        listed = "\n".join(f"- `{item}`" for item in assets[:80])
        return (
            "ДОСТУПНЫЕ АССЕТЫ ДЛЯ КОМПИЛЯЦИИ:\n"
            "Для примеров с изображениями или внешними данными используй ТОЛЬКО реальные пути из списка ниже (если подходящего файла нет — рисуй средствами Typst):\n"
            f"{listed}"
        )

    def _build_chat_messages(
        self,
        question: str,
        sources: list[DocSource],
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        context_text = "\n---\n".join(
            f"[Источник {src.label}] ({src.title}):\n{src.content}"
            for src in sources
        )
        system_prompt = SYSTEM_PROMPT.format(
            assets_section=self._build_assets_section(),
            context=context_text,
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": question})
        return messages

    async def search_only(
        self,
        question: str,
        max_sources: int = 5,
        history: list[dict[str, str]] | None = None,
    ) -> list[DocSource]:
        embedding_query, fts_query = expand_query(question, history=history)
        emb_response = await self._client.embeddings.create(
            model=self._settings.embedding_model,
            input=[embedding_query],
        )
        query_vector = emb_response.data[0].embedding
        return await self._retriever.retrieve(
            raw_query=question,
            query_vector=query_vector,
            fts_query=fts_query,
            max_sources=max_sources,
        )

    async def answer(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> RagAnswer:
        sources = await self.search_only(question, history=history)
        if not sources:
            return RagAnswer(
                answer="В документации нет информации по этому вопросу.",
                sources=[],
            )

        messages = self._build_chat_messages(question, sources, history)
        completion = await self._client.chat.completions.create(
            model=self._settings.chat_model,
            messages=messages,
            temperature=0.2,
        )
        content = (completion.choices[0].message.content or "").strip()
        if not content:
            content = "Не удалось сформировать ответ по найденной документации."
        return RagAnswer(answer=content, sources=sources)

    async def stream_answer(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[RagStreamEvent]:
        sources = await self.search_only(question, history=history)
        yield RagStreamEvent(kind="sources", sources=sources)

        if not sources:
            yield RagStreamEvent(
                kind="done",
                sources=[],
                text="В документации нет информации по этому вопросу.",
            )
            return

        messages = self._build_chat_messages(question, sources, history)
        stream = await self._client.chat.completions.create(
            model=self._settings.chat_model,
            messages=messages,
            temperature=0.2,
            stream=True,
        )

        parts: list[str] = []
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    parts.append(delta)
                    yield RagStreamEvent(kind="delta", sources=sources, text=delta)
        finally:
            await stream.close()

        full_text = "".join(parts).strip()
        if not full_text:
            full_text = "Не удалось сформировать ответ по найденной документации."
        yield RagStreamEvent(kind="done", sources=sources, text=full_text)

    async def fix_code(self, source_code: str, error_text: str) -> str:
        retrieval_query = f"ошибка typst {error_text[:300]}\n{source_code[:400]}"
        sources = await self.search_only(retrieval_query, max_sources=4)
        context_text = (
            "\n---\n".join(
                f"[Источник {src.label}] ({src.title}):\n{src.content}"
                for src in sources
            )
            if sources
            else "Дополнительный контекст не найден."
        )
        system_prompt = FIX_SYSTEM_PROMPT.format(
            assets_section=self._build_assets_section(),
            context=context_text,
        )
        user_prompt = (
            f"Исходный код:\n```typst\n{source_code}\n```\n\n"
            f"Ошибка компиляции:\n```text\n{error_text}\n```"
        )
        completion = await self._client.chat.completions.create(
            model=self._settings.chat_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        raw_reply = (completion.choices[0].message.content or "").strip()
        fixed_code = strip_code_fences(raw_reply)
        return fixed_code or source_code