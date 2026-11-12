"""
Git Tags Detector Backend.

Uses POSIX git ls-remote --tags to query upstream repositories with:
1. Zero API token consumption.
2. Zero rate limit limits.
3. Universal support for GitHub, GitLab, Codeberg, Forgejo, SourceHut, cgit, kernel.org.
"""

from __future__ import annotations

import re
import subprocess
from typing import List, Optional, Tuple

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.rules import PackageRule


class GitTagsBackend(BaseDetectorBackend):
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        # Determine git repository URL
        git_url = None
        if rule and rule.url:
            git_url = rule.url
        elif template_data.homepage and ("github.com" in template_data.homepage or "gitlab.com" in template_data.homepage or "codeberg.org" in template_data.homepage):
            git_url = template_data.homepage.rstrip("/")
            if not git_url.endswith(".git"):
                git_url += ".git"
        elif template_data.distfiles:
            # Infer from first distfile
            for df in template_data.distfiles:
                m = re.search(r'(https?://(?:github\.com|gitlab\.com|codeberg\.org)/[^/]+/[^/]+)', df)
                if m:
                    git_url = m.group(1) + ".git"
                    break

        if not git_url:
            return None

        # Execute git ls-remote --tags
        try:
            res = subprocess.run(
                ["git", "ls-remote", "--tags", "--refs", git_url],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None
        except Exception:
            return None

        tags: List[str] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                ref = parts[1]
                if ref.startswith("refs/tags/"):
                    tag = ref[len("refs/tags/"):]
                    if not tag.endswith("^{}"):  # filter dereferenced tag objects
                        tags.append(tag)

        if not tags:
            return None

        # Parse versions from tags
        parsed_versions = []
        for tag in tags:
            # Check pattern filter if rule specifies one
            if rule and rule.pattern:
                if not re.search(rule.pattern, tag):
                    continue

            # Check ignore filter
            if rule and rule.ignore:
                if re.search(rule.ignore, tag):
                    continue

            # Skip pre-releases (alpha, beta, rc) unless explicitly requested
            if not (rule and rule.options.get("allow_prerelease")):
                if re.search(r'(?:[-._]?(?:alpha|beta|rc|pre|dev|preview|nightly)[-._0-9]*)', tag, re.IGNORECASE):
                    continue

            cleaned_version = self._clean_tag_to_version(tag, rule.strip_prefix if rule else None)
            if cleaned_version:
                parsed_versions.append((cleaned_version, tag))

        if not parsed_versions:
            return None

        # Sort versions using natural / tuple sort
        parsed_versions.sort(key=lambda item: self._version_sort_key(item[0]))
        latest_version, original_tag = parsed_versions[-1]

        return DetectionResult(
            latest_version=latest_version,
            download_url=git_url,
            source_type="git_tags",
            extra={"tag": original_tag},
        )

    @classmethod
    def _clean_tag_to_version(cls, tag: str, custom_prefix: Optional[str] = None) -> Optional[str]:
        t = tag.strip()
        if custom_prefix and t.startswith(custom_prefix):
            t = t[len(custom_prefix):]

        # Ignore tags that are commit SHAs or contain commit SHAs (e.g. hl-61a71c65ace5124cb...)
        if re.search(r'[-_]?[0-9a-f]{7,40}$', t, re.IGNORECASE) and re.search(r'[a-f]', t, re.IGNORECASE):
            return None

        # Strip common prefixes like 'v' or 'v.' or '<pkgname>-'
        t = re.sub(r'^(?:v|ver|release|rel|v\.)[-_]?', '', t, flags=re.IGNORECASE)

        # Match version pattern: digits separated by dots/underscores/hyphens
        m = re.search(r'\b(\d+(?:[._-]\d+)+(?:[a-zA-Z]|[-._][a-zA-Z0-9]+)?|\d+)\b', t)
        if m:
            candidate = m.group(1).replace("-", ".").replace("_", ".")
            # Reject isolated small integers extracted from non-version tags
            if candidate.isdigit() and len(t) > len(candidate) + 4:
                return None
            return candidate
        return None

    @classmethod
    def _version_sort_key(cls, ver_str: str) -> List[int]:
        """Convert version string into a list of integers for clean natural comparison."""
        parts = []
        for token in re.split(r'[^0-9]+', ver_str):
            if token.isdigit():
                parts.append(int(token))
        return parts or [0]
