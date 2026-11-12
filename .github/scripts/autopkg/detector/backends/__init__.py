"""
Detector backends registry.
"""

from detector.backends.apt import AptBackend
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.backends.codeberg import CodebergBackend
from detector.backends.git_commits import GitCommitsBackend
from detector.backends.git_tags import GitTagsBackend
from detector.backends.github import GitHubBackend
from detector.backends.json_keys import JsonKeysBackend
from detector.backends.regex import RegexBackend

BACKENDS = {
    "git_tags": GitTagsBackend,
    "tags": GitTagsBackend,
    "tags-pattern": GitTagsBackend,
    "tags-prerelease": GitTagsBackend,
    "tags-date-transform": GitTagsBackend,
    "git_commits": GitCommitsBackend,
    "commit": GitCommitsBackend,
    "github": GitHubBackend,
    "github_api": GitHubBackend,
    "github-release-asset-regex": GitHubBackend,
    "github-release-asset-date": GitHubBackend,
    "waveform-debs-asset": GitHubBackend,
    "serein-release": GitHubBackend,
    "kernel-neko-asset": GitHubBackend,
    "regex": RegexBackend,
    "regex2": RegexBackend,
    "html-hrefs": RegexBackend,
    "user-agent-regex": RegexBackend,
    "json-keys": JsonKeysBackend,
    "apt": AptBackend,
    "codeberg": CodebergBackend,
    "codeberg-tag-version": CodebergBackend,
    "codeberg-release-asset": CodebergBackend,
}

__all__ = [
    "BaseDetectorBackend",
    "DetectionResult",
    "GitTagsBackend",
    "GitCommitsBackend",
    "GitHubBackend",
    "RegexBackend",
    "JsonKeysBackend",
    "AptBackend",
    "CodebergBackend",
    "BACKENDS",
]
