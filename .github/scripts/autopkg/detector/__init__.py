"""
Detector package for autopkg-engine.
"""

from detector.engine import DetectionEngine, UpdateCandidate
from detector.rules import PackageRule, RulesLoader, RulesRegistry
from detector.void_update import VoidUpdateConfig, VoidUpdateParser

__all__ = [
    "DetectionEngine",
    "UpdateCandidate",
    "RulesLoader",
    "RulesRegistry",
    "PackageRule",
    "VoidUpdateParser",
    "VoidUpdateConfig",
]
