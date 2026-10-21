#!/usr/bin/env python3
"""Detecta actualizaciones upstream de los templates de z-packages.

SOLO LECTURA: no escribe ni un template. El/workflow que aplica vive aparte
(update-packages.yml) para que un fallo de deteccion nunca toque master.

De donde sale la respuesta de cada paquete:

  TAGS      distfiles con /archive/.../${version}  -> git ls-remote --tags
  RELEASE   distfiles con /releases/download/      -> API de GitHub
  PIN       _commit= + repository=                 -> git ls-remote
  TABLE     fuente explicita, una linea por paquete (host sin API)
  EXCLUDE   sin automatismo posible, con motivo

La comparacion de versiones la hace `xbps-uhelper cmpver`, nunca cadenas:
un hash no ordena (2ba032c > 1f00abc) y eso haria que xbps ignorase una
actualizacion en silencio.

Uso:
    detect-updates.py [--srcpkgs DIR] [--json OUT] [--md OUT] [--only PKG ...]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

NET_TIMEOUT = 25
# Un prerelease va pegado ("2.0.0alpha1") o con guion ("2.0.0-alpha1"), pero
# "rc" no puede casar dentro de "src" ni "pre" dentro de "preview": de ahi los
# limites por letra y no \b, que fallaria con "2.0.0alpha1" (alpha pegada al
# digito no tiene frontera de palabra).
PRE_RE = re.compile(r"(?<![a-z])(alpha|beta|pre|rc|dev|nightly)(?![a-z])", re.I)
# Fuente por paquete sin forma derivable de la URL. Verificada una a una:
# (kind, url, [regex version], [regex distfiles-url])
TABLE = {
    "codex-bin": (
        "apt",
        "https://persistent.oaistatic.com/codex-app-prod/linux/deb/dists/"
        "stable/main/binary-amd64/Packages",
    ),
    "unityhub-bin": (
        "regex",
        "https://public-cdn.cloud.unity3d.com/hub/prod/latest-linux.yml",
        r"^version:\s*(\S+)\s*$",
    ),
    "vscode-bin": (
        "regex2",
        "https://update.code.visualstudio.com/api/update/linux-x64/stable/latest",
        r'"name"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"([^"]+)"',
    ),
    "zig-nk-bin": (
        "json-keys",
        "https://ziglang.org/download/index.json",
    ),
    "reaper-bin": (
        "regex",
        "https://www.reaper.fm/download.php",
        r"Version\s+(\d+\.\d+)\s*:",
    ),
    "waterfox-bin": (
        "html-hrefs",
        "https://cdn1.waterfox.net/waterfox/releases/",
        r'href="(\d+(?:\.\d+)+)/"',
    ),
    "python3-steam": (
        "tags-prerelease",
        "https://github.com/solsticegamestudios/steam",
        "v",
    ),
    "arxy": (
        "tags",
        "https://github.com/SrDicov/arxy",
        "v",
    ),
    # El pool de Spotify publica un indice html con los .deb mas nuevos.
    # La subversion (g0eeebbed) va dentro del nombre, asi que hace falta
    # capturar tambien el nombre completo para rehacer la URL.
    "spotify-bin": (
        "regex2",
        "https://repository.spotify.com/pool/non-free/s/spotify-client/",
        r"spotify-client_([0-9]+(?:\.[0-9]+)+)\.[a-z0-9]+_amd64\.deb",
        r"spotify-client_[0-9]+(?:\.[0-9]+)+\.[a-z0-9]+_amd64\.deb",
    ),
    # La version sale de los tags del repo upstream, pero el .deb se descarga
    # del fichero "prod" sin version: el deb rotates y el template ya lo
    # asume (canal rodante a proposito, aqui la version si es comparable).
    "onlyoffice-bin": (
        "tags",
        "https://github.com/ONLYOFFICE/DesktopEditors",
        "v",
    ),
}
# Tags con forma de fecha (jwmkit-neko usa 20240327). Compararlos contra un
# version= normal daria "26.5 -> 20240327": un tag de 2024 contado como nuevo.
DATE_RE = re.compile(r"^\d{6,8}$")
# Sin automatismo posible. El motivo va al reporte: no es un hueco, es una
# decision. Si alguno se vuelve automatizable, sale de aqui.
#
# Tres familias, y conviene no confundirlas:
#  - canal rodante: la URL no cambia nunca, no hay version que comparar. La
#    caducidad la detecta el build por checksum, que es donde duele.
#  - version desacoplada: la etiqueta upstream no es el `version=` de xbps
#    (commit, o run+commit, o fecha). Proponer "actualizaciones" aqui
#    inventaria versiones.
EXCLUDE = {
    "xbps-triggers": "sincronizado de void-packages; el repo oficial ya lo trae",
    "neko-base": "metapaquete sin distfiles; la revision es manual a proposito",
    "librewolf-bin": "Codeberg no expone listado de su registro de paquetes",
    "qrunsvmgr-git": "version=r<run>.<commit>: el run es un contador local",
    "pavoldcontrol-git": "version=r<run>.<commit> y sin distfiles",
    "jwmkit-neko": "los tags upstream son fechas (20240327) y el version= es local",
    "libva-intel-driver-irql": "el distfile es un commit y el version= es una etiqueta local",
    "windscribe-desktop-bin": "el tag upstream es una fecha y el version= es la app",
    "libqt6pas-bin": "el asset se llama libqt6pas_6_2_10-1: puntos->guiones bajos y rev de empaquetado",
    "ly": "el asset es ly-v1.5.0-rc1 y el version= de xbps es 1.5.0.1: no son el mismo numero",
    "trinity-launcher-ap": "canal rodante /latest/: sin version que comparar",
    "linux-cachy-void-bin": "canal rodante /latest/: sin version que comparar",
    "linux-neko-rt-bin": "canal rodante /latest/: sin version que comparar",
    "linux-neko-zen-bin": "canal rodante /latest/: sin version que comparar",
    "steam-bin": "canal rodante: el .deb no lleva version en el nombre",
    "steam-nk-bin": "canal rodante: el .deb no lleva version en el nombre",
    "spotify-app-bin": "el pool de Spotify solo indexa la serie 1.2.x; la 1.3.x no aparece",
    "stremio-bin": "dl.strem.io no expone indice de versiones",
    "lsfg-vk-git": "el distfile es el tag v2.0.0-dev y el version= es 2026",
    "mangowc-latest": "el distfile es un commit y el version= es la version del fork",
    "msttcorefonts": "12 .exe de SourceForge sin version en la URL: el version= es fijo",
    "3utools-bin": "dl.3u.com no expone listado de versiones",
    "antigravity-bin": "el asset lleva un build id propio y el bucket no es listable",
    "dmemcg-booster-nk": "el distfile es un commit pineado y el version= va aparte",
}
# Caches de red. El detector pregunta por los mismos repos (brave-bin y
# brave-origin-bin son el mismo repo) y compara las mismas versiones mil
# veces. Sin esto son ~1800 subprocesos de xbps-uhelper por pasada.
_TAGS_CACHE = {}
_REL_CACHE = {}
_CMP_CACHE = {}


def fetch(url, headers=None):
    """-> (cuerpo, url_final). Sigue redirecciones: GitHub contesta 301 si el
    repo cambio de nombre y urllib sin seguimiento se queda con el cuerpo del
    error, no con la pagina buena."""
    hdr = {"User-Agent": "z-packages-detect"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def fetch_json(url, headers=None):
    """Devuelve (data, final_url). GitHub responde 301 si el repo cambio de
    nombre y sin seguimiento urllib se queda con el cuerpo del error: eso
    hacia que un repo renombrado pareciese "sin releases" en vez de "error"."""
    body, final = fetch(url, headers)
    try:
        return json.loads(body), final
    except json.JSONDecodeError:
        return None, final


def cmpver(a, b):
    """-1 si a<b, 0 si a==b, 1 si a>b. Delegado en xbps."""
    key = (a, b)
    if key in _CMP_CACHE:
        return _CMP_CACHE[key]
    try:
        rc = subprocess.run(
            ["xbps-uhelper", "cmpver", a, b], capture_output=True, timeout=15
        ).returncode
    except (OSError, subprocess.SubprocessError):
        rc = None
    out = {0: 0, 1: 1, 255: -1}.get(rc)
    _CMP_CACHE[key] = out
    return out


def prerelease(v):
    return bool(PRE_RE.search(v))


def tag_to_version(v):
    """Tag de git -> version de xbps. 2.0.0-alpha1 -> 2.0.0alpha1.

    xbps escribe los prerelease pegados (2.0.0alpha1) y git los pone con
    guion (2.0.0-alpha1). Sin esta conversion, el tag de python3-steam
    parecia otra version y cmpver no los reconocia.

    Los limites por letra evitan que "rc" se coma el final de "src": con
    un \\b el "alpha" pegado al digito tampoco casaria.
    """
    return re.sub(
        r"(?<![a-z])[-.]?(alpha|beta|rc|pre|dev)(\d*)", r"\1\2", v, count=1
    )


class Tpl:
    pass


def parse(path):
    """Lectura estatica: nunca hace source del template."""
    t = Tpl()
    t.path = path
    t.name = os.path.basename(os.path.dirname(path))
    t.vals = {}
    dist = []
    t._in_dist = False
    t._dist_buf = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if t._in_dist:
                t._dist_buf.append(stripped)
                joined = " ".join(t._dist_buf)
                # La comilla abre y cierra: cuando hay un numero par de
                # comillas, el valor esta completo.
                if joined.count('"') % 2 == 0:
                    t._in_dist = False
                    dist.append(clean_ws(joined))
                    t._dist_buf = []
                continue
            if stripped.startswith("distfiles="):
                body = stripped.split("=", 1)[1].strip()
                if body.count('"') % 2 == 1:
                    t._in_dist = True
                    t._dist_buf = [body]
                else:
                    dist.append(clean_ws(body))
                continue
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)="?([^"]*)"?\s*$', stripped)
            if m and m.group(1) not in t.vals:
                t.vals[m.group(1)] = m.group(2)
    t.distfiles = " ".join(x for x in dist if x)
    for k in ("pkgname", "version", "revision", "archs", "homepage",
              "repository", "_repository", "_commit", "_tag"):
        setattr(t, k, t.vals.get(k, ""))
    return t


def clean_ws(s):
    """Quita comillas, barras de continuacion y colapsa espacios."""
    return " ".join(s.replace('"', " ").replace("\\", " ").split())


def expand_aux(s, vals):
    """Sustituye las variables auxiliares del template (_site, _base, _tag...).

    El valor NO se reexpande: si _base contiene ${version}, ese ${version}
    tiene que seguir ahi para poder clasificar la URL.
    """
    return re.sub(
        r"\$\{?(_[A-Za-z0-9_]+)\}?",
        lambda m: vals.get(m.group(1), m.group(0)),
        s,
    )


def strip_rename(url):
    """github/...tar.gz>local.tar.gz -> la parte de la izquierda."""
    return url.split(">", 1)[0]


def classify(t):
    """-> (kind, detail). kind: tags|release|pin|table|exclude|none"""
    if t.name in EXCLUDE:
        return "exclude", EXCLUDE[t.name]
    if t.name in TABLE:
        return "table", TABLE[t.name]
    if t._commit:
        repo = t.repository or t._repository or t.homepage
        if not repo:
            return "none", "hay _commit pero no se de donde clonarlo"
        return "pin", (repo, t._commit)
    url = strip_rename(t.distfiles.split()[0]) if t.distfiles else ""
    url = url.replace("${homepage}", t.homepage.rstrip("/"))
    url = expand_aux(url, t.vals)
    if not url:
        return "none", "sin distfiles y sin _commit"
    m = re.match(
        r"^(https?://[^/]+)/([^/]+)/([^/]+)/archive/(?:refs/tags/)?(v?)\$\{version\}", url
    )
    if m:
        return "tags", (f"{m.group(1)}/{m.group(2)}/{m.group(3)}", m.group(4))
    m = re.match(
        r"^(https?://[^/]+)/([^/]+)/([^/]+)/releases/download/(v?)\$\{version\}/", url
    )
    if m:
        return "release", (m.group(2), m.group(3), m.group(4))
    if "${version}" not in url:
        return "none", f"URL sin ${{version}}, no se puede versionar: {url[:70]}"
    return "none", f"forma de URL no reconocida: {url[:70]}"


def git_ls_remote_tags(repo):
    """Tags de un repo, sin filtrar prefijo. Cacheado: varios paquetes
    comparten repo (brave-bin y brave-origin-bin son el mismo) y el coste
    es una llamada de red."""
    if repo in _TAGS_CACHE:
        return _TAGS_CACHE[repo]
    out = []
    try:
        r = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", repo],
            capture_output=True, text=True, timeout=NET_TIMEOUT + 15,
        )
        if r.returncode == 0:
            out = [ln.split("refs/tags/")[-1].strip() for ln in r.stdout.splitlines()]
    except (OSError, subprocess.SubprocessError):
        out = []
    _TAGS_CACHE[repo] = out
    return out


def git_ls_remote_head(repo):
    """SHA de la rama por defecto. Para los paquetes con _commit=."""
    try:
        r = subprocess.run(
            ["git", "ls-remote", repo, "HEAD"],
            capture_output=True, text=True, timeout=NET_TIMEOUT + 15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in r.stdout.splitlines():
        sha = line.split()[0] if line.split() else ""
        if sha:
            return sha
    return ""


def tags(repo, prefix):
    seen = []
    for tag in git_ls_remote_tags(repo):
        if prefix and not tag.startswith(prefix):
            continue
        v = tag[len(prefix):]
        if v and re.match(r"^\d", v):
            seen.append(v)
    return seen


def releases(owner, repo, prefix, token):
    """-> (versiones, error). error != "" significa que no se pudo consultar:
    marcar eso como 'al dia' seria el peor fallo posible, un repo renombrado
    pareceria no tener releases nuevas."""
    raw, err = _api_releases(owner, repo, token)
    if err:
        return [], err
    out = []
    for tag in raw:
        if prefix and not tag.startswith(prefix):
            continue
        v = tag[len(prefix):]
        if v and re.match(r"^\d", v):
            out.append(v)
    if not out:
        return [], "GitHub API respondio pero no hay ninguna release no-prerelease"
    return out, ""


def _api_releases(owner, repo, token):
    """-> (tags de las releases, error). Cacheado por repo: brave-bin y
    brave-origin-bin consultan el mismo repo.

    Devuelve SIEMPRE una tupla: un return distinto en cada rama es como se
    rompia esto (la lista de tags se desempaquetaba como si fuera la tupla).
    """
    key = (owner, repo)
    if key in _REL_CACHE:
        return _REL_CACHE[key]
    out, err = [], ""
    hdr = {"Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"
    try:
        data, final = fetch_json(url, hdr)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            # 60 peticiones/hora sin token: este detector hace ~34 por pasada,
            # asi que dos seguidas sin token revientan. No es un "al dia".
            err = (f"GitHub API {e.code}: limite de peticiones. "
                   "Pasa GITHUB_TOKEN (5000/hora) en vez de ir sin token")
        else:
            err = f"GitHub API: HTTP {e.code}"
    except Exception as e:
        err = f"GitHub API: {type(e).__name__}: {e}"
    else:
        if data is None:
            err = f"GitHub API devolvio algo que no es JSON ({final})"
        elif isinstance(data, dict):
            err = f"GitHub API: {data.get('message', 'respuesta inesperada')}"
        else:
            for rel in data:
                if rel.get("draft") or rel.get("prerelease"):
                    continue
                tag = (rel.get("tag_name") or "").strip()
                if tag:
                    out.append(tag)
            if not out:
                err = ("GitHub API respondio pero no hay ninguna release "
                       "no-prerelease")
    _REL_CACHE[key] = (out, err)
    return out, err


def from_table(spec, current):
    """-> (lista de candidatos, distfiles nuevo o None)."""
    kind, url = spec[0], spec[1]
    if kind in ("tags", "tags-prerelease"):
        prefix = spec[2]
        out = git_ls_remote_tags(url)
        cands = []
        for tag in out:
            if not tag.startswith(prefix):
                continue
            v = tag[len(prefix):]
            if not v or not re.match(r"^\d", v):
                continue
            cands.append(tag_to_version(v))
        return cands, None
    if kind == "apt":
        try:
            body, _ = fetch(url)
        except Exception:
            return [], None
        # El indice APT lista varios paquetes (codex, codex-code-mode...):
        # se toman todas las versiones y best() elige la mayor.
        return re.findall(r"^Version:\s*(\S+)\s*$", body, re.M), None
    if kind == "json-keys":
        try:
            body, _ = fetch(url)
            data = json.loads(body)
        except Exception:
            return [], None
        if not isinstance(data, dict):
            return [], None
        keys = [k for k in data if k != "master" and re.match(r"^\d", k)]
        return keys, None
    if kind == "html-hrefs":
        try:
            body, _ = fetch(url)
        except Exception:
            return [], None
        return re.findall(spec[2], body), None
    if kind == "regex":
        try:
            body, _ = fetch(url)
        except Exception:
            return [], None
        m = re.search(spec[2], body, re.M)
        return ([m.group(1)] if m else []), None
    if kind == "regex2":
        try:
            body, _ = fetch(url)
        except Exception:
            return [], None
        mv = re.search(spec[2], body)
        mu = re.search(spec[3], body)
        if not mv:
            return [], None
        new_url = None
        if mu:
            # group(1) si el patron captura, si no el match entero.
            new_url = mu.group(1) if mu.groups() else mu.group(0)
        # Si lo capturado es un nombre de fichero, la URL completa es la
        # del indice + el nombre.
        if new_url and not new_url.startswith("http"):
            new_url = url.rstrip("/") + "/" + new_url
        return [mv.group(1)], new_url
    return [], None


def best(current, candidates):
    """-> (candidato mas nuevo, quantas versiones nuevas hay).

    El segundo valor sirve para ordenar: con un techo de actualizaciones por
    noche, elegir siempre las primeras por orden alfabetico deja en hambre
    a los ultimos. Se actualiza antes lo que mas rezago tiene.
    """
    want_pre = prerelease(current)
    want_date = bool(DATE_RE.match(current))
    best_v, best_c, behind = None, None, 0
    for v in sorted(set(candidates)):
        if prerelease(v) != want_pre:
            continue
        # Un tag-fecha nunca es comparable con un version= normal, ni al reves.
        if bool(DATE_RE.match(v)) != want_date:
            continue
        c = cmpver(v, current)
        if c is None or c <= 0:
            continue
        behind += 1
        if best_c is None or cmpver(v, best_v) > 0:
            best_v, best_c = v, c
    return best_v, behind


def selftest():
    """Autocomprobacion. Un parser roto que dice "al dia" es el peor fallo
    posible: no falla, miente."""
    tpl = (
        "# Template file for 'x'\n"
        "pkgname=x\n"
        "version=1.2.3\n"
        "revision=4\n"
        'distfiles="https://example.com/a-${version}.tar.xz\n'
        ' https://example.com/b.tar.xz>c.tar.xz"\n'
        'short_desc="hola"\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        pkgdir = os.path.join(tmp, "x")
        os.mkdir(pkgdir)
        path = os.path.join(pkgdir, "template")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(tpl)
        t = parse(path)
    assert t.version == "1.2.3", t.version
    assert t.revision == "4", t.revision
    # El distfiles multilinea (estilo Void: la comilla abre y la URL
    # sigue en la linea siguiente) tiene que dar dos ficheros.
    assert len(t.distfiles.split()) == 2, t.distfiles
    assert t.distfiles.split()[0] == \
        "https://example.com/a-${version}.tar.xz", t.distfiles
    assert "c.tar.xz" in t.distfiles, t.distfiles

    # "rc" no puede casar dentro de "src", ni "pre" dentro de "preview".
    assert prerelease("2.0.0alpha1")
    assert prerelease("3.0.0-rc.3")
    assert not prerelease("1.0.0src")
    assert not prerelease("2.45.1")
    assert not prerelease("1.23b")

    # tag de git -> version de xbps
    assert tag_to_version("2.0.0-alpha1") == "2.0.0alpha1"
    assert tag_to_version("1.2.3") == "1.2.3"
    assert tag_to_version("1.0.0src") == "1.0.0src"

    # best() con un cmpver de mentira: sin depender de xbps-uhelper.
    global cmpver
    real = cmpver

    def fake(a, b):
        ka = [int(x) for x in re.findall(r"\d+", a)]
        kb = [int(x) for x in re.findall(r"\d+", b)]
        return (ka > kb) - (ka < kb)

    cmpver = fake
    try:
        assert best("1.0.0", ["0.9.0", "1.1.0", "1.0.5"]) == ("1.1.0", 2)
        assert best("1.0.0", ["0.9.0"]) == (None, 0)
        # Un rc no sustituye a una estable ni al reves.
        assert best("1.0.0", ["1.1.0rc1"]) == (None, 0)
        assert best("1.0.0rc1", ["1.1.0rc2"]) == ("1.1.0rc2", 1)
        # Un tag-fecha no es comparable con una version normal.
        assert best("1.0.0", ["20240327"]) == (None, 0)
    finally:
        cmpver = real
    print("selftest: OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--srcpkgs", default="srcpkgs")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    # El workflow pasa GITHUB_TOKEN. Sin el son 60 peticiones/hora y este
    # detector hace ~34 por pasada: dos seguidas sin token ya revientan.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""

    rows, counts = [], {"update": 0, "current": 0, "manual": 0, "pin": 0,
                        "error": 0}
    for entry in sorted(os.listdir(args.srcpkgs)):
        d = os.path.join(args.srcpkgs, entry)
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        tpl = os.path.join(d, "template")
        if not os.path.isfile(tpl):
            continue
        if args.only and entry not in args.only:
            continue
        t = parse(tpl)
        row = {"pkgname": t.pkgname, "dir": t.name, "version": t.version}
        try:
            kind, detail = classify(t)
            row["kind"] = kind
            if kind == "exclude":
                row.update(status="manual", why=detail)
                counts["manual"] += 1
                rows.append(row)
                continue
            if kind == "none":
                row.update(status="error", why=detail)
                counts["error"] += 1
                rows.append(row)
                continue
            if kind == "pin":
                repo, cur_sha = detail
                head = git_ls_remote_head(repo)
                if not head:
                    row.update(status="error", why=f"no se pudo leer HEAD de {repo}")
                    counts["error"] += 1
                elif head == cur_sha:
                    row.update(status="current")
                    counts["current"] += 1
                else:
                    # Un pin es una decision: "este commit exacto, probado".
                    # La version= de estos paquetes es la de la app y no se
                    # deduce del commit, asi que mover el pin solo publicaria
                    # un binario distinto con la misma version. Se informa y
                    # no se toca solo.
                    row.update(status="pin", new_commit=head,
                               why="el commit upstream se movio; el pin se "
                                   "mueve a mano")
                    counts["pin"] += 1
                rows.append(row)
                continue
            new_dist, err = None, ""
            if kind == "tags":
                repo, prefix = detail
                cands = tags(repo, prefix)
            elif kind == "release":
                owner, repo, prefix = detail
                cands, err = releases(owner, repo, prefix, token)
            else:
                cands, new_dist = from_table(detail, t.version)
            if err:
                row.update(status="error", why=err)
                counts["error"] += 1
                rows.append(row)
                continue
            new, behind = best(t.version, cands)
            if new:
                row.update(status="update", new_version=new, behind=behind)
                if new_dist:
                    row["new_distfiles"] = new_dist
                counts["update"] += 1
            else:
                row.update(status="current")
                counts["current"] += 1
        except Exception as e:  # nunca rompe el job entero
            row.update(status="error", why=f"{type(e).__name__}: {e}")
            counts["error"] += 1
        rows.append(row)

    md = ["| paquete | versión | estado |", "|---|---|---|"]
    for r in rows:
        if r["status"] == "update":
            md.append(f"| {r['pkgname']} | {r['version']} | **→ {r['new_version']}** |")
        elif r["status"] == "manual":
            md.append(f"| {r['pkgname']} | {r['version']} | manual: {r['why']} |")
        elif r["status"] == "pin":
            md.append(f"| {r['pkgname']} | {r['version']} | pin: {r['why']} |")
        elif r["status"] == "error":
            md.append(f"| {r['pkgname']} | {r['version']} | ERROR: {r['why']} |")
    md += [
        "",
        f"actualizaciones: **{counts['update']}** · al día: {counts['current']} · "
        f"manual: {counts['manual']} · pin: {counts['pin']} · "
        f"error: {counts['error']}",
    ]
    payload = {"counts": counts, "packages": rows}
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
