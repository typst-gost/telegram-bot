from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocSource:
    label: int
    title: str
    url: str
    doc_url: str
    category: str
    content: str


@dataclass(frozen=True, slots=True)
class RagAnswer:
    answer: str
    sources: list[DocSource]