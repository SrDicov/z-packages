"""
Publisher backends registry.
"""

from publisher.backends.base import BasePublisherBackend, PublishResult
from publisher.backends.git_branch import GitBranchPublisher
from publisher.backends.github_release import GitHubReleasePublisher
from publisher.backends.local import LocalPublisher
from publisher.backends.rsync import RsyncPublisher

PUBLISHER_BACKENDS = {
    "github_release": GitHubReleasePublisher,
    "github": GitHubReleasePublisher,
    "git_branch": GitBranchPublisher,
    "forgejo": GitBranchPublisher,
    "local": LocalPublisher,
    "rsync": RsyncPublisher,
}

__all__ = [
    "BasePublisherBackend",
    "PublishResult",
    "GitHubReleasePublisher",
    "GitBranchPublisher",
    "LocalPublisher",
    "RsyncPublisher",
    "PUBLISHER_BACKENDS",
]
