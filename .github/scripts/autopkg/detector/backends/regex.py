r"""
Regex & HTTP Scraping Backend.

Fetches web pages or raw indices and extracts versions using regular expressions.
Translates PCRE conventions (like \K) into Python capturing groups.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Optional

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.rules import PackageRule


class RegexBackend(BaseDetectorBackend):
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        if not rule or not rule.url or not rule.pattern:
            return None

        url = rule.url
        pattern = rule.pattern

        req = urllib.request.Request(url)
        req.add_header("User-Agent", "autopkg-engine/1.0 (Mozilla/5.0 compatible)")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None

        # Convert PCRE \K syntax to Python capturing group: prefix\Kpattern -> (?:prefix)(pattern)
        compiled_re = None
        if r"\K" in pattern:
            parts = pattern.split(r"\K", 1)
            try:
                compiled_re = re.compile(f"(?:{parts[0]})({parts[1]})")
            except Exception:
                pass

        if compiled_re is None:
            try:
                compiled_re = re.compile(pattern)
            except Exception:
                return None

        try:
            matches = compiled_re.findall(text)
        except Exception:
            return None

        if not matches:
            return None

        candidates = []
        for match in matches:
            val = match if isinstance(match, str) else match[0]
            val = val.strip()
            if rule.ignore:
                try:
                    if re.search(rule.ignore, val):
                        continue
                except Exception:
                    pass
            candidates.append(val)

        if not candidates:
            return None

        # Natural sort
        candidates.sort(key=lambda s: [int(t) if t.isdigit() else 0 for t in re.split(r'[^0-9]+', s)])
        latest = candidates[-1]

        return DetectionResult(
            latest_version=latest,
            download_url=url,
            source_type="regex_http",
        )
