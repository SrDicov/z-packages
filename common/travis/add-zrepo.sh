#!/bin/sh
#
# add-zrepo.sh <target>
#
# Deja el repo de binarios del fork (z-repo o z-repo-musl) visible para
# xbps-src. La configuracion va a etc/xbps.d/custom/ y la clave publica a
# etc/repo-keys/: chroot.sh copia ambas al masterdir en cada build, asi que
# basta con dejarlas en el arbol antes de compilar.
#
# Solo hay binarios para x86_64 (glibc) y x86_64-musl. En otro target no se
# anade nada: un repodata inexistente solo ensuciaria el xbps del masterdir.

set -e

target="${1:?falta el target (x86_64, x86_64-musl, aarch64...)}"

case "$target" in
x86_64-musl) repo=https://github.com/SrDicov/z-repo-musl/releases/download/stable ;;
x86_64) repo=https://github.com/SrDicov/z-repo/releases/download/stable ;;
*)
	echo "zrepo: no hay binarios para $target, no se anade el repo"
	exit 0
	;;
esac

mkdir -p etc/xbps.d/custom etc/repo-keys
printf 'repository=%s\n' "$repo" >etc/xbps.d/custom/10-zrepo.conf

key=$(curl -fsSL https://github.com/SrDicov/z-repo/releases/expanded_assets/stable |
	grep -oE '[0-9a-f]{2}(\.[0-9a-f]{2}){15}\.plist' | head -1)
if [ -z "$key" ]; then
	echo "zrepo: no encontre la clave publica en el release" >&2
	exit 1
fi
curl -fsSL -o "etc/repo-keys/$key" \
	"https://github.com/SrDicov/z-repo/releases/download/stable/$key"

echo "zrepo: $repo (clave $key)"
