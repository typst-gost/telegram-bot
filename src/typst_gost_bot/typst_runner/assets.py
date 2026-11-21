from __future__ import annotations

import os
import shutil
from pathlib import Path


def list_compile_assets(assets_dir: Path) -> list[str]:
    if not assets_dir.exists() or not assets_dir.is_dir():
        return []
    files = [
        path.relative_to(assets_dir).as_posix()
        for path in sorted(assets_dir.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    ]
    return files


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def stage_assets(assets_dir: Path, workdir: Path) -> None:
    if not assets_dir.exists() or not assets_dir.is_dir():
        return

    mount_roots = (
        workdir,
        workdir / "compile_assets",
        workdir / "docs" / "compile_assets",
    )
    for src in assets_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(assets_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        for root in mount_roots:
            _link_or_copy(src, root / rel)