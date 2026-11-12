"""
Unit test and audit for XBPS version comparison semantics.
Compares pure-Python comparison against native /usr/bin/xbps-uhelper cmpver.
"""

import re
import subprocess
import unittest


def xbps_native_cmpver(v1: str, v2: str, pkgname: str = "pkg") -> int:
    """Run /usr/bin/xbps-uhelper cmpver <pkg>-<v1> <pkg>-<v2>."""
    p1 = f"{pkgname}-{v1}"
    p2 = f"{pkgname}-{v2}"
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
    return 0


def xbps_python_cmpver(v1: str, v2: str) -> int:
    """
    Pure-Python implementation of Void Linux XBPS version comparison semantics.
    Matches xbps_cmpver in xbps C library.
    """
    if v1 == v2:
        return 0

    # Separate revision if present (e.g. "1.0_2" -> ("1.0", 2))
    def split_rev(v: str):
        if "_" in v:
            base, rev_str = v.rsplit("_", 1)
            try:
                return base, int(rev_str)
            except ValueError:
                return v, 0
        return v, 0

    base1, rev1 = split_rev(v1)
    base2, rev2 = split_rev(v2)

    # Tokenize into digits and non-digits
    def tokenize(s: str):
        tokens = []
        for t in re.findall(r'\d+|[a-zA-Z]+', s):
            if t.isdigit():
                tokens.append((0, int(t)))
            else:
                tl = t.lower()
                # Pre-release tokens rank LOWER than standard release
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
        # If the extra token in toks2 is a pre-release (e.g. 2.0 vs 2.0rc1), then 2.0 > 2.0rc1!
        if toks2[len(toks1)][0] == 1:  # pre-release marker
            return 1
        return -1
    elif len(toks1) > len(toks2):
        if toks1[len(toks2)][0] == 1:
            return -1
        return 1

    # Base versions are equal: compare revisions
    if rev1 < rev2:
        return -1
    elif rev1 > rev2:
        return 1

    return 0


class TestVersionSemantics(unittest.TestCase):
    def test_compare_against_native_xbps(self):
        cases = [
            ("1.0_1", "1.0_2", -1),
            ("1.0_2", "1.0_1", 1),
            ("1.0_1", "1.0_1", 0),
            ("2.0rc1", "2.0", -1),
            ("2.0", "2.0rc1", 1),
            ("1.2.3", "1.2.4", -1),
            ("1.10.0", "1.9.9", 1),
            ("0.19.0", "0.19.2", -1),
            ("20260101", "20260102", -1),
            ("1.0a", "1.0b", -1),
        ]

        for v1, v2, expected in cases:
            native_res = xbps_native_cmpver(v1, v2)
            python_res = xbps_python_cmpver(v1, v2)
            self.assertEqual(native_res, expected, f"Native mismatch on {v1} vs {v2}")
            self.assertEqual(python_res, expected, f"Python mismatch on {v1} vs {v2}")


if __name__ == "__main__":
    unittest.main()
