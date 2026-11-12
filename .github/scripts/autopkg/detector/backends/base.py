"""
Base interface for upstream update detection backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from core.template import TemplateData
from detector.rules import PackageRule


@dataclass
class DetectionResult:
    latest_version: str
    download_url: Optional[str] = None
    source_type: str = "unknown"
    extra: Optional[dict] = None


class BaseDetectorBackend(ABC):
    @abstractmethod
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        """Detect the latest version available upstream."""
        pass
