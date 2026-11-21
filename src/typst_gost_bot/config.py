from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    polza_api_key: str
    polza_base_url: str
    embedding_model: str
    chat_model: str
    db_path: Path
    docs_base_url: str
    rate_limit_seconds: float
    log_level: str
    log_file: str
    repo_slug: str
    repo_branch: str
    repo_db_subdir: str


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required")

    polza_api_key = os.getenv("POLZA_AI_API_KEY", "").strip()
    if not polza_api_key:
        raise ValueError("POLZA_AI_API_KEY is required")

    return Settings(
        bot_token=bot_token,
        polza_api_key=polza_api_key,
        polza_base_url=os.getenv("POLZA_BASE_URL", "https://polza.ai/api/v1").rstrip("/"),
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"),
        chat_model=os.getenv("RAG_CHAT_MODEL", "deepseek/deepseek-v4.1-flash-1017"),
        db_path=Path(os.getenv("DOCS_DB_PATH", "docs_vector_db")),
        docs_base_url=os.getenv("DOCS_BASE_URL", "https://typst-gost.ru").rstrip("/"),
        rate_limit_seconds=float(os.getenv("RATE_LIMIT_SECONDS", "3.0")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "").strip(),
        repo_slug=os.getenv("DOCS_REPO_SLUG", "typst-gost/website").strip(),
        repo_branch=os.getenv("DOCS_REPO_BRANCH", "main").strip(),
        repo_db_subdir=os.getenv("DOCS_REPO_SUBDIR", "docs_vector_db").strip("/"),
    )