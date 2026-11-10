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
    "codex": (
        "apt",
        "https://persistent.oaistatic.com/codex-app-prod/linux/deb/dists/"
        "stable/main/binary-amd64/Packages",
    ),
    "codex-bin": (
        "apt",
        "https://persistent.oaistatic.com/codex-app-prod/linux/deb/dists/"
        "stable/main/binary-amd64/Packages",
    ),
    "unityhub": (
        "regex",
        "https://public-cdn.cloud.unity3d.com/hub/prod/latest-linux.yml",
        r"^version:\s*(\S+)\s*$",
    ),
    "unityhub-bin": (
        "regex",
        "https://public-cdn.cloud.unity3d.com/hub/prod/latest-linux.yml",
        r"^version:\s*(\S+)\s*$",
    ),
    "vscode-nk": (
        "regex2",
        "https://update.code.visualstudio.com/api/update/linux-x64/stable/latest",
        r'"name"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"([^"]+)"',
    ),
    "vscode-bin": (
        "regex2",
        "https://update.code.visualstudio.com/api/update/linux-x64/stable/latest",
        r'"name"\s*:\s*"([^"]+)"',
        r'"url"\s*:\s*"([^"]+)"',
    ),
    "zig-nk": (
        "json-keys",
        "https://ziglang.org/download/index.json",
    ),
    "zig-nk-bin": (
        "json-keys",
        "https://ziglang.org/download/index.json",
    ),
    "reaper": (
        "regex",
        "https://www.reaper.fm/download.php",
        r"Version\s+(\d+\.\d+)\s*:",
    ),
    "reaper-bin": (
        "regex",
        "https://www.reaper.fm/download.php",
        r"Version\s+(\d+\.\d+)\s*:",
    ),
    "waterfox": (
        "html-hrefs",
        "https://cdn.waterfox.com/waterfox/releases/",
        r'href="(?:/waterfox/releases/)?(\d+(?:\.\d+)+)/"',
    ),
    "waterfox-bin": (
        "html-hrefs",
        "https://cdn.waterfox.com/waterfox/releases/",
        r'href="(?:/waterfox/releases/)?(\d+(?:\.\d+)+)/"',
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
    "lua55": (
        "tags",
        "https://github.com/lua/lua",
        "v",
    ),
    "spotify-app": (
        "regex2",
        "https://repository.spotify.com/pool/non-free/s/spotify-client/",
        r"spotify-client_([0-9]+(?:\.[0-9]+)+)\.[a-z0-9]+_amd64\.deb",
        r"spotify-client_[0-9]+(?:\.[0-9]+)+\.[a-z0-9]+_amd64\.deb",
    ),
    "spotify-app-bin": (
        "regex2",
        "https://repository.spotify.com/pool/non-free/s/spotify-client/",
        r"spotify-client_([0-9]+(?:\.[0-9]+)+)\.[a-z0-9]+_amd64\.deb",
        r"spotify-client_[0-9]+(?:\.[0-9]+)+\.[a-z0-9]+_amd64\.deb",
    ),
    "spotify-bin": (
        "regex2",
        "https://repository.spotify.com/pool/non-free/s/spotify-client/",
        r"spotify-client_([0-9]+(?:\.[0-9]+)+)\.[a-z0-9]+_amd64\.deb",
        r"spotify-client_[0-9]+(?:\.[0-9]+)+\.[a-z0-9]+_amd64\.deb",
    ),
    "3utools": (
        "user-agent-regex",
        "https://www.3u.com",
        r"com\.3u\.3utools_([0-9.]+)_amd64\.deb",
        "Mozilla/5.0 (X11; Linux x86_64)",
    ),
    "3utools-bin": (
        "user-agent-regex",
        "https://www.3u.com",
        r"com\.3u\.3utools_([0-9.]+)_amd64\.deb",
        "Mozilla/5.0 (X11; Linux x86_64)",
    ),
    "librewolf": (
        "codeberg-release-asset",
        "https://codeberg.org/api/v1/repos/librewolf/bsys6/releases",
        r"^librewolf-([0-9.]+)-[0-9]+-linux-x86_64-package\.tar\.xz$",
    ),
    "librewolf-bin": (
        "codeberg-release-asset",
        "https://codeberg.org/api/v1/repos/librewolf/bsys6/releases",
        r"^librewolf-([0-9.]+)-[0-9]+-linux-x86_64-package\.tar\.xz$",
    ),
    "serein": (
        "serein-release",
        "https://api.github.com/repos/ViceVerse-cz/Serein/releases",
    ),
    "serein-bin": (
        "serein-release",
        "https://api.github.com/repos/ViceVerse-cz/Serein/releases",
    ),
    "libqt6pas": (
        "github-release-asset-regex",
        "https://api.github.com/repos/davidbannon/libqt6pas/releases",
        r"^libqt6pas_([0-9_]+)-[0-9]+_amd64\.tar\.gz$",
    ),
    "libqt6pas-bin": (
        "github-release-asset-regex",
        "https://api.github.com/repos/davidbannon/libqt6pas/releases",
        r"^libqt6pas_([0-9_]+)-[0-9]+_amd64\.tar\.gz$",
    ),
    "linux-cachy-void": (
        "kernel-neko-asset",
        "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest",
        r"neko-kernel-void-([0-9.]+)-cachy-bore\+\.tar\.gz",
    ),
    "linux-cachy-void-bin": (
        "kernel-neko-asset",
        "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest",
        r"neko-kernel-void-([0-9.]+)-cachy-bore\+\.tar\.gz",
    ),
    "linux-neko-rt": (
        "kernel-neko-asset",
        "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest",
        r"neko-kernel-void-([0-9.]+)-rt-neko\+\.tar\.gz",
    ),
    "linux-neko-rt-bin": (
        "kernel-neko-asset",
        "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest",
        r"neko-kernel-void-([0-9.]+)-rt-neko\+\.tar\.gz",
    ),
    "linux-neko-zen": (
        "kernel-neko-asset",
        "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest",
        r"zen-kernel-void-([0-9.]+)-zen-neko\+\.tar\.gz",
    ),
    "linux-neko-zen-bin": (
        "kernel-neko-asset",
        "https://api.github.com/repos/Neko-Void-Linux/kernel-neko-void/releases/tags/latest",
        r"zen-kernel-void-([0-9.]+)-zen-neko\+\.tar\.gz",
    ),
    "steam-nk": (
        "codeberg-tag-version",
        "https://codeberg.org/api/v1/repos/Neko-Void/STEAM-ALT-VOID/releases",
    ),
    "steam-bin": (
        "codeberg-tag-version",
        "https://codeberg.org/api/v1/repos/Neko-Void/STEAM-ALT-VOID/releases",
    ),
    "steam-nk-bin": (
        "codeberg-tag-version",
        "https://codeberg.org/api/v1/repos/Neko-Void/STEAM-ALT-VOID/releases",
    ),
    "windscribe-desktop": (
        "codeberg-release-asset",
        "https://codeberg.org/api/v1/repos/Neko-Void/Windscribe-void/releases",
        r"^windscribe_([0-9.]+)_amd64\.deb$",
    ),
    "windscribe-desktop-bin": (
        "codeberg-release-asset",
        "https://codeberg.org/api/v1/repos/Neko-Void/Windscribe-void/releases",
        r"^windscribe_([0-9.]+)_amd64\.deb$",
    ),
    "vimix-cursor-theme": (
        "tags-date-transform",
        "https://github.com/vinceliuice/Vimix-cursors",
    ),
    "pam-fprint-grosshack": (
        "tags-pattern",
        "https://github.com/furbyhaxx/pam-fprint-grosshack",
        r"^v(0\.\d+(?:\.\d+)*)$",
    ),
    "halley": (
        "halley-tag",
        "https://github.com/mikuri12/halley",
    ),
    "onlyoffice": (
        "tags",
        "https://github.com/ONLYOFFICE/DesktopEditors",
        "v",
    ),
    "onlyoffice-bin": (
        "tags",
        "https://github.com/ONLYOFFICE/DesktopEditors",
        "v",
    ),
    "gpu-screen-recorder-nk": (
        "tags",
        "https://repo.dec05eba.com/gpu-screen-recorder",
        "",
    ),
    "gpu-screen-recorder-notification-nk": (
        "tags",
        "https://repo.dec05eba.com/gpu-screen-recorder-notification",
        "",
    ),
    "gpu-screen-recorder-ui-nk": (
        "tags",
        "https://repo.dec05eba.com/gpu-screen-recorder-ui",
        "",
    ),
    "horizon": (
        "tags",
        "https://github.com/horizon-rust/horizon",
        "launcher@",
    ),
    "horizon-launcher": (
        "tags",
        "https://github.com/horizon-rust/horizon",
        "launcher@",
    ),
    "antigravity": (
        "antigravity-manifest",
        "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/latest-x64-linux.yml",
    ),
    "antigravity-bin": (
        "antigravity-manifest",
        "https://antigravity-hub-auto-updater-974169037036.us-central1.run.app/manifest/latest-x64-linux.yml",
    ),
    "trinity-launcher": (
        "github-release-asset-date",
        "https://api.github.com/repos/Trinity-LA/Trinity-Launcher/releases/tags/latest",
        r"^Trinity_Launcher-x86_64\.AppImage$",
    ),
    "trinity-launcher-ap": (
        "github-release-asset-date",
        "https://api.github.com/repos/Trinity-LA/Trinity-Launcher/releases/tags/latest",
        r"^Trinity_Launcher-x86_64\.AppImage$",
    ),
    "waveform-free": (
        "waveform-debs-asset",
        "https://api.github.com/repos/Neko-Void-Linux/Neko-Wizard/releases/tags/debs",
        r"^waveform14_([0-9.]+)_amd64\.deb$",
    ),
    "tracktion-download-manager": (
        "tracktion-aur",
        "https://aur.archlinux.org/rpc/v5/info/tracktion-download-manager",
    ),
    "lsfg-vk": (
        "lsfg-vk-cgit",
        "https://git.lsfg-vk.dev/lsfg-vk.git",
    ),
    "lsfg-vk-git": (
        "lsfg-vk-cgit",
        "https://git.lsfg-vk.dev/lsfg-vk.git",
    ),
}
# Tags con forma de fecha (jwmkit-neko usa 20240327). Compararlos contra un
# version= normal daria "26.5 -> 20240327": un tag de 2024 contado como nuevo.
DATE_RE = re.compile(r"^\d{6,8}$")
# xbps-src rechaza una version con guion o guion bajo ("version contains
# invalid character"). Halley etiqueta sus releases 0.8.0-mikuri.20: es una
# version valida para upstream pero imposible de meter en un template.
VALID_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+~]*$")
# Sin automatismo posible. El motivo va al reporte: no es un hueco, es una
# decision. Si alguno se vuelve automatizable, sale de aqui.
#
# Familias de templates manuales:
#  - metapaquete / empaquetado local sin distfiles externos (neko-base, triggers, arte)
#  - plugins acoplados al ABI interno de C++ de Hyprland (version.h / hyprpm.toml)
#  - repositorio upstream eliminado / 404 (kpm, pavoldcontrol)
#  - fuentes historicas congeladas por Microsoft (msttcorefonts)
#  - dependencias de build complejas fuera de estandar (ly con vendor.tar.zst)
#  - singularity-desktop: metadistribucion con arbol fijado
EXCLUDE = {
    "xbps-triggers": "sincronizado de void-packages; scripts locales en FILESDIR sin distfiles",
    "neko-base": "metapaquete sin distfiles; la revision es manual a proposito",
    "neko-backgrounds": "arte local ensamblado mediante git clone en do_build sin releases",
    "neko-icons": "arte local ensamblado mediante git clone en do_build sin releases",
    "neko-themes": "temas locales ensamblados mediante git clone en do_build sin releases",
    "pavoldcontrol": "repo upstream eliminado (404); sin fuentes externas",
    "pavoldcontrol-git": "repo upstream eliminado (404); sin fuentes externas",
    "jwmkit-neko": "los tags upstream son fechas (20240327) y el version= es local",
    "kpm": "repo upstream original inexistente (404); binario archivado en z-packages",
    "ly": "requiere empaquetado hermetico de dependencias offline (vendor.tar.zst) para Zig 0.17",
    "msttcorefonts": "12 .exe de SourceForge cerrados por Microsoft en 2002; sin nuevas versiones",
    "hyprgraphics": "los bumps suben el SONAME: exigen shlibs y revbump de rdeps en el mismo PR",
    "aquamarine": "los bumps suben el SONAME: exigen shlibs y revbump de rdeps en el mismo PR",
    "hypr-dynamic-cursors": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "hyprglass": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "hyprgrass": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "hyprland-preview-share-picker": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "imgborders": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "ryoku-hypr-plugins": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "ryoku-keysounds": "plugin acoplado al ABI interno de Hyprland (version.h); sin releases externas",
    "ryoku-desktop-hyprland": "ensamblador del entorno Ryoku sin releases semanticas independientes",
    "ryoku-keysounds-profiles": "ensamblador del entorno Ryoku sin releases semanticas independientes",
    "singularity-desktop": "metadistribucion que pinea mas de 15 repositorios git a commits especificos",
    "mozc-ut": "versionado complejo (fcitx/mozc, abseil, protobuf, diccionarios UT y snapshot mensual de jawiki con commits fijados); actualización manual coordinada",
    "mozc-ut-base": "subpaquete de mozc-ut; se actualiza junto con mozc-ut",
    "ibus-mozc-ut": "subpaquete de mozc-ut; se actualiza junto con mozc-ut",
    "fcitx5-mozc-ut": "subpaquete de mozc-ut; se actualiza junto con mozc-ut",
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
    if not t._commit and t.vals.get("_sha"):
        t._commit = t.vals.get("_sha")
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
    name = t.name
    pkg = getattr(t, "pkgname", name)
    if name in EXCLUDE:
        return "exclude", EXCLUDE[name]
    if pkg in EXCLUDE:
        return "exclude", EXCLUDE[pkg]
    if name in TABLE:
        return "table", TABLE[name]
    if pkg in TABLE:
        return "table", TABLE[pkg]
    if t._commit:
        if name == "geforcenow":
            return "pin", (
                "https://international.download.nvidia.com/GFNLinux/flatpak/geforcenow_repo/refs/heads/app/com.nvidia.geforcenow/x86_64/master",
                t._commit,
            )
        repo = t.repository or t._repository
        if not repo and t.distfiles:
            m_dist = re.match(r"^(https?://[^/]+/[^/]+/[^/]+)/archive/", t.distfiles)
            if m_dist:
                repo = m_dist.group(1)
        if not repo:
            repo = t.homepage
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
        r"^(https?://[^/]+)/([^/]+)/([^/]+)/releases/download/(v?)\$\{version\}/(.+)$",
        url,
    )
    if m:
        # El grupo 5 es el nombre del asset: se usa para comprobar que la
        # release lo publica de verdad.
        return "release", (m.group(2), m.group(3), m.group(4), m.group(5))
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


def releases(owner, repo, prefix, token, asset=None):
    """-> (versiones, error). error != "" significa que no se pudo consultar:
    marcar eso como 'al dia' seria el peor fallo posible, un repo renombrado
    pareceria no tener releases nuevas.

    asset: nombre del fichero que se espera en la release. Sin esta
    comprobacion el detector propone versiones cuyo asset no existe (las
    nightlies de Brave se etiquetan v1.100.7 pero no publican el binario) y
    el actualizador se queda atascado en un 404 cada noche.
    """
    raw, err = _api_releases(owner, repo, token)
    if err:
        return [], err
    out = []
    for tag, assets in raw:
        if prefix and not tag.startswith(prefix):
            continue
        v = tag[len(prefix):]
        if not v or not re.match(r"^\d", v):
            continue
        if asset:
            want = asset.replace("${version}", v)
            if want not in assets:
                continue
        out.append(v)
    if not out:
        if asset:
            # Hay releases pero ninguna publica este asset: el paquete no
            # se puede versionar solo. No es un fallo del detector.
            return [], "NO_ASSET"
        return [], "GitHub API respondio pero no hay ninguna release no-prerelease"
    return out, ""


def _api_releases(owner, repo, token):
    """-> ([(tag, [assets])], error). Cacheado por repo: brave-bin y
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
                if not tag:
                    continue
                assets = [a.get("name", "") for a in rel.get("assets", [])]
                out.append((tag, assets))
            if not out:
                err = ("GitHub API respondio pero no hay ninguna release "
                       "no-prerelease")
    _REL_CACHE[key] = (out, err)
    return out, err


def from_table(spec, current, token=None):
    """-> (lista de candidatos, distfiles nuevo o None, [tag nuevo o None])."""
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
    if kind == "user-agent-regex":
        try:
            body, _ = fetch(url, {"User-Agent": spec[3]})
        except Exception:
            return [], None
        m = re.search(spec[2], body)
        return ([m.group(1)] if m else []), None
    if kind == "codeberg-release-asset":
        try:
            data, _ = fetch_json(url)
        except Exception:
            return [], None
        cands = []
        new_url = None
        if isinstance(data, list):
            for rel in data:
                for a in rel.get("assets", []):
                    m = re.match(spec[2], a.get("name", ""))
                    if m:
                        cands.append(m.group(1))
                        if not new_url:
                            new_url = a.get("browser_download_url")
        return cands, new_url
    if kind == "codeberg-tag-version":
        try:
            data, _ = fetch_json(url)
        except Exception:
            return [], None
        cands = []
        if isinstance(data, list):
            for rel in data:
                tag = (rel.get("tag_name") or "").strip()
                if tag and re.match(r"^\d", tag):
                    cands.append(tag)
        return cands, None
    if kind == "serein-release":
        hdr = {"Accept": "application/vnd.github+json"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        try:
            data, _ = fetch_json(url, hdr)
        except Exception:
            return [], None
        cands = []
        new_url = None
        new_tag = None
        if isinstance(data, list):
            for rel in data:
                tag = (rel.get("tag_name") or "").strip()
                m = re.match(r"^v?1\.0\.0-nightly\.(\d{8})\.(\d+)$", tag)
                if m:
                    ver = f"1.0.0pre{m.group(1)}.{m.group(2)}"
                    cands.append(ver)
                    if not new_url:
                        new_url = (f"https://github.com/ViceVerse-cz/Serein/releases/"
                                   f"download/{tag}/serein-{tag}-Linux-X64.AppImage")
                        new_tag = tag
        return cands, new_url, new_tag
    if kind == "github-release-asset-regex":
        hdr = {"Accept": "application/vnd.github+json"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        try:
            data, _ = fetch_json(url, hdr)
        except Exception:
            return [], None
        cands = []
        if isinstance(data, list):
            for rel in data:
                tag = (rel.get("tag_name") or "").lstrip("v").strip()
                for a in rel.get("assets", []):
                    if re.match(spec[2], a.get("name", "")):
                        cands.append(tag)
                        break
        return cands, None
    if kind == "kernel-neko-asset":
        hdr = {"Accept": "application/vnd.github+json"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        try:
            data, _ = fetch_json(url, hdr)
        except Exception:
            return [], None
        cands = []
        if isinstance(data, dict):
            for a in data.get("assets", []):
                m = re.match(spec[2], a.get("name", ""))
                if m:
                    cands.append(m.group(1))
        return cands, None
    if kind == "tags-date-transform":
        out = git_ls_remote_tags(url)
        cands = []
        for tag in out:
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", tag)
            if m:
                cands.append(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
        return cands, None
    if kind == "tags-pattern":
        out = git_ls_remote_tags(url)
        cands = []
        for tag in out:
            m = re.match(spec[2], tag)
            if m:
                cands.append(m.group(1))
        return cands, None
    if kind == "halley-tag":
        out = git_ls_remote_tags(url)
        cands = []
        for tag in out:
            m = re.match(r"^v?(\d+\.\d+\.\d+)-mikuri\.(\d+)$", tag)
            if m:
                cands.append(m.group(1))
        return cands, None
    if kind == "antigravity-manifest":
        try:
            body, _ = fetch(url)
        except Exception:
            return [], None
        mv = re.search(r"^version:\s*['\"]?([^'\"\s]+)['\"]?", body, re.M)
        mu = re.search(r"url:\s*['\"]?(https://storage\.googleapis\.com/antigravity-public/\S+)/linux-x64/\S+['\"]?", body)
        if mv:
            ver = mv.group(1)
            new_url = None
            if mu:
                new_url = f"{mu.group(1)}/linux-x64/Antigravity.tar.gz"
            return [ver], new_url
        return [], None
    if kind == "github-release-asset-date":
        hdr = {"Accept": "application/vnd.github+json"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        try:
            data, _ = fetch_json(url, hdr)
        except Exception:
            return [], None
        cands = []
        if isinstance(data, dict):
            for a in data.get("assets", []):
                if re.match(spec[2], a.get("name", "")):
                    updated = a.get("updated_at") or a.get("created_at") or ""
                    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", updated)
                    if m:
                        cands.append(f"{m.group(1)}{m.group(2)}{m.group(3)}")
        return cands, None
    if kind == "waveform-debs-asset":
        hdr = {"Accept": "application/vnd.github+json"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        try:
            data, _ = fetch_json(url, hdr)
        except Exception:
            return [], None
        cands = []
        if isinstance(data, dict):
            for a in data.get("assets", []):
                m = re.match(spec[2], a.get("name", ""))
                if m:
                    cands.append(f"2026.{m.group(1)}")
        return cands, None
    if kind == "tracktion-aur":
        try:
            data, _ = fetch_json(url)
        except Exception:
            return [], None
        cands = []
        new_dist = None
        if isinstance(data, dict) and data.get("results"):
            raw_v = data["results"][0].get("Version", "").split("-")[0]
            if raw_v and re.match(r"^\d", raw_v):
                cands.append(f"2026.10.{raw_v}")
                v_dir = raw_v.replace(".", "")
                new_dist = (f"https://downloads.tracktion.com/download-mgr/{v_dir}/"
                            f"tracktion_download_manager_v{raw_v}.deb")
        return cands, new_dist
    if kind == "lsfg-vk-cgit":
        out = git_ls_remote_tags(url)
        cands = []
        for tag in out:
            t_clean = tag.lstrip("v")
            if t_clean and re.match(r"^\d", t_clean):
                cands.append(f"2026.{t_clean}")
        return cands, None
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
        # Una version que xbps-src no acepta no se puede proponer.
        if not VALID_VERSION_RE.match(v):
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
        # Una version con guion no vale para xbps-src: no se propone.
        assert best("0.5.7", ["0.8.0-mikuri.20"]) == (None, 0)
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
                # No es un fallo del detector: es que ese paquete no se
                # puede versionar solo (commit, fecha, sin distfiles). Se
                # declara no automatizable, con su motivo.
                row.update(status="manual", why=detail)
                counts["manual"] += 1
                rows.append(row)
                continue
            if kind == "pin":
                repo, cur_sha = detail
                if repo.startswith("https://international.download.nvidia.com"):
                    try:
                        b, _ = fetch(repo)
                        head = b.strip()
                    except Exception:
                        head = ""
                else:
                    head = git_ls_remote_head(repo)
                if not head:
                    # El _repository no es un repositorio git clonable: el
                    # pin no se puede verificar, asi que no es automatizable.
                    row.update(status="manual",
                               why="el _repository no es un repo git: el pin "
                                   "no se puede verificar")
                    counts["manual"] += 1
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
            new_dist, new_tag, err = None, None, ""
            if kind == "tags":
                repo, prefix = detail
                cands = tags(repo, prefix)
            elif kind == "release":
                owner, repo, prefix, asset = detail
                # El asset puede venir con ${pkgname} en vez de ${version}.
                asset = asset.replace("${pkgname}", t.pkgname)
                cands, err = releases(owner, repo, prefix, token, asset)
            else:
                res = from_table(detail, t.version, token)
                if len(res) == 3:
                    cands, new_dist, new_tag = res
                else:
                    cands, new_dist = res
            if err:
                if err == "NO_ASSET":
                    row.update(status="manual",
                               why="ninguna release publica el asset que usa "
                                   "el distfile: no se puede versionar solo")
                    counts["manual"] += 1
                else:
                    row.update(status="error", why=err)
                    counts["error"] += 1
                rows.append(row)
                continue
            new, behind = best(t.version, cands)
            if new:
                row.update(status="update", new_version=new, behind=behind)
                if new_dist:
                    row["new_distfiles"] = new_dist
                if new_tag:
                    row["new_tag"] = new_tag
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
