"""
Local Directory and Filesystem Publisher Backend.

Copies or hardlinks binary packages to a local serving directory.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List, Optional

from publisher.backends.base import BasePublisherBackend, PublishResult

logger = logging.getLogger("autopkg.publisher.local")


class LocalPublisher(BasePublisherBackend):
    def publish(
        self,
        binpkgs_dir: Path,
        arch: str,
        options: Optional[dict] = None,
    ) -> PublishResult:
        opts = options or {}
        destination = opts.get("destination")
        if not destination:
            return PublishResult(
                success=False,
                uploaded_files=[],
                deleted_files=[],
                error_message="Missing 'destination' in options for local publisher",
            )

        dest_path = Path(destination).resolve()
        dest_path.mkdir(parents=True, exist_ok=True)

        copied: List[str] = []
        for item in binpkgs_dir.iterdir():
            if item.is_file():
                target = dest_path / item.name
                shutil.copy2(item, target)
                copied.append(item.name)

        logger.info("Copied %d files to %s", len(copied), dest_path)
        return PublishResult(
            success=True,
            uploaded_files=copied,
            deleted_files=[],
        )
