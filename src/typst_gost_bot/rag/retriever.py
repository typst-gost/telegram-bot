from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lancedb
from lancedb.rerankers import RRFReranker
from loguru import logger

from typst_gost_bot.rag.models import DocSource, RetrievalStats
from typst_gost_bot.rag.query_builder import (
    GOST_HINT_RE,
    PARAMS_HINT_RE,
    PARAMS_SECTION_RE,
    SYMBOL_QUERY_RE,
    VERSION_QUERY_RE,
)

MAX_SOURCES = 5
MAX_PAGE_CHARS = 10000
MAX_TOTAL_CONTEXT_CHARS = 24000
RRF_MAX_THEORETICAL = 2.0 / 61.0
PART_SUFFIX_RE = re.compile(r"\s*\(часть \d+ из \d+\)$")


def _cosine_similarity(a: list[float], b: Any) -> float | None:
    if b is None:
        return None
    try:
        if len(b) != len(a) or not a:
            return None
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for va, vb in zip(a, b):
            fa = float(va)
            fb = float(vb)
            dot += fa * fb
            norm_a += fa * fa
            norm_b += fb * fb
        denom = math.sqrt(norm_a) * math.sqrt(norm_b)
        if denom <= 0.0:
            return None
        return dot / denom
    except (TypeError, ValueError):
        return None


@dataclass
class _SourceAccumulator:
    label: int
    url: str
    doc_url: str
    title: str
    category: str
    score: float
    cosine: float
    matched_orders: set[int] = field(default_factory=set)
    fallback_chunks: list[dict[str, Any]] = field(default_factory=list)


class DocsRetriever:
    def __init__(
        self,
        db_path: Path,
        docs_base_url: str,
        *,
        min_score: float = 0.40,
        rel_ratio: float = 0.72,
    ) -> None:
        self._db_path = db_path
        self._docs_base_url = docs_base_url.rstrip("/")
        self._min_score = min_score
        self._rel_ratio = rel_ratio
        self._table: lancedb.table.Table | None = None
        self._reranker = RRFReranker()

    def _get_table(self) -> lancedb.table.Table:
        if self._table is not None:
            return self._table
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"Vector database directory not found: {self._db_path}"
            )
        db = lancedb.connect(str(self._db_path))
        self._table = db.open_table("docs")
        return self._table

    def reload(self) -> None:
        self._table = None
        self._get_table()

    async def retrieve(
        self,
        raw_query: str,
        query_vector: list[float],
        fts_query: str,
        limit: int = 24,
        max_sources: int = MAX_SOURCES,
    ) -> list[DocSource]:
        sources, _ = await self.retrieve_with_stats(
            raw_query=raw_query,
            query_vector=query_vector,
            fts_query=fts_query,
            limit=limit,
            max_sources=max_sources,
        )
        return sources

    async def retrieve_with_stats(
        self,
        raw_query: str,
        query_vector: list[float],
        fts_query: str,
        limit: int = 24,
        max_sources: int = MAX_SOURCES,
    ) -> tuple[list[DocSource], RetrievalStats]:
        candidates = await asyncio.to_thread(
            self._search_hybrid_sync,
            query_vector,
            fts_query,
            limit,
        )
        scored_rows = self._score_candidates(candidates, raw_query, query_vector)
        return await asyncio.to_thread(
            self._filter_and_expand_sources_sync,
            scored_rows,
            max_sources,
        )

    def _search_hybrid_sync(
        self,
        query_vector: list[float],
        fts_query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        table = self._get_table()
        if fts_query:
            try:
                return (
                    table.search(
                        query_type="hybrid",
                        vector_column_name="vector",
                        fts_columns="text",
                    )
                    .vector(query_vector)
                    .text(fts_query)
                    .rerank(self._reranker)
                    .limit(limit)
                    .to_list()
                )
            except Exception as exc:
                logger.warning(f"Hybrid search failed, falling back to vector search: {exc}")

        return table.search(query_vector).limit(limit).to_list()

    def _infer_category(self, row: dict[str, Any]) -> str:
        if row.get("category"):
            return str(row["category"])
        url = str(row.get("url", ""))
        if "/docs/typst-gost" in url:
            return "typst-gost"
        if "/docs/typst/changelog" in url:
            return "typst-changelog"
        if "/docs/typst/symbols" in url:
            return "typst-symbols"
        return "typst-reference"

    def _score_candidates(
        self,
        rows: list[dict[str, Any]],
        raw_query: str,
        query_vector: list[float],
    ) -> list[tuple[float, float, dict[str, Any]]]:
        wants_changelog = bool(VERSION_QUERY_RE.search(raw_query))
        wants_symbols = bool(SYMBOL_QUERY_RE.search(raw_query))
        mentions_gost = bool(GOST_HINT_RE.search(raw_query))
        wants_params = bool(PARAMS_HINT_RE.search(raw_query))

        scored: list[tuple[float, float, dict[str, Any]]] = []
        for idx, row in enumerate(rows):
            category = self._infer_category(row)
            relevance = row.get("_relevance_score")
            if isinstance(relevance, (int, float)):
                rrf_norm = min(1.0, max(0.0, float(relevance) / RRF_MAX_THEORETICAL))
            else:
                rrf_norm = 1.0 / (1.0 + idx * 0.15)

            cos_sim = _cosine_similarity(query_vector, row.get("vector"))
            if cos_sim is not None:
                clamped_cos = max(0.0, cos_sim)
                # Гейтируем RRF-бонус по косинусному сходству, чтобы слабые BM25-совпадения не раздували мусор
                rrf_effective = rrf_norm * min(1.0, clamped_cos / 0.45)
                score = 0.72 * clamped_cos + 0.28 * rrf_effective
                cos_value = cos_sim
            else:
                score = rrf_norm
                cos_value = 0.0

            if category == "typst-changelog" and not wants_changelog:
                score *= 0.35
            if category == "typst-symbols" and not wants_symbols:
                score *= 0.40
            if category == "typst-gost" and mentions_gost:
                score *= 1.15
            if wants_params and PARAMS_SECTION_RE.search(str(row.get("section", ""))):
                score *= 1.10

            scored.append((score, cos_value, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def _fetch_siblings_sync(self, doc_url: str) -> list[dict[str, Any]]:
        table = self._get_table()
        safe_url = doc_url.replace("'", "''")
        try:
            rows = table.search().where(f"doc_url = '{safe_url}'").to_list()
            rows.sort(key=lambda r: int(r.get("chunk_order", 0)))
            return rows
        except Exception:
            return []

    def _normalize_public_url(self, raw_url: str) -> str:
        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            return raw_url
        return f"{self._docs_base_url}/{raw_url.lstrip('/')}"

    def _filter_and_expand_sources_sync(
        self,
        scored_rows: list[tuple[float, float, dict[str, Any]]],
        max_sources: int,
    ) -> tuple[list[DocSource], RetrievalStats]:
        groups: dict[str, _SourceAccumulator] = {}

        for score, cos_val, row in scored_rows:
            raw_url = str(row.get("url", ""))
            doc_url = str(row.get("doc_url") or raw_url.split("#")[0])
            group = groups.get(doc_url)

            if group is None:
                title = str(row.get("title", "Документация"))
                section = PART_SUFFIX_RE.sub("", str(row.get("section", "")))
                display_title = (
                    title
                    if not section or section in {title, "Введение"}
                    else f"{title} · {section}"
                )
                group = _SourceAccumulator(
                    label=0,
                    url=self._normalize_public_url(raw_url),
                    doc_url=doc_url,
                    title=display_title,
                    category=self._infer_category(row),
                    score=score,
                    cosine=cos_val,
                )
                groups[doc_url] = group
            else:
                if score > group.score:
                    group.score = score
                    group.cosine = max(group.cosine, cos_val)

            order = row.get("chunk_order")
            if isinstance(order, int):
                group.matched_orders.add(order)
            group.fallback_chunks.append(row)

        ranked_groups = sorted(groups.values(), key=lambda g: g.score, reverse=True)
        candidates_count = len(ranked_groups)
        top_score = ranked_groups[0].score if ranked_groups else 0.0
        top_cosine = ranked_groups[0].cosine if ranked_groups else 0.0
        cutoff_score = max(self._min_score, top_score * self._rel_ratio)

        selected_groups = [g for g in ranked_groups if g.score >= cutoff_score][:max_sources]
        for idx, g in enumerate(selected_groups, start=1):
            g.label = idx

        logger.debug(
            f"RAG retrieval: candidates={candidates_count} selected={len(selected_groups)} "
            f"top_score={top_score:.3f} top_cos={top_cosine:.3f} cutoff={cutoff_score:.3f}"
        )

        stats = RetrievalStats(
            candidates_count=candidates_count,
            selected_count=len(selected_groups),
            top_score=top_score,
            top_cosine=top_cosine,
            cutoff_score=cutoff_score,
            min_score=self._min_score,
            rel_ratio=self._rel_ratio,
        )

        if not selected_groups:
            return [], stats

        total_budget = MAX_TOTAL_CONTEXT_CHARS
        sources: list[DocSource] = []

        for group in selected_groups:
            siblings = self._fetch_siblings_sync(group.doc_url)
            if not siblings:
                text = "\n\n".join(str(c.get("text", "")) for c in group.fallback_chunks)
            else:
                full_text = "\n\n".join(str(c.get("text", "")) for c in siblings)
                if (
                    group.category == "typst-gost" or len(siblings) <= 6
                ) and len(full_text) <= MAX_PAGE_CHARS:
                    text = full_text
                else:
                    wanted_orders: set[int] = set()
                    for order in group.matched_orders:
                        wanted_orders.update((order - 1, order, order + 1))
                    selected = [
                        c
                        for c in siblings
                        if not isinstance(c.get("chunk_order"), int)
                        or c["chunk_order"] in wanted_orders
                    ]
                    chosen = selected or group.fallback_chunks
                    text = "\n\n".join(str(c.get("text", "")) for c in chosen)[:MAX_PAGE_CHARS]

            if len(text) > total_budget:
                text = text[: max(800, total_budget)]
            total_budget = max(0, total_budget - len(text))

            sources.append(
                DocSource(
                    label=group.label,
                    title=group.title,
                    url=group.url,
                    doc_url=group.doc_url,
                    category=group.category,
                    content=text,
                    score=group.score,
                    cosine=group.cosine,
                )
            )

        return sources, stats