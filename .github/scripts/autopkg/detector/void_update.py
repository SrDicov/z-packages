"""
Parser for Void Linux native 'update' files (srcpkgs/<pkg>/update).

Extracts site, pattern, ignore, url, pkgname, single_directory, and other update hints.
Supports shell-style variable expansion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class VoidUpdateConfig:
    site: Optional[str] = None
    pattern: Optional[str] = None
    ignore: Optional[str] = None
    url: Optional[str] = None
    version: Optional[str] = None
    pkgname: Optional[str] = None
    single_directory: bool = False


class VoidUpdateParser:
    @classmethod
    def parse_file(
        cls,
        path: str | Path,
        context_vars: Optional[Dict[str, str]] = None,
    ) -> Optional[VoidUpdateConfig]:
        filepath = Path(path).resolve()
        if not filepath.is_file():
            return None

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return cls.parse_string(content, context_vars=context_vars, pkgname_default=filepath.parent.name)

    @classmethod
    def parse_string(
        cls,
        content: str,
        context_vars: Optional[Dict[str, str]] = None,
        pkgname_default: str = "",
    ) -> VoidUpdateConfig:
        cfg = VoidUpdateConfig()
        vars_dict: Dict[str, str] = dict(context_vars or {})
        if "pkgname" not in vars_dict and pkgname_default:
            vars_dict["pkgname"] = pkgname_default

        var_re = re.compile(r'^(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)=(?P<val>.*)$')

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            m = var_re.match(line)
            if not m:
                continue

            name = m.group("name")
            raw_val = m.group("val").strip()

            # Strip inline comments
            val = cls._strip_inline_comment(raw_val)

            # Strip outer quotes
            if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                val = val[1:-1]

            # Expand known variables
            val = cls._expand_vars(val, vars_dict)
            vars_dict[name] = val

            if name == "site":
                cfg.site = val
            elif name == "pattern":
                cfg.pattern = val
            elif name == "ignore":
                cfg.ignore = val
            elif name == "url":
                cfg.url = val
            elif name == "version":
                cfg.version = val
            elif name == "pkgname":
                cfg.pkgname = val
            elif name == "single_directory":
                cfg.single_directory = (val.lower() in ("yes", "true", "1"))

        return cfg

    @staticmethod
    def _strip_inline_comment(s: str) -> str:
        in_dquote = False
        in_squote = False
        in_escape = False
        for idx, ch in enumerate(s):
            if in_escape:
                in_escape = False
                continue
            if ch == "\\":
                in_escape = True
                continue
            if ch == '"' and not in_squote:
                in_dquote = not in_dquote
            elif ch == "'" and not in_dquote:
                in_squote = not in_squote
            elif ch == "#" and not in_dquote and not in_squote:
                if idx > 0 and s[idx - 1] in " \t":
                    return s[:idx].rstrip()
        return s

    @staticmethod
    def _expand_vars(val: str, vars_dict: Dict[str, str]) -> str:
        def repl(m: re.Match) -> str:
            k = m.group(1) or m.group(2)
            return vars_dict.get(k, "")
        return re.sub(r'\$\{([a-zA-Z0-9_]+)\}|\$([a-zA-Z0-9_]+)', repl, val)
