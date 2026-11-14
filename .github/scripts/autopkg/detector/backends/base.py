"""
Base interface for upstream update detection backends.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from core.template import TemplateData
from detector.rules import PackageRule

_CACHED_GH_TOKEN: Optional[str] = None


def get_github_token() -> Optional[str]:
    """
    Retrieve GitHub token from environment (GITHUB_TOKEN or GH_TOKEN)
    or dynamically query 'gh auth token' with a cached 3s timeout check.
    """
    global _CACHED_GH_TOKEN
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and token.strip():
        return token.strip()
    if _CACHED_GH_TOKEN is not None:
        return _CACHED_GH_TOKEN if _CACHED_GH_TOKEN else None

    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            _CACHED_GH_TOKEN = res.stdout.strip()
            return _CACHED_GH_TOKEN
    except Exception:
        pass

    _CACHED_GH_TOKEN = ""
    return None


def verify_distfile_url(url: str, timeout: int = 10) -> bool:
    """
    Verify that a distfile URL is reachable and returns HTTP 200 or 206 (Range).
    Detects and rejects HTTP 404 or text/html responses for binary archives.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False

    clean_url = url.split(">")[0].strip()
    headers = {
        "User-Agent": "autopkg-engine/1.0 (pre-flight verifier)",
        "Range": "bytes=0-63",
    }
    token = get_github_token()
    if token and "api.github.com" in clean_url:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(clean_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", getattr(resp, "code", 200))
            if code not in (200, 206):
                return False
            content_type = resp.headers.get("Content-Type", "").lower()
            # If the URL looks like a binary archive/package, it must not be text/html
            is_archive = bool(re.search(r'\.(?:deb|rpm|tar\.[a-z0-9]+|tgz|txz|zip|7z|AppImage|gz|xz|bin)$', clean_url.split("?")[0]))
            if is_archive and "text/html" in content_type:
                return False
            return True
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


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
