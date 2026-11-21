from __future__ import annotations

import asyncio
import os
import re
import signal
import tempfile
from pathlib import Path

from loguru import logger

from typst_gost_bot.config import Settings
from typst_gost_bot.typst_runner.assets import stage_assets
from typst_gost_bot.typst_runner.models import CompileResult

PAGE_PREAMBLE = (
    "#set page(width: 500pt, height: auto, footer: none, margin: 30pt, fill: color.white)\n"
)
GOST_WITH_RE = re.compile(r"#show\s*:\s*gost\.with")


class TypstCompiler:
    def __init__(self, settings: Settings, max_concurrent: int = 3) -> None:
        self._typst_bin = settings.typst_bin
        self._assets_dir = settings.assets_dir
        self._package_cache_dir = settings.typst_cache_dir.resolve() / "packages"
        self._font_path = settings.typst_font_path
        self._timeout = settings.compile_timeout_seconds
        self._ppi = settings.compile_ppi
        self._max_source_chars = settings.max_source_chars
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _prepare_source(self, source: str) -> str:
        if GOST_WITH_RE.search(source):
            return source
        return f"{PAGE_PREAMBLE}{source}"

    def _clean_diagnostic(self, raw_error: str, workdir: Path) -> str:
        text = raw_error.strip()
        for prefix in (str(workdir.resolve()), str(workdir)):
            text = text.replace(f"{prefix}/", "").replace(f"{prefix}\\", "").replace(prefix, "")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:2500] or "Неизвестная ошибка компиляции."

    async def compile_source(
        self,
        source: str,
        *,
        max_pages: int | None = None,
    ) -> CompileResult:
        if not source.strip():
            return CompileResult(pages=[], error="Пустой код Typst.")

        if len(source) > self._max_source_chars:
            return CompileResult(
                pages=[],
                error=f"Код слишком длинный (лимит {self._max_source_chars} символов).",
            )

        self._package_cache_dir.mkdir(parents=True, exist_ok=True)

        async with self._semaphore:
            with tempfile.TemporaryDirectory(prefix="typst_bot_") as tmp_dir:
                workdir = Path(tmp_dir)
                stage_assets(self._assets_dir, workdir)

                prepared_source = self._prepare_source(source)
                main_typ = workdir / "main.typ"
                main_typ.write_text(prepared_source, encoding="utf-8")
                out_pattern = workdir / "page-{0p}.png"

                cmd = [
                    self._typst_bin,
                    "compile",
                    "--root",
                    str(workdir),
                    "--format",
                    "png",
                    "--ppi",
                    str(self._ppi),
                    "--diagnostic-format",
                    "short",
                    "--package-cache-path",
                    str(self._package_cache_dir),
                ]
                if self._font_path is not None and self._font_path.exists():
                    cmd.extend(["--font-path", str(self._font_path)])

                probe_limit = max_pages + 1 if max_pages is not None and max_pages > 0 else None
                if probe_limit is not None:
                    cmd.extend(["--pages", f"1-{probe_limit}"])

                cmd.extend([str(main_typ), str(out_pattern)])

                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        start_new_session=(os.name == "posix"),
                    )
                except FileNotFoundError:
                    logger.error(f"Typst binary not found: {self._typst_bin}")
                    return CompileResult(
                        pages=[],
                        error="Компилятор typst не установлен или не найден в PATH.",
                    )

                try:
                    _, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=self._timeout,
                    )
                except TimeoutError:
                    if os.name == "posix":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        proc.kill()
                    await proc.wait()
                    return CompileResult(
                        pages=[],
                        error=(
                            f"Превышено время компиляции ({self._timeout:.0f} с). "
                            "Упростите код или уменьшите объём генерируемого документа."
                        ),
                        timed_out=True,
                    )

                if proc.returncode != 0:
                    err_text = stderr_bytes.decode("utf-8", errors="replace")
                    return CompileResult(
                        pages=[],
                        error=self._clean_diagnostic(err_text, workdir),
                    )

                rendered_files = sorted(workdir.glob("page-*.png"))
                truncated = False
                if max_pages is not None and len(rendered_files) > max_pages:
                    rendered_files = rendered_files[:max_pages]
                    truncated = True

                pages = [path.read_bytes() for path in rendered_files]
                return CompileResult(pages=pages, truncated=truncated)