from __future__ import annotations

import argparse
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger


def _extract_subdir(
    tar: tarfile.TarFile,
    subdir: str,
    dest_dir: Path,
) -> int:
    normalized = subdir.strip("/")
    prefix = f"{normalized}/"
    count = 0

    for member in tar.getmembers():
        parts = member.name.split("/", 1)
        if len(parts) < 2:
            continue
        rel_in_repo = parts[1]
        if not rel_in_repo.startswith(prefix):
            continue
        inner_rel = rel_in_repo[len(prefix) :]
        if not inner_rel or member.issym() or member.islnk():
            continue

        dest = (dest_dir / inner_rel).resolve()
        if not dest.is_relative_to(dest_dir.resolve()):
            raise ValueError(f"Unsafe archive entry: {member.name}")

        if member.isdir():
            dest.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            dest.parent.mkdir(parents=True, exist_ok=True)
            src_file = tar.extractfile(member)
            if src_file is not None:
                with src_file, dest.open("wb") as dst_file:
                    shutil.copyfileobj(src_file, dst_file)
                count += 1
    return count


def _atomic_swap(staging_dir: Path, target_dir: Path) -> None:
    target_dir = target_dir.resolve()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = target_dir.parent / f".{target_dir.name}.backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    try:
        if target_dir.exists():
            target_dir.rename(backup_dir)
        staging_dir.rename(target_dir)
    except Exception:
        if backup_dir.exists() and not target_dir.exists():
            backup_dir.rename(target_dir)
        raise
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def sync_compile_assets(
    repo_slug: str,
    branch: str,
    *,
    assets_subdir: str,
    assets_target: Path,
) -> None:
    archive_url = f"https://codeload.github.com/{repo_slug}/tar.gz/refs/heads/{branch}"
    logger.info(f"Downloading compile assets from {repo_slug}@{branch}")

    with tempfile.TemporaryDirectory(prefix="typst_gost_sync_") as tmp_root:
        tmp_path = Path(tmp_root)
        archive_path = tmp_path / "repo.tar.gz"

        request = urllib.request.Request(
            archive_url,
            headers={"User-Agent": "typst-gost-bot-sync/1.0"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            with archive_path.open("wb") as out_file:
                shutil.copyfileobj(response, out_file)

        with tarfile.open(archive_path, "r:gz") as tar:
            assets_target = assets_target.resolve()
            assets_target.parent.mkdir(parents=True, exist_ok=True)
            staging_assets = Path(
                tempfile.mkdtemp(dir=assets_target.parent, prefix=f".{assets_target.name}.")
            )
            try:
                extracted_files = _extract_subdir(tar, assets_subdir, staging_assets)
                _atomic_swap(staging_assets, assets_target)
                logger.info(
                    f"Compile assets synced to {assets_target} ({extracted_files} files)"
                )
            except Exception:
                shutil.rmtree(staging_assets, ignore_errors=True)
                raise


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download or update compile_assets from typst-gost/website",
    )
    parser.add_argument(
        "--repo",
        default=os.getenv("DOCS_REPO_SLUG", "typst-gost/website"),
    )
    parser.add_argument(
        "--branch",
        default=os.getenv("DOCS_REPO_BRANCH", "main"),
    )
    parser.add_argument(
        "--assets-subdir",
        default=os.getenv("DOCS_REPO_ASSETS_SUBDIR", "public/docs/compile_assets"),
    )
    parser.add_argument(
        "--assets-target",
        type=Path,
        default=Path(os.getenv("COMPILE_ASSETS_DIR", "compile_assets")),
    )
    args = parser.parse_args()

    sync_compile_assets(
        args.repo,
        args.branch,
        assets_subdir=args.assets_subdir,
        assets_target=args.assets_target,
    )


if __name__ == "__main__":
    main()