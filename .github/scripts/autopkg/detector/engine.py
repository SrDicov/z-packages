"""
Upstream Version Detection Engine & Orchestrator.

Combines:
1. Declarative rules (updates.rules.yml)
2. Void Linux native 'update' files
3. Git tags zero-cost querying
4. HTTP ETag caching

Ensures 0 API rate limit exhaustion and accurate version diffs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.adapter import RepoAdapter
from core.template import TemplateData, TemplateEditor
from detector.backends import BACKENDS, DetectionResult, GitHubBackend, GitTagsBackend, RegexBackend
from detector.rules import PackageRule, RulesLoader, RulesRegistry
from detector.void_update import VoidUpdateConfig, VoidUpdateParser

logger = logging.getLogger("autopkg.detector")


@dataclass
class UpdateCandidate:
    pkgname: str
    current_version: str
    latest_version: str
    needs_update: bool
    source_type: str
    download_url: Optional[str] = None
    extra: Optional[dict] = None


class DetectionEngine:
    def __init__(
        self,
        repo_adapter: RepoAdapter,
        rules_path: Optional[str | Path] = None,
        cache_dir: Optional[str | Path] = ".cache",
    ) -> None:
        self.adapter = repo_adapter
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else Path(".cache").resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.etag_cache_file = self.cache_dir / "etag_cache.json"

        # Load rules with multi-path fallback
        loaded = False
        rule_candidates = []
        if rules_path:
            rule_candidates.append(Path(rules_path))
        rule_candidates.extend([
            self.adapter.root_dir / "config" / "updates.rules.yml",
            self.adapter.root_dir / ".github" / "scripts" / "autopkg" / "config" / "updates.rules.yml",
            Path(__file__).resolve().parent.parent / "config" / "updates.rules.yml",
        ])
        for p in rule_candidates:
            if p.is_file():
                self.rules = RulesLoader.load_file(p)
                logger.info("Loaded package rules from %s (%d rules, %d exclusions)", p, len(self.rules.packages), len(self.rules.exclude))
                loaded = True
                break
        if not loaded:
            self.rules = RulesRegistry()

        self.etag_cache: Dict[str, dict] = self._load_etag_cache()

    def _load_etag_cache(self) -> Dict[str, dict]:
        if self.etag_cache_file.is_file():
            try:
                with open(self.etag_cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_etag_cache(self) -> None:
        try:
            with open(self.etag_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.etag_cache, f, indent=2)
        except Exception:
            pass

    def check_package(self, pkgname: str) -> Optional[UpdateCandidate]:
        """Check a single package for updates."""
        if self.rules.is_excluded(pkgname):
            logger.info("Package %s is in exclude list, skipping", pkgname)
            return None

        tmpl_path = self.adapter.get_template_path(pkgname)
        if not tmpl_path:
            return None

        try:
            tmpl_data = TemplateEditor.parse_file(tmpl_path)
        except Exception as e:
            logger.warning("Failed to parse template for %s: %s", pkgname, e)
            return None

        if not tmpl_data.version:
            return None

        detection: Optional[DetectionResult] = None

        # 1. Check explicit rule from updates.rules.yml
        rule = self.rules.get_rule(pkgname)
        if rule:
            backend_cls = BACKENDS.get(rule.backend, GitTagsBackend)
            backend = backend_cls()
            detection = backend.detect_latest(tmpl_data, rule, self.etag_cache)

        # 2. Check native Void 'update' file
        if not detection:
            update_file = self.adapter.get_update_file_path(pkgname)
            if update_file:
                void_cfg = VoidUpdateParser.parse_file(update_file)
                if void_cfg:
                    detection = self._detect_from_void_update(tmpl_data, void_cfg)

        # 3. Prefer GitHubBackend if distfiles points to GitHub releases
        has_github_release = any("github.com" in df and "/releases/download/" in df for df in tmpl_data.distfiles)
        if not detection and has_github_release:
            gh_backend = GitHubBackend()
            detection = gh_backend.detect_latest(tmpl_data, None, self.etag_cache)

        # 4. Default fallback: GitTagsBackend (0 API cost)
        if not detection:
            git_backend = GitTagsBackend()
            detection = git_backend.detect_latest(tmpl_data, None, self.etag_cache)

        if not detection:
            return None

        needs_update = self._is_newer_version(detection.latest_version, tmpl_data.version)

        # If template uses custom _tag and already matches detected tag, it is up-to-date
        if needs_update and detection.extra and detection.extra.get("tag"):
            m_tag = re.search(r'^\s*_tag=[\"\'\']?([^\"\'\s#]+)', tmpl_data.raw_content, re.MULTILINE)
            if m_tag and m_tag.group(1) == detection.extra["tag"]:
                needs_update = False

        return UpdateCandidate(
            pkgname=pkgname,
            current_version=tmpl_data.version,
            latest_version=detection.latest_version,
            needs_update=needs_update,
            source_type=detection.source_type,
            download_url=detection.download_url,
            extra=detection.extra,
        )

    def check_all(self, target_packages: Optional[List[str]] = None) -> List[UpdateCandidate]:
        """Check all packages or a given subset."""
        packages = target_packages or self.adapter.list_packages()
        results: List[UpdateCandidate] = []

        for pkg in packages:
            res = self.check_package(pkg)
            if res:
                results.append(res)

        self.save_etag_cache()
        return results

    def _detect_from_void_update(
        self, tmpl_data: TemplateData, void_cfg: VoidUpdateConfig
    ) -> Optional[DetectionResult]:
        site = void_cfg.site or void_cfg.url
        pattern = void_cfg.pattern

        if site and pattern:
            # Construct synthetic rule for RegexBackend
            rule = PackageRule(
                pkgname=tmpl_data.pkgname,
                backend="regex",
                url=site,
                pattern=pattern,
                ignore=void_cfg.ignore,
            )
            return RegexBackend().detect_latest(tmpl_data, rule, self.etag_cache)

        # If site points to a git repo, use GitTags
        if site and any(h in site for h in ("github.com", "gitlab.com", "codeberg.org")):
            rule = PackageRule(pkgname=tmpl_data.pkgname, backend="git_tags", url=site)
            return GitTagsBackend().detect_latest(tmpl_data, rule, self.etag_cache)

        return None

    @classmethod
    def compare_versions(cls, ver1: str, ver2: str, pkgname: str = "pkg") -> int:
        """
        Compare two versions following Void Linux XBPS semantics.
        Returns:
           1 if ver1 > ver2
           0 if ver1 == ver2
          -1 if ver1 < ver2
        """
        if ver1 == ver2:
            return 0

        # Fast path: try native xbps-uhelper if installed
        try:
            import subprocess
            p1 = f"{pkgname}-{ver1}"
            p2 = f"{pkgname}-{ver2}"
            res = subprocess.run(
                ["xbps-uhelper", "cmpver", p1, p2],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if res.returncode == 0:
                return 0
            elif res.returncode == 1:
                return 1
            elif res.returncode == 255:
                return -1
        except Exception:
            pass

        # Fallback pure Python comparison
        def split_rev(v: str):
            if "_" in v:
                base, rev_str = v.rsplit("_", 1)
                try:
                    return base, int(rev_str)
                except ValueError:
                    return v, 0
            return v, 0

        base1, rev1 = split_rev(ver1)
        base2, rev2 = split_rev(ver2)

        def tokenize(s: str):
            tokens = []
            for t in re.findall(r'\d+|[a-zA-Z]+', s):
                if t.isdigit():
                    tokens.append((0, int(t)))
                else:
                    tl = t.lower()
                    if tl in ("alpha", "beta", "rc", "pre"):
                        prerelease_rank = {"alpha": -4, "beta": -3, "rc": -2, "pre": -1}[tl]
                        tokens.append((1, prerelease_rank))
                    else:
                        tokens.append((2, tl))
            return tokens

        toks1 = tokenize(base1)
        toks2 = tokenize(base2)

        for t1, t2 in zip(toks1, toks2):
            if t1 < t2:
                return -1
            elif t1 > t2:
                return 1

        if len(toks1) < len(toks2):
            if toks2[len(toks1)][0] == 1:
                return 1
            return -1
        elif len(toks1) > len(toks2):
            if toks1[len(toks2)][0] == 1:
                return -1
            return 1

        if rev1 < rev2:
            return -1
        elif rev1 > rev2:
            return 1

        return 0

    @classmethod
    def _is_newer_version(cls, latest: str, current: str) -> bool:
        return cls.compare_versions(latest, current) > 0
