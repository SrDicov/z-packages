#!/usr/bin/env python3
"""Elige que paquetes se actualizan esta noche.

    plan-updates.py [updates.json] [max] -> imprime el JSON de la matrix

Techo de 5 por noche, a proposito: un upstream roto (un .deb renombrado, un
tag cambiado) no debe entrar en 60 paquetes de golpe.

Orden: primero los que mas rezago tienen. Con orden alfabetico, los
ultimos de la lista no se actualizarian nunca mientras haya cola.
"""

import json
import sys

CAP = 5


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "updates.json"
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else CAP
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)

    cands = [r for r in data["packages"] if r.get("status") == "update"]
    cands.sort(key=lambda r: (-r.get("behind", 0), r["pkgname"]))
    out = [
        {
            "pkg": r["pkgname"],
            "dir": r["dir"],
            "version": r["new_version"],
            "distfiles": r.get("new_distfiles") or "",
        }
        for r in cands[:cap]
    ]
    print(json.dumps(out))


if __name__ == "__main__":
    main()
