"""
Repository Indexer, Key Management, and Signing for Void Linux xbps repositories.

Implements all battle-tested invariants from z-repo:
1. Prunes old duplicate versions/revisions of packages.
2. Signs .xbps packages with private key.
3. Cleans and generates repodata (xbps-rindex).
4. Generates <md5_fingerprint>.plist for automatic key trusting in /var/db/xbps/keys/.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import plistlib
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("autopkg.publisher.indexer")


@dataclass
class KeyInfo:
    privkey_path: Path
    pubkey_path: Path
    fingerprint: str
    plist_path: Path
    bits: int
    signed_by: str


class RepoIndexer:
    def __init__(
        self,
        binpkgs_dir: str | Path,
        arch: str = "x86_64",
        signed_by: str = "Void Package Auto-Builder <autobuild@local>",
    ) -> None:
        self.binpkgs_dir = Path(binpkgs_dir).resolve()
        self.arch = arch
        self.signed_by = signed_by

    def prune_old_versions(self) -> List[Path]:
        """
        Keep only the newest <pkg>-<ver>_<rev> per pkgname.
        Returns the list of removed files.
        """
        removed: List[Path] = []
        pkgs = defaultdict(list)

        pattern = re.compile(rf"^(.+)-([^-]+)_([^.]+)\.{re.escape(self.arch)}\.xbps$")

        for f in self.binpkgs_dir.glob(f"*.{self.arch}.xbps"):
            m = pattern.match(f.name)
            if m:
                pkgname = m.group(1)
                ver = m.group(2)
                rev_str = m.group(3)
                try:
                    rev = int(rev_str)
                except ValueError:
                    rev = 0
                pkgs[pkgname].append((ver, rev, f))

        def version_key(v: str) -> tuple:
            return tuple(
                (0, int(p)) if p.isdigit() else (1, p)
                for p in re.findall(r"\d+|[A-Za-z]+", v)
            )

        for name, versions in pkgs.items():
            if len(versions) <= 1:
                continue
            # Sort by version tuple, then integer revision
            versions.sort(key=lambda t: (version_key(t[0]), t[1]))
            # All except the last are older and should be pruned
            for _, _, old_file in versions[:-1]:
                for ext in ("", ".sig2", ".sig"):
                    target = Path(str(old_file) + ext)
                    if target.is_file():
                        try:
                            target.unlink()
                            removed.append(target)
                            logger.info("Pruned old package artifact: %s", target.name)
                        except OSError:
                            pass

        return removed

    def sign_and_index(
        self,
        privkey_pem: str | Path,
        pubkey_name: str = "repo.pub",
    ) -> Optional[KeyInfo]:
        """
        Signs all packages, generates clean repodata, and creates <fingerprint>.plist.
        """
        # Ensure private key is accessible
        if isinstance(privkey_pem, str) and not Path(privkey_pem).is_file():
            # Treat as PEM string content
            key_path = Path("/tmp/autopkg_privkey.pem")
            key_path.write_text(privkey_pem, encoding="utf-8")
            key_path.chmod(0o600)
        else:
            key_path = Path(privkey_pem).resolve()

        # Derive public key and fingerprint
        key_info = self._generate_key_metadata(key_path, pubkey_name)
        if not key_info:
            logger.error("Failed to generate key metadata and plist")
            return None

        xbps_files = list(self.binpkgs_dir.glob(f"*.{self.arch}.xbps"))
        if not xbps_files:
            logger.warning("No xbps packages found to index in %s", self.binpkgs_dir)
            return key_info

        # 1. Sign all .xbps packages
        # Remove leftover signatures from retired keys
        for sig in self.binpkgs_dir.glob(f"*.{self.arch}.xbps.sig2"):
            try:
                sig.unlink()
            except OSError:
                pass

        sign_cmd = [
            "xbps-rindex", "--sign-pkg",
            "--privkey", str(key_path),
        ] + [f.name for f in xbps_files]
        subprocess.run(sign_cmd, cwd=str(self.binpkgs_dir), check=False)

        # 2. Clean repodata and rebuild fresh
        for old_repodata in self.binpkgs_dir.glob("*-repodata*"):
            try:
                old_repodata.unlink()
            except OSError:
                pass

        add_cmd = ["xbps-rindex", "-a"] + [f.name for f in xbps_files]
        subprocess.run(add_cmd, cwd=str(self.binpkgs_dir), check=False)

        # 3. Sign the repository metadata
        sign_repo_cmd = [
            "xbps-rindex", "--sign",
            "--signedby", self.signed_by,
            "--privkey", str(key_path),
            ".",
        ]
        subprocess.run(sign_repo_cmd, cwd=str(self.binpkgs_dir), check=False)

        return key_info

    def _generate_key_metadata(self, privkey_path: Path, pubkey_name: str) -> Optional[KeyInfo]:
        """Derive public key in PEM, compute MD5 fingerprint, and generate <fp>.plist."""
        pubkey_path = self.binpkgs_dir / pubkey_name

        try:
            # openssl pkey -in privkey -pubout -out pubkey
            res = subprocess.run(
                ["openssl", "pkey", "-in", str(privkey_path), "-pubout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            pub_bytes = res.stdout
            pubkey_path.write_bytes(pub_bytes)

            # Compute MD5 fingerprint matching ssh-keygen / xbps format
            # xbps uses the RSA public key's MD5 fingerprint in format: xx:yy:zz:...
            # Derived via: ssh-keygen -i -m PKCS8 -f pub.pem | ssh-keygen -lf - -E md5
            p1 = subprocess.Popen(
                ["ssh-keygen", "-i", "-m", "PKCS8", "-f", str(pubkey_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                ["ssh-keygen", "-lf", "-", "-E", "md5"],
                stdin=p1.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if p1.stdout:
                p1.stdout.close()
            if p1.stderr:
                p1.stderr.close()
            out, _ = p2.communicate()
            p1.wait()

            # Output is like: "2048 MD5:12:34:56:... no (RSA)"
            parts = out.strip().split()
            bits = int(parts[0]) if parts else 2048
            fp_raw = parts[1] if len(parts) > 1 else ""
            if fp_raw.startswith("MD5:"):
                fingerprint = fp_raw[4:]
            else:
                fingerprint = fp_raw

            if not fingerprint:
                raise ValueError("Could not extract fingerprint from ssh-keygen")

            # Generate <fingerprint>.plist
            plist_data = {
                "public-key": pub_bytes,
                "public-key-size": bits,
                "signature-by": self.signed_by,
            }
            plist_path = self.binpkgs_dir / f"{fingerprint}.plist"
            with open(plist_path, "wb") as f:
                plistlib.dump(plist_data, f, fmt=plistlib.FMT_XML)

            return KeyInfo(
                privkey_path=privkey_path,
                pubkey_path=pubkey_path,
                fingerprint=fingerprint,
                plist_path=plist_path,
                bits=bits,
                signed_by=self.signed_by,
            )
        except Exception as e:
            logger.warning("Falling back to internal python fingerprint derivation: %s", e)
            # Internal fallback: hash the raw DER bytes
            der_bytes = self._pem_to_der(privkey_path.read_text(encoding="utf-8"))
            if der_bytes:
                md5_hex = hashlib.md5(der_bytes).hexdigest()
                fingerprint = ":".join(md5_hex[i:i+2] for i in range(0, len(md5_hex), 2))
                plist_path = self.binpkgs_dir / f"{fingerprint}.plist"
                plist_data = {
                    "public-key": pubkey_path.read_bytes() if pubkey_path.is_file() else b"",
                    "public-key-size": 2048,
                    "signature-by": self.signed_by,
                }
                with open(plist_path, "wb") as f:
                    plistlib.dump(plist_data, f, fmt=plistlib.FMT_XML)
                return KeyInfo(
                    privkey_path=privkey_path,
                    pubkey_path=pubkey_path,
                    fingerprint=fingerprint,
                    plist_path=plist_path,
                    bits=2048,
                    signed_by=self.signed_by,
                )
        return None

    @staticmethod
    def _pem_to_der(pem_str: str) -> Optional[bytes]:
        lines = [line.strip() for line in pem_str.splitlines() if not line.startswith("-----")]
        try:
            return base64.b64decode("".join(lines))
        except Exception:
            return None
