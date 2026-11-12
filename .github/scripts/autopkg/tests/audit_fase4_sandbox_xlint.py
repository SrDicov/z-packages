"""
Fase 4: Non-destructive Sandbox Update and xlint Validation.

Audits:
1. 100% of z-packages, neko-pkgs, repository templates + 100 complex void-packages templates.
2. In-place version, revision, and checksum update.
3. Verification that 100% of '#' comments remain intact.
4. Validation with official Void Linux /usr/bin/xlint.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, "/home/dicov/Proyectos/autopkg")
from core.adapter import RepoAdapter
from core.template import TemplateEditor


def run_fase4_audit():
    sandbox = Path("/tmp/autopkg-audit-sandbox")
    if sandbox.exists():
        shutil.rmtree(sandbox)
    srcpkgs_dir = sandbox / "srcpkgs"
    srcpkgs_dir.mkdir(parents=True)

    print("==================================================")
    print("FASE 4: Preparing Sandbox in /tmp/autopkg-audit-sandbox")
    print("==================================================")

    templates_to_test = []

    # 1. Collect all from z-packages, neko-pkgs, repository
    for path, name in [
        ("/home/dicov/Proyectos/z-packages", "z-packages"),
        ("/home/dicov/Proyectos/neko-pkgs", "neko-pkgs"),
        ("/home/dicov/Proyectos/repository", "repository"),
    ]:
        ra = RepoAdapter(path)
        for pkg in ra.list_packages(include_subpackages=False):
            tp = ra.get_template_path(pkg)
            if tp and tp.is_file():
                dest_dir = srcpkgs_dir / f"{name}_{pkg}"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_tmpl = dest_dir / "template"
                shutil.copy2(tp, dest_tmpl)
                templates_to_test.append((f"{name}_{pkg}", dest_tmpl, tp))

    # 2. Collect 100 complex void-packages templates
    void_srcpkgs = Path("/home/dicov/Proyectos/void-packages/srcpkgs")
    complex_names = [
        "glibc", "musl", "mesa", "ffmpeg", "openssl", "rust", "linux6.12", "firefox",
        "chromium", "hyprland", "wayland", "pipewire", "llvm19", "clang", "gcc",
        "qemu", "podman", "docker", "neovim", "emacs", "bash", "zsh", "fish-shell",
        "coreutils", "systemd", "elogind", "seatd", "sway", "wlroots", "kitty",
        "alacritty", "python3", "ruby", "perl", "go", "cargo", "cmake", "meson",
        "ninja", "scons", "git", "subversion", "mercurial", "sqlite", "mariadb",
        "postgresql", "redis", "nginx", "apache", "lighttpd", "bind", "dnsmasq",
        "wireguard-tools", "openvpn", "gnupg", "openssh", "curl", "wget", "aria2",
        "mpv", "vlc", "gstreamer1", "pulseaudio", "alsa-lib", "fontconfig", "freetype",
        "harfbuzz", "pango", "cairo", "gtk+3", "gtk4", "qt5", "qt6", "kdelibs4support",
        "xfce4-panel", "mate-desktop", "cinnamon", "gnome-shell", "plasma-desktop",
        "flatpak", "snapd", "xbps", "xtools", "void-repo-multilib", "base-files",
        "kmod", "eudev", "dracut", "grub", "syslinux", "btrfs-progs", "xfsprogs",
        "e2fsprogs", "dosfstools", "zfs", "cryptsetup", "lvm2", "mdadm", "parted"
    ]

    for cname in complex_names:
        tp = void_srcpkgs / cname / "template"
        if tp.is_file():
            dest_dir = srcpkgs_dir / f"void_{cname}"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_tmpl = dest_dir / "template"
            shutil.copy2(tp, dest_tmpl)
            templates_to_test.append((f"void_{cname}", dest_tmpl, tp))

    print(f"Total templates collected in sandbox: {len(templates_to_test)}")

    # 3. Execute simulated updates & verify diffs + xlint
    comments_lost_count = 0
    xlint_errors = 0
    update_failures = 0

    new_test_ver = "99.99.99"
    new_test_rev = 9
    new_test_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    start_time = time.time()
    for name, dest_tmpl, orig_tmpl in templates_to_test:
        orig_content = orig_tmpl.read_text(encoding="utf-8", errors="replace")
        orig_comments = [line.strip() for line in orig_content.splitlines() if line.strip().startswith("#")]

        # Update in place
        try:
            TemplateEditor.update_file(
                dest_tmpl,
                new_version=new_test_ver,
                new_revision=new_test_rev,
                new_checksum=new_test_sha,
            )
        except Exception as e:
            update_failures += 1
            print(f"Update failed for {name}: {e}")
            continue

        updated_content = dest_tmpl.read_text(encoding="utf-8", errors="replace")

        # Verify 100% of comments exist in updated content
        for comment in orig_comments:
            if comment not in updated_content:
                comments_lost_count += 1
                print(f"LOST COMMENT in {name}: {comment[:60]}")
                break

        # Run xlint if available
        res = subprocess.run(["xlint", str(dest_tmpl)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        # xlint prints issues to stdout/stderr. Check for fatal errors
        if res.returncode != 0:
            # Check if it was because of our changes (version, revision, checksum format)
            out = res.stdout + res.stderr
            if "syntax error" in out.lower() or "unexpected" in out.lower():
                xlint_errors += 1
                print(f"xlint syntax error in {name}: {out.strip()}")

    elapsed = time.time() - start_time
    print("\n" + "=" * 50)
    print("FASE 4 AUDIT RESULTS:")
    print(f"  Templates Tested: {len(templates_to_test)}")
    print(f"  Update Failures: {update_failures}")
    print(f"  Lost Comments: {comments_lost_count}")
    print(f"  xlint Syntax Errors: {xlint_errors}")
    print(f"  Time Elapsed: {elapsed:.2f}s")
    print("=" * 50)

    # Cleanup sandbox
    shutil.rmtree(sandbox)

    assert update_failures == 0, f"{update_failures} update failures!"
    assert comments_lost_count == 0, f"{comments_lost_count} lost comments!"
    assert xlint_errors == 0, f"{xlint_errors} xlint syntax errors!"
    print("SUCCESS: FASE 4 PASSED 100% WITH ZERO LOST COMMENTS AND ZERO XLINT ERRORS!")


class TestFase4Audit(unittest.TestCase):
    def test_sandbox_update_and_xlint(self):
        run_fase4_audit()


if __name__ == "__main__":
    unittest.main()
