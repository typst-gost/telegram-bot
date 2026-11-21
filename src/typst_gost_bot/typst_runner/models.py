from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompileResult:
    pages: list[bytes]
    error: str | None = None
    timed_out: bool = False
    truncated: bool = False