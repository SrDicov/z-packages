"""
Isolated Package Build Runner for glibc and musl architectures.

Executes ./xbps-src pkg <pkgname> with environment guards and quarantine tracking.
Handles same-CPU different-libc architecture switching (-A) vs cross-compilation (-a).
"""

from __future__ import annotations

import fnmatch
import logging
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.adapter import RepoAdapter
from core.quarantine import QuarantineManager

logger = logging.getLogger("autopkg.builder.runner")


def is_arch_supported(archs: List[str], target_arch: str) -> bool:
    """
    Check if target_arch satisfies the archs restriction of a Void template.
    Void Linux rules:
    - Empty archs: all architectures supported.
    - ~pattern: architecture blacklisted (e.g. ~*-musl, ~x86_64-musl).
    - pattern: architecture whitelisted (e.g. x86_64, aarch64).
    """
    if not archs:
        return True

    # Check blacklist first
    for a in archs:
        if a.startswith("~"):
            if fnmatch.fnmatch(target_arch, a[1:]):
                return False

    # Check whitelist if any positive archs exist
    positive_archs = [a for a in archs if not a.startswith("~")]
    if not positive_archs:
        return True

    for a in positive_archs:
        if fnmatch.fnmatch(target_arch, a):
            return True

    return False


def needs_alt_host(target_arch: str, host_arch: Optional[str] = None) -> bool:
    """
    xbps-src can't build a cross-toolchain for a same-CPU different-libc target
    (e.g. building x86_64-musl from an x86_64 glibc host fails with
    'Can't build crosstoolchain to itself'). Those targets must be built as
    a different host (xbps-src -A <host>) inside a libc-specific masterdir,
    not as a cross-target via -a. Returns True when -A should be used.
    """
    if not host_arch:
        machine = platform.machine()
        host_arch = "x86_64" if machine in ("x86_64", "AMD64") else machine

    if target_arch == host_arch:
        return False

    base_target, sep_target, _ = target_arch.partition("-")
    base_host, sep_host, _ = host_arch.partition("-")
    return base_target == base_host and target_arch != host_arch


@dataclass
class BuildResult:
    pkgname: str
    arch: str
    success: bool
    returncode: int
    duration_seconds: float
    error_summary: str = ""
    log_path: Optional[Path] = None


class BuildRunner:
    def __init__(
        self,
        repo_adapter: RepoAdapter,
        quarantine_mgr: Optional[QuarantineManager] = None,
        logs_dir: str | Path = "build_logs",
    ) -> None:
        self.adapter = repo_adapter
        self.quarantine = quarantine_mgr
        self.logs_dir = Path(logs_dir).resolve()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.xbps_src = self.adapter.root_dir / "xbps-src"

    def ensure_masterdir(self, arch: str, host_arch: Optional[str] = None) -> bool:
        """Bootstrap masterdir-<arch> if it does not exist yet."""
        masterdir = self.adapter.root_dir / f"masterdir-{arch}"
        if masterdir.is_dir() and (masterdir / ".xbps_chroot_init").is_file():
            return True

        logger.info("Bootstrapping masterdir-%s...", arch)
        cmd = [str(self.xbps_src)]
        if needs_alt_host(arch, host_arch):
            cmd += ["-A", arch, "binary-bootstrap"]
        elif arch != (host_arch or "x86_64"):
            cmd += ["-a", arch, "binary-bootstrap"]
        else:
            cmd += ["binary-bootstrap"]

        res = subprocess.run(cmd, cwd=str(self.adapter.root_dir), check=False)
        return res.returncode == 0

    def build_package(
        self,
        pkgname: str,
        arch: str = "x86_64",
        force_clean: bool = False,
        host_arch: Optional[str] = None,
    ) -> BuildResult:
        """Compile a single package using xbps-src."""
        start_time = time.time()
        log_file = self.logs_dir / f"{pkgname}_{arch}_{int(start_time)}.log"

        # Check architecture compatibility before invoking xbps-src
        tmpl_path = self.adapter.get_template_path(pkgname)
        if tmpl_path:
            try:
                from core.template import TemplateEditor
                tmpl = TemplateEditor.parse_file(tmpl_path)
                if not is_arch_supported(tmpl.archs, arch):
                    logger.info("Skipping %s for %s: architecture not supported by template (archs=%s)", pkgname, arch, tmpl.archs)
                    return BuildResult(
                        pkgname=pkgname,
                        arch=arch,
                        success=True,
                        returncode=0,
                        duration_seconds=0.0,
                        error_summary=f"Skipped: arch {arch} not supported by template archs={tmpl.archs}",
                        log_path=log_file,
                    )
            except Exception as e:
                logger.warning("Could not check arch compatibility for %s: %s", pkgname, e)

        cmd = [str(self.xbps_src)]
        if needs_alt_host(arch, host_arch):
            # Same-CPU different-libc: ensure masterdir is bootstrapped deterministically
            if not self.ensure_masterdir(arch, host_arch):
                logger.error("Failed to bootstrap masterdir-%s for same-CPU alt host build", arch)
                return BuildResult(
                    pkgname=pkgname,
                    arch=arch,
                    success=False,
                    returncode=1,
                    duration_seconds=round(time.time() - start_time, 2),
                    error_summary=f"Could not bootstrap masterdir-{arch}",
                    log_path=log_file,
                )
            cmd += ["-A", arch, "-m", f"masterdir-{arch}"]
        elif arch != (host_arch or "x86_64"):
            # Cross-compilation to a different CPU architecture
            cmd += ["-a", arch, "-m", f"masterdir-{arch}"]

        if force_clean:
            cmd.append("-f")
        cmd += ["pkg", pkgname]

        logger.info("Starting build for %s (%s): %s", pkgname, arch, " ".join(cmd))

        error_lines: List[str] = []
        returncode = -1

        try:
            with open(log_file, "w", encoding="utf-8") as f_log:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.adapter.root_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                if proc.stdout:
                    for line in proc.stdout:
                        f_log.write(line)
                        if any(err_kw in line.lower() for err_kw in ("error:", "fatal:", "failed:", "cannot find")):
                            error_lines.append(line.strip())
                    proc.stdout.close()

                proc.wait()
                returncode = proc.returncode

        except Exception as e:
            error_lines.append(f"Execution exception: {e}")
            returncode = -1

        duration = time.time() - start_time
        success = (returncode == 0)
        summary = " | ".join(error_lines[-5:]) if error_lines else f"Process exited with {returncode}"

        # Update quarantine manager
        if self.quarantine:
            # Get current template version
            tmpl_path = self.adapter.get_template_path(pkgname)
            ver = "unknown"
            if tmpl_path:
                try:
                    from core.template import TemplateEditor
                    ver = TemplateEditor.parse_file(tmpl_path).version
                except Exception:
                    pass

            if success:
                self.quarantine.record_success(pkgname)
            else:
                self.quarantine.record_failure(pkgname, ver, summary)

        return BuildResult(
            pkgname=pkgname,
            arch=arch,
            success=success,
            returncode=returncode,
            duration_seconds=round(duration, 2),
            error_summary=summary if not success else "",
            log_path=log_file,
        )
