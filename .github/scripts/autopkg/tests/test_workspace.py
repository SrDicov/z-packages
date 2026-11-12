"""Unit tests for build workspace preparation."""

import tempfile
import unittest
from pathlib import Path

from builder.workspace import is_full_void_tree, prepare_build_tree


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.repo = self.root / "repo"
        self.build_tree = self.root / "void-packages"
        self.repo.mkdir()
        self.build_tree.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    @staticmethod
    def _make_full_tree(root: Path) -> None:
        (root / "common" / "xbps-src").mkdir(parents=True)
        (root / "xbps-src").write_text("#!/bin/sh\n")
        (root / "srcpkgs").mkdir(exist_ok=True)

    def test_full_tree_is_returned_unchanged(self):
        self._make_full_tree(self.repo)

        tree = prepare_build_tree(self.repo, self.build_tree)

        self.assertEqual(tree, self.repo.resolve())
        self.assertTrue(is_full_void_tree(tree))

    def test_flat_layout_is_overlaid_with_symlinks(self):
        self._make_full_tree(self.build_tree)
        (self.repo / "pkg-a").mkdir()
        (self.repo / "pkg-a" / "template").write_text("pkgname=pkg-a\n")
        (self.repo / "alias-a").symlink_to("pkg-a")
        (self.repo / ".github" / "scripts" / "autopkg").mkdir(parents=True)
        (self.repo / ".github" / "scripts" / "autopkg" / "cli.py").write_text("")

        tree = prepare_build_tree(self.repo, self.build_tree)

        self.assertTrue((tree / "srcpkgs" / "pkg-a" / "template").is_file())
        self.assertTrue((tree / "srcpkgs" / "alias-a").is_symlink())
        self.assertEqual((tree / "srcpkgs" / "alias-a").readlink(), Path("pkg-a"))
        self.assertTrue((tree / ".github" / "scripts" / "autopkg" / "cli.py").is_file())

    def test_srcpkgs_layout_is_overlaid_and_replaced(self):
        self._make_full_tree(self.build_tree)
        (self.build_tree / "srcpkgs" / "pkg-a").mkdir()
        (self.build_tree / "srcpkgs" / "pkg-a" / "template").write_text("stale\n")

        (self.repo / "srcpkgs" / "pkg-a").mkdir(parents=True)
        (self.repo / "srcpkgs" / "pkg-a" / "template").write_text("fresh\n")

        tree = prepare_build_tree(self.repo, self.build_tree)

        self.assertEqual((tree / "srcpkgs" / "pkg-a" / "template").read_text(), "fresh\n")

    def test_non_empty_unknown_destination_is_rejected(self):
        (self.repo / "pkg-a").mkdir()
        (self.repo / "pkg-a" / "template").write_text("pkgname=pkg-a\n")
        (self.build_tree / "keep.txt").write_text("do not delete")

        with self.assertRaises(RuntimeError):
            prepare_build_tree(self.repo, self.build_tree)


if __name__ == "__main__":
    unittest.main()
