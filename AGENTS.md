# AGENTS.md

XBPS source-packages collection (Void Linux fork). Packages are shell `template` files built with `xbps-src`, not normal source code.

## Layout

- `srcpkgs/<pkgname>/template` — package recipe (mandatory). Optional siblings: `patches/*.patch|diff`, `files/`, `update` (sets `site`/`pattern`/`ignore` for `update-check`).
- `xbps-src` — the only build entrypoint (bash). `common/build-style/*.sh` — `build_style` implementations. `common/shlibs` — soname→package mapping. `common/cross-profiles/`, `common/build-profiles/` — arch flags.
- `etc/defaults.conf` — defaults, never edit. Local overrides go in `etc/conf` (create it; already gitignored). Build outputs: `hostdir/binpkgs`, `hostdir/sources`, `masterdir-<arch>/`.
- Root `.gitignore` ignores everything at root except an explicit whitelist (`AGENTS.md`, `.VURINFO`, `srcpkgs`, …). A new root file needs a `!/name` entry or it silently never gets committed.

## Commands (run from repo root, never as root)

- `./xbps-src pkg <name>` — build one package + deps into `hostdir/binpkgs`.
- `./xbps-src -Q pkg <name>` — same, plus `do_check` for target only (always run before submitting; `-K` also checks deps).
- `./xbps-src -a armv6l pkg <name>` — cross-build smoke test (CONTRIBUTING recommends at least this).
- `./xbps-src show-options <name>` / `./xbps-src -o opt,~opt2 pkg <name>` — list / toggle build options.
- `./xbps-src update-check <name>` — check upstream version; per-package overrides live in `srcpkgs/<name>/update`.
- `xlint template` (from `xtools`, run inside `srcpkgs/<name>/`) — required lint. `xgensum -i <name>` — refresh `checksum`.
- CI entrypoints are the wrappers in `common/travis/`, not the `common/scripts/` ones directly: `xlint.sh` runs `lint-commits` + `xlint` + `lint-version-change` on changed templates, `verify-update-check.sh` runs `./xbps-src update-check` on each, and `build.yaml` runs `common/scripts/lint-conflicts` on x86_64 only (needs `xlocate -S`).
- `./xbps-src binary-bootstrap` / `./xbps-src bootstrap-update` / `./xbps-src zap` — init / update / recreate masterdir. Never commit `hostdir/`, `masterdir-*/`.

## Template rules (see `Manual.md`, `CONTRIBUTING.md`)

- One commit per package. Messages: `New package: <name>-<version>`, `<name>: update to <version>.`, `<name>: <reason>` (template-only change), `<name>: remove package`.
- Version bump → `revision=1` + new `checksum`. Template-only change affecting binaries → increment `revision`, keep `version`. Orphan/adopt-only → don't bump.
- `version` has no `-`/`_` and no shell substitution. Lines ≤80 cols; continuation lines indent one space. `short_desc` ≤72 chars.
- Subpackage dirs (`srcpkgs/<name>-devel/`) are not new packages: they declare the parent `pkgname` and add `<name>_package()`. The dir name is the subpackage; build the parent.
- Deps: `hostmakedepends` (runs on host: compilers, tools) vs `makedepends` (links against, usually `-devel`) vs `checkdepends` (only for `do_check`, needs `-Q`/`XBPS_CHECK_PKGS`) vs `depends` (runtime only; never list auto-detected ELF/soname deps). `makedepends` with no build effect → drop it.
- `make_check=no` needs the reason on the same line. `nocross=`/`broken=` must state why (or link a build log). `restricted=` packages need `XBPS_ALLOW_RESTRICTED=yes` in `etc/conf` and never enter official repos. Scope `archs` only with upstream justification.
- Soname bump → update `common/shlibs` and revbump every dependent in the same PR (one commit each).
- CI matrix is glibc x86_64/i686/aarch64/armv7l + musl x86_64/aarch64/armv6l; tests (`-Q` equivalent) run only on x86_64, i686 and x86_64-musl. `[ci skip]` in the PR title or body skips the build job only (lint still runs); for multi-hour or >14G builds, report local glibc+musl × 64/32-bit results instead.
- CI fails if the branch is ≥200 commits behind `origin/HEAD`: rebase before pushing. `changed_templates.sh` derives the base with `git merge-base origin/HEAD`.

## The template contract (ley de forma)

Estas reglas hacen que `format-template.py` sea **idempotente** y que el autoactualizador pueda reescribir cualquier template sin dejar basura en el diff. `format-template.py` las aplica solo; el resto se comprueba con `xlint`.

1. **Línea 1 exacta**: `# Template file for '<pkgname>'`. El directorio `srcpkgs/<dir>/` y el `pkgname=` coinciden 1:1.
2. **Cero comentarios `#` en las líneas ≥2.** Excepciones: expansiones POSIX (`${var#pat}`, `${var##pat}`, `${#var}`), shebangs `#!/bin/sh` dentro de heredocs o de `files/<svc>/run`, y la línea `make_check=no # <motivo>` (xlint la exige: `xtools/xlint:52`; void-packages lo hace en 651 paquetes).
3. **Orden canónico de variables** = el de `xlint` (`xtools/xlint:variables_order`). Sin reordenar, el updater ensucia el diff en cada actualización:

   `pkgname` → `reverts` → `version` → `revision` → `archs` → (6,7 = variables `_aux`) → `create_wrksrc` → `build_wrksrc` → `build_style`/`build_helper` → `metapackage` → `configure_args`/`make_*`/`go_*`/… → (13,14) → `hostmakedepends` → `makedepends` → `depends` → `checkdepends` → `short_desc` → `maintainer` → `license` → `homepage` → `changelog` → `distfiles` → `checksum` → `alternatives`/`fetch_cmd` → (27 = `provides`/`conflicts`/`replaces`/`build_options`/…) → `conf_files`/`make_dirs` → funciones.

   Las variables auxiliares `_xxx` van **justo antes de `distfiles`**: bash expande en la asignación y `homepage` (de donde suelen salir) está definida después en el orden canónico. Si una auxiliar depende de otra variable, va después de ella.
4. **≤80 columnas** donde se pueda sin romper nada. Las listas (`depends`, `makedepends`, …) se parten con una continuación de **un espacio** y reparto codicioso. Una URL larga **no se parte**: bash concatena las líneas de un valor entrecomillado con un espacio, y romperla la deja inservible (void-packages oficial tiene 6395 líneas así).
5. **Indentación con tabs** en las funciones. Los heredocs no se tocan.
6. **Helpers de xbps-src, nunca comandos crudos**: `vbin`, `vman`, `vdoc`, `vconf`, `vlicense`, `vinstall`, `vmkdir`, `vcopy`, `vmove`, `vsv`, `vcompletion`, `vsed -i` (siempre con `-i`: si el parche queda obsoleto, el build falla en vez de publicar algo roto). `cp`/`mv`/`mkdir`/`install`/`sed -i`/`strip` crudos solo si no hay equivalencia exacta (y entonces, fuera de `$DESTDIR`).
7. **Hermético**: `do_fetch`/`do_build`/`do_install` no hacen red. Nada de `git clone`, `curl` ni `wget` en el build: todo lo que se compila viene de `distfiles` + `checksum`.
8. **`wrksrc=` está muerto**: `setup_pkg()` sourcea el template (`common/xbps-src/shutils/common.sh:480`) y en la línea 724 asigna `wrksrc="$XBPS_BUILDDIR/${sourcepkg}-${version}"`, sobreescribiendo lo que puso el template. Se borra.
9. **`license` SPDX + `vlicense` según la lista de xlint** (`xtools/xlint:421`): `MIT`, `BSD*`, `ISC`, `Zlib`, `X11`, `AGPL`, `custom:*` **exigen** `vlicense`; `GPL*`/`LGPL*`/`GFDL*`/`Apache*` **prohíben** `vlicense` (ya las instala el sistema). `vlicense` siempre apunta a un fichero que existe (el del distfile o `files/LICENSE`).
10. **`provides` vs `conflicts`/`replaces`**: `provides="foo-${version}_${revision}"` (versión exacta; XBPS rechaza `>=0` en `provides` — 0 de 421 paquetes de void-packages lo usan), `conflicts="foo>=0"`, `replaces="foo>=0"`.
11. **Subpaquetes**: `depends="${sourcepkg}>=${version}_${revision}"` (nunca literal), y el dir del subpaquete declara el `pkgname` del padre.
12. **`version`**: formato `^[0-9][0-9A-Za-z.+~]*$`. `xbps-src` solo rechaza `-` y `_` (`common.sh:496-503`); `+` y `~` son válidos y void-packages tiene 195 versiones con ellos. Sin comillas, sin sustitución de shell.

## ABI, libc y arquitectura

1. **Código fuente portable o datos/scripts** → sin `archs` (se construye en todas las arquitecturas y en glibc y musl).
2. **Binario precompilado que depende de una libc** (glibc) → `archs="x86_64"` (o el arch que sea) **sin** `*`: así los constructores musl lo rechazan limpio. Nunca un ELF glibc en un repo musl.
3. **Binario independiente de la libc** (kernel, estático Go/Rust) → `archs="x86_64*"`: sale en glibc y en musl. Verificar con `file`/`readelf -d` (sin `PT_INTERP` = sin libc).
4. **Fuente con restricción de CPU o de libc** → `archs` explícito, o `~*-musl` si usa extensiones de glibc.
5. **Shims de musl** cuando el código usa extensiones de glibc: `<execinfo.h>` → `libexecinfo-devel` + `-lexecinfo`; `<argp.h>` → `argp-standalone` + `-largp`; `<fts.h>` → `musl-fts-devel`; `<obstack.h>` → `musl-obstack-devel`; `<sys/queue.h>` → `musl-legacy-compat`.
6. **Atomics de 64 bits en 32 bits** (`$XBPS_TARGET_NO_ATOMIC8`): `libatomic-devel` + `-latomic`.
7. **`hostmakedepends` vs `makedepends`**: lo que se *ejecuta* en el host va en `hostmakedepends`; lo que se *enlaza* contra el target va en `makedepends` (normalmente `-devel`). `nocross=` solo si el build necesita ejecutar binarios del target y `build_helper="qemu"` no basta.
8. **Headers, `.a`, `pkgconfig`, configs CMake y symlinks `.so` sin versión** → subpaquete `-devel` con `vmove`.

## Precompilado o desde fuente

- Si el software es de código abierto, compila rápido (no es un navegador ni compila Electron) y depende de una libc, se compila DESDE FUENTE: así el paquete sale en glibc y en musl. Precompilado solo si el upstream no publica fuentes, si el build tarda horas, o si es proprietary.
- Si el binario precompilado NO depende de una libc (un kernel, un binario estático), da igual cuál se use: se deja el precompilado y se pone `archs="x86_64*"`, que lo publica en ambas.
- `archs="x86_64"` (sin `*`) es lo que deja el paquete en glibc-only. Solo tiene sentido si el binario depende de una libc.
- Un paquete sin `archs=` se construye para todas las arquitecturas: si el binario o las fuentes son de una sola, hay que poner `archs`.
- Convención del repo: todo paquete de binario precompilado termina en `-bin`; los compilados desde fuente no. Excepción: `trinity-launcher-ap`.

## VUR index (for vary)

- Default branch stays `master`; vary auto-detects it (`--branch` override exists).
- Root `.VURINFO` (JSON array, schema v1) is generated, never hand-edited: `.github/workflows/vurinfo.yaml` runs `.github/scripts/gen-vurinfo.py` (static parsing, same semantics as vary's template fallback — no container, no masterdir, on purpose: building happens in z-repo/Vary) on every `master` push touching `srcpkgs/**` and commits the result. Never `source template` anywhere.
- Several dirs declare a different `pkgname` (`*-devel/` subpackages; `srcpkgs/chatgpt/` → `codex-bin`), so the templates index to fewer entries: first wins, later duplicates are warned and dropped. Fix the template, never the generator.
- `/.VURINFO` is allow-listed in `.gitignore`; the workflow commit only touches it, so it never retriggers itself.

## Auto-updates

- `.github/scripts/detect-updates.py` — solo lectura. Clasifica cada template por la forma de su URL (tags / releases / pin / tabla / excluido) y compara versiones con `xbps-uhelper cmpver`, nunca con cadenas. `--selftest` comprueba el parser sin red. Salida con `--json`/`--md`. Localmente exporta `GITHUB_TOKEN`: sin él son 60 req/h y una pasada hace ~34.
- `.github/workflows/detect-updates.yml` — cron diario; no toca ningún template, sube `updates.json` como artefacto.
- `.github/workflows/update-packages.yml` — cron nocturno: elige 5 paquetes (primero los que más rezago tienen), actualiza `version`/`revision`/`distfiles`, recalcula el `checksum` con `xgensum`, compila con `./xbps-src -Q pkg`, abre un PR por paquete y lo mergea cuando el CI queda verde. Techo de 5 por noche a propósito: un upstream roto no entra en 60 paquetes de golpe.
- `.github/scripts/apply-update.py` y `plan-updates.py` — los dos pasos del updater (editar el template, elegir a quién actualizar).
- `.github/scripts/format-template.py` — normaliza un template al estilo de `xlint` (header, cero comentarios, orden de variables, tabs, wrap a 80 columnas, `short_desc`, licencia SPDX). Es **idempotente**: correrlo dos veces no cambia el archivo la segunda vez. El updater lo ejecuta antes de commitear, porque el CI corre xlint sobre el template tocado.
- El secret `PR_TOKEN` (un PAT con permisos de `contents` y `pull_requests`) es obligatorio: los workflows lanzados con `GITHUB_TOKEN` no activan otros workflows, así que el CI del PR nunca se dispararía y el PR se quedaría sin checks para siempre.
- El merge automático exige que los **builds** estén en verde; el lint es cosmético y sus problemas se reportan en una Issue.
- Un paquete con `_commit=` (pin) no se actualiza nunca solo: el pin es una decisión y su `version=` no se deduce del commit. El detector lo informa como `pin` y lo mueve una persona.
- `distfiles` con la comilla abierta en la línea siguiente, o con variables `_site=`/`_base=`, se expanden antes de clasificar. Los paquetes que no se pueden versionar solos se declaran no automatizables, con su motivo, no como error.

### Contrato de automatizabilidad (lo que decide si un paquete se actualiza solo)

| Requisito | Regla | Si se incumple |
|---|---|---|
| `version=` | `^[0-9][0-9A-Za-z.+~]*$`, sin comillas | xbps-src aborta: *"version contains invalid character"* |
| `distfiles` | el **primer** entry contiene `${version}` en una URL determinista de tags (`/archive/refs/tags/v${version}.tar.gz`) o de release (`/releases/download/v${version}/<asset>`) | el detector lo marca `manual` con su motivo |
| Canal | nada de `/latest/` ni de assets sin versión | idem (`manual: canal rodante`) |
| Pin | sin `_commit=` | el detector lo marca `pin`: nunca automático |
| Checksum | siempre recalculado descargando el asset (`xgensum`), nunca heredado | build roto para siempre |
| `revision` | `1` en cada subida de versión | el repo se instala dos veces la misma versión |
| Subpaquetes | `depends="${sourcepkg}>=${version}_${revision}"` | el `-devel` pide una versión que ya no existe |
| Metadatos | `provides="foo-${version}_${revision}"`, `conflicts`/`replaces` con `>=0` | repo incoherente al actualizar |
| Build | 100% desde `distfiles`, sin red | el checksum no significa nada y el build no es reproducible |

Lo que **no** se puede prometer: que el upstream no publique un tag roto (lo detecta el CI), que no borre un asset (404 → el detector lo descarta), ni que una app gráfica arranque (sin display no hay smoke test; `do_check` upstream es el techo).

## Verificación antes de commitear

```sh
# 1. xlint limpio
LICENSE_LIST=$PWD/common/travis/license.lst xlint srcpkgs/*/template
# 2. el formateador no cambia nada (idempotencia)
python3 .github/scripts/format-template.py srcpkgs/*/template   # -> "0 template(s) normalizados"
# 3. sintaxis
for f in srcpkgs/*/template; do bash -n "$f" || echo "ROTA: $f"; done
# 4. build real
./xbps-src -Q pkg <nombre>
```
