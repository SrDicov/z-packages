"""
APT Repository Packages Backend.

Parses Debian/Ubuntu Packages or Packages.gz files to extract latest package version.
"""

from __future__ import annotations

import gzip
import re
import urllib.request
from typing import Optional

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.rules import PackageRule


class AptBackend(BaseDetectorBackend):
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        if not rule or not rule.url:
            return None

        url = rule.url
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "autopkg-engine/1.0 (APT compatible)")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_bytes = resp.read()
                if url.endswith(".gz") or raw_bytes[:2] == b"\x1f\x8b":
                    text = gzip.decompress(raw_bytes).decode("utf-8", errors="replace")
                else:
                    text = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            return None

        # Search for Version: <ver> stanzas
        versions = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Version:"):
                ver = line[len("Version:"):].strip()
                # Clean epoch if present (e.g. 1:2.0.0-1 -> 2.0.0)
                if ":" in ver:
                    ver = ver.split(":", 1)[1]
                # Strip Debian revision (e.g. 2.0.0-1 -> 2.0.0)
                if "-" in ver:
                    ver = ver.split("-", 1)[0]
                versions.append(ver)

        if not versions:
            return None

        versions.sort(key=lambda s: [int(t) if t.isdigit() else 0 for t in re.split(r'[^0-9]+', s)])
        latest = versions[-1]

        return DetectionResult(
            latest_version=latest,
            download_url=url,
            source_type="apt_repository",
        )
