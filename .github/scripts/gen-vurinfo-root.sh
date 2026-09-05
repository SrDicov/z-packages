#!/usr/bin/env bash
# ============================================================================
# .github/scripts/gen-vurinfo-root.sh
#
# Genera el .VURINFO RAÍZ (array JSON, esquema v1) que consume vary.
# Ver https://github.com/SrDicov/Vary/blob/vary-mvp/docs/VURINFO.md
#
# Qué hace:
#   Para cada plantilla indicada (o todas las srcpkgs/*/template), pide a
#   ./xbps-src las variables YA EVALUADAS (`xbps-src show`, dentro del
#   masterdir) y las convierte a JSON con el vurinfo.jq de vary; luego las
#   combina en un único array ordenado por pkgname en $OUT_FILE
#   (diffs estables).
#
# Seguridad:
#   Las plantillas NUNCA se interpretan con bash del host: aquí no se hace
#   `source template`. Todo se evalúa dentro del masterdir controlado por
#   xbps-src; este script solo captura su salida (líneas clave=valor) y la
#   transforma con jq.
#
# Requisitos:
#   - Ejecutar desde la raíz de un checkout COMPLETO de void-packages con
#     las plantillas a indexar ya presentes en srcpkgs/ (p. ej. overlay de
#     este repo) y masterdir booteado (`./xbps-src binary-bootstrap`).
#   - jq + curl instalados; red para descargar vurinfo.jq de vary.
#
# Uso:
#   gen-vurinfo-root.sh                  # todas las srcpkgs/*/template
#   gen-vurinfo-root.sh foo bar          # solo los paquetes indicados
#
# Variables:
#   VARY_REF   ref de Vary del que descargar scripts/vurinfo.jq (def: vary-mvp).
#   OUT_FILE   destino del índice (def: ./.VURINFO).
#
# Salida:
#   $OUT_FILE (array). Los paquetes que fallen se omiten con aviso; si no se
#   indexa ninguno, falla.
# ============================================================================

set -euo pipefail

VARY_REF="${VARY_REF:-vary-mvp}"
VARY_RAW="https://raw.githubusercontent.com/SrDicov/Vary/${VARY_REF}"
OUT_FILE="${OUT_FILE:-.VURINFO}"

die() { printf 'gen-vurinfo-root: error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'gen-vurinfo-root: aviso: %s\n' "$*" >&2; }

# --- Comprobaciones de entorno ---------------------------------------------
command -v jq >/dev/null 2>&1 || die "jq no está instalado"
command -v curl >/dev/null 2>&1 || die "curl no está instalado"
[[ -x ./xbps-src ]] || die "ejecútalo desde la raíz de un checkout de void-packages (no se encontró ./xbps-src)"
[[ -d masterdir ]] || die "masterdir ausente: arranca el entorno con './xbps-src binary-bootstrap'"

WORK="$(mktemp -d)"
OUT="$(mktemp -d)"
trap 'rm -rf "$WORK" "$OUT"' EXIT

# vurinfo.jq es la fuente de verdad del esquema (vive en vary, el consumidor).
curl -fsSL "${VARY_RAW}/scripts/vurinfo.jq" -o "${WORK}/vurinfo.jq" \
    || die "no se pudo descargar vurinfo.jq de Vary@${VARY_REF}"

# --- Lista de paquetes -------------------------------------------------------
if (($#)); then
    mapfile -t PKGS < <(printf '%s\n' "$@")
    for pkg in "${PKGS[@]}"; do
        [[ -f "srcpkgs/${pkg}/template" ]] || die "plantilla inexistente: srcpkgs/${pkg}/template"
    done
else
    mapfile -t PKGS < <(for tmpl in srcpkgs/*/template; do basename "$(dirname "$tmpl")"; done)
fi
[[ "${#PKGS[@]}" -gt 0 ]] || die "ningún paquete a indexar"

# --- Conversión de plantillas a JSON ------------------------------------------
count=0
for pkg in "${PKGS[@]}"; do
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
jq -s -S 'sort_by(.pkgname)' "${OUT}"/*.json > "${OUT}/index.json"

# Sanidad: array no vacío del esquema v1.
jq -e 'type == "array" and length > 0
    and all(.[];
        .format_version == 1
        and (.pkgname | type == "string")
        and (.version | type == "string")
        and (.revision >= 1)
        and (.archs | type == "array" and length > 0))' "${OUT}/index.json" >/dev/null \
    || die ".VURINFO inválido"

mv -f "${OUT}/index.json" "$OUT_FILE"
printf 'indexados: %d paquetes -> %s\n' "$count" "$OUT_FILE" >&2
