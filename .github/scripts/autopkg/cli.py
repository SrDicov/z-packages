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
from builder.workspace import prepare_build_tree
from core.adapter import RepoAdapter
from core.dag import DependencyPlanner
from core.quarantine import QuarantineManager
from core.template import TemplateData, TemplateEditor
from detector.backends.base import get_github_token, verify_distfile_url
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


def fetch_repodata_from_github(repo: str, arch: str = "x86_64", cache_dir: Optional[Path] = None) -> Optional[dict]:
    """Download {arch}-repodata and parse published assets from GitHub release 'stable'."""
    import tempfile
    token = get_github_token()
    headers = {"User-Agent": "autopkg-engine/1.0", "Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_url = f"https://api.github.com/repos/{repo}/releases/tags/stable"
    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning("Could not fetch release 'stable' for %s: %s", repo, e)
        return None

    index_dict: dict = {}

    # 1. Parse all physical .xbps assets in the release
    asset_regex = re.compile(rf'^(.+)-([0-9][a-zA-Z0-9_\.]*_\d+)\.{re.escape(arch)}\.xbps$')
    for asset in data.get("assets", []):
        aname = asset.get("name", "")
        m = asset_regex.match(aname)
        if m:
            pkg, ver_rev = m.group(1), m.group(2)
            index_dict[pkg] = {"pkgver": f"{pkg}-{ver_rev}"}

    # 2. Overlay index.plist from {arch}-repodata if available
    target_asset = f"{arch}-repodata"
    download_url = None
    for asset in data.get("assets", []):
        if asset.get("name") == target_asset:
            download_url = asset.get("browser_download_url")
            break

    if download_url:
        temp_dir = cache_dir or Path(tempfile.gettempdir())
        local_repodata = temp_dir / f"{repo.replace('/', '_')}_{target_asset}"
        req_dl = urllib.request.Request(download_url, headers={"User-Agent": "autopkg-engine/1.0"})
        try:
            with urllib.request.urlopen(req_dl, timeout=30) as resp, open(local_repodata, "wb") as f:
                f.write(resp.read())
            plist_data = RepoValidator._extract_index_plist(local_repodata)
            if plist_data:
                index_dict.update(plist_data)
        except Exception as e:
            logger.warning("Could not download or parse %s: %s", download_url, e)

    return index_dict if index_dict else None


def cmd_plan(args: argparse.Namespace) -> int:
    adapter = RepoAdapter(args.repo)
    engine = DetectionEngine(adapter, rules_path=args.rules, cache_dir=args.cache_dir)
    qm = QuarantineManager(args.quarantine_file)
    planner = DependencyPlanner(adapter)

    to_update: List[str] = []

    # Check if state-diff comparison against binary repo or repodata is requested
    check_binrepo = getattr(args, "check_binrepo", None)
    repodata_path = getattr(args, "repodata", None)
    index_data: Optional[dict] = None

    if check_binrepo:
        target_arch = getattr(args, "arch", "x86_64") or "x86_64"
        logger.info("Fetching binary repodata from %s (arch: %s) for state-diff planning...", check_binrepo, target_arch)
        index_data = fetch_repodata_from_github(check_binrepo, arch=target_arch)
    elif repodata_path:
        p = Path(repodata_path).resolve()
        if p.is_file():
            index_data = RepoValidator._extract_index_plist(p)

    if index_data is not None:
        targets = args.packages if args.packages else adapter.list_packages()
        for p in targets:
            if qm.is_quarantined(p):
                continue
            tmpl_path = adapter.get_template_path(p)
            if not tmpl_path:
                continue
            try:
                tmpl = TemplateEditor.parse_file(tmpl_path)
            except Exception:
                continue
            local_ver = f"{tmpl.version}_{tmpl.revision}"
            remote_entry = index_data.get(p)
            if not remote_entry:
                logger.info("State-diff: %s not in remote repodata, queued for build", p)
                to_update.append(p)
            else:
                remote_pkgver = remote_entry.get("pkgver", "")
                remote_ver = remote_pkgver[len(p) + 1:] if remote_pkgver.startswith(f"{p}-") else remote_pkgver
                if DetectionEngine.compare_versions(local_ver, remote_ver, pkgname=p) > 0:
                    logger.info("State-diff: %s local (%s) > remote (%s), queued for build", p, local_ver, remote_ver)
                    to_update.append(p)
    elif getattr(args, "all", False):
        targets = args.packages if args.packages else adapter.list_packages()
        to_update = [p for p in targets if not qm.is_quarantined(p)]
    else:
        candidates = engine.check_all(args.packages if args.packages else None)
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
    if not re.match(r'^[a-zA-Z0-9_\+\-\.]+$', args.pkgname):
        logger.error("Invalid package name '%s'", args.pkgname)
        return 1
    adapter = RepoAdapter(args.repo)
    tmpl_path = adapter.get_template_path(args.pkgname)
    if not tmpl_path:
        logger.error("Template for package '%s' not found", args.pkgname)
        return 1

    tmpl = TemplateEditor.parse_file(tmpl_path)
    new_version = args.version
    effective_revision = args.revision
    extra_vars: dict = {}
    tag_val = None

    # If new version not explicitly passed, detect it
    if not new_version:
        engine = DetectionEngine(adapter, rules_path=args.rules)
        cand = engine.check_package(args.pkgname)
        if cand and cand.needs_update:
            if cand.extra and cand.extra.get("commit"):
                # Commit-pinned template: move the pin and bump revision, keep version.
                commit_var = cand.extra.get("commit_var")
                if not commit_var:
                    logger.error("Commit update for %s but no pin variable found", args.pkgname)
                    return 1
                extra_vars[commit_var] = cand.extra["commit"]
                effective_revision = tmpl.revision + 1
                new_version = tmpl.version
            else:
                new_version = cand.latest_version
                if cand.extra and cand.extra.get("tag"):
                    tag_val = cand.extra["tag"]
                if cand.extra and cand.extra.get("named_vars"):
                    extra_vars.update(cand.extra["named_vars"])
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
        for i, df_url in enumerate(distfiles_to_resolve):
            clean_url = df_url.split(">")[0].strip()
            vars_dict = {
                **TemplateEditor.DEFAULT_SITE_VARS,
                **extra_vars,
                "pkgname": tmpl.pkgname,
                "version": new_version,
            }
            if tag_val:
                vars_dict["_tag"] = tag_val
            resolved_url = TemplateEditor.expand_bash_vars(clean_url, vars_dict)

            # If unresolved bash variables remain, fallback to expanded distfiles
            if "${" in resolved_url and i < len(tmpl.distfiles):
                fallback_clean = tmpl.distfiles[i].split(">")[0].strip()
                resolved_url = fallback_clean.replace(tmpl.version, new_version).replace("${pkgname}", tmpl.pkgname)
                for k, v in extra_vars.items():
                    resolved_url = resolved_url.replace("${" + k + "}", v).replace("$" + k, v)
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
                    new_revision=effective_revision,
                    additional_vars=extra_vars or None,
                    allow_same_checksum=True,
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
                        allow_same_checksum=True,
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
        new_revision=effective_revision,
        new_checksum=new_checksums if new_checksums else None,
        additional_vars=extra_vars or None,
    )
    logger.info("Successfully updated %s to version %s (revision %d) with %d checksum(s)", args.pkgname, new_version, effective_revision, len(new_checksums))

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


def cmd_prepare_tree(args: argparse.Namespace) -> int:
    try:
        tree = prepare_build_tree(
            repo_root=args.repo,
            dest=args.dest,
            clone_url=args.clone_url,
            ref=args.ref,
            force=args.force,
        )
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    print(tree)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    for pkg in (args.packages or []):
        if not re.match(r'^[a-zA-Z0-9_\+\-\.]+$', pkg):
            logger.error("Invalid package name '%s'", pkg)
            return 1
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


def cmd_doctor(args: argparse.Namespace) -> int:
    import shutil
    import subprocess
    from concurrent.futures import ThreadPoolExecutor, as_completed

    adapter = RepoAdapter(args.repo)
    packages = args.packages if getattr(args, "packages", None) else adapter.list_packages()

    logger.info("Running autopkg doctor across %d packages in %s...", len(packages), adapter.root_dir)

    repodata_index: Optional[dict] = None
    if getattr(args, "check_binrepo", None):
        target_arch = getattr(args, "arch", "x86_64") or "x86_64"
        logger.info("Fetching remote binary repodata from %s (arch: %s)...", args.check_binrepo, target_arch)
        repodata_index = fetch_repodata_from_github(args.check_binrepo, arch=target_arch)

    xlint_bin = shutil.which("xlint")
    check_urls = getattr(args, "check_urls", False)

    def diagnose_package(pkgname: str) -> dict:
        issues: List[str] = []
        warnings: List[str] = []
        tmpl_path = adapter.get_template_path(pkgname)
        if not tmpl_path or not tmpl_path.is_file():
            return {"pkgname": pkgname, "status": "FAIL", "issues": ["Template file not found"], "warnings": []}

        # 1. bash -n syntax check
        res_bash = subprocess.run(["bash", "-n", str(tmpl_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res_bash.returncode != 0:
            issues.append(f"Syntax error (bash -n): {res_bash.stderr.strip()}")

        # 2. xlint check (Strict enforcement: 0 errors allowed)
        if xlint_bin:
            res_xlint = subprocess.run([xlint_bin, str(tmpl_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if res_xlint.returncode != 0:
                out = (res_xlint.stderr + "\n" + res_xlint.stdout).strip()
                if out:
                    issues.append(f"xlint: {out}")

        # 3. Parse template and validate structure
        try:
            tmpl = TemplateEditor.parse_file(tmpl_path)
            is_valid, tmpl_errors, tmpl_warnings = TemplateEditor.validate_template(tmpl)
            if not is_valid:
                issues.extend(tmpl_errors)
            warnings.extend(tmpl_warnings)

            # 4. Check unbound secondary variables in raw_distfiles
            assigned_vars = set(re.findall(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)=', tmpl.raw_content, re.MULTILINE))
            assigned_vars.update(TemplateEditor.DEFAULT_SITE_VARS.keys())
            assigned_vars.update(["pkgname", "version", "revision", "arch", "XBPS_TARGET_MACHINE"])
            for df in tmpl.raw_distfiles:
                var_refs = re.findall(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)[^}]*\}|\$([a-zA-Z_][a-zA-Z0-9_]*)', df)
                for v1, v2 in var_refs:
                    v = v1 or v2
                    if v and v not in assigned_vars:
                        issues.append(f"Unbound variable '${v}' referenced in raw_distfiles")

            # 5. Checksum integrity
            if tmpl.distfiles:
                if not tmpl.checksums:
                    issues.append("Template has distfiles but checksums list is empty")
                elif len(tmpl.checksums) != len(tmpl.distfiles):
                    issues.append(f"Checksum mismatch: {len(tmpl.checksums)} checksums vs {len(tmpl.distfiles)} distfiles")
                for c in tmpl.checksums:
                    clean_c = c.strip()
                    if not re.match(r'^[0-9a-fA-F]{64}$', clean_c):
                        issues.append(f"Invalid sha256 checksum format: '{clean_c}'")

            # 6. Check URLs if requested
            if check_urls and tmpl.distfiles:
                for df in tmpl.distfiles:
                    clean_url = df.split(">")[0].strip()
                    if not verify_distfile_url(clean_url, timeout=12):
                        issues.append(f"Distfile URL unreachable (non-200/404): {clean_url}")

            # 7. Check binary repo state
            if repodata_index is not None:
                local_ver = f"{tmpl.version}_{tmpl.revision}"
                remote_entry = repodata_index.get(pkgname)
                if not remote_entry:
                    warnings.append(f"Not published in binary repo (local: {local_ver})")
                else:
                    remote_pkgver = remote_entry.get("pkgver", "")
                    remote_ver = remote_pkgver[len(pkgname) + 1:] if remote_pkgver.startswith(f"{pkgname}-") else remote_pkgver
                    cmp = DetectionEngine.compare_versions(local_ver, remote_ver, pkgname=pkgname)
                    if cmp > 0:
                        warnings.append(f"Binary repo outdated: local {local_ver} > remote {remote_ver}")
                    elif cmp < 0:
                        issues.append(f"Binary repo ahead of template: remote {remote_ver} > local {local_ver}")

        except Exception as e:
            issues.append(f"Parser exception: {e}")

        status = "FAIL" if issues else ("WARN" if warnings else "OK")
        return {
            "pkgname": pkgname,
            "status": status,
            "issues": issues,
            "warnings": warnings,
        }

    results = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        future_map = {executor.submit(diagnose_package, p): p for p in packages}
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda r: r["pkgname"])

    print("\n" + "=" * 90)
    print(f"{'PACKAGE':<30} {'STATUS':<10} {'DETAILS'}")
    print("=" * 90)
    fail_count = 0
    warn_count = 0
    ok_count = 0

    for r in results:
        status = r["status"]
        if status == "FAIL":
            fail_count += 1
            details = "; ".join(r["issues"])
            print(f"\033[91m{r['pkgname']:<30} {status:<10} {details}\033[0m")
        elif status == "WARN":
            warn_count += 1
            details = "; ".join(r["warnings"])
            print(f"\033[93m{r['pkgname']:<30} {status:<10} {details}\033[0m")
        else:
            ok_count += 1
            print(f"\033[92m{r['pkgname']:<30} {status:<10} Clean\033[0m")

    print("=" * 90)
    print(f"Doctor Summary: {ok_count} OK, {warn_count} Warnings, {fail_count} Failed (Total: {len(results)})")
    print("=" * 90 + "\n")

    return 1 if fail_count > 0 else 0


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
    p_plan.add_argument("--check-binrepo", default=None, help="Compare against GitHub Release stable in owner/repo")
    p_plan.add_argument("--repodata", default=None, help="Path to local repodata archive")
    p_plan.add_argument("--all", "-a", action="store_true", help="Plan for all specified packages regardless of update status")
    p_plan.add_argument("--limit", type=int, default=5, help="Max batch size (0 for unlimited)")
    p_plan.add_argument("--json", action="store_true", help="Output JSON list")
    p_plan.add_argument("packages", nargs="*", help="Optional package subset")
    p_plan.set_defaults(func=cmd_plan)

    # doctor
    p_doctor = subparsers.add_parser("doctor", help="Run comprehensive linting, syntax, unbound variable, URL and repo checks")
    p_doctor.add_argument("--repo", default=".", help="Repository root path")
    p_doctor.add_argument("--check-urls", action="store_true", help="Verify HTTP 200 on all distfiles")
    p_doctor.add_argument("--check-binrepo", default=None, help="Compare with remote GitHub binary repo (owner/repo)")
    p_doctor.add_argument("--arch", default="x86_64", help="Architecture for binary repo comparison")
    p_doctor.add_argument("packages", nargs="*", help="Optional package subset")
    p_doctor.set_defaults(func=cmd_doctor)

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

    # prepare-tree
    p_tree = subparsers.add_parser(
        "prepare-tree",
        help="Compose a buildable void-packages tree for template-only repositories",
    )
    p_tree.add_argument("--repo", default=".", help="Repository root path")
    p_tree.add_argument("--dest", required=True, help="Destination for the void-packages checkout")
    p_tree.add_argument("--clone-url", default="https://github.com/void-linux/void-packages.git", help="void-packages clone URL")
    p_tree.add_argument("--ref", default=None, help="Optional branch/tag to clone")
    p_tree.add_argument("--force", action="store_true", help="Recreate the destination tree")
    p_tree.set_defaults(func=cmd_prepare_tree)

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
