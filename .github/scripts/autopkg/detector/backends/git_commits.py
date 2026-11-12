"""
Git commit (HEAD) detector backend for templates pinned via _commit/_sha.

Uses `git ls-remote` (zero API cost) to resolve the tip of a fork branch and
compares it against the pin already stored in the template. Only packages that
opt in through updates.rules.yml are affected; deliberately frozen forks keep
their exclude entry.
"""

from __future__ import annotations

import re
import subprocess
from typing import List, Optional

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.rules import PackageRule

PIN_VARS = ("_commit", "_githash", "_sha", "_v_commit")
DEFAULT_BRANCHES = ("main", "master")


class GitCommitsBackend(BaseDetectorBackend):
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        url = rule.url if rule and rule.url else None
        if not url:
            return None

        branch_opt = rule.options.get("branch") if rule else None
        branches: List[str] = [branch_opt] if branch_opt else list(DEFAULT_BRANCHES)

        sha = None
        for branch in branches:
            sha = self._head(url, branch)
            if sha:
                break
        if not sha:
            return None

        commit_var = (rule.options.get("commit_var") if rule else None) or self._pin_var(
            template_data.raw_content
        )
        if not commit_var:
            return None

        return DetectionResult(
            latest_version=sha,
            download_url=url,
            source_type="git_commits",
            extra={"commit": sha, "commit_var": commit_var},
        )

    @staticmethod
    def _head(url: str, branch: str) -> Optional[str]:
        try:
            res = subprocess.run(
                ["git", "ls-remote", url, f"refs/heads/{branch}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=20,
                check=False,
            )
        except Exception:
            return None
        if res.returncode != 0 or not res.stdout.strip():
            return None
        return res.stdout.split()[0]

    @staticmethod
    def _pin_var(raw: str) -> Optional[str]:
        for var in PIN_VARS:
            if re.search(rf'(?m)^\s*{re.escape(var)}=', raw):
                return var
        return None
