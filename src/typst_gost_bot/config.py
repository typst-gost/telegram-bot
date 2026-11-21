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
    assets_dir: Path
    typst_bin: str
    typst_cache_dir: Path
    typst_font_path: Path | None
    compile_timeout_seconds: float
    compile_ppi: int
    max_source_chars: int
    max_render_attachments: int
    rate_limit_seconds: float
    log_level: str
    log_file: str
    repo_slug: str
    repo_branch: str
    repo_db_subdir: str
    repo_assets_subdir: str


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required")

    polza_api_key = os.getenv("POLZA_AI_API_KEY", "").strip()
    if not polza_api_key:
        raise ValueError("POLZA_AI_API_KEY is required")

    raw_font_path = os.getenv("TYPST_FONT_PATH", "").strip()
    font_path = Path(raw_font_path) if raw_font_path else None

    return Settings(
        bot_token=bot_token,
        polza_api_key=polza_api_key,
        polza_base_url=os.getenv("POLZA_BASE_URL", "https://polza.ai/api/v1").rstrip("/"),
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"),
        chat_model=os.getenv("RAG_CHAT_MODEL", "deepseek/deepseek-v4.1-flash-1017"),
        db_path=Path(os.getenv("DOCS_DB_PATH", "docs_vector_db")),
        docs_base_url=os.getenv("DOCS_BASE_URL", "https://typst-gost.ru").rstrip("/"),
        assets_dir=Path(os.getenv("COMPILE_ASSETS_DIR", "compile_assets")),
        typst_bin=os.getenv("TYPST_BIN", "typst").strip(),
        typst_cache_dir=Path(os.getenv("TYPST_CACHE_DIR", ".typst_cache")),
        typst_font_path=font_path,
        compile_timeout_seconds=float(os.getenv("TYPST_COMPILE_TIMEOUT", "15.0")),
        compile_ppi=int(os.getenv("TYPST_COMPILE_PPI", "144")),
        max_source_chars=int(os.getenv("TYPST_MAX_SOURCE_CHARS", "50000")),
        max_render_attachments=int(os.getenv("MAX_RENDER_ATTACHMENTS", "10")),
        rate_limit_seconds=float(os.getenv("RATE_LIMIT_SECONDS", "3.0")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "").strip(),
        repo_slug=os.getenv("DOCS_REPO_SLUG", "typst-gost/website").strip(),
        repo_branch=os.getenv("DOCS_REPO_BRANCH", "main").strip(),
        repo_db_subdir=os.getenv("DOCS_REPO_SUBDIR", "docs_vector_db").strip("/"),
        repo_assets_subdir=os.getenv(
            "DOCS_REPO_ASSETS_SUBDIR",
            "public/docs/compile_assets",
        ).strip("/"),
    )