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


def sync_docs_vector_db(
    repo_slug: str,
    branch: str,
    subdir: str,
    target_dir: Path,
) -> None:
    archive_url = (
        f"https://codeload.github.com/{repo_slug}/tar.gz/refs/heads/{branch}"
    )
    target_dir = target_dir.resolve()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    normalized_subdir = subdir.strip("/")

    logger.info(f"Downloading {normalized_subdir} from {repo_slug}@{branch}")

    with tempfile.TemporaryDirectory(
        dir=target_dir.parent,
        prefix=f".{target_dir.name}.",
    ) as tmp_root:
        tmp_path = Path(tmp_root)
        archive_path = tmp_path / "repo.tar.gz"

        request = urllib.request.Request(
            archive_url,
            headers={"User-Agent": "typst-gost-bot-sync/1.0"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            with archive_path.open("wb") as out_file:
                shutil.copyfileobj(response, out_file)

        extracted_db = tmp_path / "extracted_db"
        extracted_db.mkdir()

        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 1)
                if len(parts) < 2:
                    continue
                rel_in_repo = parts[1]
                if not rel_in_repo.startswith(f"{normalized_subdir}/"):
                    continue
                inner_rel = rel_in_repo[len(normalized_subdir) + 1 :]
                if not inner_rel or member.issym() or member.islnk():
                    continue
                dest = (extracted_db / inner_rel).resolve()
                if not dest.is_relative_to(extracted_db.resolve()):
                    raise ValueError(f"Unsafe archive entry: {member.name}")
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    src_file = tar.extractfile(member)
                    if src_file is not None:
                        with src_file, dest.open("wb") as dst_file:
                            shutil.copyfileobj(src_file, dst_file)

        if not (extracted_db / "docs.lance").exists():
            raise RuntimeError(
                f"Directory '{normalized_subdir}' does not contain docs.lance"
            )

        backup_dir = target_dir.parent / f".{target_dir.name}.backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

        try:
            if target_dir.exists():
                target_dir.rename(backup_dir)
            extracted_db.rename(target_dir)
        except Exception:
            if backup_dir.exists() and not target_dir.exists():
                backup_dir.rename(target_dir)
            raise
        finally:
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

    logger.info(f"Vector database synced to {target_dir}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download or update docs_vector_db from typst-gost/website",
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
        "--subdir",
        default=os.getenv("DOCS_REPO_SUBDIR", "docs_vector_db"),
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(os.getenv("DOCS_DB_PATH", "docs_vector_db")),
    )
    args = parser.parse_args()
    sync_docs_vector_db(args.repo, args.branch, args.subdir, args.target)


if __name__ == "__main__":
    main()