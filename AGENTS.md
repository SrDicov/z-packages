# AGENTS.md

XBPS source-packages collection (Void Linux fork). Packages are shell `template` files built with `xbps-src`, not normal source code.

## Layout

- `srcpkgs/<pkgname>/template` — package recipe (mandatory). Optional siblings: `patches/*.patch|diff`, `files/`, `update` (sets `site`/`pattern`/`ignore` for `update-check`).
- `xbps-src` — the only build entrypoint (bash). `common/build-style/*.sh` — `build_style` implementations. `common/shlibs` — soname→package mapping. `common/cross-profiles/`, `common/build-profiles/` — arch flags.
- `etc/defaults.conf` — defaults, never edit. Local overrides go in `etc/conf` (create it). Build outputs: `hostdir/binpkgs`, `hostdir/sources`, `masterdir-<arch>/`.

## Commands (run from repo root, never as root)

- `./xbps-src pkg <name>` — build one package + deps into `hostdir/binpkgs`.
- `./xbps-src -Q pkg <name>` — same, plus `do_check` for target only (always run before submitting; `-K` also checks deps).
- `./xbps-src -a armv6l pkg <name>` — cross-build smoke test (CONTRIBUTING recommends at least this).
- `./xbps-src show-options <name>` / `./xbps-src -o opt,~opt2 pkg <name>` — list / toggle build options.
- `./xbps-src update-check <name>` — check upstream version; per-package overrides live in `srcpkgs/<name>/update`.
- `xlint template` (from `xtools`, run inside `srcpkgs/<name>/`) — required lint. `xgensum -i <name>` — refresh `checksum`. `common/scripts/lint-commits`, `lint-version-change`, `lint-conflicts` — what CI runs.
- `./xbps-src binary-bootstrap` / `./xbps-src bootstrap-update` / `./xbps-src zap` — init / update / recreate masterdir. Never commit `hostdir/`, `masterdir-*/`.

## Template rules (see `Manual.md`, `CONTRIBUTING.md`)

- One commit per package. Messages: `New package: <name>-<version>`, `<name>: update to <version>.`, `<name>: <reason>` (template-only change), `<name>: remove package`.
- Version bump → `revision=1` + new `checksum`. Template-only change affecting binaries → increment `revision`, keep `version`. Orphan/adopt-only → don't bump.
- `version` has no `-`/`_` and no shell substitution. `pkgname` matches its directory. Lines ≤80 cols; continuation lines indent one space. `short_desc` ≤72 chars.
- Deps: `hostmakedepends` (runs on host: compilers, tools) vs `makedepends` (links against, usually `-devel`) vs `checkdepends` (only for `do_check`, needs `-Q`/`XBPS_CHECK_PKGS`) vs `depends` (runtime only; never list auto-detected ELF/soname deps). `makedepends` with no build effect → drop it.
- `make_check=no` needs a comment explaining why. `nocross=`/`broken=` must state why (or link a build log). `restricted=` packages need `XBPS_ALLOW_RESTRICTED=yes` in `etc/conf` and never enter official repos. Scope `archs` only with upstream justification.
- Soname bump → update `common/shlibs` and revbump every dependent in the same PR (one commit each).
- CI builds glibc+musl × x86_64/i686/aarch64/armv7l(+musl variants) with tests on some; add `[ci skip]` to the PR title/body for multi-hour or >14G builds and report local glibc+musl × 64/32-bit results instead. PRs touching `srcpkgs/**` get lint+build; `master`-branch repo name maps to global repo scope.
