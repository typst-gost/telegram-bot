from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal


@dataclass(frozen=True, slots=True)
class DocSource:
    label: int
    title: str
    url: str
    doc_url: str
    category: str
    content: str
    score: float = 0.0
    cosine: float = 0.0


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

    def with_cited(self, cited_count: int) -> RetrievalStats:
        return replace(self, cited_count=cited_count)


@dataclass(frozen=True, slots=True)
class RagAnswer:
    answer: str
    sources: list[DocSource]
    stats: RetrievalStats | None = None


@dataclass(frozen=True, slots=True)
class RagStreamEvent:
    kind: Literal["sources", "delta", "done"]
    sources: list[DocSource] = field(default_factory=list)
    text: str = ""
    stats: RetrievalStats | None = None