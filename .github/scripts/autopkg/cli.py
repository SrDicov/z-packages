#!/usr/bin/env python3
"""
autopkg-engine (v-auto): Definitive Void Linux Template Auto-updater, Packager, and Publisher.

Unified CLI replacing all legacy scripts with robust, high-performance, non-destructive workflows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

# Add autopkg root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from builder.bootstrap import MasterdirBootstrapper
from builder.runner import BuildRunner
from builder.selective_seed import SelectiveSeeder
from core.adapter import RepoAdapter
from core.dag import DependencyPlanner
from core.quarantine import QuarantineManager
from core.template import TemplateData, TemplateEditor
from detector.engine import DetectionEngine
from publisher.backends import PUBLISHER_BACKENDS
from publisher.indexer import RepoIndexer
from publisher.validator import RepoValidator

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("autopkg")


def compute_distfile_sha256(url: str) -> str:
    """Stream download distfile and compute SHA256 without saving full gigabytes to disk."""
    logger.info("Computing SHA256 checksum in streaming for: %s", url)
    h = hashlib.sha256()
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "autopkg-engine/1.0 (streaming hasher)")
    with urllib.request.urlopen(req, timeout=60) as resp:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def cmd_detect(args: argparse.Namespace) -> int:
    adapter = RepoAdapter(args.repo)
    engine = DetectionEngine(adapter, rules_path=args.rules, cache_dir=args.cache_dir)
    qm = QuarantineManager(args.quarantine_file) if args.check_quarantine else None

    logger.info("Detecting updates in %s (layout: %s)...", adapter.root_dir, adapter.kind.value)
    candidates = engine.check_all(args.packages if args.packages else None)

    results = []
    for c in candidates:
        if args.updates_only and not c.needs_update:
            continue

        quarantined = qm.is_quarantined(c.pkgname, c.latest_version) if qm else False
        if args.skip_quarantined and quarantined:
            logger.info("Skipping quarantined package %s (%s)", c.pkgname, c.latest_version)
            continue

        item = {
            "pkgname": c.pkgname,
            "current_version": c.current_version,
            "latest_version": c.latest_version,
            "needs_update": c.needs_update,
            "source_type": c.source_type,
            "quarantined": quarantined,
        }
        results.append(item)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n{'PACKAGE':<25} {'CURRENT':<15} {'LATEST':<15} {'UPDATE':<8} {'SOURCE':<15}")
        print("-" * 80)
        for r in results:
            up_str = "YES" if r["needs_update"] else "NO"
            print(f"{r['pkgname']:<25} {r['current_version']:<15} {r['latest_version']:<15} {up_str:<8} {r['source_type']:<15}")

    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    adapter = RepoAdapter(args.repo)
    engine = DetectionEngine(adapter, rules_path=args.rules, cache_dir=args.cache_dir)
    qm = QuarantineManager(args.quarantine_file)
    planner = DependencyPlanner(adapter)

    if getattr(args, "all", False):
        targets = args.packages if args.packages else adapter.list_packages()
        to_update = [p for p in targets if not qm.is_quarantined(p)]
    else:
        candidates = engine.check_all(args.packages if args.packages else None)
        to_update = []
        for c in candidates:
            if c.needs_update and not qm.is_quarantined(c.pkgname, c.latest_version):
                to_update.append(c.pkgname)

    # Order topologically via DAG
    ordered = planner.plan_build_order(to_update)

    target_arch = getattr(args, "arch", None)
    if target_arch:
        from builder.runner import is_arch_supported
        filtered = []
        for p in ordered:
            tmpl_path = adapter.get_template_path(p)
            if tmpl_path:
                try:
                    tmpl = TemplateEditor.parse_file(tmpl_path)
                    if not is_arch_supported(tmpl.archs, target_arch):
                        logger.info("Skipping %s in plan: arch %s not supported", p, target_arch)
                        continue
                except Exception:
                    pass
            filtered.append(p)
        ordered = filtered

    if args.limit and args.limit > 0:
        ordered = ordered[:args.limit]

    logger.info("Planned update order (%d packages): %s", len(ordered), ordered)
    if args.json:
        print(json.dumps(ordered))
    else:
        for p in ordered:
            print(p)

    return 0


def cmd_update(args: argparse.Namespace) -> int:
    adapter = RepoAdapter(args.repo)
    tmpl_path = adapter.get_template_path(args.pkgname)
    if not tmpl_path:
        logger.error("Template for package '%s' not found", args.pkgname)
        return 1

    tmpl = TemplateEditor.parse_file(tmpl_path)
    new_version = args.version
    extra_vars: dict = {}
    tag_val = None

    # If new version not explicitly passed, detect it
    if not new_version:
        engine = DetectionEngine(adapter, rules_path=args.rules)
        cand = engine.check_package(args.pkgname)
        if cand and cand.needs_update:
            new_version = cand.latest_version
            if cand.extra and cand.extra.get("tag"):
                tag_val = cand.extra["tag"]
        else:
            logger.info("Package %s is already up-to-date (%s)", args.pkgname, tmpl.version)
            return 0

    if tag_val and ("_tag" in tmpl.raw_content or any("${_tag}" in df for df in tmpl.raw_distfiles)):
        extra_vars["_tag"] = tag_val

    new_checksums: List[str] = []
    if args.checksum:
        new_checksums = [args.checksum] if isinstance(args.checksum, str) else list(args.checksum)
    elif tmpl.distfiles:
        computed: List[str] = []
        streaming_ok = True
        distfiles_to_resolve = tmpl.raw_distfiles if tmpl.raw_distfiles else tmpl.distfiles
        for df_url in distfiles_to_resolve:
            clean_url = df_url.split(">")[0].strip()
            if "${version}" in clean_url or "$version" in clean_url:
                resolved_url = clean_url.replace("${version}", new_version).replace("$version", new_version)
            else:
                resolved_url = clean_url.replace(tmpl.version, new_version)
            resolved_url = resolved_url.replace("${pkgname}", tmpl.pkgname).replace("$pkgname", tmpl.pkgname)
            if tag_val:
                resolved_url = resolved_url.replace("${_tag}", tag_val).replace("$_tag", tag_val)
            try:
                computed.append(compute_distfile_sha256(resolved_url))
            except Exception as e:
                logger.warning("Could not compute checksum via streaming for %s: %s", resolved_url, e)
                streaming_ok = False
                break

        if streaming_ok and len(computed) == len(distfiles_to_resolve):
            new_checksums = computed
        else:
            xbps_src = adapter.root_dir / "xbps-src"
            if xbps_src.is_file():
                logger.info("Attempting xbps-src xgensum fallback with new version...")
                # Write new version first so xbps-src xgensum computes hashes for the new release
                TemplateEditor.update_file(
                    tmpl_path,
                    new_version=new_version,
                    new_revision=args.revision,
                    additional_vars=extra_vars or None,
                )
                import subprocess
                res = subprocess.run([str(xbps_src), "xgensum", "-f", args.pkgname], cwd=str(adapter.root_dir), check=False)
                if res.returncode == 0:
                    updated_tmpl = TemplateEditor.parse_file(tmpl_path)
                    if updated_tmpl.checksums:
                        new_checksums = updated_tmpl.checksums
                else:
                    # Revert to original state on failure
                    TemplateEditor.update_file(
                        tmpl_path,
                        new_version=tmpl.version,
                        new_revision=tmpl.revision,
                        new_checksum=tmpl.checksums,
                    )

    if tmpl.distfiles and not new_checksums:
        logger.error("Failed to obtain valid SHA256 checksum for %s (%s). Aborting template update.", args.pkgname, new_version)
        return 1

    if new_checksums and tmpl.checksums and new_version != tmpl.version and new_checksums == tmpl.checksums:
        logger.info("Calculated checksum for %s (%s) is identical to existing checksum. Package is already up-to-date with this artifact.", args.pkgname, new_version)
        return 0

    # Update template cleanly with validated checksums
    TemplateEditor.update_file(
        tmpl_path,
        new_version=new_version,
        new_revision=args.revision,
        new_checksum=new_checksums if new_checksums else None,
        additional_vars=extra_vars or None,
    )
    logger.info("Successfully updated %s to version %s (revision %d) with %d checksum(s)", args.pkgname, new_version, args.revision, len(new_checksums))

    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    adapter = RepoAdapter(args.repo)
    seeder = SelectiveSeeder(adapter, target_dir=args.target_dir, arch=args.arch)

    if not args.packages:
        logger.error("Must specify at least one target package to seed dependencies for")
        return 1

    downloaded = seeder.seed_from_github_release(
        github_repo=args.from_github,
        target_packages=args.packages,
        token=args.token,
    )
    logger.info("Seeded %d dependency packages into %s", len(downloaded), seeder.binpkgs_arch_dir)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    adapter = RepoAdapter(args.repo)
    qm = QuarantineManager(args.quarantine_file)
    runner = BuildRunner(adapter, quarantine_mgr=qm, logs_dir=args.logs_dir)
    planner = DependencyPlanner(adapter)

    targets = planner.plan_build_order(args.packages)
    all_success = True

    for pkg in targets:
        logger.info("Building package: %s (%s)", pkg, args.arch)
        res = runner.build_package(pkg, arch=args.arch, force_clean=args.force)
        if not res.success:
            logger.error("Build FAILED for %s (exit %d): %s", pkg, res.returncode, res.error_summary)
            all_success = False
            if not args.keep_going:
                return 1
        else:
            logger.info("Build SUCCEEDED for %s in %.2fs", pkg, res.duration_seconds)

    return 0 if all_success else 1


def cmd_publish(args: argparse.Namespace) -> int:
    binpkgs_dir = Path(args.binpkgs_dir).resolve()

    if not getattr(args, "upload_only", False):
        indexer = RepoIndexer(binpkgs_dir, arch=args.arch, signed_by=args.signed_by)

        # 1. Prune superseded versions
        if args.prune:
            pruned = indexer.prune_old_versions()
            logger.info("Pruned %d older package versions", len(pruned))

        # 2. Sign and index
        privkey = args.privkey or os.environ.get("XBPS_PRIVATE_KEY")
        if not privkey:
            logger.error("Missing private key for signing (use --privkey or XBPS_PRIVATE_KEY)")
            return 1

        key_info = indexer.sign_and_index(privkey)
        if not key_info:
            logger.error("Signing or indexing failed")
            return 1

        # 3. Validate local repository
        validator = RepoValidator(binpkgs_dir, arch=args.arch)
        report = validator.validate_local_repository()
        if not report.is_valid:
            logger.error("Repository validation failed: %s", report.errors)
            if not args.force:
                return 1

    # 4. Dispatch to publisher backend
    backend_cls = PUBLISHER_BACKENDS.get(args.backend)
    if not backend_cls:
        logger.error("Unknown publisher backend: %s", args.backend)
        return 1

    backend = backend_cls()
    options = {}
    if args.backend in ("github", "github_release"):
        options["repo"] = args.dest
        options["tag"] = args.tag or f"rolling-{args.arch}"
    elif args.backend in ("git_branch", "forgejo"):
        options["branch"] = args.branch
        options["repo_url"] = args.dest
    elif args.backend in ("local", "rsync"):
        options["destination"] = args.dest

    res = backend.publish(binpkgs_dir, arch=args.arch, options=options)
    if not res.success:
        logger.error("Publishing failed: %s", res.error_message)
        return 1

    logger.info("Publishing completed successfully via %s!", args.backend)
    return 0


def cmd_quarantine(args: argparse.Namespace) -> int:
    qm = QuarantineManager(args.quarantine_file)
    if args.action == "list":
        records = qm.list_quarantined()
        print(f"\n{'PACKAGE':<25} {'FAILED VER':<15} {'COUNT':<8} {'BACKOFF UNTIL':<25}")
        print("-" * 75)
        for r in records:
            import datetime
            dt = datetime.datetime.fromtimestamp(r.backoff_until).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{r.pkgname:<25} {r.last_failed_version:<15} {r.failure_count:<8} {dt:<25}")
    elif args.action == "clear":
        if args.pkgname:
            qm.record_success(args.pkgname)
            logger.info("Cleared quarantine for %s", args.pkgname)
        else:
            qm.records = {}
            qm.save()
            logger.info("Cleared all quarantined packages")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="autopkg",
        description="autopkg-engine (v-auto): Unified Void Linux Auto-updater, Packager, and Publisher",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # detect
    p_detect = subparsers.add_parser("detect", help="Detect upstream package updates")
    p_detect.add_argument("--repo", default=".", help="Repository root path")
    p_detect.add_argument("--rules", default=None, help="Path to updates.rules.yml")
    p_detect.add_argument("--cache-dir", default=".cache", help="Cache directory")
    p_detect.add_argument("--quarantine-file", default="quarantine.json", help="Quarantine state file")
    p_detect.add_argument("--check-quarantine", action="store_true", help="Mark quarantined items")
    p_detect.add_argument("--skip-quarantined", action="store_true", help="Omit quarantined items")
    p_detect.add_argument("--updates-only", action="store_true", help="Only show packages needing updates")
    p_detect.add_argument("--json", action="store_true", help="Output JSON")
    p_detect.add_argument("packages", nargs="*", help="Optional package subset")
    p_detect.set_defaults(func=cmd_detect)

    # plan
    p_plan = subparsers.add_parser("plan", help="Plan topological update order with DAG & quarantine")
    p_plan.add_argument("--repo", default=".", help="Repository root path")
    p_plan.add_argument("--rules", default=None, help="Path to updates.rules.yml")
    p_plan.add_argument("--cache-dir", default=".cache", help="Cache directory")
    p_plan.add_argument("--quarantine-file", default="quarantine.json", help="Quarantine state file")
    p_plan.add_argument("--arch", default=None, help="Target architecture (e.g. x86_64, x86_64-musl)")
    p_plan.add_argument("--all", "-a", action="store_true", help="Plan for all specified packages regardless of update status")
    p_plan.add_argument("--limit", type=int, default=5, help="Max batch size (0 for unlimited)")
    p_plan.add_argument("--json", action="store_true", help="Output JSON list")
    p_plan.add_argument("packages", nargs="*", help="Optional package subset")
    p_plan.set_defaults(func=cmd_plan)

    # update
    p_update = subparsers.add_parser("update", help="Update template version/checksum without losing comments")
    p_update.add_argument("--repo", default=".", help="Repository root path")
    p_update.add_argument("--rules", default=None, help="Path to updates.rules.yml")
    p_update.add_argument("--version", default=None, help="Target version (auto-detected if omitted)")
    p_update.add_argument("--revision", type=int, default=1, help="New revision")
    p_update.add_argument("--checksum", default=None, help="New SHA256 checksum")
    p_update.add_argument("--use-xgensum", action="store_true", help="Run xbps-src xgensum fallback")
    p_update.add_argument("pkgname", help="Package to update")
    p_update.set_defaults(func=cmd_update)

    # seed
    p_seed = subparsers.add_parser("seed", help="Surgically seed only needed dependencies to save disk/bandwidth")
    p_seed.add_argument("--repo", default=".", help="Repository root path")
    p_seed.add_argument("--from-github", required=True, help="GitHub repo containing releases (owner/repo)")
    p_seed.add_argument("--target-dir", default="hostdir/binpkgs", help="Directory to save .xbps assets")
    p_seed.add_argument("--arch", default="x86_64", help="Architecture")
    p_seed.add_argument("--token", default=None, help="GitHub token")
    p_seed.add_argument("packages", nargs="+", help="Target packages to resolve dependencies for")
    p_seed.set_defaults(func=cmd_seed)

    # build
    p_build = subparsers.add_parser("build", help="Compile packages with quarantine tracking")
    p_build.add_argument("--repo", default=".", help="Repository root path")
    p_build.add_argument("--arch", default="x86_64", help="Architecture (x86_64 or x86_64-musl)")
    p_build.add_argument("--quarantine-file", default="quarantine.json", help="Quarantine state file")
    p_build.add_argument("--logs-dir", default="build_logs", help="Build logs directory")
    p_build.add_argument("--force", action="store_true", help="Force rebuild (-f)")
    p_build.add_argument("--keep-going", action="store_true", help="Continue on errors")
    p_build.add_argument("packages", nargs="+", help="Packages to build")
    p_build.set_defaults(func=cmd_build)

    # publish
    p_publish = subparsers.add_parser("publish", help="Sign, index, and publish binary repository")
    p_publish.add_argument("--binpkgs-dir", required=True, help="Path to binpkgs directory containing .xbps")
    p_publish.add_argument("--arch", default="x86_64", help="Architecture")
    p_publish.add_argument("--privkey", default=None, help="Private key PEM path or env")
    p_publish.add_argument("--signed-by", default="Void Community Autobuilder", help="Signed-by identity string")
    p_publish.add_argument("--upload-only", action="store_true", help="Skip signing/indexing and only upload existing indexed artifacts to backend")
    p_publish.add_argument("--backend", choices=list(PUBLISHER_BACKENDS.keys()), default="github", help="Publishing backend")
    p_publish.add_argument("--dest", required=True, help="Destination (owner/repo, branch, URL or local path)")
    p_publish.add_argument("--tag", default=None, help="Release tag (for GitHub Releases)")
    p_publish.add_argument("--branch", default="binpkgs", help="Branch name (for Forgejo/Git)")
    p_publish.add_argument("--prune", action="store_true", default=True, help="Prune older versions of packages")
    p_publish.add_argument("--force", action="store_true", help="Proceed despite warnings")
    p_publish.set_defaults(func=cmd_publish)

    # quarantine
    p_quar = subparsers.add_parser("quarantine", help="Inspect and manage quarantine")
    p_quar.add_argument("action", choices=["list", "clear"], help="Action to perform")
    p_quar.add_argument("--quarantine-file", default="quarantine.json", help="Quarantine state file")
    p_quar.add_argument("pkgname", nargs="?", default=None, help="Optional package to clear")
    p_quar.set_defaults(func=cmd_quarantine)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
