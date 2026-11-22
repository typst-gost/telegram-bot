from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    ai_gateway_url: str
    ai_gateway_timeout: float
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
    stream_update_interval: float
    log_level: str
    log_file: str
    repo_slug: str
    repo_branch: str
    repo_assets_subdir: str
    telegram_proxy: str | None


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required")

    raw_font_path = os.getenv("TYPST_FONT_PATH", "").strip()
    font_path = Path(raw_font_path) if raw_font_path else None
    telegram_proxy = os.getenv("TELEGRAM_PROXY", "").strip() or None

    return Settings(
        bot_token=bot_token,
        ai_gateway_url=os.getenv("AI_GATEWAY_URL", "http://localhost:8085").rstrip("/"),
        ai_gateway_timeout=float(os.getenv("AI_GATEWAY_TIMEOUT", "120.0")),
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
        stream_update_interval=float(os.getenv("STREAM_UPDATE_INTERVAL", "0.35")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file=os.getenv("LOG_FILE", "").strip(),
        repo_slug=os.getenv("DOCS_REPO_SLUG", "typst-gost/website").strip(),
        repo_branch=os.getenv("DOCS_REPO_BRANCH", "main").strip(),
        repo_assets_subdir=os.getenv(
            "DOCS_REPO_ASSETS_SUBDIR",
            "public/docs/compile_assets",
        ).strip("/"),
        telegram_proxy=telegram_proxy,
    )