"""
Build workspace preparation for every supported repository layout.

A full void-packages fork already contains ``xbps-src`` and ``common/``.
Template-only repositories (flat root like neko-pkgs, ``srcpkgs/`` like
z-packages, ``pkgs/`` or categorized trees) do not: xbps-src cannot build
anything there because the engine itself is missing.

``prepare_build_tree`` composes a pristine void-packages checkout with the
repository templates, preserving symlinked subpackages. The result is a
complete tree that can be mounted as ``/void-packages`` and used by both the
seeder and the builder.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Optional

from core.adapter import RepoAdapter, RepoKind

logger = logging.getLogger("autopkg.builder.workspace")

VOID_PACKAGES_URL = "https://github.com/void-linux/void-packages.git"

_IGNORED_ENTRIES = {
    ".git",
    ".github",
    ".forgejo",
    "common",
    "etc",
    "hostdir",
    "masterdir",
    "tests",
    "distfiles",
    "__pycache__",
}


def is_full_void_tree(root: str | Path) -> bool:
    """Return True when *root* already contains a usable xbps-src tree."""
    root = Path(root)
    return (root / "xbps-src").is_file() and (root / "common" / "xbps-src").is_dir()


def _iter_package_dirs(adapter: RepoAdapter) -> Iterator[Path]:
    """Yield every package directory (real dir or subpackage symlink)."""
    if adapter.kind == RepoKind.CATEGORIZED:
        for template in adapter._pkg_to_path.values():
            yield template.parent
        for name in adapter._subpackages_map:
            yield adapter.pkgs_dir / name
        return

    try:
        entries = sorted(os.scandir(adapter.pkgs_dir), key=lambda e: e.name)
    except (OSError, PermissionError):
        return

    for entry in entries:
        if entry.name.startswith(".") or entry.name in _IGNORED_ENTRIES:
            continue

        path = Path(entry.path)
        if entry.is_symlink():
            try:
                target = path.resolve(strict=True)
            except OSError:
                continue
            if (target / "template").is_file():
                yield path
        elif entry.is_dir() and (path / "template").is_file():
            yield path


def _replace_path(source: Path, target: Path) -> None:
    """Atomically replace *target* with a copy (or symlink) of *source*."""
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)

    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    else:
        shutil.copytree(source, target, symlinks=True)


def overlay_templates(repo_root: str | Path, dest_srcpkgs: str | Path) -> int:
    """Overlay repository packages into ``<build-tree>/srcpkgs``.

    Returns the number of package entries copied. Existing entries with the
    same name are replaced so a re-run never mixes stale templates with new
    ones.
    """
    adapter = RepoAdapter(repo_root)
    dest_srcpkgs = Path(dest_srcpkgs)
    dest_srcpkgs.mkdir(parents=True, exist_ok=True)

    count = 0
    for package_dir in _iter_package_dirs(adapter):
        _replace_path(package_dir, dest_srcpkgs / package_dir.name)
        count += 1

    logger.info(
        "Overlaid %d package(s) from %s (%s layout) into %s",
        count,
        adapter.root_dir,
        adapter.kind.value,
        dest_srcpkgs,
    )
    return count


def _sync_engine(repo_root: Path, build_tree: Path) -> None:
    """Copy the autopkg engine into the build tree for in-container use."""
    source = repo_root / ".github" / "scripts" / "autopkg"
    target = build_tree / ".github" / "scripts" / "autopkg"
    if not source.is_dir() or source.resolve() == target.resolve():
        return

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".cache"),
    )


def prepare_build_tree(
    repo_root: str | Path,
    dest: str | Path,
    clone_url: str = VOID_PACKAGES_URL,
    ref: Optional[str] = None,
    force: bool = False,
) -> Path:
    """Return a buildable void-packages tree for *repo_root*.

    Full forks are returned unchanged. Template-only repositories are overlaid
    on a shallow clone of *clone_url* placed at *dest*.
    """
    repo_root = Path(repo_root).resolve()
    dest = Path(dest).expanduser().resolve()

    if is_full_void_tree(repo_root):
        logger.info("%s already is a full void-packages tree", repo_root)
        return repo_root

    if force and dest.exists():
        shutil.rmtree(dest)

    if not is_full_void_tree(dest):
        if dest.exists() and any(dest.iterdir()):
            raise RuntimeError(
                f"Destination {dest} is not empty and is not a void-packages tree; "
                "refusing to overwrite it (use --force to recreate it)"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1", "--single-branch"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [clone_url, str(dest)]
        logger.info("Cloning %s into %s", clone_url, dest)
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0 or not is_full_void_tree(dest):
            raise RuntimeError(f"Could not prepare a void-packages tree at {dest}")

    overlay_templates(repo_root, dest / "srcpkgs")
    _sync_engine(repo_root, dest)
    return dest
