#!/usr/bin/env python3
"""Normaliza un template de z-packages al estilo que exige xlint.

    format-template.py <template> [<template> ...]

Que hace (y solo esto):
  - pone el header "# Template file for '<pkgname>'" como primera linea,
    conservando los comentarios que ya hubiera debajo;
  - reordena el bloque de variables segun el orden que xlint exige;
  - convierte la indentacion de las funciones a tabs.

No toca el contenido: ni el maintainer, ni las deps, ni las versiones. Un
template que xlint rechaza por otra causa (maintainer sin email, licencia sin
vlicense) sigue sin pasar: eso lo decide una persona, no un bot.

Las variables auxiliares (_site, _subver, _base...) se dejan antes de
distfiles: en bash una variable se expande al asignarse, asi que _site= tiene
que existir antes de la linea que la usa.
"""

import re
import sys

# El orden es el de xlint (common/xbps-src/shutils no lo impone, pero el
# linter de void-packages si).
ORDER = [
    (1, ["pkgname"]),
    (2, ["reverts"]),
    (3, ["version"]),
    (4, ["revision"]),
    (5, ["archs"]),
    (8, ["create_wrksrc"]),
    (9, ["build_wrksrc"]),
    (10, ["build_style", "build_helper"]),
    (11, ["metapackage"]),
    (12, ["cmake_args", "cmake_builddir", "configure_args", "configure_script",
           "go_build_tags", "go_get", "go_import_path", "go_ldflags",
           "go_package", "go_mod_mode", "make_build_args", "make_build_target",
           "make_check_args", "make_check_target", "make_cmd",
           "make_install_args", "make_install_target", "make_use_env",
           "meson_builddir", "meson_cmd", "meson_crossfile",
           "perl_configure_dirs", "pycompile_dirs", "pycompile_module",
           "python_versions", "qmake_default_version", "scons_use_destdir",
           "stackage", "cabal_index_state"]),
    (13, []),  # hueco para las variables auxiliares (_xxx)
    (15, ["hostmakedepends"]),
    (16, ["makedepends"]),
    (17, ["depends"]),
    (18, ["checkdepends"]),
    (19, ["short_desc"]),
    (20, ["maintainer"]),
    (21, ["license"]),
    (22, ["homepage"]),
    (23, ["changelog"]),
    (24, ["distfiles"]),
    (25, ["checksum"]),
    (26, ["alternatives", "fetch_cmd"]),
]
INDEX = {v: i for i, vs in ORDER for v in vs}
SKIP = {"conf_files", "make_dirs", "bootstrap"}
AUX_INDEX = 13


def to_tabs(line):
    m = re.match(r"^([ \t]+)", line)
    if not m:
        return line
    ind = m.group(1)
    spaces, tabs = ind.count(" "), ind.count("\t")
    return "\t" * (spaces // 4 + tabs) + " " * (spaces % 4) + line[len(ind):]


# Licencias que upstream escribe en texto plano y xlint quiere en SPDX.
LICENSE_MAP = {
    "Mozilla Public License Version 2.0": "MPL-2.0",
    "Proprietary": "custom:Proprietary",
}


def strip_quotes(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def fix_short_desc(value):
    """xlint: sin punto final y sin articulo inicial."""
    v = strip_quotes(value).strip().rstrip(".")
    for art in ("The ", "A ", "An "):
        if v.startswith(art):
            v = v[len(art):]
            break
    return v


def fix_license(value):
    """xlint: identificador SPDX (o custom:*)."""
    parts = [LICENSE_MAP.get(p.strip(), p.strip())
             for p in strip_quotes(value).split(",")]
    return ", ".join(parts)


def split_entries(lines):
    """-> (cabecera, entradas, resto). Una entrada es una asignacion con sus
    lineas de continuacion.

    El bloque de variables termina en la primera linea que no es una
    asignacion (un comentario no lo corta: los comentarios se guardan aparte).
    """
    entries, head, rest = [], [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "" or line.startswith("{"):
            # Una linea vacia solo corta el bloque si ya hay variables:
            # al principio del template puede separating el comentario
            # inicial del bloque.
            if entries:
                rest = lines[i:]
                break
            head.append(line)
            i += 1
            continue
        if line.startswith("#"):
            head.append(line)
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if not m:
            # No es una variable (una funcion, un if...): aqui acaba el
            # bloque. Si se perdiera, el template quedaria sin do_install.
            rest = lines[i:]
            break
        entry = [line]
        i += 1
        while i < len(lines) and lines[i].startswith((" ", "\t")):
            entry.append(lines[i])
            i += 1
        entries.append(entry)
    return head, entries, rest


def entry_index(entry):
    name = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", entry[0]).group(1)
    if name in SKIP:
        return 100, name
    if name.startswith("_"):
        return AUX_INDEX, name
    return INDEX.get(name, 27), name


def process(path):
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    lines = original.splitlines()

    pkgname = ""
    for line in lines:
        m = re.match(r"^pkgname=(\S+)", line)
        if m:
            pkgname = m.group(1).strip('"')
            break
    if not pkgname:
        print(f"{path}: sin pkgname, se deja como esta")
        return False

    head, entries, rest = split_entries(lines)
    if not entries:
        print(f"{path}: sin bloque de variables, se deja como esta")
        return False

    keyed = []
    for pos, entry in enumerate(entries):
        idx, name = entry_index(entry)
        # short_desc y license tienen reglas propias del linter.
        if name == "short_desc":
            entry = [f'short_desc="{fix_short_desc(entry[0].split("=", 1)[1])}"'] \
                + entry[1:]
        elif name == "license":
            entry = [f'license="{fix_license(entry[0].split("=", 1)[1])}"'] \
                + entry[1:]
        keyed.append((idx, pos, entry))
    keyed.sort(key=lambda t: (t[0], t[1]))

    header = f"# Template file for '{pkgname}'"
    comments = [l for l in head
                if l.startswith("#") and l.strip() != header]
    new_lines = [header] + comments
    for _, _, entry in keyed:
        new_lines.extend(entry)
    new_lines.extend(rest)

    # La indentacion a tabs, sin tocar el contenido de los heredocs.
    out, in_here = [], None
    for line in new_lines:
        if in_here:
            out.append(line)
            if line.strip() == in_here:
                in_here = None
            continue
        m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?", line)
        if m:
            in_here = m.group(1)
            out.append(to_tabs(line))
            continue
        out.append(to_tabs(line))

    result = "\n".join(out) + "\n"
    if result == original:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(result)
    return True


def main():
    changed = 0
    for path in sys.argv[1:]:
        if process(path):
            changed += 1
            print(f"normalizado {path}")
    print(f"{changed} template(s) normalizados")


if __name__ == "__main__":
    main()
