# autopkg-engine (`v-auto`)

**autopkg-engine** es el sistema unificado y definitivo para la autoactualización de templates, planificación topológica de dependencias, compilación quirúrgica y publicación segura de repositorios binarios XBPS en Void Linux.

Diseñado para reemplazar los scripts monolíticos y fragmentados de `z-packages`, `neko-pkgs`, `z-repo`, `z-repo-musl` y `repository`, resolviendo de raíz los cuellos de botella críticos de CI/CD.

---

## ⚡ Problemas Críticos Resueltos

1. **Agotamiento de Rate Limits de GitHub API:**
   - Consulta tags upstream mediante `git ls-remote --tags` (**costo cero de cuota de API**).
   - Soporte para cabeceras condicionales HTTP `ETag` / `If-None-Match` (respuestas `304 Not Modified`).
2. **Edición No Destructiva de Templates:**
   - Analizador léxico que sustituye quirúrgicamente `version`, `revision` y `checksum` conservando **el 100% de los comentarios (`#`)**, funciones de shell y formato.
3. **Planificación DAG y Cuarentena Exponencial:**
   - Ordena la compilación topológicamente basándose en `makedepends`, `hostmakedepends` y `depends`.
   - Bloquea paquetes rotos ("poison pills") en un ledger `quarantine.json` con reintentos escalonados (6h, 24h, 72h, 7d). Si upstream publica una nueva versión, se rehabilita de inmediato.
4. **Optimización Extrema de Disco en CI (Zero 30GB Bloat):**
   - Prohíbe la descarga ciega (`gh release download *.xbps*`). Descarga **únicamente** los `.xbps` requeridos como dependencias del paquete a compilar.
5. **CI/CD Orientado a Eventos (Zero Sleep Loops):**
   - Elimina los bucles de espera activa (`sleep 30` hasta 160 veces). El workflow de build ejecuta el auto-merge una vez que la matriz de compilación está en verde.

---

## 📁 Arquitectura del Proyecto

```text
autopkg/
├── cli.py                     # CLI unificado (detect, plan, update, seed, prepare-tree, build, publish, quarantine)
├── core/
│   ├── adapter.py             # URA: autodetección de layouts (srcpkgs, pkgs, ./) y subpaquetes
│   ├── template.py            # Parser/editor léxico NO destructivo
│   ├── dag.py                 # Planificador de dependencias topológicas (Kahn's DAG)
│   └── quarantine.py          # Gestión de cuarentena y backoff exponencial
├── detector/
│   ├── engine.py              # Orquestador de detección de versiones upstream
│   ├── rules.py               # Lector declarativo sin dependencias externas obligatorias
│   ├── void_update.py         # Soporte nativo de archivos 'update' oficiales de Void
│   └── backends/              # Plugins: git_tags, github, apt, codeberg, regex
├── builder/
│   ├── bootstrap.py           # Configuración de etc/conf y masterdir
│   ├── selective_seed.py      # Descarga quirúrgica de solo dependencias necesarias
│   ├── workspace.py           # Compone void-packages + templates para repos sin xbps-src
│   └── runner.py              # Ejecución de compilación glibc/musl y logs
├── publisher/
│   ├── indexer.py             # xbps-rindex, firmado, podado y generación de <fp>.plist
│   ├── validator.py           # Verificación de integridad index.plist vs assets
│   └── backends/              # Backends: github_release, git_branch (Forgejo), local, rsync
├── config/
│   └── updates.rules.yml      # Reglas declarativas extraídas de detect-updates.py
├── workflows/
│   ├── github/                # Workflows optimizados para GitHub Actions
│   └── forgejo/               # Workflows listos para Forgejo / Gitea
└── tests/                     # Suite de pruebas unitarias e integración
```

---

## 🚀 Uso del CLI Unificado

El comando principal es `./cli.py` (o `python3 -m autopkg.cli`):

### 1. Detectar actualizaciones upstream
```bash
# Escanear todo el repositorio respetando reglas y mostrando sólo pendientes
./cli.py detect --repo /ruta/al/repo --rules config/updates.rules.yml --updates-only

# Escanear paquetes específicos
./cli.py detect --repo /ruta/al/repo bibata-cursor-theme artcraft-launcher

# Salida en JSON para scripts de automatización
./cli.py detect --repo /ruta/al/repo --json
```

### 2. Planificar el orden de actualización (DAG + Cuarentena)
```bash
# Obtener los próximos 5 paquetes a actualizar ordenados por dependencias
./cli.py plan --repo /ruta/al/repo --limit 5

# Salida en JSON list para matrices de CI
./cli.py plan --repo /ruta/al/repo --limit 5 --json
```

### 3. Actualizar un template quirúrgicamente
```bash
# Actualizar detectando automáticamente la última versión y calculando SHA256 por streaming
./cli.py update --repo /ruta/al/repo artcraft-launcher

# Forzar una versión y revisión específica
./cli.py update --repo /ruta/al/repo --version 1.2.0 --revision 1 artcraft-launcher
```

### 4. Descarga quirúrgica de dependencias (Seed)
```bash
# Descarga ÚNICAMENTE las dependencias necesarias de 'hyprland' desde el release remoto
./cli.py seed --repo /ruta/al/repo --from-github "usuario/z-repo" --arch x86_64 hyprland
```

### 5. Preparar árbol de build para repos template-only
```bash
# Clona void-packages y superpone los templates del repo (conserva symlinks).
# En repos que ya son un fork completo, devuelve la misma ruta sin clonar.
./cli.py prepare-tree --repo /ruta/al/repo --dest /tmp/void-packages
```

### 6. Compilar paquetes con registro de cuarentena
```bash
# Compilar paquete; en caso de error lo envía a cuarentena con backoff
./cli.py build --repo /ruta/al/repo --arch x86_64 artcraft-launcher
```

### 7. Publicar repositorio binario (Firma + Repodata + Verificación)
```bash
# Publicar a GitHub Releases con verificación estricta de hash
./cli.py publish \
  --binpkgs-dir hostdir/binpkgs/x86_64 \
  --arch x86_64 \
  --backend github \
  --dest "usuario/z-repo" \
  --tag "rolling-x86_64" \
  --signed-by "Z Linux Core <SrDicov@gmail.com>" \
  --privkey /ruta/a/rsa.pem \
  --prune

# Publicar a rama git de Forgejo (ej. rama 'binpkgs')
./cli.py publish \
  --binpkgs-dir hostdir/binpkgs/x86_64 \
  --arch x86_64 \
  --backend forgejo \
  --dest "https://git.voiders.dev/usuario/repository.git" \
  --branch binpkgs \
  --signed-by "Voiders Community" \
  --privkey /ruta/a/rsa.pem
```

### 8. Gestionar Cuarentena
```bash
# Listar paquetes actualmente en cuarentena y su fecha de desbloqueo
./cli.py quarantine list

# Liberar un paquete de la cuarentena
./cli.py quarantine clear artcraft-launcher

# Limpiar toda la cuarentena
./cli.py quarantine clear
```

---

## 🧪 Ejecutar la Suite de Pruebas

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_*.py" -v
```

Todas las pruebas se ejecutan en milisegundos sin dependencias externas requeridas.
