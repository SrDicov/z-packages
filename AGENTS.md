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
- `make_check=no` needs a comment explaining why. `nocross=`/`broken=` must state why (or link a build log). `restricted=` packages need `XBPS_ALLOW_RESTRICTED=yes` in `etc/conf` and never enter official repos. Scope `archs` only with upstream justification.
- Soname bump → update `common/shlibs` and revbump every dependent in the same PR (one commit each).
- CI matrix is glibc x86_64/i686/aarch64/armv7l + musl x86_64/aarch64/armv6l; tests (`-Q` equivalent) run only on x86_64, i686 and x86_64-musl. `[ci skip]` in the PR title or body skips the build job only (lint still runs); for multi-hour or >14G builds, report local glibc+musl × 64/32-bit results instead.
- CI fails if the branch is ≥200 commits behind `origin/HEAD`: rebase before pushing. `changed_templates.sh` derives the base with `git merge-base origin/HEAD`.

## VUR index (for vary)

- Default branch stays `master`; vary auto-detects it (`--branch` override exists).
- Root `.VURINFO` (JSON array, schema v1) is generated, never hand-edited: `.github/workflows/vurinfo.yaml` runs `.github/scripts/gen-vurinfo.py` (static parsing, same semantics as vary's template fallback — no container, no masterdir, on purpose: building happens in z-repo/Vary) on every `master` push touching `srcpkgs/**` and commits the result. Never `source template` anywhere.
- Several dirs declare a different `pkgname` (`*-devel/` subpackages; `srcpkgs/chatgpt/` → `codex-bin`), so 146 templates index to 135 entries: first wins, later duplicates are warned and dropped. Fix the template, never the generator.
- `/.VURINFO` is allow-listed in `.gitignore`; the workflow commit only touches it, so it never retriggers itself.

## Auto-updates

- `.github/scripts/detect-updates.py` — solo lectura. Clasifica cada template por la forma de su URL (tags / releases / pin / tabla / excluido) y compara versiones con `xbps-uhelper cmpver`, nunca con cadenas. `--selftest` comprueba el parser sin red. Salida con `--json`/`--md`. Localmente exporta `GITHUB_TOKEN`: sin él son 60 req/h y una pasada hace ~34.
- `.github/workflows/detect-updates.yml` — cron diario; no toca ningún template, sube `updates.json` como artefacto.
- `.github/workflows/update-packages.yml` — cron nocturno: elige 5 paquetes (primero los que más rezago tienen), actualiza `version`/`revision`/`distfiles`, recalcula el `checksum` con `xgensum`, compila con `./xbps-src -Q pkg`, abre un PR por paquete y lo mergea cuando el CI queda verde. Techo de 5 por noche a propósito: un upstream roto no entra en 60 paquetes de golpe.
- `.github/scripts/apply-update.py` y `plan-updates.py` — los dos pasos del updater (editar el template, elegir a quién actualizar).
- Un paquete con `_commit=` (pin) no se actualiza nunca solo: el pin es una decisión y su `version=` no se deduce del commit. El detector lo informa como `pin` y lo mueve una persona.
- `distfiles` con la comilla abierta en la línea siguiente, o con variables `_site=`/`_base=`, se expanden antes de clasificar. Los paquetes que no se pueden versionar solos se declaran no automatizables, con su motivo, no como error.
