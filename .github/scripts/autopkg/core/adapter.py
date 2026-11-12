"""
Unified Repository Adapter (URA).

Automatically detects Void package repository layouts:
- Standard Void: srcpkgs/<pkg>/template
- Subdir/Forgejo: pkgs/<pkg>/template
- Flat root (e.g. neko-pkgs): <pkg>/template
- Categorized (e.g. vup): srcpkgs/<category>/<pkg>/template

Handles main templates, categories, and subpackage symlinks safely.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class RepoKind(str, Enum):
    VOID_STANDARD = "srcpkgs"
    PKGS_SUBDIR = "pkgs"
    FLAT_ROOT = "flat"
    CATEGORIZED = "categorized"


class RepoAdapter:
    def __init__(self, root_dir: str | Path = ".") -> None:
        self.root_dir = Path(root_dir).resolve()
        self._pkg_to_path: Dict[str, Path] = {}
        self._pkg_to_category: Dict[str, str] = {}
        self._subpackages_map: Dict[str, str] = {}
        self.kind, self.pkgs_dir = self._detect_layout()
        if self.kind == RepoKind.CATEGORIZED:
            self._index_categorized()

    @staticmethod
    def _is_categorized_dir(d: Path) -> bool:
        """Check if directory contains category subfolders rather than package templates directly."""
        if not d.is_dir():
            return False
        ignored = {".git", ".github", ".forgejo", "common", "masterdir", "hostdir", "etc", "tests", "distfiles", "__pycache__"}
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_dir() and not entry.name.startswith(".") and entry.name not in ignored:
                        # If direct child has template, it's flat/standard, NOT categorized
                        if (Path(entry.path) / "template").is_file():
                            return False
                        # Check if this subfolder contains packages with templates
                        try:
                            with os.scandir(entry.path) as sub_it:
                                for subentry in sub_it:
                                    if subentry.is_dir() and not subentry.name.startswith("."):
                                        if (Path(subentry.path) / "template").is_file():
                                            return True
                        except (OSError, PermissionError):
                            pass
        except (OSError, PermissionError):
            pass
        return False

    def _detect_layout(self) -> Tuple[RepoKind, Path]:
        """Detect repository structure with zero guesswork."""
        # Determine candidate packages directory
        if (self.root_dir / "vup" / "srcpkgs").is_dir():
            pkgs_dir = self.root_dir / "vup" / "srcpkgs"
        elif (self.root_dir / "srcpkgs").is_dir():
            pkgs_dir = self.root_dir / "srcpkgs"
        elif (self.root_dir / "pkgs").is_dir():
            pkgs_dir = self.root_dir / "pkgs"
        else:
            pkgs_dir = self.root_dir

        # 1. Check if the detected packages directory is categorized
        if self._is_categorized_dir(pkgs_dir):
            return RepoKind.CATEGORIZED, pkgs_dir

        # 2. Standard Void Linux repository: srcpkgs/
        if pkgs_dir.name == "srcpkgs":
            return RepoKind.VOID_STANDARD, pkgs_dir

        # 3. Forgejo/Custom layout: pkgs/
        if pkgs_dir.name == "pkgs":
            return RepoKind.PKGS_SUBDIR, pkgs_dir

        # 4. Flat layout (neko-pkgs): templates directly under <pkg>/template
        has_flat_templates = False
        try:
            with os.scandir(self.root_dir) as it:
                for entry in it:
                    if entry.is_dir() and not entry.name.startswith("."):
                        if (Path(entry.path) / "template").is_file():
                            has_flat_templates = True
                            break
        except (OSError, PermissionError):
            pass

        if has_flat_templates:
            return RepoKind.FLAT_ROOT, self.root_dir

        # Fallback to standard if nothing detected yet
        return RepoKind.VOID_STANDARD, self.root_dir / "srcpkgs"

    def _index_categorized(self) -> None:
        """Index all packages and categories in a categorized repository."""
        self._pkg_to_path.clear()
        self._pkg_to_category.clear()
        self._subpackages_map.clear()
        if not self.pkgs_dir.is_dir():
            return

        ignored_names = {
            ".git", ".github", ".forgejo", "common", "masterdir",
            "hostdir", "etc", "tests", "distfiles", "__pycache__"
        }

        try:
            with os.scandir(self.pkgs_dir) as it:
                for cat_entry in it:
                    if cat_entry.name.startswith(".") or cat_entry.name in ignored_names:
                        continue
                    if not cat_entry.is_dir(follow_symlinks=False):
                        continue

                    category = cat_entry.name
                    try:
                        with os.scandir(cat_entry.path) as cat_it:
                            for pkg_entry in cat_it:
                                if pkg_entry.name.startswith("."):
                                    continue
                                pkg_path = Path(pkg_entry.path)
                                if pkg_entry.is_symlink():
                                    try:
                                        link_target = os.readlink(pkg_entry.path)
                                        canonical = os.path.basename(os.path.normpath(link_target))
                                        self._subpackages_map[pkg_entry.name] = canonical
                                    except OSError:
                                        pass
                                    continue

                                if (pkg_path / "template").is_file():
                                    self._pkg_to_path[pkg_entry.name] = pkg_path / "template"
                                    self._pkg_to_category[pkg_entry.name] = category
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

    def get_template_path(self, pkgname: str) -> Optional[Path]:
        """Return the absolute path to a package's template file, or None if not found."""
        if self.kind == RepoKind.CATEGORIZED:
            if pkgname in self._pkg_to_path:
                return self._pkg_to_path[pkgname]
            for cat_dir in self.pkgs_dir.iterdir():
                if cat_dir.is_dir():
                    candidate = cat_dir / pkgname / "template"
                    if candidate.is_file():
                        self._pkg_to_path[pkgname] = candidate
                        self._pkg_to_category[pkgname] = cat_dir.name
                        return candidate
            return None

        target = self.pkgs_dir / pkgname / "template"
        if target.is_file():
            return target
        return None

    def get_package_category(self, pkgname: str) -> Optional[str]:
        """Return the category name if repository is categorized, else None."""
        if self.kind == RepoKind.CATEGORIZED:
            if pkgname in self._pkg_to_category:
                return self._pkg_to_category[pkgname]
            tmpl = self.get_template_path(pkgname)
            if tmpl:
                return tmpl.parent.parent.name
        return None

    def list_categories(self) -> List[str]:
        """Return list of categories if repository is categorized, else empty list."""
        if self.kind == RepoKind.CATEGORIZED:
            return sorted(list(set(self._pkg_to_category.values())))
        return []

    def get_update_file_path(self, pkgname: str) -> Optional[Path]:
        """Return the path to an 'update' policy file if present."""
        tmpl = self.get_template_path(pkgname)
        if tmpl and (tmpl.parent / "update").is_file():
            return tmpl.parent / "update"
        target = self.pkgs_dir / pkgname / "update"
        if target.is_file():
            return target
        return None

    def list_packages(self, include_subpackages: bool = False) -> List[str]:
        """
        List all packages found in the repository.
        If include_subpackages is False, symlinks to other packages are filtered out.
        """
        if self.kind == RepoKind.CATEGORIZED:
            pkgs = list(self._pkg_to_path.keys())
            if include_subpackages:
                pkgs.extend(self._subpackages_map.keys())
            return sorted(pkgs)

        if not self.pkgs_dir.is_dir():
            return []

        packages: List[str] = []
        ignored_names = {
            ".git", ".github", ".forgejo", "common", "masterdir",
            "hostdir", "etc", "tests", "distfiles", "__pycache__"
        }

        try:
            with os.scandir(self.pkgs_dir) as it:
                entries = sorted(it, key=lambda e: e.name)
                for entry in entries:
                    if entry.name.startswith(".") or entry.name in ignored_names:
                        continue

                    pkg_path = Path(entry.path)
                    if not entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        continue

                    # Check if it's a subpackage symlink
                    if entry.is_symlink():
                        if not include_subpackages:
                            continue
                        target = pkg_path.resolve()
                        if (target / "template").is_file() or (target.name == "template" and target.is_file()):
                            packages.append(entry.name)
                        continue

                    # It is a real directory: verify it has a 'template' file
                    if (pkg_path / "template").is_file():
                        packages.append(entry.name)
        except (OSError, PermissionError):
            return []

        return packages

    def get_subpackages(self) -> Dict[str, str]:
        """
        Return a mapping of subpackage_name -> canonical_parent_name
        by inspecting directory symlinks in the packages dir.
        """
        if self.kind == RepoKind.CATEGORIZED:
            return dict(self._subpackages_map)

        if not self.pkgs_dir.is_dir():
            return {}

        subpkgs: Dict[str, str] = {}
        try:
            with os.scandir(self.pkgs_dir) as it:
                for entry in it:
                    if entry.is_symlink():
                        link_target = os.readlink(entry.path)
                        canonical = os.path.basename(os.path.normpath(link_target))
                        subpkgs[entry.name] = canonical
        except OSError:
            pass

        return subpkgs

    def is_subpackage(self, pkgname: str) -> bool:
        """Check if a package is actually a symlink to another package."""
        if self.kind == RepoKind.CATEGORIZED:
            return pkgname in self._subpackages_map
        target = self.pkgs_dir / pkgname
        return target.is_symlink()
