"""
Full 100% Universe Template Parsing Stress Test.
Audits all templates in z-packages, neko-pkgs, repository, and void-packages.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, "/home/dicov/Proyectos/autopkg")
from core.adapter import RepoAdapter
from core.template import TemplateEditor


def audit_repository(repo_path: str, name: str):
    print(f"\n==========================================")
    print(f"Auditing 100% of templates in: {name} ({repo_path})")
    print(f"==========================================")

    adapter = RepoAdapter(repo_path)
    # Get all template paths
    template_files = []
    root = Path(repo_path).resolve()
    if (root / ".git").is_dir():
        import subprocess
        # Use git ls-files for speed if available
        if adapter.kind.value == "srcpkgs":
            cmd = ["git", "ls-files", "srcpkgs/*/template"]
        elif adapter.kind.value == "pkgs":
            cmd = ["git", "ls-files", "pkgs/*/template"]
        elif adapter.kind.value == "categorized":
            cmd = ["git", "ls-files", "**/template"]
        else:
            cmd = ["git", "ls-files", "*/template"]
        res = subprocess.run(cmd, cwd=str(root), stdout=subprocess.PIPE, text=True, check=False)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line:
                template_files.append(root / line)

    if not template_files:
        for pkg in adapter.list_packages(include_subpackages=False):
            tp = adapter.get_template_path(pkg)
            if tp:
                template_files.append(tp)

    total = len(template_files)
    print(f"Total template files located: {total}")

    success_count = 0
    fail_count = 0
    missing_fields = []
    errors = []

    start_time = time.time()

    for idx, tmpl_path in enumerate(template_files, 1):
        try:
            data = TemplateEditor.parse_file(tmpl_path)
            # Verify fundamental fields
            if not data.pkgname:
                missing_fields.append((tmpl_path.name, "missing pkgname"))
            if not data.version:
                missing_fields.append((data.pkgname or tmpl_path.parent.name, "missing version"))
            if data.revision < 0:
                missing_fields.append((data.pkgname, "invalid revision"))

            success_count += 1
        except Exception as e:
            fail_count += 1
            errors.append((str(tmpl_path), str(e)))

        if idx % 2000 == 0 or idx == total:
            elapsed = time.time() - start_time
            print(f"Progress: {idx}/{total} ({idx/total*100:.1f}%) in {elapsed:.2f}s...")

    print(f"\nResults for {name}:")
    print(f"  Total Processed: {total}")
    print(f"  Successfully Parsed: {success_count}")
    print(f"  Exceptions/Crashes: {fail_count}")
    print(f"  Missing fundamental fields: {len(missing_fields)}")

    if errors:
        print("First 5 errors:")
        for ep, err in errors[:5]:
            print(f"    {ep}: {err}")

    if missing_fields:
        print("First 10 missing fundamental fields:")
        for pkg, issue in missing_fields[:10]:
            print(f"    {pkg}: {issue}")

    return total, success_count, fail_count, missing_fields


def main():
    grand_total = 0
    grand_success = 0
    grand_fails = 0

    repos = [
        ("/home/dicov/Proyectos/z-packages", "z-packages"),
        ("/home/dicov/Proyectos/neko-pkgs", "neko-pkgs"),
        ("/home/dicov/Proyectos/repository", "repository"),
        ("/home/dicov/Proyectos/vup", "vup"),
        ("/home/dicov/Proyectos/void-packages", "void-packages"),
    ]

    for path, name in repos:
        total, succ, fails, issues = audit_repository(path, name)
        grand_total += total
        grand_success += succ
        grand_fails += fails

    print("\n" + "=" * 50)
    print("GRAND TOTAL AUDIT SUMMARY:")
    print(f"  Templates Audited: {grand_total}")
    print(f"  Successfully Parsed: {grand_success}")
    print(f"  Failed: {grand_fails}")
    print("=" * 50)

    assert grand_fails == 0, f"{grand_fails} templates failed to parse!"


class TestStressTemplates(unittest.TestCase):
    def test_parse_all_templates(self):
        main()


if __name__ == "__main__":
    unittest.main()
