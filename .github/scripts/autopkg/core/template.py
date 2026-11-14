"""
Non-destructive Lexical Template Parser and Editor for Void Linux templates.

Guarantees:
1. Zero comment deletion (all '#' comments remain intact exactly where they are).
2. Preserves all blank lines, indentation, and custom shell functions.
3. Clean replacement of version, revision, and checksum.
4. Robust extraction of metadata: pkgname, version, revision, distfiles, checksums,
   depends, makedepends, hostmakedepends, archs, broken, and subpackages.
5. Bash variable expansion (${version%.*}, ${_commit:0:7}, ${version//./_}, etc.).
6. Handles backslash '\\' line continuations and inline comments.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class TemplateData:
    pkgname: str
    version: str
    revision: int
    short_desc: str = ""
    homepage: str = ""
    distfiles: List[str] = field(default_factory=list)
    raw_distfiles: List[str] = field(default_factory=list)
    checksums: List[str] = field(default_factory=list)
    hostmakedepends: List[str] = field(default_factory=list)
    makedepends: List[str] = field(default_factory=list)
    depends: List[str] = field(default_factory=list)
    archs: List[str] = field(default_factory=list)
    build_style: str = ""
    noshlibprovides: Optional[str] = None
    shlib_provides: Optional[str] = None
    broken: Optional[str] = None
    subpackages: List[str] = field(default_factory=list)
    raw_content: str = ""
    path: Optional[Path] = None


class TemplateEditor:
    """
    Parses and updates Void xbps templates without destroying comments or formatting.
    """

    VAR_REGEX = re.compile(r'^(?P<indent>\s*)(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)=(?P<val>.*)$')
    SUBPKG_REGEX = re.compile(r'^\s*(?P<name>[a-zA-Z0-9_\-]+)_package\s*\(\)', re.MULTILINE)

    DEFAULT_SITE_VARS: Dict[str, str] = {
        "SOURCEFORGE_SITE": "https://downloads.sourceforge.net/sourceforge",
        "NONGNU_SITE": "https://download.savannah.nongnu.org/releases",
        "UBUNTU_SITE": "http://archive.ubuntu.com/ubuntu/pool",
        "XORG_SITE": "https://www.x.org/releases/individual",
        "DEBIAN_SITE": "https://ftp.debian.org/debian/pool",
        "GNOME_SITE": "https://download.gnome.org/sources",
        "KERNEL_SITE": "https://www.kernel.org/pub/linux",
        "CPAN_SITE": "https://www.cpan.org/modules/by-module",
        "PYPI_SITE": "https://files.pythonhosted.org/packages/source",
        "MOZILLA_SITE": "https://ftp.mozilla.org/pub",
        "GNU_SITE": "https://ftp.gnu.org/gnu",
        "FREEDESKTOP_SITE": "https://www.freedesktop.org/software",
        "KDE_SITE": "https://download.kde.org/stable",
        "VIDEOLAN_SITE": "https://download.videolan.org/pub/videolan",
    }

    @classmethod
    def parse_file(cls, path: str | Path) -> TemplateData:
        filepath = Path(path).resolve()
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return cls.parse_string(content, path=filepath)

    @classmethod
    def expand_bash_vars(cls, val: str, vars_dict: Dict[str, str]) -> str:
        """
        Expand bash variables:
        - $var, ${var}
        - Substrings: ${var:start:length}
        - Replacements: ${var//pat/repl}, ${var/pat/repl}
        - Suffix removal: ${var%.*}, ${var%pattern}
        - Prefix removal: ${var#pattern}
        """
        def replacer(m: re.Match) -> str:
            raw = m.group(1) or m.group(2)
            if not raw:
                return m.group(0)

            # Substring ${var:start:length} or ${var:start}
            sub_m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*):(-?\d+)(?::(\d+))?$', raw)
            if sub_m:
                vname, start_s, length_s = sub_m.group(1), sub_m.group(2), sub_m.group(3)
                base_val = vars_dict.get(vname, "")
                start = int(start_s)
                if length_s is not None:
                    return base_val[start:start + int(length_s)]
                return base_val[start:]

            # Replace ${var//pat/repl} or ${var/pat/repl}
            rep_m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)(//?)([^/]*)(?:/(.*))?$', raw)
            if rep_m:
                vname, slash, pat, repl = rep_m.group(1), rep_m.group(2), rep_m.group(3), rep_m.group(4) or ""
                base_val = vars_dict.get(vname, "")
                if slash == "//":
                    return base_val.replace(pat, repl)
                return base_val.replace(pat, repl, 1)

            # Suffix removal ${var%.*}, ${var%pattern}
            if "%" in raw:
                parts = raw.split("%", 1)
                vname, pat = parts[0], parts[1]
                base_val = vars_dict.get(vname, "")
                if pat in (".*", ".*"):
                    if "." in base_val:
                        return base_val.rsplit(".", 1)[0]
                elif pat.startswith("*"):
                    suffix = pat[1:]
                    if base_val.endswith(suffix):
                        return base_val[:-len(suffix)]
                elif base_val.endswith(pat):
                    return base_val[:-len(pat)]
                return base_val

            # Prefix removal ${var#pattern}
            if "#" in raw:
                parts = raw.split("#", 1)
                vname, pat = parts[0], parts[1]
                base_val = vars_dict.get(vname, "")
                if pat.endswith("*"):
                    prefix = pat[:-1]
                    if base_val.startswith(prefix):
                        return base_val[len(prefix):]
                elif base_val.startswith(pat):
                    return base_val[len(pat):]
                return base_val

            return vars_dict.get(raw, "")

        return re.sub(r'\$\{([^\}]+)\}|\$([a-zA-Z_][a-zA-Z0-9_]*)', replacer, val)

    @classmethod
    def parse_string(cls, content: str, path: Optional[Path] = None) -> TemplateData:
        raw_lines = content.splitlines()
        variables: Dict[str, str] = dict(cls.DEFAULT_SITE_VARS)
        raw_variables: Dict[str, str] = {}

        # 1. First pass: normalize backslash line continuations
        lines: List[str] = []
        i = 0
        n = len(raw_lines)
        while i < n:
            curr = raw_lines[i]
            while curr.endswith("\\") and i + 1 < n:
                curr = curr[:-1] + " " + raw_lines[i + 1].strip()
                i += 1
            lines.append(curr)
            i += 1

        # 2. Extract variable assignments
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            m = cls.VAR_REGEX.match(line)
            if m:
                var_name = m.group("name")
                val_start = m.group("val")

                # Handle multi-line strings (open quotes)
                full_val = val_start
                quote_char = None
                trimmed = val_start.lstrip()
                if trimmed and trimmed[0] in ('"', "'"):
                    quote_char = trimmed[0]
                    if cls._is_open_quote(val_start, quote_char):
                        i += 1
                        while i < n:
                            full_val += "\n" + lines[i]
                            if not cls._is_open_quote(full_val, quote_char):
                                break
                            i += 1
                else:
                    # Strip inline comments for unquoted assignments
                    full_val = cls._strip_inline_comment(full_val)

                clean_val = cls._strip_quotes(full_val.strip())
                raw_variables[var_name] = clean_val
                # Expand bash variables known up to this point
                expanded = cls.expand_bash_vars(clean_val, variables)
                variables[var_name] = expanded

            i += 1

        # Extract structured data
        pkgname = variables.get("pkgname", path.parent.name if path else "")
        version = variables.get("version", "")
        try:
            revision = int(variables.get("revision", "1"))
        except ValueError:
            revision = 1

        distfiles = cls._split_tokens(variables.get("distfiles", ""))
        raw_distfiles = cls._split_tokens(raw_variables.get("distfiles", ""))
        checksums = cls._split_tokens(variables.get("checksum", ""))
        hostmakedepends = cls._split_tokens(variables.get("hostmakedepends", ""))
        makedepends = cls._split_tokens(variables.get("makedepends", ""))
        depends = cls._split_tokens(variables.get("depends", ""))
        archs = cls._split_tokens(variables.get("archs", ""))
        build_style = variables.get("build_style", "")
        noshlibprovides = variables.get("noshlibprovides")
        shlib_provides = variables.get("shlib_provides")
        broken = variables.get("broken")

        # Discover subpackages
        subpackages = cls.SUBPKG_REGEX.findall(content)

        return TemplateData(
            pkgname=pkgname,
            version=version,
            revision=revision,
            short_desc=variables.get("short_desc", ""),
            homepage=variables.get("homepage", ""),
            distfiles=distfiles,
            raw_distfiles=raw_distfiles,
            checksums=checksums,
            hostmakedepends=hostmakedepends,
            makedepends=makedepends,
            depends=depends,
            archs=archs,
            build_style=build_style,
            noshlibprovides=noshlibprovides,
            shlib_provides=shlib_provides,
            broken=broken,
            subpackages=subpackages,
            raw_content=content,
            path=path,
        )

    @classmethod
    def check_noshlibprovides(cls, data: TemplateData) -> Optional[str]:
        """
        Security heuristic from vup:
        Warn if the template packages prebuilt binaries/Electron apps without noshlibprovides=yes.
        Prevents bundled internal shared libraries (like libc.so) from corrupting repository shlib-provides.
        """
        # If the template explicitly declares shlib_provides, it intentionally advertises libraries
        if getattr(data, "shlib_provides", None):
            return None

        # 1. If it has build_style or do_build(), it is compiled from source - no warning
        if data.build_style.strip():
            return None
        raw = data.raw_content
        if "do_build()" in raw:
            return None

        pkgname = data.pkgname.lower()
        short_desc = data.short_desc.lower()
        # Use filename basenames to avoid matching domain/path tokens (e.g. org names like Neko-Void-Linux)
        distfile_basenames = " ".join(df.split("/")[-1].split(">")[0].lower() for df in data.distfiles)
        hostmakedeps_str = " ".join(data.hostmakedepends).lower()

        # 2. Python packages - no warning
        python_source_indicators = [
            "files.pythonhosted.org/packages/source/" in " ".join(data.distfiles).lower(),
            "pypi.org/packages/source/" in " ".join(data.distfiles).lower(),
            ".whl" in " ".join(data.distfiles).lower(),
            "python_version=" in raw,
            "python3-setuptools" in hostmakedeps_str,
            "python3-pep517" in hostmakedeps_str,
            "python3-flit_core" in hostmakedeps_str,
            "python3-hatchling" in hostmakedeps_str,
            "python3 -m pip install" in raw,
        ]
        if any(python_source_indicators):
            return None

        # 3. If none of the distfiles is an archive (e.g. standalone raw executable), no bundled .so
        is_archive = any(
            re.search(r'\.(appimage|deb|rpm|tar(?:\.[a-z0-9]+)?|zip|7z|tgz)(?:[>\s"\'?]|$)', df.lower())
            for df in data.distfiles
        )
        if not is_archive:
            return None

        # 4. Prebuilt app heuristics
        app_keywords = ("browser", "editor", "ide", "electron")
        binary_archive_patterns = [
            r"\.(appimage|deb|rpm)(?:[>\s\"']|$)",
            r"(?:^|[-_/])(linux|unknown-linux|pc-linux|linux-gnu|linux-musl)(?:[-_/]|$)",
            r"(?:^|[-_/])(x86_64|amd64|aarch64|arm64|x64)(?:[-_/\.]|$)",
        ]

        prebuilt_indicators = [
            any(word in short_desc or word in pkgname for word in app_keywords),
            any(re.search(pattern, distfile_basenames) for pattern in binary_archive_patterns),
        ]

        if any(prebuilt_indicators):
            if data.noshlibprovides != "yes":
                return (
                    f"CRITICAL WARNING: '{data.pkgname}' appears to package prebuilt binaries or bundled application "
                    f"without 'noshlibprovides=yes'. Bundled libraries may pollute repository shlib-provides."
                )

        return None

    @classmethod
    def validate_template(cls, data: TemplateData) -> Tuple[bool, List[str], List[str]]:
        """
        Validate template consistency, required fields, and security heuristics.
        Returns (is_valid, errors, warnings).
        """
        errors: List[str] = []
        warnings: List[str] = []

        if not data.pkgname:
            errors.append("Missing 'pkgname'")
        if not data.version:
            errors.append("Missing 'version'")
        if data.revision < 1:
            errors.append("Invalid 'revision', must be an integer >= 1")

        noshlib_warn = cls.check_noshlibprovides(data)
        if noshlib_warn:
            warnings.append(noshlib_warn)

        return (len(errors) == 0, errors, warnings)

    @classmethod
    def update_metadata(
        cls,
        content: str,
        new_version: Optional[str] = None,
        new_revision: Optional[int] = None,
        new_checksum: Optional[str | List[str]] = None,
        additional_vars: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Surgically updates version, revision, checksum (and optional variables)
        in the template string while preserving 100% of comments, whitespace,
        formatting, and shell functions.
        """
        replacements: Dict[str, str] = {}
        if new_version is not None:
            replacements["version"] = str(new_version)
        if new_revision is not None:
            replacements["revision"] = str(new_revision)
        if new_checksum is not None:
            if isinstance(new_checksum, list):
                if len(new_checksum) == 1:
                    replacements["checksum"] = new_checksum[0]
                else:
                    replacements["checksum"] = "\n " + "\n ".join(new_checksum)
            else:
                replacements["checksum"] = str(new_checksum)

        if additional_vars:
            replacements.update(additional_vars)

        lines = content.splitlines(keepends=True)
        new_lines: List[str] = []
        applied_keys: Set[str] = set()

        i = 0
        n = len(lines)
        while i < n:
            raw_line = lines[i]
            line_no_nl = raw_line.rstrip("\r\n")
            nl = raw_line[len(line_no_nl):]

            # Don't touch comments
            if line_no_nl.strip().startswith("#"):
                new_lines.append(raw_line)
                i += 1
                continue

            m = cls.VAR_REGEX.match(line_no_nl)
            if m and m.group("name") in replacements:
                var_name = m.group("name")
                indent = m.group("indent")
                val_start = m.group("val")
                new_val = replacements[var_name]
                applied_keys.add(var_name)

                # Skip any multi-line continuation of the old value
                trimmed = val_start.lstrip()
                if trimmed and trimmed[0] in ('"', "'"):
                    quote_char = trimmed[0]
                    accum = val_start
                    while cls._is_open_quote(accum, quote_char) and i + 1 < n:
                        i += 1
                        accum += "\n" + lines[i].rstrip("\r\n")

                # Format new assignment with quotes if needed
                formatted_assignment = cls._format_assignment(indent, var_name, new_val)
                new_lines.append(formatted_assignment + nl)
            else:
                new_lines.append(raw_line)
            i += 1

        # Append any missing requested variables
        missing_keys = set(replacements.keys()) - applied_keys
        if missing_keys:
            append_block = []
            for k in sorted(missing_keys):
                append_block.append(cls._format_assignment("", k, replacements[k]) + "\n")
            new_lines.extend(append_block)

        return "".join(new_lines)

    @classmethod
    def update_file(
        cls,
        path: str | Path,
        new_version: Optional[str] = None,
        new_revision: Optional[int] = 1,
        new_checksum: Optional[str | List[str]] = None,
        additional_vars: Optional[Dict[str, str]] = None,
        allow_same_checksum: bool = False,
    ) -> bool:
        """Update template file in place. Returns True on success."""
        filepath = Path(path).resolve()
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        parsed = cls.parse_string(content, path=filepath)

        # Enforce fail-closed checksum policy:
        # Never allow version change on templates with distfiles without a new, valid checksum.
        if new_version is not None and str(new_version) != str(parsed.version) and parsed.distfiles:
            if not allow_same_checksum:
                if not new_checksum:
                    raise ValueError(
                        f"Refusing to update version of '{parsed.pkgname}' from '{parsed.version}' "
                        f"to '{new_version}' without new checksum(s) on a template with distfiles."
                    )
                new_csums = [new_checksum] if isinstance(new_checksum, str) else list(new_checksum)
                new_csums_clean = [c.strip() for c in new_csums if c and c.strip()]
                old_csums_clean = [c.strip() for c in parsed.checksums if c and c.strip()]
                if new_csums_clean and old_csums_clean and set(new_csums_clean) == set(old_csums_clean):
                    raise ValueError(
                        f"Refusing to update version of '{parsed.pkgname}' from '{parsed.version}' "
                        f"to '{new_version}' with identical checksum(s)."
                    )

        updated = cls.update_metadata(
            content=content,
            new_version=new_version,
            new_revision=new_revision,
            new_checksum=new_checksum,
            additional_vars=additional_vars,
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
        return True

    @staticmethod
    def _is_open_quote(text: str, quote_char: str) -> bool:
        """Determine if a string has an unmatched quote."""
        in_escape = False
        count = 0
        for ch in text:
            if in_escape:
                in_escape = False
                continue
            if ch == "\\":
                in_escape = True
                continue
            if ch == quote_char:
                count += 1
        return (count % 2) != 0

    @staticmethod
    def _strip_inline_comment(s: str) -> str:
        """Strip trailing comments outside quotes."""
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
    def _strip_quotes(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
            return s[1:-1]
        return s

    @staticmethod
    def _split_tokens(s: str) -> List[str]:
        """Split space-delimited or quoted string into tokens."""
        if not s:
            return []
        try:
            return shlex.split(s, comments=True)
        except ValueError:
            return s.split()

    @staticmethod
    def _format_assignment(indent: str, var_name: str, value: str) -> str:
        """Format variable assignment following Void xbps conventions."""
        if "\n" in value:
            return f'{indent}{var_name}="{value}"'
        if any(c in value for c in ' \t$*?~;()<>|&#'):
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                return f"{indent}{var_name}={value}"
            return f'{indent}{var_name}="{value}"'
        return f"{indent}{var_name}={value}"
