"""
Comprehensive Fuzzing & Architectural Hardening Test Suite for autopkg-engine.

Validates the 5 active defense mechanisms:
1. Strict Rule Policy (Zero fallback to heuristic GitTags on rule failure/403).
2. Semantic Anti-Garbage Filter (Rejects video, build, epoch jumps, absurd major version jumps).
3. Pre-flight Distfile HTTP 200 Verification & Asset Matching.
4. Named Groups Regex Extraction & Multi-variable Atomic Template Updates.
5. Fail-Closed Checksum Policy in TemplateEditor.update_file.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.adapter import RepoAdapter
from core.template import TemplateData, TemplateEditor
from detector.backends import BACKENDS, DetectionResult, GitHubBackend, GitTagsBackend
from detector.engine import DetectionEngine, UpdateCandidate
from detector.rules import PackageRule, RulesRegistry


class TestFuzzDetectorHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        (self.repo_dir / "srcpkgs" / "testpkg").mkdir(parents=True)
        self.template_file = self.repo_dir / "srcpkgs" / "testpkg" / "template"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_template(self, content: str) -> None:
        self.template_file.write_text(content, encoding="utf-8")

    # --------------------------------------------------------------------------
    # 1. Strict Rule Enforcement Test
    # --------------------------------------------------------------------------
    def test_strict_rule_no_fallback_on_backend_none(self):
        """If a package has an explicit rule that returns None, it must NEVER fall back to GitTags."""
        tmpl_content = """# Template file for 'testpkg'
pkgname=testpkg
version=2.3.0
revision=1
homepage="https://github.com/upstream/testpkg"
distfiles="https://github.com/upstream/testpkg/releases/download/v${version}/testpkg-${version}.tar.gz"
checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
"""
        self._write_template(tmpl_content)

        adapter = RepoAdapter(self.repo_dir)
        engine = DetectionEngine(adapter)

        # Register explicit rule for testpkg
        engine.rules.packages["testpkg"] = PackageRule(
            pkgname="testpkg",
            backend="github",
            url="https://github.com/upstream/testpkg",
        )

        # Mock GitHubBackend to simulate rate limit / 403 (returns None)
        with patch.object(GitHubBackend, "detect_latest", return_value=None) as mock_gh:
            with patch.object(GitTagsBackend, "detect_latest") as mock_git:
                cand = engine.check_package("testpkg")
                # Strict rule policy: must return None
                self.assertIsNone(cand)
                # GitTagsBackend MUST NOT be invoked as fallback
                mock_git.assert_not_called()
                mock_gh.assert_called_once()

    # --------------------------------------------------------------------------
    # 2. Semantic Anti-Garbage Filter Tests
    # --------------------------------------------------------------------------
    def test_is_sane_version_transition_garbage_tokens(self):
        """Tokens like 'video', 'build', 'nightly' must be rejected unless allow_prerelease is set."""
        self.assertFalse(DetectionEngine.is_sane_version_transition("2.3.0", "56106.video"))
        self.assertFalse(DetectionEngine.is_sane_version_transition("1.0.0", "1.0.1-nightly"))
        self.assertFalse(DetectionEngine.is_sane_version_transition("1.0.0", "build1234"))
        self.assertFalse(DetectionEngine.is_sane_version_transition("1.0.0", "2.0-android"))

    def test_is_sane_version_transition_absurd_major_jumps(self):
        """Absurd major version jumps like 2.2 -> 1410987542 or 2026.5 -> 2995.2 must be rejected."""
        # Case 1: current major < 100 jumping to epoch/build number > 500
        self.assertFalse(DetectionEngine.is_sane_version_transition("2.2", "1410987542"))
        self.assertFalse(DetectionEngine.is_sane_version_transition("2.3.0", "56106"))
        self.assertFalse(DetectionEngine.is_sane_version_transition("0.1.0", "99999"))

        # Case 2: candidate major is > 500 above current major
        self.assertFalse(DetectionEngine.is_sane_version_transition("2026.5", "2995.2"))

        # Valid transitions
        self.assertTrue(DetectionEngine.is_sane_version_transition("1.99.57", "1.100.56"))
        self.assertTrue(DetectionEngine.is_sane_version_transition("7.85", "7.86"))
        self.assertTrue(DetectionEngine.is_sane_version_transition("2024.1", "2026.5"))

    # --------------------------------------------------------------------------
    # 3. Pre-flight Distfile Verification Tests
    # --------------------------------------------------------------------------
    def test_candidate_distfile_404_rejected(self):
        """If candidate version distfile returns 404, candidate is rejected."""
        tmpl_content = """# Template file for 'testpkg'
pkgname=testpkg
version=1.0.0
revision=1
distfiles="https://example.com/downloads/v${version}/testpkg-${version}.tar.gz"
checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
"""
        self._write_template(tmpl_content)
        adapter = RepoAdapter(self.repo_dir)
        engine = DetectionEngine(adapter)

        # Mock GitTagsBackend returning candidate version 1.1.0
        fake_res = DetectionResult(
            latest_version="1.1.0",
            download_url="https://example.com/repo.git",
            source_type="git_tags",
        )
        with patch.object(GitTagsBackend, "detect_latest", return_value=fake_res):
            # Mock verify_distfile_url to simulate HTTP 404 on the candidate URL
            with patch("detector.engine.verify_distfile_url", return_value=False) as mock_verify:
                cand = engine.check_package("testpkg")
                self.assertIsNone(cand)
                mock_verify.assert_called_once()

    def test_github_backend_skips_nightly_with_missing_assets(self):
        """GitHubBackend skips releases where the expected asset name is missing."""
        tmpl_data = TemplateData(
            pkgname="brave",
            version="1.100.56",
            revision=1,
            distfiles=["https://github.com/brave/brave-browser/releases/download/v${version}/brave-browser_${version}_amd64.deb"],
            raw_distfiles=["https://github.com/brave/brave-browser/releases/download/v${version}/brave-browser_${version}_amd64.deb"],
        )

        backend = GitHubBackend()

        # Release 1: Nightly (only has brave-browser-nightly_1.102.4_amd64.deb)
        rel_nightly = {
            "tag_name": "v1.102.4",
            "html_url": "https://github.com/brave/brave-browser/releases/tag/v1.102.4",
            "assets": [
                {"name": "brave-browser-nightly_1.102.4_amd64.deb", "browser_download_url": "https://..."},
            ],
        }
        # Release 2: Stable release (has brave-browser_1.100.56_amd64.deb)
        rel_stable = {
            "tag_name": "v1.100.56",
            "html_url": "https://github.com/brave/brave-browser/releases/tag/v1.100.56",
            "assets": [
                {"name": "brave-browser_1.100.56_amd64.deb", "browser_download_url": "https://..."},
            ],
        }

        # Mock urlopen: first call (/releases/latest) returns nightly, second call (/releases?per_page=15) returns [nightly, stable]
        import io
        import json

        resp_latest = MagicMock()
        resp_latest.headers = {}
        resp_latest.read.return_value = json.dumps(rel_nightly).encode("utf-8")
        resp_latest.__enter__.return_value = resp_latest

        resp_list = MagicMock()
        resp_list.headers = {}
        resp_list.read.return_value = json.dumps([rel_nightly, rel_stable]).encode("utf-8")
        resp_list.__enter__.return_value = resp_list

        with patch("urllib.request.urlopen", side_effect=[resp_latest, resp_list]):
            res = backend.detect_latest(tmpl_data)
            self.assertIsNotNone(res)
            # Must skip 1.102.4 nightly and return 1.100.56 stable
            self.assertEqual(res.latest_version, "1.100.56")

    # --------------------------------------------------------------------------
    # 4. Named Groups Regex Extraction Test
    # --------------------------------------------------------------------------
    def test_named_groups_pattern_extraction(self):
        """Pattern with (?P<version>...) and (?P<_subver>...) correctly populates named_vars."""
        tmpl_data = TemplateData(
            pkgname="spotify-app",
            version="1.3.4",
            revision=1,
            homepage="https://github.com/upstream/repo",
        )
        rule = PackageRule(
            pkgname="spotify-app",
            backend="git_tags",
            url="https://github.com/upstream/repo.git",
            options={
                "version_regex": r"v(?P<version>\d+\.\d+\.\d+)\.(?P<_subver>[0-9a-z.]+)",
            },
        )

        backend = GitTagsBackend()
        git_output = "refs/tags/v1.3.4.258.gc60b10b0946a\trefs/tags/v1.3.4.258.gc60b10b0946a"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=f"abc {git_output}\n")
            res = backend.detect_latest(tmpl_data, rule)
            self.assertIsNotNone(res)
            self.assertEqual(res.latest_version, "1.3.4")
            self.assertIn("named_vars", res.extra)
            self.assertEqual(res.extra["named_vars"]["_subver"], "258.gc60b10b0946a")

    # --------------------------------------------------------------------------
    # 5. Fail-Closed Checksum Invariant Test
    # --------------------------------------------------------------------------
    def test_template_editor_checksum_invariant(self):
        """TemplateEditor.update_file must refuse version change without new valid checksum."""
        tmpl_content = """# Template file for 'testpkg'
pkgname=testpkg
version=1.0.0
revision=1
distfiles="https://example.com/testpkg-1.0.0.tar.gz"
checksum="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""
        self._write_template(tmpl_content)

        # 1. Version change without new_checksum -> ValueError
        with self.assertRaises(ValueError):
            TemplateEditor.update_file(self.template_file, new_version="1.1.0")

        # 2. Version change with identical checksum -> ValueError
        with self.assertRaises(ValueError):
            TemplateEditor.update_file(
                self.template_file,
                new_version="1.1.0",
                new_checksum="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )

        # 3. Version change with allow_same_checksum=True -> succeeds
        success = TemplateEditor.update_file(
            self.template_file,
            new_version="1.1.0",
            allow_same_checksum=True,
        )
        self.assertTrue(success)

        # 4. Version change with new checksum -> succeeds
        success2 = TemplateEditor.update_file(
            self.template_file,
            new_version="1.2.0",
            new_checksum="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        self.assertTrue(success2)
        parsed = TemplateEditor.parse_file(self.template_file)
        self.assertEqual(parsed.version, "1.2.0")
        self.assertEqual(parsed.checksums, ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"])


if __name__ == "__main__":
    unittest.main()
