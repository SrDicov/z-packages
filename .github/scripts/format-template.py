#!/usr/bin/env python3
"""Normaliza un template de z-packages al estilo que exige el AGENTS.md.

    format-template.py [--check] [--report] <template> [<template> ...]

Que hace (y solo esto):
  - pone el header "# Template file for '<pkgname>'" como primera linea;
  - borra los comentarios "#" de las lineas >= 2 (ley de cero comentarios);
  - reordena el bloque de variables segun el orden que xlint exige;
  - pasa la indentacion de las funciones a tabs;
  - parte en varias lineas las listas que no caben en 80 columnas, con una
    continuacion de un solo espacio;
  - normaliza short_desc (sin punto final, sin articulo, en mayuscula) y
    license (SPDX).

No toca el contenido: ni el maintainer, ni las deps, ni las versiones, ni los
nombres de archivo. Un template que xlint rechaza por otra causa (maintainer
sin email, licencia sin vlicense) sigue sin pasar: eso lo decide una persona,
no un bot.

Idempotencia: correrlo dos veces seguidas no cambia el archivo la segunda vez.
Es lo que permite al autoactualizador reescribir cualquier template sin dejar
basura en el diff.

Excepciones asumidas (y por que):
  - Una URL larga en distfiles no se parte: bash concatena las lineas de un
    valor entrecomillado con un espacio, asi que partir la URL la rompe. En
    void-packages oficial hay 6395 lineas asi: upstream tampoco las parte.
  - Las variables auxiliares (_xxx) van justo antes de distfiles: bash expande
    en el momento de asignar, y homepage (que suele ser de donde salen) esta
    definido despues en el orden canonico.
"""

import re
import sys

# El orden es el de xlint (xtools). Los huecos (6, 7, 13, 14) son los que
# xlint salta; las auxiliares van aparte, justo antes de distfiles.
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
# Las auxiliares se colocan antes de distfiles pero despues de homepage:
# bash expande al asignar y homepage suele ser de donde salen.
AUX_INDEX = 23.5
UNKNOWN_INDEX = 27

# Licencias que xlint exige instalar con vlicense (xtools/xlint:421).
V_LICENSE = re.compile(
    r"\b(MIT|ISC|Zlib|BSD-[0-9A-Za-z-]+|Apache-[0-9.]+|custom:[^\s\",]+"
    r"|GPL-[0-9.]+-or-later|LGPL-[0-9.]+)\b")

MAXLEN = 80


def to_tabs(line):
    m = re.match(r"^([ \t]+)", line)
    if not m:
        return line
    ind = m.group(1)
    spaces, tabs = ind.count(" "), ind.count("\t")
    return "\t" * (spaces // 4 + tabs) + " " * (spaces % 4) + line[len(ind):]


def strip_quotes(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def fix_short_desc(value):
    """xlint: sin punto final, sin articulo inicial, en mayuscula, <=72."""
    v = strip_quotes(value).strip().rstrip(".")
    for art in ("The ", "A ", "An "):
        if v.startswith(art):
            v = v[len(art):]
            break
    if v and v[0].islower():
        v = v[0].upper() + v[1:]
    if len(v) > 72:
        v = v[:72].rsplit(" ", 1)[0]
    return v.rstrip()


LICENSE_MAP = {
    "Mozilla Public License Version 2.0": "MPL-2.0",
    "Proprietary": "custom:Proprietary",
    "BSD": "BSD-3-Clause",
    "GPL2": "GPL-2.0-or-later",
    "GPL3": "GPL-3.0-or-later",
    "LGPL2.1": "LGPL-2.1-or-later",
    "LGPL3": "LGPL-3.0-or-later",
    "MPL2": "MPL-2.0",
    "Public Domain": "custom:Public Domain",
}


def fix_license(value):
    """xlint: identificador SPDX (o custom:*)."""
    parts = [LICENSE_MAP.get(p.strip(), p.strip())
             for p in strip_quotes(value).split(",")]
    return ", ".join(p for p in parts if p)


def strip_comment(line):
    """Quita el comentario final de una linea de codigo.

    '#' es comentario solo si no esta dentro de comillas ni dentro de una
    expansion ${...}: '${pkgname#-git}' es codigo, no un comentario.

    Las lineas con sustitucion de comandos ($( ) o `) se dejan tal cual: bash
    re-parsea las comillas dentro de la sustitucion, y un parser de comillas
    plano las contaria mal y cortaria codigo (el clasico
    $(sed -n "s/^#define x" file)). Perder un comentario inline es mucho mas
    barato que perder una linea de codigo.
    """
    if "$(" in line or "`" in line or "${" in line:
        return line.rstrip()
    single = double = brace = 0
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and not single:
            i += 2
            continue
        if c == "'" and not double:
            single = not single
        elif c == '"' and not single:
            double = not double
        elif c == "$" and line[i + 1:i + 2] == "{" and not single and not double:
            brace += 1
        elif c == "}" and brace:
            brace -= 1
        elif c == "#" and not single and not double and not brace:
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def heredoc_tag(line):
    m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?", line)
    return m.group(1) if m else None


def open_quote(s):
    """True si la linea deja una comilla doble abierta."""
    n = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            n += 1
        i += 1
    return n % 2 == 1


def split_entries(lines):
    """-> (entradas, resto).

    Una entrada es una asignacion con sus lineas de continuacion. El bloque de
    variables termina en la primera linea vacia o que no es una asignacion.
    Los comentarios se descartan: la ley de cero comentarios va a ser la que
    decida que no hay que adivinar a que variable perteneceria.
    """
    entries, rest = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#"):
            i += 1
            continue
        if line.strip() == "" or line.startswith("{"):
            if entries:
                rest = lines[i:]
                break
            i += 1
            continue
        m = ENTRY_RE.match(line)
        if not m:
            # No es una variable (una funcion, un if...): aqui acaba el
            # bloque. Si se perdiera, el template quedaria sin do_install.
            rest = lines[i:]
            break
        entry = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.startswith((" ", "\t")):
                entry.append(nxt)
                i += 1
                continue
            # La comilla abierta en su propia linea se cierra mas abajo:
            # esa linea sigue siendo parte del valor.
            if open_quote("".join(entry)):
                entry.append(nxt)
                i += 1
                continue
            break
        entries.append(entry)
    return entries, rest


ENTRY_RE = re.compile(r"^(export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")


def entry_name(entry):
    return ENTRY_RE.match(entry[0]).group(2)


def entry_prefix(entry):
    return ENTRY_RE.match(entry[0]).group(1) or ""


def entry_value(entry):
    """Valor unificado de una entrada: sin las comillas, continuaciones
    pegadas con un espacio (es lo que bash hace al|sourcear)."""
    first = entry[0][ENTRY_RE.match(entry[0]).end():].strip()
    quoted = first.startswith('"')
    if len(first) >= 2 and first[0] == '"' and first[-1] == '"':
        value = first[1:-1]
    elif quoted:
        body = first[1:]
        body += "".join(" " + l.strip() for l in entry[1:])
        value = body.rstrip('"')
    else:
        value = first
    # Normaliza: 'makedepends="' con la comilla abierta en su propia linea
    # deja un espacio inicial que al re-renderizar produce un '"' suelto.
    return " ".join(value.split()), quoted


def render_entry(name, value, quoted, prefix=""):
    """Reescribe la entrada, partiendo la lista en lineas si no cabe.

    Solo se parte si el valor tiene mas de un elemento: partir un elemento
    unico (una URL) no es posible sin romperlo. El reparto es codicioso
    (rellena hasta 80 columnas), igual que en void-packages.
    """
    head = f"{prefix}{name}="
    line = f'{head}"{value}"' if quoted else f"{head}{value}"
    if len(line) <= MAXLEN:
        return [line]
    elements = value.split(" ")
    if len(elements) < 2:
        return [line]
    out = []
    cur = f'{head}"{elements[0]}'
    for el in elements[1:]:
        if len(cur) + 1 + len(el) <= MAXLEN - 1:
            cur += " " + el
        else:
            out.append(cur)
            cur = " " + el
    out.append(cur + '"')
    return out


def entry_index(entry):
    name = entry_name(entry)
    if name in SKIP:
        return 100, name
    if name.startswith("_"):
        return AUX_INDEX, name
    return INDEX.get(name, UNKNOWN_INDEX), name


def process(path, report):
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
        report.append(f"{path}: sin pkgname, se deja como esta")
        return None

    entries, rest = split_entries(lines)
    if not entries:
        report.append(f"{path}: sin bloque de variables, se deja como esta")
        return None

    keyed = []
    for pos, entry in enumerate(entries):
        name = entry_name(entry)
        prefix = entry_prefix(entry)
        value, quoted = entry_value(entry)
        if name == "short_desc":
            value = fix_short_desc(value)
            quoted = True
        elif name == "license":
            value = fix_license(value)
            quoted = True
        idx, _ = entry_index(entry)
        keyed.append((idx, pos, name, value, quoted, prefix))

    keyed.sort(key=lambda t: (t[0], t[1]))

    var_lines = []
    for _, _, name, value, quoted, prefix in keyed:
        var_lines.extend(render_entry(name, value, quoted, prefix))

    # Comentarios fuera de los heredocs: se borran (ley de cero comentarios).
    # Solo en el bloque de codigo: las variables ya vinieron limpias y el
    # header se pone al final, cuando ya no puede ser Eliminado.
    code_lines, in_here = [], None
    for line in rest:
        if in_here:
            code_lines.append(line)
            if line.strip() == in_here:
                in_here = None
            continue
        tag = heredoc_tag(line)
        if tag:
            in_here = tag
            code_lines.append(to_tabs(line))
            continue
        if line.lstrip().startswith("#"):
            continue
        # xlint exige que make_check= lleve el motivo (asi lo hace
        # void-packages: "make_check=no # los tests fallan"). Se preserva.
        if line.lstrip().startswith("make_check="):
            code_lines.append(to_tabs(line))
            continue
        code_lines.append(to_tabs(strip_comment(line)))

    new_lines = [f"# Template file for '{pkgname}'"] + var_lines + code_lines
    result = "\n".join(new_lines) + "\n"
    return original, result


def main():
    args = sys.argv[1:]
    check = "--check" in args
    do_report = "--report" in args
    paths = [a for a in args if not a.startswith("--")]
    report = []
    changed = 0
    for path in paths:
        res = process(path, report)
        if res is None:
            continue
        original, result = res
        if result != original:
            changed += 1
            if not check:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(result)
        if do_report:
            for ln in result.splitlines():
                if len(ln) > MAXLEN:
                    report.append(f"{path}: {len(ln)} columnas: {ln[:60]}...")
            if V_LICENSE.search(result) and "vlicense" not in result:
                report.append(f"{path}: license que exige vlicense, sin vlicense")
    for line in report:
        print(line)
    print(f"{changed} template(s) {'pendientes' if check else 'normalizados'}")
    return 1 if check and changed else 0


if __name__ == "__main__":
    sys.exit(main())
