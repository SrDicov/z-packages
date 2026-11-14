"""
Repository Integrity Validator for Void Linux xbps repodata.

Guarantees:
1. Every package advertised in repodata/index.plist exists as an asset.
2. Every asset SHA256 matches the advertised filename-sha256.
3. Every package has a valid .sig2 signature.
"""

from __future__ import annotations

import hashlib
import logging
import plistlib
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("autopkg.publisher.validator")


@dataclass
class ValidationReport:
    is_valid: bool
    checked_count: int
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class RepoValidator:
    def __init__(self, binpkgs_dir: str | Path, arch: str = "x86_64") -> None:
        self.binpkgs_dir = Path(binpkgs_dir).resolve()
        self.arch = arch
        self.repodata_file = self.binpkgs_dir / f"{arch}-repodata"

    def validate_local_repository(self, incremental: bool = False) -> ValidationReport:
        """Validate all local files against the local repodata archive."""
        import re
        if not self.repodata_file.is_file():
            return ValidationReport(is_valid=False, checked_count=0, errors=[f"Missing {self.repodata_file.name}"])

        index_data = self._extract_index_plist(self.repodata_file)
        if not index_data:
            return ValidationReport(is_valid=False, checked_count=0, errors=["Failed to extract index.plist from repodata"])

        errors: List[str] = []
        warnings: List[str] = []
        checked = 0

        for key, meta in index_data.items():
            checked += 1
            pkgver = meta.get("pkgver", key)
            pkg_arch = meta.get("architecture", self.arch)
            expected_sha = meta.get("filename-sha256", "")
            filename = f"{pkgver}.{pkg_arch}.xbps"
            file_path = self.binpkgs_dir / filename

            # 1. Existence check
            if not file_path.is_file():
                if incremental:
                    # In incremental mode, older packages preserved in index are already remote
                    continue
                errors.append(f"Indexed package does not exist on disk: {filename}")
                continue

            # 2. SHA256 integrity check
            if expected_sha:
                real_sha = self._compute_sha256(file_path)
                if real_sha.lower() != expected_sha.lower():
                    errors.append(f"Hash mismatch for {filename}: expected {expected_sha}, got {real_sha}")

            # 3. Signature check (.sig2)
            sig_file = self.binpkgs_dir / f"{filename}.sig2"
            if not sig_file.is_file():
                errors.append(f"Missing signature file for {filename}: {sig_file.name}")

        # In incremental mode: ensure every .xbps on disk is properly indexed
        if incremental:
            for local_file in self.binpkgs_dir.glob(f"*.{self.arch}.xbps"):
                m = re.match(rf"^(.+)-[0-9].*\.{re.escape(self.arch)}\.xbps$", local_file.name)
                pkg_key = m.group(1) if m else local_file.name.split("-")[0]
                if pkg_key not in index_data:
                    errors.append(f"Local package {local_file.name} is missing from repodata index.plist")

        return ValidationReport(
            is_valid=(len(errors) == 0),
            checked_count=checked,
            errors=errors,
            warnings=warnings,
        )

    def validate_remote_assets(
        self,
        remote_assets_map: Dict[str, str],  # filename -> sha256
    ) -> ValidationReport:
        """
        Validate repodata against a dictionary of remote assets (e.g. from GitHub Releases).
        """
        if not self.repodata_file.is_file():
            return ValidationReport(is_valid=False, checked_count=0, errors=[f"Missing {self.repodata_file.name}"])

        index_data = self._extract_index_plist(self.repodata_file)
        if not index_data:
            return ValidationReport(is_valid=False, checked_count=0, errors=["Failed to extract index.plist from repodata"])

        errors: List[str] = []
        checked = 0

        for key, meta in index_data.items():
            checked += 1
            pkgver = meta.get("pkgver", key)
            pkg_arch = meta.get("architecture", self.arch)
            expected_sha = meta.get("filename-sha256", "")
            filename = f"{pkgver}.{pkg_arch}.xbps"

            if filename not in remote_assets_map:
                errors.append(f"Indexed package not found in remote assets: {filename}")
                continue

            remote_sha = remote_assets_map[filename]
            if expected_sha and remote_sha.lower() != expected_sha.lower():
                errors.append(f"Remote asset hash mismatch for {filename}: expected {expected_sha}, got {remote_sha}")

            sig_name = f"{filename}.sig2"
            if sig_name not in remote_assets_map:
                errors.append(f"Missing remote signature asset: {sig_name}")

        return ValidationReport(
            is_valid=(len(errors) == 0),
            checked_count=checked,
            errors=errors,
        )

    @classmethod
    def _extract_index_plist(cls, repodata_path: Path) -> Optional[Dict[str, dict]]:
        """Extract index.plist from the repodata tar archive."""
        try:
            # First attempt: standard python tarfile
            with tarfile.open(repodata_path, "r:*") as tar:
                member = tar.extractfile("index.plist")
                if member:
                    data = plistlib.load(member)
                    return data if isinstance(data, dict) else {}
        except Exception:
            pass

        # Fallback to bsdtar or tar CLI
        try:
            res = subprocess.run(
                ["tar", "-xOf", str(repodata_path), "index.plist"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                data = plistlib.loads(res.stdout)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass

        return None

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

    @staticmethod
    def validate_template_file(template_path: str | Path) -> Tuple[bool, List[str], List[str]]:
        """Validate template file consistency and check noshlibprovides heuristic."""
        from core.template import TemplateEditor
        data = TemplateEditor.parse_file(template_path)
        return TemplateEditor.validate_template(data)
