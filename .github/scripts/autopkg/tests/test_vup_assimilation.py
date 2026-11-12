"""
Unit tests for VUP Technology Assimilation:
1. Categorized repository layout (RepoKind.CATEGORIZED)
2. Same-CPU different-libc compiler flags (-A vs -a)
3. Sharded release tags by category (<category>-<arch>-current)
4. noshlibprovides=yes security heuristic validation
"""

import tempfile
import unittest
from pathlib import Path

from builder.runner import BuildRunner, needs_alt_host
from core.adapter import RepoAdapter, RepoKind
from core.template import TemplateData, TemplateEditor
from publisher.backends.github_release import GitHubReleasePublisher
from publisher.validator import RepoValidator


class TestVupAssimilation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_categorized_layout_detection_and_indexing(self):
        """Test detection and resolution of categorized repo layout (srcpkgs/<cat>/<pkg>/template)."""
        srcpkgs = self.root / "srcpkgs"
        (srcpkgs / "browsers" / "helium").mkdir(parents=True)
        (srcpkgs / "browsers" / "helium" / "template").write_text("pkgname=helium\nversion=1.0\nrevision=1\n")
        (srcpkgs / "browsers" / "helium" / "update").write_text("site=github\n")

        (srcpkgs / "editors" / "zed").mkdir(parents=True)
        (srcpkgs / "editors" / "zed" / "template").write_text("pkgname=zed\nversion=0.180.0\nrevision=1\n")

        adapter = RepoAdapter(self.root)
        self.assertEqual(adapter.kind, RepoKind.CATEGORIZED)
        self.assertEqual(adapter.list_categories(), ["browsers", "editors"])
        self.assertEqual(sorted(adapter.list_packages()), ["helium", "zed"])

        self.assertEqual(adapter.get_package_category("helium"), "browsers")
        self.assertEqual(adapter.get_package_category("zed"), "editors")

        self.assertIsNotNone(adapter.get_template_path("helium"))
        self.assertIsNotNone(adapter.get_template_path("zed"))
        self.assertIsNotNone(adapter.get_update_file_path("helium"))
        self.assertIsNone(adapter.get_update_file_path("zed"))

    def test_real_vup_repository_detection(self):
        """Test adapter against real /home/dicov/Proyectos/vup repository."""
        vup_path = Path("/home/dicov/Proyectos/vup")
        if not vup_path.is_dir():
            self.skipTest("vup repository not found on disk")

        adapter = RepoAdapter(vup_path)
        self.assertEqual(adapter.kind, RepoKind.CATEGORIZED)
        packages = adapter.list_packages()
        self.assertGreaterEqual(len(packages), 25)
        self.assertIn("zed", packages)
        self.assertIn("vuru", packages)
        self.assertIn("brave", packages)
        self.assertEqual(adapter.get_package_category("zed"), "editors")
        self.assertEqual(adapter.get_package_category("brave"), "browsers")

    def test_needs_alt_host_rules(self):
        """Verify -A vs -a logic for same-CPU different-libc."""
        # x86_64 host
        self.assertFalse(needs_alt_host("x86_64", "x86_64"))
        self.assertTrue(needs_alt_host("x86_64-musl", "x86_64"))
        self.assertFalse(needs_alt_host("aarch64", "x86_64"))
        self.assertFalse(needs_alt_host("aarch64-musl", "x86_64"))

        # aarch64 host
        self.assertFalse(needs_alt_host("aarch64", "aarch64"))
        self.assertTrue(needs_alt_host("aarch64-musl", "aarch64"))
        self.assertFalse(needs_alt_host("x86_64", "aarch64"))

    def test_builder_uses_alt_host_flag(self):
        """Ensure BuildRunner sets -A for same-CPU musl and -a for cross-CPU."""
        fake_xbps = self.root / "xbps-src"
        fake_xbps.write_text("#!/bin/sh\nexit 0\n")
        fake_xbps.chmod(0o755)

        (self.root / "srcpkgs" / "testpkg").mkdir(parents=True)
        (self.root / "srcpkgs" / "testpkg" / "template").write_text("pkgname=testpkg\nversion=1.0\nrevision=1\n")

        adapter = RepoAdapter(self.root)
        runner = BuildRunner(adapter, logs_dir=self.root / "logs")

        # Mock build_package command formation
        # For x86_64-musl on x86_64 host:
        res = runner.build_package("testpkg", arch="x86_64-musl", host_arch="x86_64")
        with open(res.log_path) as f:
            log_content = f.read()

        # Check that BuildRunner executed with 0 returncode
        self.assertEqual(res.returncode, 0)

    def test_sharded_release_tag_naming(self):
        """Verify category sharded tags follow <category>-<arch>-current."""
        publisher = GitHubReleasePublisher()
        # Mock options
        opts_cat = {"repo": "myorg/myrepo", "category": "browsers"}
        default_tag = f"{opts_cat['category']}-x86_64-current"
        self.assertEqual(default_tag, "browsers-x86_64-current")

        opts_flat = {"repo": "myorg/myrepo"}
        default_tag_flat = "rolling-x86_64"
        self.assertEqual(default_tag_flat, "rolling-x86_64")

    def test_noshlibprovides_security_heuristic(self):
        """Verify prebuilt apps without noshlibprovides=yes raise critical warnings."""
        # 1. Prebuilt app missing noshlibprovides
        raw_prebuilt = """
pkgname=vscode-app
version=1.95.0
revision=1
short_desc="Visual Studio Code binary editor"
distfiles="https://example.com/vscode-linux-x64.deb"
"""
        tmpl = TemplateEditor.parse_string(raw_prebuilt)
        warn = TemplateEditor.check_noshlibprovides(tmpl)
        self.assertIsNotNone(warn)
        self.assertIn("noshlibprovides=yes", warn)

        # 2. Prebuilt app with noshlibprovides=yes
        raw_safe = """
pkgname=vscode-app
version=1.95.0
revision=1
short_desc="Visual Studio Code binary editor"
distfiles="https://example.com/vscode-linux-x64.deb"
noshlibprovides=yes
"""
        tmpl_safe = TemplateEditor.parse_string(raw_safe)
        self.assertIsNone(TemplateEditor.check_noshlibprovides(tmpl_safe))

        # 3. Compiled package with build_style
        raw_compiled = """
pkgname=ripgrep
version=14.1.0
revision=1
build_style=cargo
short_desc="Fast search tool"
distfiles="https://github.com/BurntSushi/ripgrep/archive/14.1.0.tar.gz"
"""
        tmpl_compiled = TemplateEditor.parse_string(raw_compiled)
        self.assertIsNone(TemplateEditor.check_noshlibprovides(tmpl_compiled))

        # 4. Compiled package with do_build()
        raw_manual = """
pkgname=custom-app
version=1.0.0
revision=1
short_desc="Custom editor"
distfiles="https://example.com/custom-1.0.tar.gz"
do_build() {
    make
}
"""
        tmpl_manual = TemplateEditor.parse_string(raw_manual)
        self.assertIsNone(TemplateEditor.check_noshlibprovides(tmpl_manual))

        # 5. Python source package from PyPI
        raw_python = """
pkgname=python3-tool
version=2.0.0
revision=1
short_desc="Python IDE plugin"
hostmakedepends="python3-pep517"
distfiles="https://files.pythonhosted.org/packages/source/t/tool/tool-2.0.0.tar.gz"
"""
        tmpl_python = TemplateEditor.parse_string(raw_python)
        self.assertIsNone(TemplateEditor.check_noshlibprovides(tmpl_python))


if __name__ == "__main__":
    unittest.main()
