from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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


@dataclass(frozen=True, slots=True)
class RagStreamEvent:
    kind: Literal["sources", "delta", "done"]
    sources: list[DocSource] = field(default_factory=list)
    text: str = ""