"""
Selective Dependency Seeder for Void Linux binary builds.

MANDATORY OPTIMIZATION:
Replaces 'gh release download *.xbps*' (which crashes CI by downloading 30+ GB)
with surgical dependency downloading:
1. Parses makedepends, hostmakedepends, depends from target templates.
2. Queries the remote repository assets list (GitHub, Forgejo, or HTTP index).
3. Downloads ONLY the specific .xbps packages needed as build dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.adapter import RepoAdapter
from core.template import TemplateEditor

logger = logging.getLogger("autopkg.builder.seed")


class SelectiveSeeder:
    def __init__(
        self,
        repo_adapter: RepoAdapter,
        target_dir: str | Path = "hostdir/binpkgs",
        arch: str = "x86_64",
    ) -> None:
        self.adapter = repo_adapter
        self.target_dir = Path(target_dir).resolve()
        self.arch = arch
        self.binpkgs_arch_dir = self.target_dir / arch
        self.binpkgs_arch_dir.mkdir(parents=True, exist_ok=True)

    def resolve_target_dependencies(self, target_packages: List[str]) -> Set[str]:
        """Collect all makedepends, hostmakedepends, and depends for target packages."""
        needed_deps: Set[str] = set()
        subpkg_map = self.adapter.get_subpackages()

        for pkg in target_packages:
            tmpl_path = self.adapter.get_template_path(pkg)
            if not tmpl_path:
                continue

            try:
                tmpl_data = TemplateEditor.parse_file(tmpl_path)
            except Exception:
                continue

            for raw_dep in tmpl_data.hostmakedepends + tmpl_data.makedepends + tmpl_data.depends:
                # Strip versions, e.g. "openssl-devel>=1.1.1" -> "openssl-devel"
                dep_name = re.split(r'[<>=!]', raw_dep)[0].strip()
                if not dep_name:
                    continue
                # Map subpackages if relevant
                canonical = subpkg_map.get(dep_name, dep_name)
                needed_deps.add(canonical)
                needed_deps.add(dep_name)

        return needed_deps

    def seed_from_github_release(
        self,
        github_repo: str,
        target_packages: List[str],
        token: Optional[str] = None,
    ) -> List[Path]:
        """
        Surgically download only needed .xbps dependencies from a GitHub Release.
        """
        needed_deps = self.resolve_target_dependencies(target_packages)
        logger.info("Target packages require dependencies: %s", sorted(needed_deps))

        # Fetch latest release assets from GitHub API
        api_url = f"https://api.github.com/repos/{github_repo}/releases/latest"
        req = urllib.request.Request(api_url)
        req.add_header("User-Agent", "autopkg-engine/1.0")
        req.add_header("Accept", "application/vnd.github.v3+json")

        auth_token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if auth_token:
            req.add_header("Authorization", f"Bearer {auth_token}")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                release_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Could not fetch release assets from %s: %s", github_repo, e)
            return []

        assets = release_data.get("assets", [])
        downloaded: List[Path] = []

        # Always download repodata files first if present
        for asset in assets:
            name = asset.get("name", "")
            if "-repodata" in name or name.endswith(".plist"):
                dest = self.binpkgs_arch_dir / name
                self._download_file(asset.get("browser_download_url"), dest)
                downloaded.append(dest)

        # Download only assets matching needed dependencies and current architecture
        for asset in assets:
            name = asset.get("name", "")
            if not name.endswith(".xbps"):
                continue

            # Format: <pkgname>-<version>_<revision>.<arch>.xbps
            # Extract package name before the last '-' that separates version
            m = re.match(r'^(.+?)-[0-9].*\.xbps$', name)
            if not m:
                continue

            asset_pkgname = m.group(1)
            # Check if this package is in needed_deps or is one of the target packages
            if asset_pkgname in needed_deps:
                dest = self.binpkgs_arch_dir / name
                if not dest.is_file():
                    logger.info("Downloading needed dependency: %s", name)
                    self._download_file(asset.get("browser_download_url"), dest)
                    downloaded.append(dest)

        return downloaded

    def _download_file(self, url: str, dest_path: Path) -> bool:
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "autopkg-engine/1.0")
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            return True
        except Exception as e:
            logger.warning("Failed to download %s: %s", url, e)
            return False
