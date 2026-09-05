#!/usr/bin/env bash
# ============================================================================
# .github/scripts/gen-vurinfo-root.sh
#
# Genera el .VURINFO RAÍZ (array JSON, esquema v1) que consume vary.
# Ver https://github.com/SrDicov/Vary/blob/vary-mvp/docs/VURINFO.md
#
# Qué hace:
#   Para cada srcpkgs/*/template, pide a ./xbps-src las variables YA
#   EVALUADAS (`xbps-src show`, dentro del masterdir) y las convierte a JSON
#   con el vurinfo.jq de vary; luego las combina en un único array ordenado
#   por pkgname en ./.VURINFO (diffs estables).
#
# Seguridad:
#   Las plantillas NUNCA se interpretan con bash del host: aquí no se hace
#   `source template`. Todo se evalúa dentro del masterdir controlado por
#   xbps-src; este script solo captura su salida (líneas clave=valor) y la
#   transforma con jq.
#
# Requisitos:
#   - Ejecutar desde la raíz de z-packages.
#   - Masterdir booteado (`./xbps-src binary-bootstrap`).
#   - jq + curl instalados; red para descargar vurinfo.jq de vary.
#
# Variables:
#   VARY_REF  ref de Vary del que descargar scripts/vurinfo.jq (def: vary-mvp).
#
# Salida:
#   ./.VURINFO (array). Los paquetes que fallen se omiten con aviso; si no se
#   indexa ninguno, falla.
# ============================================================================

set -euo pipefail

VARY_REF="${VARY_REF:-vary-mvp}"
VARY_RAW="https://raw.githubusercontent.com/SrDicov/Vary/${VARY_REF}"

die() { printf 'gen-vurinfo-root: error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'gen-vurinfo-root: aviso: %s\n' "$*" >&2; }

# --- Comprobaciones de entorno ---------------------------------------------
command -v jq >/dev/null 2>&1 || die "jq no está instalado"
command -v curl >/dev/null 2>&1 || die "curl no está instalado"
[[ -x ./xbps-src ]] || die "ejecútalo desde la raíz de z-packages (no se encontró ./xbps-src)"
[[ -d masterdir ]] || die "masterdir ausente: arranca el entorno con './xbps-src binary-bootstrap'"

WORK="$(mktemp -d)"
OUT="$(mktemp -d)"
trap 'rm -rf "$WORK" "$OUT"' EXIT

# vurinfo.jq es la fuente de verdad del esquema (vive en vary, el consumidor).
curl -fsSL "${VARY_RAW}/scripts/vurinfo.jq" -o "${WORK}/vurinfo.jq" \
    || die "no se pudo descargar vurinfo.jq de Vary@${VARY_REF}"

# --- Conversión de plantillas a JSON ----------------------------------------
count=0
for tmpl in srcpkgs/*/template; do
    pkg="$(basename "$(dirname "$tmpl")")"
    printf '==> %s\n' "$pkg" >&2
    if ! vars="$(./xbps-src show "$pkg" 2>/dev/null)"; then
        warn "fallo al mostrar $pkg; se omite"
        continue
    fi
    if printf '%s\n' "$vars" | jq -S -n -R -f "${WORK}/vurinfo.jq" >"${OUT}/${pkg}.json"; then
        count=$((count + 1))
    else
        warn "no se pudo convertir $pkg a JSON; se omite"
    fi
done

[[ "$count" -gt 0 ]] || die "ningún paquete indexado"

# --- Combinar en el índice raíz ----------------------------------------------
jq -s -S 'sort_by(.pkgname)' "${OUT}"/*.json > .VURINFO.tmp

# Sanidad: array no vacío del esquema v1.
jq -e 'type == "array" and length > 0
    and all(.[];
        .format_version == 1
        and (.pkgname | type == "string")
        and (.version | type == "string")
        and (.revision >= 1)
        and (.archs | type == "array" and length > 0))' .VURINFO.tmp >/dev/null \
    || die ".VURINFO inválido"

mv -f .VURINFO.tmp .VURINFO
printf 'indexados: %d paquetes -> .VURINFO\n' "$count" >&2
