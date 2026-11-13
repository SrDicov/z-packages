"""
Declarative rule loader for package update strategies.

Loads config/updates.rules.yml (or .json / .toml) with zero external dependency requirements.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


@dataclass
class PackageRule:
    pkgname: str
    backend: str
    url: Optional[str] = None
    pattern: Optional[str] = None
    ignore: Optional[str] = None
    strip_prefix: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RulesRegistry:
    packages: Dict[str, PackageRule] = field(default_factory=dict)
    exclude: List[str] = field(default_factory=list)

    def is_excluded(self, pkgname: str) -> bool:
        return pkgname in self.exclude

    def get_rule(self, pkgname: str) -> Optional[PackageRule]:
        return self.packages.get(pkgname)


class RulesLoader:
    @classmethod
    def load_file(cls, path: str | Path) -> RulesRegistry:
        filepath = Path(path).resolve()
        if not filepath.is_file():
            return RulesRegistry()

        # Try pyyaml if available
        if yaml is not None and filepath.suffix in (".yml", ".yaml"):
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls._from_dict(data)

        # Try JSON
        if filepath.suffix == ".json":
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls._from_dict(data)

        # Try TOML
        if tomllib is not None and filepath.suffix == ".toml":
            with open(filepath, "rb") as f:
                data = tomllib.load(f)
            return cls._from_dict(data)

        # Fallback: lightweight YAML parser for key-value / lists / basic maps
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return cls._parse_simple_yaml(content)

    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> RulesRegistry:
        registry = RulesRegistry()
        registry.exclude = list(data.get("exclude", []))

        pkgs_data = data.get("packages", {})
        for name, entry in pkgs_data.items():
            if isinstance(entry, dict):
                rule = PackageRule(
                    pkgname=name,
                    backend=entry.get("backend", "git_tags"),
                    url=entry.get("url"),
                    pattern=entry.get("pattern"),
                    ignore=entry.get("ignore"),
                    strip_prefix=entry.get("strip_prefix"),
                    options={k: v for k, v in entry.items() if k not in {
                        "backend", "url", "pattern", "ignore", "strip_prefix"
                    }},
                )
            elif isinstance(entry, (list, tuple)):
                # Tuple format legacy from detect-updates.py TABLE:
                # ('apt', 'url') or ('regex', 'url', 'pattern')
                backend = entry[0] if len(entry) > 0 else "git_tags"
                url = entry[1] if len(entry) > 1 else None
                pattern = entry[2] if len(entry) > 2 else None
                rule = PackageRule(pkgname=name, backend=backend, url=url, pattern=pattern)
            else:
                rule = PackageRule(pkgname=name, backend=str(entry))
            registry.packages[name] = rule

        return registry

    @staticmethod
    def _unquote_yaml_value(v: str) -> str:
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            try:
                return json.loads(v)
            except Exception:
                return v[1:-1]
        return v

    @classmethod
    def _parse_simple_yaml(cls, text: str) -> RulesRegistry:
        """
        Minimal pure-Python YAML subset parser that handles:
        exclude:
          - pkg1
          - pkg2
        packages:
          pkgname:
            backend: ...
            url: ...
        """
        registry = RulesRegistry()
        current_section = None
        current_pkg = None
        current_options = False

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            if indent == 0 and line.endswith(":"):
                section_name = line[:-1].strip()
                current_section = section_name
                current_pkg = None
                continue

            if current_section == "exclude":
                if stripped.startswith("- "):
                    val = cls._unquote_yaml_value(stripped[2:])
                    registry.exclude.append(val)
            elif current_section == "packages":
                if indent == 2 and line.endswith(":"):
                    current_pkg = stripped[:-1].strip()
                    current_options = False
                    registry.packages[current_pkg] = PackageRule(pkgname=current_pkg, backend="git_tags")
                elif indent == 4 and current_pkg:
                    current_options = False
                    rule = registry.packages[current_pkg]
                    if ":" in stripped:
                        k, v = stripped.split(":", 1)
                        k = k.strip()
                        v = cls._unquote_yaml_value(v)
                        if k == "options" and not v:
                            current_options = True
                        elif k == "backend":
                            rule.backend = v
                        elif k == "url":
                            rule.url = v
                        elif k == "pattern":
                            rule.pattern = v
                        elif k == "ignore":
                            rule.ignore = v
                        elif k == "strip_prefix":
                            rule.strip_prefix = v
                        else:
                            rule.options[k] = v
                elif indent >= 6 and current_pkg and current_options:
                    rule = registry.packages[current_pkg]
                    if ":" in stripped:
                        k, v = stripped.split(":", 1)
                        rule.options[k.strip()] = cls._unquote_yaml_value(v)

        return registry
