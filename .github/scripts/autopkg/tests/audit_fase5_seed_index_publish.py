"""
Fase 5: Real Seeding, Indexing, Signing, Pruning and Hash Corruption Validation Audit.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/dicov/Proyectos/autopkg")
from builder.selective_seed import SelectiveSeeder
from core.adapter import RepoAdapter
from publisher.indexer import RepoIndexer
from publisher.validator import RepoValidator


class TestFase5SeedAndPublisher(unittest.TestCase):
    def setUp(self):
        self.sandbox = Path("/tmp/autopkg-repo-sandbox")
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True)

        self.key_pem = Path("/tmp/autopkg_test_rsa.pem")
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-out", str(self.key_pem), "-pkeyopt", "rsa_keygen_bits:2048"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

    def tearDown(self):
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        if self.key_pem.exists():
            self.key_pem.unlink()

    def test_selective_seed_resolution(self):
        adapter = RepoAdapter("/home/dicov/Proyectos/z-packages")
        seeder = SelectiveSeeder(adapter)
        deps = seeder.resolve_target_dependencies(["artcraft-launcher"])
        print(f"artcraft-launcher resolved dependencies: {sorted(deps)}")
        # Check that artcraft-launcher dependencies are resolved
        self.assertTrue(len(deps) > 0)
        # Should not resolve unrelated packages like 'aquamarine'
        self.assertNotIn("aquamarine", deps)

    def test_indexing_signing_pruning_and_corruption_detection(self):
        arch = "x86_64"
        cache_dir = Path("/var/cache/xbps")

        # Copy real packages from /var/cache/xbps
        sample_pkgs = list(cache_dir.glob(f"*.{arch}.xbps"))[:3]
        self.assertTrue(len(sample_pkgs) >= 2, "Need at least 2 real xbps packages in /var/cache/xbps")

        for p in sample_pkgs:
            shutil.copy2(p, self.sandbox / p.name)

        # Create an older artificial duplicate of the first package to test pruning
        first_pkg = sample_pkgs[0].name
        # e.g. 7zip-26.06_1.x86_64.xbps -> 7zip-26.05_1.x86_64.xbps
        import re
        m = re.match(r"^(.+)-([0-9.]+)_([0-9]+)\." + arch + r"\.xbps$", first_pkg)
        if m:
            pkgname, ver, rev = m.group(1), m.group(2), m.group(3)
            old_pkg_name = f"{pkgname}-0.0.1_1.{arch}.xbps"
            (self.sandbox / old_pkg_name).write_bytes(b"dummy older package content")

        indexer = RepoIndexer(self.sandbox, arch=arch, signed_by="Test Autobuilder <test@local>")

        # 1. Test pruning
        pruned = indexer.prune_old_versions()
        print(f"Pruned files: {[p.name for p in pruned]}")
        self.assertTrue(any(p.name.startswith(f"{pkgname}-0.0.1") for p in pruned))
        self.assertTrue((self.sandbox / first_pkg).is_file())

        # 2. Test signing and indexing
        key_info = indexer.sign_and_index(self.key_pem)
        self.assertIsNotNone(key_info)
        print(f"Key fingerprint: {key_info.fingerprint}")
        self.assertTrue(key_info.plist_path.is_file())
        self.assertTrue((self.sandbox / f"{arch}-repodata").is_file())

        # 3. Test integrity validation (valid repo)
        validator = RepoValidator(self.sandbox, arch=arch)
        report = validator.validate_local_repository()
        print(f"Local repo validation: valid={report.is_valid}, checked={report.checked_count}, errors={report.errors}")
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.errors), 0)

        # 4. Test corruption detection: intentionally corrupt a package byte
        victim = sample_pkgs[1].name
        with open(self.sandbox / victim, "r+b") as f:
            f.seek(100)
            f.write(b"CORRUPT")

        corrupt_report = validator.validate_local_repository()
        print(f"Corrupted repo validation: valid={corrupt_report.is_valid}, errors={corrupt_report.errors}")
        self.assertFalse(corrupt_report.is_valid)
        self.assertTrue(any("Hash mismatch" in err for err in corrupt_report.errors))


if __name__ == "__main__":
    unittest.main()
