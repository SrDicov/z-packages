"""
Codeberg & Forgejo API Backend.

Queries Codeberg / Forgejo releases or tags API with graceful fallback to git_tags.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.backends.git_tags import GitTagsBackend
from detector.rules import PackageRule


class CodebergBackend(BaseDetectorBackend):
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        # Fast path: use GitTags directly on codeberg url
        git_res = GitTagsBackend().detect_latest(template_data, rule, etag_cache)
        if git_res:
            git_res.source_type = "codeberg_git_tags"
            return git_res

        # Fallback to Forgejo REST API if rule specifies owner/repo
        if rule and rule.url and "codeberg.org" in rule.url:
            m = re.search(r'codeberg\.org/([^/]+/[^/]+)', rule.url)
            if m:
                repo_slug = m.group(1).rstrip(".git")
                api_url = f"https://codeberg.org/api/v1/repos/{repo_slug}/releases"
                req = urllib.request.Request(api_url)
                req.add_header("User-Agent", "autopkg-engine/1.0")
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        items = json.loads(resp.read().decode("utf-8"))
                        if items and isinstance(items, list):
                            tag = items[0].get("tag_name", "")
                            cleaned = re.sub(r'^(?:v|ver|release)[-_]?', '', tag, flags=re.IGNORECASE)
                            m_ver = re.search(r'(\d+[\d._+-]*\d+|\d+)', cleaned)
                            ver = m_ver.group(1) if m_ver else cleaned
                            return DetectionResult(
                                latest_version=ver,
                                download_url=items[0].get("html_url"),
                                source_type="codeberg_api",
                            )
                except Exception:
                    pass

        return None
