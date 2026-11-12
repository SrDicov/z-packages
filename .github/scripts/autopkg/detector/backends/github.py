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

        # Determine expected asset file extension or rule pattern
        expected_ext = None
        if template_data.distfiles:
            for df in template_data.distfiles:
                if "/releases/download/" in df:
                    filename = df.split("/")[-1].split(">")[0]
                    m_ext = re.search(r'(\.(?:deb|tar\.[a-z0-9]+|tgz|zip|rpm|AppImage|txz|bin))$', filename)
                    if m_ext:
                        expected_ext = m_ext.group(1)
                        break

        def _is_release_matching(rel: dict) -> bool:
            if not isinstance(rel, dict):
                return False
            if rel.get("prerelease") and not (rule and rule.options.get("allow_prerelease")):
                return False
            tag = rel.get("tag_name", "")
            if tag.startswith(("android/", "ios/", "mobile/")):
                return False
            if rule and rule.ignore and re.search(rule.ignore, tag):
                return False
            assets = [a.get("name", "") for a in rel.get("assets", [])]
            if expected_ext and assets:
                if not any(a.endswith(expected_ext) for a in assets):
                    return False
            if rule and rule.pattern:
                pat = rule.pattern
                if r"\K" in pat:
                    parts = pat.split(r"\K", 1)
                    pat = f"(?:{parts[0]})({parts[1]})"
                tag_match = bool(re.search(pat, tag))
                asset_match = any(bool(re.search(pat, a)) for a in assets)
                if not (tag_match or asset_match):
                    return False
            return True

        def _parse_release(rel: dict) -> DetectionResult:
            tag_name = rel.get("tag_name", "")
            cleaned = re.sub(r'^(?:v|ver|release)[-_]?', '', tag_name, flags=re.IGNORECASE)
            m = re.search(r'\b(\d+(?:[._-][a-zA-Z0-9]+)*)\b', cleaned)
            latest_version = m.group(1).replace("-", ".").replace("_", ".") if m else cleaned
            return DetectionResult(
                latest_version=latest_version,
                download_url=rel.get("html_url"),
                source_type="github_api",
                extra={"tag": tag_name},
            )

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        headers = {
            "User-Agent": "autopkg-engine/1.0 (Void Linux package updater)",
            "Accept": "application/vnd.github.v3+json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # 1. Try /releases/latest first with ETag caching
        api_url = f"https://api.github.com/repos/{repo_owner_name}/releases/latest"
        cached_ver = None
        if etag_cache is not None and api_url in etag_cache:
            entry = etag_cache[api_url]
            cached_etag = entry.get("etag")
            cached_ver = entry.get("version")
            if cached_etag:
                headers["If-None-Match"] = cached_etag

        req = urllib.request.Request(api_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                new_etag = resp.headers.get("ETag")
                raw_body = resp.read().decode("utf-8")
                data = json.loads(raw_body)
                if _is_release_matching(data):
                    res = _parse_release(data)
                    if etag_cache is not None and new_etag:
                        etag_cache[api_url] = {
                            "etag": new_etag,
                            "version": res.latest_version,
                            "tag": res.extra.get("tag") if res.extra else "",
                        }
                    return res
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached_ver:
                return DetectionResult(latest_version=cached_ver, source_type="github_api_cached")
        except Exception:
            pass

        # 2. Fallback to /releases?per_page=15 when latest is non-matching (e.g. mobile release)
        list_url = f"https://api.github.com/repos/{repo_owner_name}/releases?per_page=15"
        req_list = urllib.request.Request(list_url, headers=headers)
        try:
            with urllib.request.urlopen(req_list, timeout=15) as resp:
                releases = json.loads(resp.read().decode("utf-8"))
                for rel in releases:
                    if _is_release_matching(rel):
                        return _parse_release(rel)
        except Exception:
            pass

        return None
