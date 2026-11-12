"""
Unit tests for builder: selective_seed, bootstrap, runner.
"""

import tempfile
import unittest
from pathlib import Path

from builder.selective_seed import SelectiveSeeder
from core.adapter import RepoAdapter


class TestBuilder(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_selective_seed_dependency_resolution(self):
        # Create templates with dependencies
        pkg_dir = self.root / "srcpkgs" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "template").write_text(
            'pkgname=mypkg\nversion=1.0\nrevision=1\nmakedepends="libssl-devel>=1.1 curl-devel"\nhostmakedepends="pkg-config"\n'
        )

        adapter = RepoAdapter(self.root)
        seeder = SelectiveSeeder(adapter, target_dir=self.root / "binpkgs")

        deps = seeder.resolve_target_dependencies(["mypkg"])
        self.assertIn("libssl-devel", deps)
        self.assertIn("curl-devel", deps)
        self.assertIn("pkg-config", deps)


if __name__ == "__main__":
    unittest.main()
