"""
End-to-end integration tests for autopkg-engine.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.cli_path = Path(__file__).resolve().parents[1] / "cli.py"

        # Set up a mock repo structure
        (self.root / "srcpkgs" / "libcore").mkdir(parents=True)
        (self.root / "srcpkgs" / "libcore" / "template").write_text(
            """# Template for libcore
# Maintainer: dicov
pkgname=libcore
version=1.0.0
revision=1
distfiles="https://github.com/example/libcore/archive/refs/tags/v${version}.tar.gz"
checksum=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""
        )

        (self.root / "srcpkgs" / "app").mkdir(parents=True)
        (self.root / "srcpkgs" / "app" / "template").write_text(
            """# Template for app
# Maintainer: dicov
pkgname=app
version=2.0.0
revision=1
makedepends="libcore"
distfiles="https://github.com/example/app/archive/refs/tags/v${version}.tar.gz"
checksum=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_cli_plan_ordering(self):
        # Run cli plan with packages specified
        res = subprocess.run(
            [
                sys.executable,
                str(self.cli_path),
                "plan",
                "--repo", str(self.root),
                "--all",
                "--json",
                "app", "libcore",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        self.assertIn("libcore", res.stdout)
        self.assertIn("app", res.stdout)

    def test_cli_update_preserves_comments(self):
        # Run cli update on libcore with explicit version and checksum
        res = subprocess.run(
            [
                sys.executable,
                str(self.cli_path),
                "update",
                "--repo", str(self.root),
                "--version", "1.1.0",
                "--revision", "2",
                "--checksum", "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                "libcore",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        tmpl_content = (self.root / "srcpkgs" / "libcore" / "template").read_text()
        self.assertIn("# Template for libcore", tmpl_content)
        self.assertIn("# Maintainer: dicov", tmpl_content)
        self.assertIn("version=1.1.0", tmpl_content)
        self.assertIn("revision=2", tmpl_content)
        self.assertIn("checksum=cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", tmpl_content)


if __name__ == "__main__":
    unittest.main()
