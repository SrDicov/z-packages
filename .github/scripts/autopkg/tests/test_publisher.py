"""
Unit tests for publisher: indexer pruning, validator, and local publisher.
"""

import subprocess
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

    def test_incremental_indexing_skips_identical_preexisting_package(self):
        """Test that identical pre-existing packages (seeded dependencies) are unlinked and not re-signed."""
        import shutil
        if not shutil.which("xbps-rindex"):
            self.skipTest("xbps-rindex not installed on host")

        arch = "x86_64"
        indexer = RepoIndexer(self.root, arch=arch)

        # 1. Create a dummy repodata with pkg-seed-1.0_1
        pkg_seed = self.root / f"pkg-seed-1.0_1.{arch}.xbps"
        pkg_seed_src = self.root / "pkg-seed"
        pkg_seed_src.mkdir()
        (pkg_seed_src / "seed.txt").write_text("seeded content")
        subprocess.run(
            ["xbps-create", "-A", arch, "-n", "pkg-seed-1.0_1", "-s", "desc seed", "-m", "s <s@s.com>", "-l", "MIT", str(pkg_seed_src)],
            cwd=str(self.root),
            check=True,
        )
        subprocess.run(["xbps-rindex", "-a", pkg_seed.name], cwd=str(self.root), check=True)

        # 2. Add a newly built package pkg-new-1.0_1
        pkg_new = self.root / f"pkg-new-1.0_1.{arch}.xbps"
        pkg_new_src = self.root / "pkg-new"
        pkg_new_src.mkdir()
        (pkg_new_src / "new.txt").write_text("new content")
        subprocess.run(
            ["xbps-create", "-A", arch, "-n", "pkg-new-1.0_1", "-s", "desc new", "-m", "n <n@n.com>", "-l", "MIT", str(pkg_new_src)],
            cwd=str(self.root),
            check=True,
        )

        # 3. Generate test key and run sign_and_index with clean_repodata=False
        privkey_path = self.root / "privkey.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(privkey_path), "1024"], check=True, capture_output=True)
        key_info = indexer.sign_and_index(privkey_path, clean_repodata=False)
        self.assertIsNotNone(key_info)

        # pkg-seed should have been detected as pre-existing identical and unlinked
        self.assertFalse(pkg_seed.is_file(), "pkg-seed was not unlinked from dist")
        # pkg-new should remain on disk and have .sig2
        self.assertTrue(pkg_new.is_file(), "pkg-new was unexpectedly removed")
        self.assertTrue((self.root / f"pkg-new-1.0_1.{arch}.xbps.sig2").is_file(), "pkg-new was not signed")

    def test_sanitize_and_commit_staged_repodata(self):
        """Verify that stalled packages in stage.plist are committed to index.plist and system shlibs stripped."""
        import plistlib
        from publisher.validator import write_repodata_members, read_repodata_members

        arch = "x86_64"
        indexer = RepoIndexer(self.root, arch=arch)
        repodata = self.root / f"{arch}-repodata"

        index_data = {
            "mullvad-browser": {
                "pkgver": "mullvad-browser-16.0.1_1",
                "shlib-provides": ["libstdc++.so.6", "libcustom.so.1"],
            },
            "other-pkg": {
                "pkgver": "other-pkg-1.0_1",
            },
        }
        stage_data = {
            "goverlay": {
                "pkgver": "goverlay-1.9.4_2",
            },
        }

        # Write repodata with stage.plist and bad shlib-provides
        write_repodata_members(
            repodata,
            {
                "index.plist": plistlib.dumps(index_data),
                "stage.plist": plistlib.dumps(stage_data),
            },
        )

        # Sanitize repodata
        indexer._sanitize_existing_repodata()

        members = read_repodata_members(repodata)
        new_index = plistlib.loads(members["index.plist"])

        # goverlay should have been promoted to index.plist
        self.assertIn("goverlay", new_index)
        self.assertEqual(new_index["goverlay"]["pkgver"], "goverlay-1.9.4_2")

        # libstdc++.so.6 should have been stripped from mullvad-browser shlib-provides
        self.assertEqual(new_index["mullvad-browser"]["shlib-provides"], ["libcustom.so.1"])


if __name__ == "__main__":
    unittest.main()
