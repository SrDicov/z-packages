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


if __name__ == "__main__":
    unittest.main()
