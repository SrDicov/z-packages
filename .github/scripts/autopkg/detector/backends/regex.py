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

        candidates = []
        try:
            for m in compiled_re.finditer(text):
                named_dict = m.groupdict() if m.groupdict() else None
                val = None
                if named_dict and "version" in named_dict:
                    val = named_dict["version"]
                elif m.groups():
                    val = m.group(1)
                else:
                    val = m.group(0)
                val = str(val).strip()
                if rule.ignore:
                    try:
                        if re.search(rule.ignore, val):
                            continue
                    except Exception:
                        pass
                candidates.append((val, named_dict))
        except Exception:
            return None

        if not candidates:
            return None

        # Natural sort
        candidates.sort(key=lambda item: [int(t) if t.isdigit() else 0 for t in re.split(r'[^0-9]+', item[0])])
        latest, named_vars = candidates[-1]

        extra = {}
        if named_vars:
            extra["named_vars"] = named_vars

        return DetectionResult(
            latest_version=latest,
            download_url=url,
            source_type="regex_http",
            extra=extra if extra else None,
        )
