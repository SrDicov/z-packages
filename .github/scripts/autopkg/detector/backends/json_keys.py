"""
JSON index detector backend.

Fetches a JSON document whose top-level keys are version strings (e.g. the
ziglang download index) and returns the highest numeric key.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import List, Optional

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.rules import PackageRule


class JsonKeysBackend(BaseDetectorBackend):
    DEFAULT_IGNORE = ("master", "nightly", "latest", "canary")

    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        if not rule or not rule.url:
            return None

        req = urllib.request.Request(rule.url)
        req.add_header("User-Agent", "autopkg-engine/1.0")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None

        ignore = set(rule.options.get("ignore_keys") or self.DEFAULT_IGNORE)
        candidates: List[str] = [
            str(k) for k in data.keys()
            if k not in ignore and re.match(r"^\d+(?:\.\d+)*", str(k))
        ]
        if not candidates:
            return None

        latest = max(candidates, key=self._sort_key)
        return DetectionResult(
            latest_version=latest,
            download_url=rule.url,
            source_type="json_keys",
        )

    @staticmethod
    def _sort_key(ver: str) -> List[int]:
        return [int(t) for t in re.split(r"[^0-9]+", ver) if t.isdigit()] or [0]
