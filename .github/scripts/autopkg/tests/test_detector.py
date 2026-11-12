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

    def test_git_commits_backend(self):
        from unittest import mock

        from core.template import TemplateEditor
        from detector.backends.git_commits import GitCommitsBackend
        from detector.rules import PackageRule

        tmpl = TemplateEditor.parse_string(
            "pkgname=foo\nversion=1.0\nrevision=1\n_commit=aaaa\n"
            'distfiles="https://github.com/o/r/archive/${_commit}.tar.gz"\n'
        )
        rule = PackageRule(pkgname="foo", backend="git_commits", url="https://github.com/o/r")
        done = mock.Mock(returncode=0, stdout="b" * 40 + "\trefs/heads/main\n")
        with mock.patch("detector.backends.git_commits.subprocess.run", return_value=done):
            res = GitCommitsBackend().detect_latest(tmpl, rule)

        self.assertIsNotNone(res)
        self.assertEqual(res.extra["commit"], "b" * 40)
        self.assertEqual(res.extra["commit_var"], "_commit")

    def test_engine_commit_pin_comparison(self):
        from unittest import mock

        pkg = self.root / "srcpkgs" / "foo"
        pkg.mkdir(parents=True)
        (pkg / "template").write_text(
            "pkgname=foo\nversion=1.0\nrevision=1\n_commit=aaaa\n"
            'distfiles="https://github.com/o/r/archive/${_commit}.tar.gz"\n'
        )
        rule_file = self.root / "rules.yml"
        rule_file.write_text(
            "packages:\n  foo:\n    backend: git_commits\n    url: https://github.com/o/r\n"
        )
        engine = DetectionEngine(
            RepoAdapter(self.root), rules_path=rule_file, cache_dir=self.root / ".cache"
        )
        done = mock.Mock(returncode=0, stdout="bbbb\trefs/heads/main\n")
        with mock.patch("detector.backends.git_commits.subprocess.run", return_value=done):
            cand = engine.check_package("foo")

        self.assertIsNotNone(cand)
        self.assertTrue(cand.needs_update)
        self.assertEqual(cand.current_version, "aaaa")

    def test_github_repo_from_api_url(self):
        from detector.backends.github import GitHubBackend

        self.assertEqual(
            GitHubBackend._repo_from_url(
                "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest"
            ),
            "Neko-Void-Linux/kernel-neko-void",
        )
        self.assertEqual(
            GitHubBackend._repo_from_url("https://github.com/owner/repo"), "owner/repo"
        )

    def test_json_keys_backend(self):
        import json
        from unittest import mock

        from detector.backends.json_keys import JsonKeysBackend
        from detector.rules import PackageRule

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"master": {}, "0.13.0": {}, "0.9.0": {}}).encode()

        rule = PackageRule(pkgname="zig", backend="json-keys", url="https://example.test/index.json")
        with mock.patch(
            "detector.backends.json_keys.urllib.request.urlopen", return_value=FakeResp()
        ):
            res = JsonKeysBackend().detect_latest(None, rule)

        self.assertIsNotNone(res)
        self.assertEqual(res.latest_version, "0.13.0")

    def test_git_tags_version_regex(self):
        from unittest import mock

        from detector.backends.git_tags import GitTagsBackend
        from detector.rules import PackageRule

        out = (
            "aaa\trefs/tags/35.0.2_Release\n"
            "bbb\trefs/tags/35.0.3_Release\n"
            "ccc\trefs/tags/v2019.01.01\n"
        )
        done = mock.Mock(returncode=0, stdout=out)
        rule = PackageRule(
            pkgname="palemoon",
            backend="tags",
            url="https://example.test/Pale-Moon.git",
            pattern="_Release$",
            options={"version_regex": r"^(\d+(?:\.\d+)+)_Release$"},
        )
        with mock.patch("detector.backends.git_tags.subprocess.run", return_value=done):
            res = GitTagsBackend().detect_latest(None, rule)

        self.assertIsNotNone(res)
        self.assertEqual(res.latest_version, "35.0.3")


if __name__ == "__main__":
    unittest.main()
