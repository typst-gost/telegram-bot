from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class DocSource:
    label: int
    title: str
    url: str
    doc_url: str
    category: str
    score: float = 0.0
    cosine: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocSource:
        return cls(
            label=int(data.get("label", 0)),
            title=str(data.get("title", "Документация")),
            url=str(data.get("url", "")),
            doc_url=str(data.get("doc_url", "")),
            category=str(data.get("category", "")),
            score=float(data.get("score", 0.0)),
            cosine=float(data.get("cosine", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class RetrievalStats:
    candidates_count: int
    selected_count: int
    top_score: float
    top_cosine: float
    cutoff_score: float
    min_score: float
    rel_ratio: float
    cited_count: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetrievalStats | None:
        if not data:
            return None
        return cls(
            candidates_count=int(data.get("candidates_count", 0)),
            selected_count=int(data.get("selected_count", 0)),
            top_score=float(data.get("top_score", 0.0)),
            top_cosine=float(data.get("top_cosine", 0.0)),
            cutoff_score=float(data.get("cutoff_score", 0.0)),
            min_score=float(data.get("min_score", 0.0)),
            rel_ratio=float(data.get("rel_ratio", 0.0)),
            cited_count=(
                int(data["cited_count"]) if data.get("cited_count") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RagAnswer:
    answer: str
    raw_answer: str
    sources: list[DocSource]
    stats: RetrievalStats | None = None
    blocked_by_guard: bool = False
    no_docs_found: bool = False
    debug_markdown: str | None = None
    already_renumbered: bool = True


@dataclass(frozen=True, slots=True)
class RagStreamEvent:
    kind: Literal["meta", "delta", "done"]
    sources: list[DocSource] = field(default_factory=list)
    text: str = ""
    raw_text: str = ""
    stats: RetrievalStats | None = None
    blocked_by_guard: bool = False
    no_docs_found: bool = False
    debug_markdown: str | None = None


@dataclass(frozen=True, slots=True)
class FixResult:
    fixed_code: str
    blocked_by_guard: bool = False
    refusal_message: str | None = None
    debug_markdown: str | None = None