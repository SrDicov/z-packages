"""
Stress tests for DAG topological planner and Quarantine manager.
"""

import tempfile
import time
import unittest
from pathlib import Path

from core.adapter import RepoAdapter
from core.dag import DependencyPlanner
from core.quarantine import QuarantineManager


class TestStressDAGAndQuarantine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_subpackage_function_resolution_in_dag(self):
        """
        If package 'app' depends on 'libfoo-devel', and 'libfoo' defines
        'libfoo-devel_package()', 'libfoo' must be sorted before 'app'.
        """
        for p in ["libfoo", "app"]:
            (self.root / "srcpkgs" / p).mkdir(parents=True)

        (self.root / "srcpkgs" / "libfoo" / "template").write_text(
            """pkgname=libfoo
version=1.0.0
revision=1

libfoo-devel_package() {
    depends="${sourcepkg}>=${version}_${revision}"
}
"""
        )

        (self.root / "srcpkgs" / "app" / "template").write_text(
            """pkgname=app
version=1.0.0
revision=1
makedepends="libfoo-devel>=1.0.0"
"""
        )

        adapter = RepoAdapter(self.root)
        planner = DependencyPlanner(adapter)
        ordered = planner.plan_build_order(["app", "libfoo"])
        self.assertEqual(ordered, ["libfoo", "app"])

    def test_circular_dependency_graceful_breaker(self):
        """
        A depends on B, B depends on A.
        Must resolve without hanging and return both packages.
        """
        for p in ["cycle-a", "cycle-b"]:
            (self.root / "srcpkgs" / p).mkdir(parents=True)

        (self.root / "srcpkgs" / "cycle-a" / "template").write_text(
            'pkgname=cycle-a\nversion=1.0\nrevision=1\nmakedepends="cycle-b"\n'
        )
        (self.root / "srcpkgs" / "cycle-b" / "template").write_text(
            'pkgname=cycle-b\nversion=1.0\nrevision=1\nmakedepends="cycle-a"\n'
        )

        adapter = RepoAdapter(self.root)
        planner = DependencyPlanner(adapter)
        ordered = planner.plan_build_order(["cycle-a", "cycle-b"])
        self.assertEqual(set(ordered), {"cycle-a", "cycle-b"})
        self.assertEqual(len(ordered), 2)

    def test_dag_on_real_repositories_speed(self):
        """
        Plan build order across 100 packages from z-packages in under 1 second.
        """
        z_adapter = RepoAdapter("/home/dicov/Proyectos/z-packages")
        planner = DependencyPlanner(z_adapter)
        pkgs = z_adapter.list_packages()[:80]

        start_time = time.time()
        ordered = planner.plan_build_order(pkgs)
        elapsed = time.time() - start_time

        self.assertEqual(len(ordered), len(pkgs))
        self.assertLess(elapsed, 1.0, f"DAG took too long: {elapsed:.2f}s")
        print(f"DAG sorted {len(pkgs)} packages in {elapsed*1000:.2f}ms")

    def test_quarantine_exact_backoff_schedule(self):
        state_file = self.root / "quarantine.json"
        qm = QuarantineManager(state_file)

        now = time.time()
        # 1st failure: 6h
        qm.record_failure("testpkg", "1.0", "error 1")
        rec = qm.records["testpkg"]
        self.assertEqual(rec.failure_count, 1)
        self.assertAlmostEqual(rec.backoff_until - now, 6 * 3600, delta=10)

        # 2nd failure: 24h
        qm.record_failure("testpkg", "1.0", "error 2")
        rec = qm.records["testpkg"]
        self.assertEqual(rec.failure_count, 2)
        self.assertAlmostEqual(rec.backoff_until - now, 24 * 3600, delta=10)

        # 3rd failure: 72h
        qm.record_failure("testpkg", "1.0", "error 3")
        rec = qm.records["testpkg"]
        self.assertEqual(rec.failure_count, 3)
        self.assertAlmostEqual(rec.backoff_until - now, 72 * 3600, delta=10)

        # 4th failure: 7d
        qm.record_failure("testpkg", "1.0", "error 4")
        rec = qm.records["testpkg"]
        self.assertEqual(rec.failure_count, 4)
        self.assertAlmostEqual(rec.backoff_until - now, 7 * 24 * 3600, delta=10)

        # Verify disk persistence
        qm2 = QuarantineManager(state_file)
        self.assertIn("testpkg", qm2.records)
        self.assertEqual(qm2.records["testpkg"].failure_count, 4)

        # Immediate un-quarantine for new version
        self.assertTrue(qm2.is_quarantined("testpkg", "1.0"))
        self.assertFalse(qm2.is_quarantined("testpkg", "1.1"))


if __name__ == "__main__":
    unittest.main()
