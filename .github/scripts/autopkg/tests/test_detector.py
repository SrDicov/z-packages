"""
Unit tests for detector: void_update, rules, backends, engine.
"""

import tempfile
import unittest
from pathlib import Path

from core.adapter import RepoAdapter
from detector.engine import DetectionEngine
from detector.rules import RulesLoader
from detector.void_update import VoidUpdateParser


class TestDetector(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_void_update_parser(self):
        raw = """
site="https://example.com/releases"
pattern='href="pkg-\\K[\\d.]+(?=\\.tar\\.gz")'
ignore="alpha"
"""
        cfg = VoidUpdateParser.parse_string(raw)
        self.assertEqual(cfg.site, "https://example.com/releases")
        self.assertEqual(cfg.pattern, 'href="pkg-\\K[\\d.]+(?=\\.tar\\.gz")')
        self.assertEqual(cfg.ignore, "alpha")

    def test_rules_loader(self):
        yaml_content = """
exclude:
  - badpkg1
  - badpkg2

packages:
  mypkg:
    backend: "git_tags"
    url: "https://github.com/foo/mypkg.git"
"""
        rule_file = self.root / "rules.yml"
        rule_file.write_text(yaml_content)

        registry = RulesLoader.load_file(rule_file)
        self.assertTrue(registry.is_excluded("badpkg1"))
        self.assertFalse(registry.is_excluded("mypkg"))
        rule = registry.get_rule("mypkg")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.backend, "git_tags")
        self.assertEqual(rule.url, "https://github.com/foo/mypkg.git")

    def test_detection_engine_version_comparison(self):
        self.assertTrue(DetectionEngine._is_newer_version("1.2.4", "1.2.3"))
        self.assertTrue(DetectionEngine._is_newer_version("2.0.0", "1.99.9"))
        self.assertFalse(DetectionEngine._is_newer_version("1.2.3", "1.2.3"))
        self.assertFalse(DetectionEngine._is_newer_version("1.2.2", "1.2.3"))


if __name__ == "__main__":
    unittest.main()
