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
        # If rule specifies codeberg releases API or release asset
        if rule and rule.url and "codeberg.org" in rule.url:
            m = re.search(r'codeberg\.org/(?:api/v1/repos/)?([^/]+/[^/]+)', rule.url)
            if m:
                repo_slug = m.group(1).rstrip(".git").rstrip("/releases")
                api_url = f"https://codeberg.org/api/v1/repos/{repo_slug}/releases"
                req = urllib.request.Request(api_url)
                req.add_header("User-Agent", "autopkg-engine/1.0")
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        items = json.loads(resp.read().decode("utf-8"))
                        if items and isinstance(items, list):
                            for rel in items:
                                assets = [a.get("name", "") for a in rel.get("assets", [])]
                                tag = rel.get("tag_name", "")
                                if rule.pattern:
                                    # Try asset match first
                                    for a in assets:
                                        m_asset = re.search(rule.pattern, a)
                                        if m_asset:
                                            ver = m_asset.group(1) if m_asset.groups() else m_asset.group(0)
                                            return DetectionResult(
                                                latest_version=ver,
                                                download_url=rel.get("html_url"),
                                                source_type="codeberg_asset",
                                                extra={"tag": tag},
                                            )
                                    # Try tag match
                                    m_tag = re.search(rule.pattern, tag)
                                    if m_tag:
                                        ver = m_tag.group(1) if m_tag.groups() else m_tag.group(0)
                                        return DetectionResult(
                                            latest_version=ver,
                                            download_url=rel.get("html_url"),
                                            source_type="codeberg_tag",
                                            extra={"tag": tag},
                                        )
                                else:
                                    cleaned = re.sub(r'^(?:v|ver|release)[-_]?', '', tag, flags=re.IGNORECASE)
                                    m_ver = re.search(r'(\d+[\d._+-]*\d+|\d+)', cleaned)
                                    ver = m_ver.group(1) if m_ver else cleaned
                                    return DetectionResult(
                                        latest_version=ver,
                                        download_url=rel.get("html_url"),
                                        source_type="codeberg_api",
                                        extra={"tag": tag},
                                    )
                except Exception:
                    pass

        # Fallback to GitTags directly
        git_res = GitTagsBackend().detect_latest(template_data, rule, etag_cache)
        if git_res:
            git_res.source_type = "codeberg_git_tags"
            return git_res

        return None
