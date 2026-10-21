#!/usr/bin/env python3
"""Aplica una version nueva a un template de z-packages.

    apply-update.py <pkgname> <new_version> [<new_distfiles_url>]

Escribe SOLO srcpkgs/<dir>/template:

  version=   la nueva
  revision=  1 (subida de version upstream: el binario es distinto)
  distfiles= la nueva URL, si se pasa (canales rodantes cuya URL no se
             deduce de la version: vscode, spotify)

El checksum NO se toca: lo recalcula xgensum, que es la unica forma de no
inventarse un sha256.

Lostemplates no se ejecutan nunca: se parsean.
"""

import os
import re
import sys


def find_template(root, name):
    """Localiza el template por nombre de directorio o por pkgname."""
    srcpkgs = os.path.join(root, "srcpkgs")
    direct = os.path.join(srcpkgs, name, "template")
    if os.path.isfile(direct):
        return direct
    for entry in sorted(os.listdir(srcpkgs)):
        path = os.path.join(srcpkgs, entry, "template")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^pkgname=(.+)$", line.strip())
                if m and m.group(1).strip('"') == name:
                    return path
    return None


def apply(path, version, new_distfiles=None):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    out, i, changed = [], 0, []
    while i < len(lines):
        line = lines[i]
        if re.match(r"^version=", line):
            out.append(f"version={version}")
            changed.append("version")
            i += 1
            continue
        if re.match(r"^revision=", line):
            out.append("revision=1")
            changed.append("revision")
            i += 1
            continue
        if new_distfiles and re.match(r"^distfiles=", line):
            # El bloque puede seguir en las lineas siguientes (comilla abierta
            # al estilo Void): se come hasta que las comillas cuadran.
            out.append(f'distfiles="{new_distfiles}"')
            changed.append("distfiles")
            i += 1
            joined = line
            while i < len(lines) and joined.count('"') % 2 == 1:
                joined += lines[i]
                i += 1
            continue
        out.append(line)
        i += 1

    if "version" not in changed:
        raise SystemExit(f"{path}: no tiene linea version=")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return changed


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    root = os.environ.get("REPO_ROOT", os.getcwd())
    name, version = sys.argv[1], sys.argv[2]
    new_distfiles = sys.argv[3] if len(sys.argv) > 3 else None
    path = find_template(root, name)
    if not path:
        raise SystemExit(f"no encuentro el template de {name}")
    changed = apply(path, version, new_distfiles)
    print(f"{name}: {version} ({', '.join(changed)}) en {path}")


if __name__ == "__main__":
    main()
