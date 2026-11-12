"""
GitHub API Backend with ETag / If-None-Match conditional request caching.

Drastically reduces rate limit consumption by returning 304 Not Modified
when releases or tags have not changed upstream.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from core.template import TemplateData
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.rules import PackageRule


class GitHubBackend(BaseDetectorBackend):
    def detect_latest(
        self,
        template_data: TemplateData,
        rule: Optional[PackageRule] = None,
        etag_cache: Optional[dict] = None,
    ) -> Optional[DetectionResult]:
        repo_owner_name = None
        if rule and rule.url:
            m = re.search(r'github\.com/([^/]+/[^/]+)', rule.url)
            if m:
                repo_owner_name = m.group(1).rstrip(".git")
        elif template_data.homepage and "github.com" in template_data.homepage:
            m = re.search(r'github\.com/([^/]+/[^/]+)', template_data.homepage)
            if m:
                repo_owner_name = m.group(1).rstrip(".git")
        elif template_data.distfiles:
            for df in template_data.distfiles:
                m = re.search(r'github\.com/([^/]+/[^/]+)', df)
                if m:
                    repo_owner_name = m.group(1).rstrip(".git")
                    break

        if not repo_owner_name:
            return None

        api_url = f"https://api.github.com/repos/{repo_owner_name}/releases/latest"
        req = urllib.request.Request(api_url)
        req.add_header("User-Agent", "autopkg-engine/1.0 (Void Linux package updater)")
        req.add_header("Accept", "application/vnd.github.v3+json")

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        cached_etag = None
        cached_ver = None
        if etag_cache is not None and api_url in etag_cache:
            entry = etag_cache[api_url]
            cached_etag = entry.get("etag")
            cached_ver = entry.get("version")
            if cached_etag:
                req.add_header("If-None-Match", cached_etag)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                new_etag = resp.headers.get("ETag")
                raw_body = resp.read().decode("utf-8")
                data = json.loads(raw_body)

                tag_name = data.get("tag_name", "")
                cleaned = re.sub(r'^(?:v|ver|release)[-_]?', '', tag_name, flags=re.IGNORECASE)
                m = re.search(r'(\d+[\d._+-]*\d+|\d+)', cleaned)
                latest_version = m.group(1) if m else cleaned

                if etag_cache is not None and new_etag:
                    etag_cache[api_url] = {
                        "etag": new_etag,
                        "version": latest_version,
                        "tag": tag_name,
                    }

                return DetectionResult(
                    latest_version=latest_version,
                    download_url=data.get("html_url"),
                    source_type="github_api",
                    extra={"tag": tag_name},
                )
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached_ver:
                # 304 Not Modified: Cache hit, 0 rate limit cost!
                return DetectionResult(
                    latest_version=cached_ver,
                    source_type="github_api_cached",
                )
            return None
        except Exception:
            return None
