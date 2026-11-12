"""
Detector backends registry.
"""

from detector.backends.apt import AptBackend
from detector.backends.base import BaseDetectorBackend, DetectionResult
from detector.backends.codeberg import CodebergBackend
from detector.backends.git_tags import GitTagsBackend
from detector.backends.github import GitHubBackend
from detector.backends.regex import RegexBackend

BACKENDS = {
    "git_tags": GitTagsBackend,
    "tags": GitTagsBackend,
    "github": GitHubBackend,
    "github_api": GitHubBackend,
    "regex": RegexBackend,
    "regex2": RegexBackend,
    "apt": AptBackend,
    "codeberg": CodebergBackend,
    "codeberg-tag-version": CodebergBackend,
    "codeberg-release-asset": CodebergBackend,
}

__all__ = [
    "BaseDetectorBackend",
    "DetectionResult",
    "GitTagsBackend",
    "GitHubBackend",
    "RegexBackend",
    "AptBackend",
    "CodebergBackend",
    "BACKENDS",
]
