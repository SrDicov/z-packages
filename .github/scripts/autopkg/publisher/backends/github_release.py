"""
GitHub Releases Publisher Backend.

Implements all battle-tested invariants from z-repo:
1. Re-upload prevention via SHA256 comparison.
2. Safe stale asset pruning without deleting unbuilt packages.
3. STRICT upload ordering: packages and keys first, repodata LAST.
4. Post-upload integrity check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from publisher.backends.base import BasePublisherBackend, PublishResult
from publisher.validator import RepoValidator

logger = logging.getLogger("autopkg.publisher.github")


class GitHubReleasePublisher(BasePublisherBackend):
    def publish(
        self,
        binpkgs_dir: Path,
        arch: str,
        options: Optional[dict] = None,
    ) -> PublishResult:
        opts = options or {}
        repo = opts.get("repo") or os.environ.get("GH_REPO")
        category = opts.get("category")
        default_tag = f"{category}-{arch}-current" if category else f"rolling-{arch}"
        tag = opts.get("tag") or os.environ.get("REPO_TAG", default_tag)

        if not repo:
            return PublishResult(
                success=False,
                uploaded_files=[],
                deleted_files=[],
                error_message="Missing GitHub repository (specify in options or set GH_REPO)",
            )

        # 1. Ensure release exists
        self._ensure_release_exists(repo, tag, arch)

        # 2. Get current remote assets and their sha256 digests
        remote_assets = self._fetch_remote_assets(repo, tag)
        logger.info("Found %d existing remote assets on release %s", len(remote_assets), tag)

        # 3. Categorize local files to upload
        local_files: List[Path] = []
        repodata_file: Optional[Path] = None

        for item in binpkgs_dir.iterdir():
            if not item.is_file():
                continue
            if item.name.startswith(f"{arch}-repodata"):
                repodata_file = item
            elif item.name.endswith(".xbps") or item.name.endswith(".sig2") or item.name.endswith(".plist") or item.name.endswith(".pub"):
                local_files.append(item)

        # 4. Prune stale assets on GitHub Release
        deleted = self._prune_stale_assets(repo, tag, remote_assets, local_files, arch)

        # 5. Upload files: Packages and keys first
        uploaded: List[str] = []
        for file_path in sorted(local_files, key=lambda f: f.name):
            if self._upload_if_changed(repo, tag, file_path, remote_assets):
                uploaded.append(file_path.name)

        # 6. Upload repodata STRICTLY LAST
        if repodata_file and repodata_file.is_file():
            if self._upload_if_changed(repo, tag, repodata_file, remote_assets):
                uploaded.append(repodata_file.name)
        else:
            logger.warning("No repodata file found to upload for %s", arch)

        # 7. Post-upload verification
        updated_remote_assets = self._fetch_remote_assets(repo, tag)
        validator = RepoValidator(binpkgs_dir, arch=arch)
        report = validator.validate_remote_assets(updated_remote_assets)

        if not report.is_valid:
            return PublishResult(
                success=False,
                uploaded_files=uploaded,
                deleted_files=deleted,
                error_message=f"Post-upload verification failed: {report.errors}",
            )

        logger.info("Publication to %s (%s) verified 100%% successfully", repo, tag)
        return PublishResult(
            success=True,
            uploaded_files=uploaded,
            deleted_files=deleted,
        )

    def publish_sharded(
        self,
        binpkgs_dir: Path,
        arch: str,
        repo_adapter,
        options: Optional[dict] = None,
    ) -> Dict[str, PublishResult]:
        """
        Partition packages by category and publish each category to its own sharded tag:
        <category>-<arch>-current.
        """
        opts = dict(options or {})
        from core.adapter import RepoKind
        if repo_adapter.kind != RepoKind.CATEGORIZED:
            res = self.publish(binpkgs_dir, arch, opts)
            return {"default": res}

        pkg_pattern = re.compile(r"^(?P<name>.*)-(?P<ver>[0-9][^-]*)_[0-9]+\.[^.]+\.xbps$")
        category_files: Dict[str, List[Path]] = {}

        for item in binpkgs_dir.iterdir():
            if not item.is_file():
                continue
            if item.name.endswith(".xbps"):
                m = pkg_pattern.match(item.name)
                pkg_name = m.group("name") if m else item.name.split("-")[0]
                cat = repo_adapter.get_package_category(pkg_name) or "uncategorized"
                category_files.setdefault(cat, []).append(item)
                sig2 = item.with_suffix(".xbps.sig2")
                if sig2.is_file():
                    category_files.setdefault(cat, []).append(sig2)

        key_files = [
            f for f in binpkgs_dir.iterdir()
            if f.is_file() and (f.suffix in (".plist", ".pub") or f.name.endswith(".pem"))
        ]

        results: Dict[str, PublishResult] = {}
        for cat, files in sorted(category_files.items()):
            cat_opts = dict(opts)
            cat_opts["category"] = cat

            with tempfile.TemporaryDirectory(prefix=f"autopkg-shard-{cat}-") as tmp_shard:
                shard_dir = Path(tmp_shard)
                for f in files:
                    shutil.copy2(f, shard_dir / f.name)
                for k in key_files:
                    shutil.copy2(k, shard_dir / k.name)

                # Generate repodata for this category shard
                privkey = opts.get("privkey") or os.environ.get("XBPS_PRIVATE_KEY")
                if privkey:
                    from publisher.indexer import RepoIndexer
                    indexer = RepoIndexer(shard_dir, arch=arch, private_key=privkey)
                    indexer.generate_repodata()
                else:
                    xbps_files = [str(p) for p in shard_dir.glob("*.xbps")]
                    if xbps_files:
                        subprocess.run(["xbps-rindex", "-a"] + xbps_files, check=False)

                res = self.publish(shard_dir, arch, cat_opts)
                results[cat] = res

        return results

    def _ensure_release_exists(self, repo: str, tag: str, arch: str) -> None:
        check = subprocess.run(
            ["gh", "release", "view", tag, "-R", repo],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if check.returncode != 0:
            logger.info("Creating release tag %s on %s", tag, repo)
            subprocess.run(
                [
                    "gh", "release", "create", tag,
                    "-R", repo,
                    "--title", f"XBPS Repository ({arch})",
                    "--notes", f"Rolling binary packages repository for {arch}.",
                ],
                check=True,
            )

    def _fetch_remote_assets(self, repo: str, tag: str) -> Dict[str, str]:
        """Return dict of filename -> sha256 from GitHub API."""
        cmd = [
            "gh", "api", f"repos/{repo}/releases/tags/{tag}",
            "--paginate",
            "--jq", ".assets[] | .name + \" \" + (.digest // \"\")",
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        assets_map: Dict[str, str] = {}
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    name = parts[0]
                    digest = parts[1]
                    if digest.startswith("sha256:"):
                        digest = digest[7:]
                    assets_map[name] = digest
                elif len(parts) == 1:
                    assets_map[parts[0]] = ""
        return assets_map

    def _upload_if_changed(
        self,
        repo: str,
        tag: str,
        file_path: Path,
        remote_assets: Dict[str, str],
    ) -> bool:
        filename = file_path.name
        local_sha = self._compute_sha256(file_path)

        if filename in remote_assets and remote_assets[filename].lower() == local_sha.lower():
            logger.info("Asset %s is already up-to-date (SHA matched)", filename)
            return False

        logger.info("Uploading %s to %s/%s", filename, repo, tag)
        for attempt in range(1, 4):
            cmd = ["gh", "release", "upload", tag, str(file_path), "-R", repo, "--clobber"]
            res = subprocess.run(cmd, check=False)
            if res.returncode == 0:
                return True
            time.sleep(2 * attempt)

        raise RuntimeError(f"Failed to upload {filename} after 3 attempts")

    def _prune_stale_assets(
        self,
        repo: str,
        tag: str,
        remote_assets: Dict[str, str],
        local_files: List[Path],
        arch: str,
    ) -> List[str]:
        """
        Delete ONLY assets that are proven superseded by this run's built packages.
        Preserves all assets for packages not in this build run.
        """
        local_pkgnames: Set[str] = set()
        for f in local_files:
            if f.name.endswith(".xbps"):
                m = re.match(r"^(.+)-[0-9].*\.xbps$", f.name)
                if m:
                    local_pkgnames.add(m.group(1))

        deleted: List[str] = []
        expected_names = {f.name for f in local_files}

        for asset_name in remote_assets:
            if asset_name in expected_names:
                continue

            should_delete = False
            # Repodata, pubkey and plist are overwritten safely via --clobber, never pre-deleted
            if asset_name.endswith(".plist") or asset_name.endswith(".pub") or "-repodata" in asset_name:
                should_delete = False
            elif asset_name.endswith(".xbps") or asset_name.endswith(".xbps.sig2"):
                # Only delete if this package was rebuilt in the current run!
                m = re.match(r"^(.+)-[0-9].*\.xbps(?:\.sig2)?$", asset_name)
                if m and m.group(1) in local_pkgnames:
                    should_delete = True

            if should_delete:
                logger.info("Deleting superseded stale asset: %s", asset_name)
                res = subprocess.run(
                    ["gh", "release", "delete-asset", tag, asset_name, "-R", repo, "-y"],
                    check=False,
                )
                if res.returncode == 0:
                    deleted.append(asset_name)

        return deleted

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
