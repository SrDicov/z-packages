"""
Stress test for 100% of native 'update' files in void-packages.
"""

import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, "/home/dicov/Proyectos/autopkg")
from detector.void_update import VoidUpdateParser


def test_all_update_files():
    void_repo = Path("/home/dicov/Proyectos/void-packages")
    res = subprocess.run(
        ["git", "ls-files", "srcpkgs/*/update"],
        cwd=str(void_repo),
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    files = [void_repo / line.strip() for line in res.stdout.splitlines() if line.strip()]

    total = len(files)
    print(f"Total update files found in void-packages: {total}")

    success_count = 0
    fail_count = 0
    parsed_patterns = 0
    parsed_sites = 0
    parsed_single_dirs = 0
    parsed_ignores = 0

    start_time = time.time()
    for idx, f in enumerate(files, 1):
        try:
            cfg = VoidUpdateParser.parse_file(f)
            if cfg:
                success_count += 1
                if cfg.pattern:
                    parsed_patterns += 1
                if cfg.site or cfg.url:
                    parsed_sites += 1
                if cfg.single_directory:
                    parsed_single_dirs += 1
                if cfg.ignore:
                    parsed_ignores += 1
            else:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            print(f"Error parsing {f}: {e}")

    elapsed = time.time() - start_time
    print(f"\nUpdate Files Stress Audit Results:")
    print(f"  Total Processed: {total}")
    print(f"  Successfully Parsed: {success_count}")
    print(f"  Failures/Crashes: {fail_count}")
    print(f"  Entries with pattern: {parsed_patterns}")
    print(f"  Entries with site/url: {parsed_sites}")
    print(f"  Entries with ignore: {parsed_ignores}")
    print(f"  Entries with single_directory: {parsed_single_dirs}")
    print(f"  Elapsed: {elapsed:.2f}s")

    assert fail_count == 0, f"Found {fail_count} failures in update files!"
    print("SUCCESS: 100% of void-packages update files parsed cleanly with 0 errors!")


class TestStressUpdates(unittest.TestCase):
    def test_parse_all_update_files(self):
        test_all_update_files()


if __name__ == "__main__":
    unittest.main()
