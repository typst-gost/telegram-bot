from __future__ import annotations

import re

SYNONYM_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"реферат", re.I), "abstract keywords count body modern-g7-32"),
    (
        re.compile(r"титул|титульник|гриф|утверждаю|согласовано", re.I),
        "title gost.with hide-title approved-by agreed-by title-template",
    ),
    (
        re.compile(r"исполнител|соисполнител|нормоконтрол", re.I),
        "performers co-performer force-performers",
    ),
    (re.compile(r"содержан|оглавлен|отточ", re.I), "outline depth indent"),
    (re.compile(r"введен|структурн\w+ заголов", re.I), "introduction structure-heading"),
    (re.compile(r"основн\w+ част|заголовк|раздел|подраздел", re.I), "body add-pagebreaks heading"),
    (re.compile(r"заключен", re.I), "conclusion"),
    (
        re.compile(r"источник|литератур|библиограф|\bbib\b", re.I),
        "references bibliography gost-r-705-2008-numeric",
    ),
    (re.compile(r"приложен", re.I), "appendixes appendix-heading"),
    (re.compile(r"листинг|исходн\w+ код|\braw\b", re.I), "code raw breakable"),
    (re.compile(r"перечислен|спис[оа]к|маркер", re.I), "enum list enum-numbering"),
    (re.compile(r"таблиц", re.I), "table figure header"),
    (re.compile(r"рисун|иллюстрац|картинк|схем", re.I), "image figure caption"),
    (re.compile(r"формул|уравнен", re.I), "equation math numbering"),
    (re.compile(r"сноск|примечан", re.I), "footnote"),
    (
        re.compile(r"шрифт|полях|поля|отступ|интервал|настро|кастом", re.I),
        "customization text-size margin indent leading pagination-align",
    ),
    (re.compile(r"word|docx|ворд|экспорт", re.I), "export docx"),
]

VERSION_QUERY_RE = re.compile(
    r"\b0\.\d{1,2}(?:\.\d+)?\b|changelog|список изменений|что нового|миграц|верси",
    re.I,
)
SYMBOL_QUERY_RE = re.compile(r"символ|эмодзи|emoji|\bsym\b|значок|иконк", re.I)
GOST_HINT_RE = re.compile(
    r"гост|gost|modern-g7-32|реферат|титул|исполнител|приложен|нормоконтрол",
    re.I,
)
PARAMS_HINT_RE = re.compile(r"параметр|аргумент|свойств|опци", re.I)
PARAMS_SECTION_RE = re.compile(r"параметр|свойств|таблиц", re.I)
FOLLOWUP_HINT_RE = re.compile(
    r"^(?:а\s+)?(?:как|что|какие|какой|какая|где|почему|зачем|можно\s+ли|покажи|приведи|подробнее|ещё|еще)\b|"
    r"\b(?:он|его|её|ее|их|ним|нём|ней|это|этого|этом|этой|там|туда|сюда|параметр|пример)\b",
    re.I,
)
FTS_NOISE_RE = re.compile(r"[?!@#$%^&*()[\]{}\"'`,;:<>\\/+=~|]+")


def sanitize_fts_query(text: str) -> str:
    cleaned = FTS_NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def expand_query(
    query: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    combined = query
    if history:
        prev_user_queries = [m["content"] for m in history if m.get("role") == "user"]
        if prev_user_queries:
            last_user_query = prev_user_queries[-1]
            words = query.split()
            if len(words) <= 6 or FOLLOWUP_HINT_RE.search(query):
                combined = f"{query} {last_user_query}"

    extras = [extra for pattern, extra in SYNONYM_MAP if pattern.search(combined)]
    extra_str = " ".join(extras)
    embedding_query = f"{combined} ({extra_str})" if extra_str else combined
    fts_query = sanitize_fts_query(f"{combined} {extra_str}")
    return embedding_query, fts_query