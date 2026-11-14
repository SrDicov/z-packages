"""
Unit tests for publisher: indexer pruning, validator, and local publisher.
"""

import tempfile
import unittest
from pathlib import Path

from publisher.backends.local import LocalPublisher
from publisher.indexer import RepoIndexer
from publisher.validator import RepoValidator


class TestPublisher(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_prune_old_versions(self):
        arch = "x86_64"
        # Create multiple versions of 'mypkg'
        f1 = self.root / f"mypkg-1.0.0_1.{arch}.xbps"
        f2 = self.root / f"mypkg-1.0.1_1.{arch}.xbps"
        f3 = self.root / f"mypkg-1.0.1_2.{arch}.xbps"  # newest

        f1.touch()
        f2.touch()
        f3.touch()

        indexer = RepoIndexer(self.root, arch=arch)
        removed = indexer.prune_old_versions()

        self.assertEqual(len(removed), 2)
        self.assertIn(f1, removed)
        self.assertIn(f2, removed)
        self.assertTrue(f3.is_file())
        self.assertFalse(f1.is_file())
        self.assertFalse(f2.is_file())

    def test_local_publisher(self):
        arch = "x86_64"
        test_file = self.root / f"sample-1.0_1.{arch}.xbps"
        test_file.write_text("dummy binary package")

        dest_dir = self.root / "served_repo"
        publisher = LocalPublisher()
        res = publisher.publish(self.root, arch=arch, options={"destination": str(dest_dir)})

        self.assertTrue(res.success)
        self.assertTrue((dest_dir / test_file.name).is_file())

    def test_incremental_indexing_preserves_existing_repodata(self):
        """Verify that incremental indexing preserves previous packages in index.plist."""
        import shutil
        import subprocess
        if not shutil.which("xbps-rindex") or not shutil.which("xbps-create"):
            self.skipTest("xbps-rindex or xbps-create not installed")

        arch = "x86_64"
        # 1. Create pkg-a with xbps-create
        pkg_a_src = self.root / "pkg-a"
        pkg_a_src.mkdir()
        (pkg_a_src / "file-a.txt").write_text("content-a")
        res_a = subprocess.run(
            ["xbps-create", "-A", arch, "-n", "pkg-a-1.0_1", "-s", "desc a", "-m", "a <a@a.com>", "-l", "MIT", str(pkg_a_src)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_a.returncode, 0, res_a.stderr)

        # Index pkg-a into x86_64-repodata
        res_index1 = subprocess.run(
            ["xbps-rindex", "-a", f"pkg-a-1.0_1.{arch}.xbps"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_index1.returncode, 0, res_index1.stderr)

        # 2. Simulate incremental CI build: remove pkg-a from disk so only pkg-b will be present
        (self.root / f"pkg-a-1.0_1.{arch}.xbps").unlink()

        # 3. Create pkg-b
        pkg_b_src = self.root / "pkg-b"
        pkg_b_src.mkdir()
        (pkg_b_src / "file-b.txt").write_text("content-b")
        res_b = subprocess.run(
            ["xbps-create", "-A", arch, "-n", "pkg-b-2.0_1", "-s", "desc b", "-m", "b <b@b.com>", "-l", "MIT", str(pkg_b_src)],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_b.returncode, 0, res_b.stderr)

        # Index pkg-b into existing x86_64-repodata
        res_index2 = subprocess.run(
            ["xbps-rindex", "-a", f"pkg-b-2.0_1.{arch}.xbps"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_index2.returncode, 0, res_index2.stderr)

        # 4. Verify repodata index.plist retains both pkg-a and pkg-b
        repodata_path = self.root / f"{arch}-repodata"
        index_data = RepoValidator._extract_index_plist(repodata_path)
        self.assertIsNotNone(index_data)
        self.assertIn("pkg-a", index_data, "pkg-a was lost from index.plist during incremental indexing!")
        self.assertIn("pkg-b", index_data, "pkg-b was not added to index.plist!")
        self.assertEqual(index_data["pkg-a"]["pkgver"], "pkg-a-1.0_1")
        self.assertEqual(index_data["pkg-b"]["pkgver"], "pkg-b-2.0_1")


if __name__ == "__main__":
    unittest.main()
