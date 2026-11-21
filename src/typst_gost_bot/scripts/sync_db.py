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


def sync_website_resources(
    repo_slug: str,
    branch: str,
    *,
    db_subdir: str | None,
    db_target: Path | None,
    assets_subdir: str | None,
    assets_target: Path | None,
) -> None:
    archive_url = f"https://codeload.github.com/{repo_slug}/tar.gz/refs/heads/{branch}"
    logger.info(f"Downloading repository archive {repo_slug}@{branch}")

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
            if db_subdir and db_target is not None:
                db_target = db_target.resolve()
                db_target.parent.mkdir(parents=True, exist_ok=True)
                staging_db = Path(tempfile.mkdtemp(dir=db_target.parent, prefix=f".{db_target.name}."))
                try:
                    _extract_subdir(tar, db_subdir, staging_db)
                    if not (staging_db / "docs.lance").exists():
                        raise RuntimeError(
                            f"Directory '{db_subdir}' does not contain docs.lance"
                        )
                    _atomic_swap(staging_db, db_target)
                    logger.info(f"Vector database synced to {db_target}")
                except Exception:
                    shutil.rmtree(staging_db, ignore_errors=True)
                    raise

            if assets_subdir and assets_target is not None:
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
        description="Download or update docs_vector_db and compile_assets from typst-gost/website",
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
        "--db-subdir",
        default=os.getenv("DOCS_REPO_SUBDIR", "docs_vector_db"),
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(os.getenv("DOCS_DB_PATH", "docs_vector_db")),
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
    parser.add_argument("--db-only", action="store_true")
    parser.add_argument("--assets-only", action="store_true")
    args = parser.parse_args()

    sync_website_resources(
        args.repo,
        args.branch,
        db_subdir=None if args.assets_only else args.db_subdir,
        db_target=None if args.assets_only else args.target,
        assets_subdir=None if args.db_only else args.assets_subdir,
        assets_target=None if args.db_only else args.assets_target,
    )


if __name__ == "__main__":
    main()