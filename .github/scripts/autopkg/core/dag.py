"""
Dependency Graph & Topological Sort Planner (DAG).

Solves dependency order across target packages to prevent build failures.
Uses:
1. Native topological sort (Kahn's algorithm with iterative cycle-breaker).
2. Maps subpackages from both symlinks and *_package() declarations to canonical parent packages.
3. Optional delegation to 'xbps-src sort-dependencies' when available.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.adapter import RepoAdapter
from core.template import TemplateData, TemplateEditor


class DependencyPlanner:
    def __init__(self, repo_adapter: RepoAdapter) -> None:
        self.adapter = repo_adapter

    def plan_build_order(self, target_packages: List[str], prefer_xbps_src: bool = False) -> List[str]:
        """
        Order target_packages so dependencies are built before dependents.
        Returns the topologically sorted list.
        Uses blazing-fast internal DAG (sub-millisecond per package).
        """
        if not target_packages or len(target_packages) <= 1:
            return list(target_packages)

        if prefer_xbps_src:
            xbps_src = self.adapter.root_dir / "xbps-src"
            if xbps_src.is_file():
                sorted_xbps = self._sort_with_xbps_src(xbps_src, target_packages)
                if sorted_xbps:
                    return sorted_xbps

        # Blazing fast native internal DAG
        return self._sort_internal(target_packages)

    def _sort_with_xbps_src(self, xbps_src_path: Path, targets: List[str]) -> Optional[List[str]]:
        try:
            cmd = [str(xbps_src_path), "sort-dependencies"] + targets
            res = subprocess.run(
                cmd,
                cwd=str(self.adapter.root_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                ordered = [p.strip() for p in res.stdout.split() if p.strip() in targets]
                for t in targets:
                    if t not in ordered:
                        ordered.append(t)
                return ordered
        except Exception:
            pass
        return None

    def _sort_internal(self, targets: List[str]) -> List[str]:
        target_set = set(targets)
        subpkg_map = self.adapter.get_subpackages()
        parsed_templates: Dict[str, TemplateData] = {}

        # 1. Parse templates and register declared *_package() subpackages
        for pkg in targets:
            tmpl_path = self.adapter.get_template_path(pkg)
            if not tmpl_path:
                continue
            try:
                tmpl_data = TemplateEditor.parse_file(tmpl_path)
                parsed_templates[pkg] = tmpl_data
                for sub in tmpl_data.subpackages:
                    subpkg_map[sub] = pkg
            except Exception:
                continue

        # 2. Build adjacency graph (edge: dep -> pkg)
        in_degree: Dict[str, int] = {p: 0 for p in targets}
        graph: Dict[str, Set[str]] = defaultdict(set)

        for pkg in targets:
            tmpl_data = parsed_templates.get(pkg)
            if not tmpl_data:
                continue

            all_deps = set()
            for dep_str in tmpl_data.hostmakedepends + tmpl_data.makedepends + tmpl_data.depends:
                dep_name = dep_str.split(">=")[0].split("<=")[0].split(">")[0].split("<")[0].split("=")[0].strip()
                canonical_dep = subpkg_map.get(dep_name, dep_name)
                all_deps.add(canonical_dep)

            for dep in all_deps:
                if dep in target_set and dep != pkg:
                    if pkg not in graph[dep]:
                        graph[dep].add(pkg)
                        in_degree[pkg] += 1

        # 3. Kahn's algorithm with smart iterative cycle-breaking
        ordered: List[str] = []
        remaining = set(targets)

        while remaining:
            # Find nodes with 0 in-degree
            ready = [p for p in remaining if in_degree[p] == 0]
            if ready:
                # Deterministic sort for tie-breaking
                ready.sort()
                for node in ready:
                    ordered.append(node)
                    remaining.remove(node)
                    for neighbor in graph[node]:
                        in_degree[neighbor] = max(0, in_degree[neighbor] - 1)
            else:
                # Cycle detected: pick node with minimum in-degree to break cycle gracefully
                min_node = min(remaining, key=lambda p: (in_degree[p], p))
                ordered.append(min_node)
                remaining.remove(min_node)
                for neighbor in graph[min_node]:
                    in_degree[neighbor] = max(0, in_degree[neighbor] - 1)

        return ordered
