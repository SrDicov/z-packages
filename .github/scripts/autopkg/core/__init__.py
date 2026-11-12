"""
Core package for autopkg-engine.
"""

from core.adapter import RepoAdapter, RepoKind
from core.dag import DependencyPlanner
from core.quarantine import QuarantineManager, QuarantineRecord
from core.template import TemplateData, TemplateEditor

__all__ = [
    "RepoAdapter",
    "RepoKind",
    "TemplateEditor",
    "TemplateData",
    "DependencyPlanner",
    "QuarantineManager",
    "QuarantineRecord",
]
