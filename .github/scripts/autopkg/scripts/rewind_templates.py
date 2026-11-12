#!/usr/bin/env python3
"""
Stress Test Rewinder for Void Linux templates.
Rewinds templates in z-packages by up to 4 versions behind upstream/current,
ensuring genuine historical versions and matching valid checksums.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapter import RepoAdapter
from core.template import TemplateEditor
from detector.backends.git_tags import GitTagsBackend
from detector.rules import RulesLoader
from cli import compute_distfile_sha256


def get_git_history_versions(repo_path: Path, pkg: str) -> List[Tuple[str, str, str]]:
    """Return list of (version, checksum, commit_hash) from git history in reverse chronological order."""
    cmd = ['git', '-C', str(repo_path), 'log', '--follow', '--format=%H', '--', f'srcpkgs/{pkg}/template']
    try:
        commits = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).splitlines()
    except Exception:
        return []

    versions: List[Tuple[str, str, str]] = []
    seen = set()
    for c in commits:
        try:
            show = subprocess.check_output(
                ['git', '-C', str(repo_path), 'show', f'{c}:srcpkgs/{pkg}/template'],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            tmpl = TemplateEditor.parse_string(show)
            if tmpl.version and tmpl.version not in seen:
                seen.add(tmpl.version)
                chk = tmpl.checksums[0] if tmpl.checksums else ""
                versions.append((tmpl.version, chk, c))
        except Exception:
            continue
    return versions


def rewind_repository(repo_path: Path, max_versions_back: int = 4) -> None:
    adapter = RepoAdapter(repo_path)
    rules_file = repo_path / ".github" / "scripts" / "autopkg" / "config" / "updates.rules.yml"
    if not rules_file.is_file():
        rules_file = Path(__file__).resolve().parent.parent / "config" / "updates.rules.yml"

    rules = RulesLoader.load_file(rules_file) if rules_file.is_file() else None
    excluded = set(rules.exclude) if rules else set()

    pkgs = sorted(adapter.list_packages())
    print(f"[*] Scanning {len(pkgs)} packages in {repo_path}...")

    rewound_git = 0
    rewound_upstream = 0
    skipped_exclude = 0
    untouched = 0

    for pkg in pkgs:
        if pkg in excluded:
            skipped_exclude += 1
            continue

        tmpl_path = adapter.get_template_path(pkg)
        if not tmpl_path or not tmpl_path.is_file():
            continue

        tmpl = TemplateEditor.parse_file(tmpl_path)
        cur_version = tmpl.version

        # 1. Try Git History
        hist = get_git_history_versions(repo_path, pkg)
        if len(hist) >= 2:
            idx = min(max_versions_back, len(hist) - 1)
            target_ver, target_chk, commit_hash = hist[idx]
            if target_ver != cur_version:
                # Checkout or update
                TemplateEditor.update_file(
                    tmpl_path,
                    new_version=target_ver,
                    new_revision=1,
                    new_checksum=target_chk if target_chk else None,
                )
                print(f"[GIT-REWIND] {pkg}: {cur_version} -> {target_ver} (commit {commit_hash[:8]})")
                rewound_git += 1
                continue

        # 2. Try Upstream Tags
        if tmpl.distfiles and tmpl.homepage and ("github.com" in tmpl.homepage or "codeberg.org" in tmpl.homepage):
            git_url = tmpl.homepage.rstrip("/") + ".git"
            try:
                res = subprocess.run(
                    ["git", "ls-remote", "--tags", "--refs", git_url],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    raw_tags = []
                    for line in res.stdout.splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[1].startswith("refs/tags/"):
                            tag = parts[1][len("refs/tags/"):]
                            if not tag.endswith("^{}"):
                                raw_tags.append(tag)

                    parsed = []
                    for t in raw_tags:
                        v = GitTagsBackend._clean_tag_to_version(t)
                        if v:
                            parsed.append((v, t))

                    if len(parsed) >= 5:
                        parsed.sort(key=lambda item: GitTagsBackend._version_sort_key(item[0]))
                        # Pick 4 versions back
                        older_v, older_tag = parsed[-5]
                        if older_v != cur_version:
                            # Try computing checksum for older version
                            distfiles_to_resolve = tmpl.raw_distfiles if tmpl.raw_distfiles else tmpl.distfiles
                            computed = []
                            for df in distfiles_to_resolve:
                                clean = df.split(">")[0].strip()
                                if "${version}" in clean or "$version" in clean:
                                    resolved = clean.replace("${version}", older_v).replace("$version", older_v)
                                else:
                                    resolved = clean.replace(tmpl.version, older_v)
                                resolved = resolved.replace("${pkgname}", tmpl.pkgname).replace("$pkgname", tmpl.pkgname)
                                try:
                                    computed.append(compute_distfile_sha256(resolved))
                                except Exception:
                                    break

                            if len(computed) == len(distfiles_to_resolve):
                                TemplateEditor.update_file(
                                    tmpl_path,
                                    new_version=older_v,
                                    new_revision=1,
                                    new_checksum=computed,
                                )
                                print(f"[UPSTREAM-REWIND] {pkg}: {cur_version} -> {older_v} (tag {older_tag})")
                                rewound_upstream += 1
                                continue
            except Exception:
                pass

        untouched += 1

    print("\n" + "=" * 50)
    print(f"Rewind summary for {repo_path.name}:")
    print(f"  Rewound via Git History:   {rewound_git}")
    print(f"  Rewound via Upstream Tags: {rewound_upstream}")
    print(f"  Skipped (Excluded):        {skipped_exclude}")
    print(f"  Untouched:                 {untouched}")
    print("=" * 50)


if __name__ == "__main__":
    repo_arg = sys.argv[1] if len(sys.argv) > 1 else "/home/dicov/Proyectos/z-packages"
    rewind_repository(Path(repo_arg).resolve(), max_versions_back=4)
