"""
Git Branch Publisher Backend (for Forgejo / Gitea / GitHub binpkgs branches).

Commits and pushes built .xbps, signatures, and repodata directly to a git branch
(e.g., 'binpkgs' branch in Forgejo repositories).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from publisher.backends.base import BasePublisherBackend, PublishResult

logger = logging.getLogger("autopkg.publisher.git_branch")


class GitBranchPublisher(BasePublisherBackend):
    def publish(
        self,
        binpkgs_dir: Path,
        arch: str,
        options: Optional[dict] = None,
    ) -> PublishResult:
        opts = options or {}
        branch = opts.get("branch", "binpkgs")
        repo_url = opts.get("repo_url") or os.environ.get("FORGEJO_REPO_URL") or os.environ.get("GIT_REPO_URL")
        commit_msg = opts.get("commit_message", f"[autopkg] update {arch} binary packages")
        token = opts.get("token") or os.environ.get("FORGEJO_TOKEN") or os.environ.get("GITHUB_TOKEN")

        target_worktree = binpkgs_dir.resolve()

        # If repo_url provided with token, update remote push url
        if repo_url and token:
            if "://" in repo_url:
                proto, rest = repo_url.split("://", 1)
                push_url = f"{proto}://{token}@{rest}"
                subprocess.run(
                    ["git", "remote", "set-url", "--push", "origin", push_url],
                    cwd=str(target_worktree),
                    check=False,
                )

        # Stage all files
        subprocess.run(["git", "add", "."], cwd=str(target_worktree), check=False)

        # Check if there are changes to commit
        diff_check = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(target_worktree),
            check=False,
        )

        if diff_check.returncode == 0:
            logger.info("No changes to commit on branch %s", branch)
            return PublishResult(success=True, uploaded_files=[], deleted_files=[])

        # Commit and push
        author_name = opts.get("author_name", "Autopkg Builder")
        author_email = opts.get("author_email", "autopkg@local")

        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = author_name
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_NAME"] = author_name
        env["GIT_COMMITTER_EMAIL"] = author_email

        commit_res = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(target_worktree),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if commit_res.returncode != 0:
            return PublishResult(
                success=False,
                uploaded_files=[],
                deleted_files=[],
                error_message=f"Git commit failed: {commit_res.stderr}",
            )

        push_res = subprocess.run(
            ["git", "push", "origin", branch],
            cwd=str(target_worktree),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if push_res.returncode != 0:
            return PublishResult(
                success=False,
                uploaded_files=[],
                deleted_files=[],
                error_message=f"Git push to {branch} failed: {push_res.stderr}",
            )

        return PublishResult(
            success=True,
            uploaded_files=[f"pushed to {branch}"],
            deleted_files=[],
        )
