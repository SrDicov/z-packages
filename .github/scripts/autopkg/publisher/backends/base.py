"""
Base publisher backend interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class PublishResult:
    success: bool
    uploaded_files: List[str]
    deleted_files: List[str]
    error_message: str = ""


class BasePublisherBackend(ABC):
    @abstractmethod
    def publish(
        self,
        binpkgs_dir: Path,
        arch: str,
        options: Optional[dict] = None,
    ) -> PublishResult:
        """Publish repository artifacts to storage backend."""
        pass
