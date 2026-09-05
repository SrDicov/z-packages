#!/usr/bin/env python3
"""Genera el .VURINFO raíz (array JSON, esquema v1) que consume vary.

Sin masterdir ni contenedor: parseo ESTÁTICO de srcpkgs/*/template con la
misma semántica que el fallback de vary (`parse_template_text` en
vary/src/vur_client.rs). Vale porque en este repo las plantillas son
asignaciones estáticas (version sin sustituciones, ver AGENTS.md).

Limitaciones conocidas (iguales que el fallback de vary):
  - Sin evaluación: no hay subpaquetes (siempre []), ni variables
    condicionales, ni XBPS_PKG_OPTIONS.
  - pkgname duplicado entre plantillas (p. ej. font-inter-variable declara
    pkgname=font-inter): gana la primera, se avisa por stderr.

Uso:
    gen-vurinfo.py [salida] [dir-srcpkgs]
    Defectos: ./.VURINFO  ./srcpkgs

Salida: array JSON ordenado por pkgname (diffs estables). Falla si no se
indexa ningún paquete o si la validación del esquema v1 no pasa.
"""

import json
import os
import re
import sys

FORMAT_VERSION = 1

RELEVANT = {
    "pkgname", "version", "revision", "archs", "only_for_archs",
    "depends", "hostmakedepends", "makedepends", "checkdepends",
    "build_style", "distfiles", "checksum", "provides", "replaces",
    "restricted", "maintainer", "short_desc", "license", "homepage",
}

RE_PKGNAME = re.compile(r"^[a-zA-Z0-9._+\-]+$")
RE_VERSION = re.compile(r"^[A-Za-z0-9._+]+$")


def warn(msg):
    print("gen-vurinfo: aviso: {}".format(msg), file=sys.stderr)


def die(msg):
    print("gen-vurinfo: error: {}".format(msg), file=sys.stderr)
    sys.exit(1)


def parse_template(text):
    """Extrae variables relevantes sin evaluar bash (réplica del fallback)."""
    variables = {}
    lines = iter(text.splitlines())
    buf = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Ignorar definiciones de funciones y bloques shell.
        if (stripped.startswith("do_") or stripped.startswith("pre_")
                or stripped.startswith("post_") or stripped in ("}", "{")):
            if "()" in stripped:
                for inner in lines:
                    if inner.strip() == "}":
                        break
            continue
        buf = line
        # Continuación con backslash.
        while buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1]
            try:
                nxt = next(lines)
            except StopIteration:
                break
            buf += " " + nxt
        # Multilínea entre comillas dobles.
        while buf.count('"') % 2 == 1:
            try:
                nxt = next(lines)
            except StopIteration:
                break
            buf += "\n" + nxt
            if '"' in nxt:
                break
        if "=" not in buf:
            continue
        key, _, val = buf.partition("=")
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z0-9_]+", key):
            continue
        if key not in RELEVANT:
            continue
        val = val.strip()
        # Quitar comentario final (" #") fuera de comillas.
        hash_at = val.find(" #")
        if hash_at != -1:
            before = val[:hash_at]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                val = before.strip()
        # Desentrecomillar.
        if (len(val) >= 2 and val[0] == val[-1] and val[0] in ("\"", "'")):
            val = val[1:-1]
        elif val[:1] in ("\"", "'"):
            quote = val[0]
            val = val[1:]
            end = val.rfind(quote)
            if end != -1:
                val = val[:end]
        # Colapsar whitespace a espacios.
        val = " ".join(val.split())
        variables[key] = val
    return variables


def to_vurinfo(pkg_vars):
    pkgname = pkg_vars.get("pkgname", "")
    if not pkgname:
        return None, "template sin pkgname"
    version = pkg_vars.get("version", "1.0")
    try:
        revision = int(pkg_vars.get("revision", "1"))
    except ValueError:
        revision = 1
    archs_raw = pkg_vars.get("only_for_archs") or pkg_vars.get("archs", "")
    if not archs_raw:
        archs = ["all"]
    else:
        archs = [a.rstrip("*") for a in archs_raw.split()]
        archs = [a for a in archs if a]

    def split_list(key):
        return pkg_vars.get(key, "").split() if pkg_vars.get(key) else []

    checksum = []
    for entry in split_list("checksum"):
        if entry == "SKIP" or entry.startswith("sha256:"):
            checksum.append(entry)
        else:
            checksum.append("sha256:" + entry)

    return {
        "format_version": FORMAT_VERSION,
        "pkgname": pkgname,
        "version": version,
        "revision": revision,
        "archs": archs,
        "subpackages": [],
        "depends": split_list("depends"),
        "hostmakedepends": split_list("hostmakedepends"),
        "makedepends": split_list("makedepends"),
        "checkdepends": split_list("checkdepends"),
        "build_style": pkg_vars.get("build_style") or None,
        "distfiles": split_list("distfiles"),
        "checksum": checksum,
        "provides": split_list("provides"),
        "replaces": split_list("replaces"),
        "restricted": pkg_vars.get("restricted") in ("yes", "true", "1"),
        "maintainer": pkg_vars.get("maintainer") or None,
    }, None


def validate(info):
    """Reglas del esquema v1 (réplica de VurInfo::validate)."""
    if info["format_version"] != 1:
        return "format_version != 1"
    name = info["pkgname"]
    if not name or not RE_PKGNAME.match(name) or name.startswith("-"):
        return "pkgname inválido: {!r}".format(name)
    if not info["version"] or not RE_VERSION.match(info["version"]):
        return "version inválida: {!r}".format(info["version"])
    if info["revision"] < 1:
        return "revision < 1"
    if not info["archs"]:
        return "archs vacío"
    for entry in info["checksum"]:
        if entry != "SKIP" and not entry.startswith("sha256:"):
            return "checksum inválido: {!r}".format(entry)
    return None


def main(argv):
    out_file = argv[1] if len(argv) > 1 else ".VURINFO"
    srcpkgs = argv[2] if len(argv) > 2 else "srcpkgs"
    if not os.path.isdir(srcpkgs):
        die("no existe el directorio {}".format(srcpkgs))

    entries = {}
    order = []
    for dirname in sorted(os.listdir(srcpkgs)):
        tmpl = os.path.join(srcpkgs, dirname, "template")
        if not os.path.isfile(tmpl):
            continue
        with open(tmpl, encoding="utf-8", errors="replace") as handle:
            variables = parse_template(handle.read())
        info, err = to_vurinfo(variables)
        if err is not None:
            warn("{}: {}".format(tmpl, err))
            continue
        problem = validate(info)
        if problem is not None:
            warn("{}: se omite ({})".format(tmpl, problem))
            continue
        if info["pkgname"] in entries:
            warn("{}: pkgname {!r} duplicado, gana la primera plantilla".format(
                tmpl, info["pkgname"]))
            continue
        entries[info["pkgname"]] = info
        order.append(info["pkgname"])
        print("==> {} ({})".format(dirname, info["pkgname"]), file=sys.stderr)

    if not order:
        die("ningún paquete indexado")

    index = [entries[name] for name in sorted(order)]
    tmp = out_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, out_file)
    print("indexados: {} paquetes -> {}".format(len(index), out_file),
          file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
