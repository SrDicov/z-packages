"""
Unit tests for autopkg core modules: adapter, template, dag, quarantine.
"""

import tempfile
import time
import unittest
from pathlib import Path

from core.adapter import RepoAdapter, RepoKind
from core.dag import DependencyPlanner
from core.quarantine import QuarantineManager
from core.template import TemplateEditor


class TestCoreModules(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_adapter_layouts(self):
        # 1. Void standard
        (self.root / "srcpkgs" / "pkg-a").mkdir(parents=True)
        (self.root / "srcpkgs" / "pkg-a" / "template").write_text("pkgname=pkg-a\nversion=1.0\nrevision=1\n")
        adapter = RepoAdapter(self.root)
        self.assertEqual(adapter.kind, RepoKind.VOID_STANDARD)
        self.assertIn("pkg-a", adapter.list_packages())

        # 2. Pkgs subdir
        tmp2 = tempfile.TemporaryDirectory()
        r2 = Path(tmp2.name)
        (r2 / "pkgs" / "pkg-b").mkdir(parents=True)
        (r2 / "pkgs" / "pkg-b" / "template").write_text("pkgname=pkg-b\nversion=1.0\nrevision=1\n")
        adapter2 = RepoAdapter(r2)
        self.assertEqual(adapter2.kind, RepoKind.PKGS_SUBDIR)
        self.assertIn("pkg-b", adapter2.list_packages())
        tmp2.cleanup()

    def test_template_preserves_comments(self):
        content = """# Header comment
pkgname=sample
version=1.2.3
revision=1
# Important mid comment
short_desc="Sample description"
maintainer="Alice <alice@example.org>"
checksum=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

# Trailing comment
"""
        updated = TemplateEditor.update_metadata(
            content,
            new_version="1.2.4",
            new_revision=2,
            new_checksum="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        self.assertIn("# Header comment", updated)
        self.assertIn("# Important mid comment", updated)
        self.assertIn("# Trailing comment", updated)
        self.assertIn("version=1.2.4", updated)
        self.assertIn("revision=2", updated)
        self.assertIn("checksum=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", updated)

    def test_template_multi_checksum(self):
        content = """pkgname=sample
version=1.0.0
revision=1
distfiles="https://foo.org/a.tar.gz https://foo.org/b.tar.gz"
checksum="
 1111111111111111111111111111111111111111111111111111111111111111
 2222222222222222222222222222222222222222222222222222222222222222"
"""
        updated = TemplateEditor.update_metadata(
            content,
            new_version="2.0.0",
            new_revision=1,
            new_checksum=[
                "3333333333333333333333333333333333333333333333333333333333333333",
                "4444444444444444444444444444444444444444444444444444444444444444",
            ],
        )
        self.assertIn("version=2.0.0", updated)
        self.assertIn("3333333333333333333333333333333333333333333333333333333333333333", updated)
        self.assertIn("4444444444444444444444444444444444444444444444444444444444444444", updated)

    def test_dag_dependency_ordering(self):
        # Create 3 packages: libcore, libgui (depends on libcore), app (depends on libgui)
        for p in ["libcore", "libgui", "app"]:
            (self.root / "srcpkgs" / p).mkdir(parents=True, exist_ok=True)

        (self.root / "srcpkgs" / "libcore" / "template").write_text(
            "pkgname=libcore\nversion=1.0\nrevision=1\n"
        )
        (self.root / "srcpkgs" / "libgui" / "template").write_text(
            'pkgname=libgui\nversion=1.0\nrevision=1\nmakedepends="libcore"\n'
        )
        (self.root / "srcpkgs" / "app" / "template").write_text(
            'pkgname=app\nversion=1.0\nrevision=1\nmakedepends="libgui"\n'
        )

        adapter = RepoAdapter(self.root)
        planner = DependencyPlanner(adapter)
        # Supply them in reverse/shuffled order
        ordered = planner.plan_build_order(["app", "libcore", "libgui"])
        # libcore must precede libgui, and libgui must precede app
        self.assertLess(ordered.index("libcore"), ordered.index("libgui"))
        self.assertLess(ordered.index("libgui"), ordered.index("app"))

    def test_quarantine_exponential_backoff(self):
        state_file = self.root / "quarantine.json"
        qm = QuarantineManager(state_file)

        self.assertFalse(qm.is_quarantined("broken-pkg"))

        # 1st failure
        qm.record_failure("broken-pkg", "2.0.0", "compilation error")
        self.assertTrue(qm.is_quarantined("broken-pkg", "2.0.0"))

        # A NEW upstream version bypasses quarantine
        self.assertFalse(qm.is_quarantined("broken-pkg", "2.0.1"))

        # Success clears it
        qm.record_success("broken-pkg")
        self.assertFalse(qm.is_quarantined("broken-pkg", "2.0.0"))

    def test_git_tag_cleaning_and_pcre(self):
        from detector.backends.git_tags import GitTagsBackend, safe_re_search

        # 1. Complex tag cleaning
        cleaned = GitTagsBackend._clean_tag_to_version("v0.8.0-mikuri.9")
        self.assertEqual(cleaned, "0.8.0.mikuri.9")

        cleaned2 = GitTagsBackend._clean_tag_to_version("v0.8.0-mikuri.20")
        self.assertEqual(cleaned2, "0.8.0.mikuri.20")

        # Natural sort order
        k1 = GitTagsBackend._version_sort_key(cleaned)
        k2 = GitTagsBackend._version_sort_key(cleaned2)
        self.assertLess(k1, k2)

        # Reject commit hash tags
        self.assertIsNone(GitTagsBackend._clean_tag_to_version("hl-61a71c65ace5124cb123"))

        # 2. Safe PCRE \K search
        pcre_pattern = r"releases/tag/v\K[\d.]+"
        self.assertTrue(safe_re_search(pcre_pattern, "releases/tag/v1.2.3"))
        self.assertFalse(safe_re_search(pcre_pattern, "foo/bar/baz"))

    def test_template_additional_vars(self):
        content = """# Halley template
pkgname=halley
version=0.5.6
revision=1
_tag=v0.8.0-mikuri.9
distfiles="https://github.com/mikuri12/halley/archive/refs/tags/${_tag}.tar.gz>halley-${version}.tar.gz"
checksum=95d7293acc2fb068ecbcd4b961c9ab385ee3667d99a8867b3577369cdaea6cf0
"""
        updated = TemplateEditor.update_metadata(
            content,
            new_version="0.8.0",
            new_revision=1,
            new_checksum="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            additional_vars={"_tag": "v0.8.0-mikuri.20"},
        )
        self.assertIn("version=0.8.0", updated)
        self.assertIn("_tag=v0.8.0-mikuri.20", updated)
        self.assertIn("checksum=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", updated)


if __name__ == "__main__":
    unittest.main()
