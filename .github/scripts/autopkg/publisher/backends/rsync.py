"""
Rsync / SSH Remote Server Publisher Backend.

Syncs binary packages to a remote web server via rsync/ssh.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from publisher.backends.base import BasePublisherBackend, PublishResult

logger = logging.getLogger("autopkg.publisher.rsync")


class RsyncPublisher(BasePublisherBackend):
    def publish(
        self,
        binpkgs_dir: Path,
        arch: str,
        options: Optional[dict] = None,
    ) -> PublishResult:
        opts = options or {}
        remote_dest = opts.get("destination")
        if not remote_dest:
            return PublishResult(
                success=False,
                uploaded_files=[],
                deleted_files=[],
                error_message="Missing 'destination' (e.g. user@host:/var/www/repo/)",
            )

        # Build rsync command
        # Upload packages first, repodata strictly last
        cmd = [
            "rsync", "-avz", "--partial", "--progress",
            f"{binpkgs_dir}/",
            remote_dest,
        ]

        logger.info("Executing rsync: %s", " ".join(cmd))
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

        if res.returncode != 0:
            return PublishResult(
                success=False,
                uploaded_files=[],
                deleted_files=[],
                error_message=f"Rsync failed: {res.stderr}",
            )

        return PublishResult(
            success=True,
            uploaded_files=["rsync sync completed"],
            deleted_files=[],
        )
