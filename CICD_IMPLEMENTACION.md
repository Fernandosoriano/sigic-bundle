# Implementación de CI/CD — Primer Despliegue Exitoso

## Contexto

Este documento registra el proceso de implementación del pipeline de CI/CD para SIGIC Bundle,
los errores encontrados durante las pruebas y los fixes aplicados. Cubre desde la creación
del workflow hasta el primer reinstall automatizado exitoso en `sedema-qa` (9 de julio de 2026).

---

## 1. Configuración del workflow

### Runner auto-hospedado

Se configuró un runner self-hosted en el servidor Nimbus (`10.2.102.228`) con las etiquetas
`self-hosted, nimbus`. El runner ejecuta los jobs directamente sobre `/opt/sigic-bundle`,
donde viven los stacks de los ambientes de desarrollo y QA.

### `.github/workflows/deploy.yml`

Workflow manual (`workflow_dispatch`) con dos inputs:

| Input | Opciones |
|-------|----------|
| `platform` | `idegeo`, `conafor`, `sedema` |
| `environment` | `dev`, `qa`, `prd` |

#### Control de concurrencia

```yaml
concurrency:
  group: deploy-${{ inputs.platform }}-${{ inputs.environment }}
  cancel-in-progress: false
```

Garantiza que no haya dos deploys simultáneos al mismo stack. Si se dispara un segundo
deploy mientras uno está corriendo, el segundo espera en cola en lugar de cancelar el primero.

#### Pasos del job

**1. Verificar estado del repo**

Comprueba que no haya cambios sin commitear en archivos rastreados de `/opt/sigic-bundle`
(excluyendo submodules). Si los hay, el deploy falla con mensaje descriptivo. Esto protege
contra deploys que sobreescriban trabajo en progreso en el servidor.

**2. Sync bundle**

```bash
git fetch origin
git checkout ${{ github.ref_name }}   # rama desde la que se disparó el workflow
git pull origin ${{ github.ref_name }}
git submodule update --init --recursive
```

Actualiza el bundle y todos sus submodules a la versión de la rama seleccionada.

**3. Deploy**

```bash
./sigic_install.sh ${{ inputs.platform }} ${{ inputs.environment }}
```

Delega toda la lógica de instalación al script existente, que:
- Genera `.env` vía `create-envfile.py`
- Preserva passwords de la instalación anterior (si existe)
- Reconstruye `DATABASE_URL`/`GEODATABASE_URL` desde los passwords preservados
- Levanta el stack con `docker compose up -d`
- Importa configuración de Keycloak
- Carga fixtures de Django (socialaccount OIDC)

**4. Verificar stack**

```bash
docker ps -a --filter "label=com.docker.compose.project=$PROJECT"
```

Valida que:
- Existan contenedores para el proyecto (detecta si `COMPOSE_PROJECT_NAME` no se fijó)
- No haya contenedores detenidos inesperadamente (excluye `init-*` que terminan por diseño)

#### Timeout y runner

```yaml
runs-on: [self-hosted, nimbus]
timeout-minutes: 90
```

El job tiene un timeout de 90 minutos para absorber cold starts lentos de Django
(migraciones de GeoNode en primera instalación) y de Keycloak.

#### Flujo completo

```
GitHub Actions UI
  └─ workflow_dispatch (platform + environment)
       └─ Runner en Nimbus
            ├─ 1. Verificar repo limpio
            ├─ 2. git pull + submodule update
            ├─ 3. sigic_install.sh
            │    ├─ create-envfile.py → .env
            │    ├─ Preservar passwords de .env.<platform>-<environment>
            │    ├─ Reconstruir DATABASE_URL / GEODATABASE_URL
            │    ├─ docker compose up -d
            │    ├─ Esperar Keycloak → import clientes
            │    └─ Esperar Django → loaddata socialaccount.json
            └─ 4. Verificar stack (docker ps)
```

---

## 2. Problemas encontrados y fixes

### 2.1 Timeout de pip durante `docker compose build`

**Síntoma:** El build del contenedor `django` fallaba en el paso de instalación de paquetes pip
con un error de read timeout al descargar el wheel de `geonode-4.4.0.dev0` desde GitHub Releases.

**Causa:** Docker NAT añade latencia al tráfico saliente; el timeout por defecto de pip (15s)
no era suficiente para archivos grandes desde GitHub Releases.

**Fix:** Construir la imagen con `--network=host` para bypass el NAT de Docker:

```bash
docker build --network=host -t ghcr.io/centrogeo/sigic-geonode-wrapper/sigic_geonode:latest geonode/
```

No se requirió ningún cambio en el `Dockerfile`.

---

### 2.2 Crash de Django con geonode v0.1.18

**Síntoma:** Django crasheaba al arrancar con `NoReverseMatch: Reverse for 'account_login'`
justo después de actualizar el submodule `geonode/` a v0.1.18.

**Causa:** El wheel mutable de `geonode==4.4.0.dev0` instalado por pip incluía una llamada a
`reverse("account_login")` a nivel de módulo en `geonode/security/middleware.py`. Esta llamada
se ejecuta durante la carga de URLs de Django, antes de que el sistema de routing esté listo.

**Fix:** Revertir el submodule `geonode/` a la etiqueta `v0.1.17`, que tenía imagen estable
confirmada en `sedema-qa`. Se reconstruyó la imagen con `--network=host`:

```bash
# En el repositorio geonode/
git checkout v0.1.17

# Rebuild desde el bundle
docker build --network=host -t ghcr.io/centrogeo/sigic-geonode-wrapper/sigic_geonode:latest geonode/
```

El bundle registra el submodule en v0.1.17 en el commit:
`revert: geonode submodule a v0.1.17 (imagen estable confirmada en sedema-qa)`

---

### 2.3 `--noinput` ausente en CI/CD

**Síntoma:** `create-envfile.py` se bloqueaba esperando input del usuario cuando se ejecutaba
desde el runner (sin terminal interactiva).

**Fix:** Detectar si hay terminal disponible y pasar `--noinput` en caso contrario:

```bash
NOINPUT_FLAG=""
[ ! -t 0 ] && NOINPUT_FLAG="--noinput"

python3 create-envfile.py ... $NOINPUT_FLAG
```

Commit: `fix: pasar --noinput a create-envfile.py cuando no hay terminal (CI/CD)`

---

### 2.4 Passwords de DB no preservados en reinstalls (root cause principal)

Este fue el problema más complejo y el que causó más iteraciones.

#### Contexto del problema

En modo plataforma, `sigic_install.sh` llama a `create-envfile.py` en **cada** deploy,
lo cual regenera passwords aleatorios para todos los servicios. Si el stack ya existe
con volúmenes de PostgreSQL, los usuarios de DB ya tienen passwords anteriores y
el contenedor recién creado no puede conectarse.

El script tiene un loop de preservación que lee el `.env.<platform>-<environment>` anterior
y restaura los valores en el nuevo `.env`. Sin embargo, faltaban varias variables clave.

#### Variables añadidas al loop de preservación

| Variable | Commit |
|----------|--------|
| `POSTGRES_PASSWORD` | inicial |
| `KC_DB_PASSWORD` | `fix: preservar KC_DB_PASSWORD en reinstalls` |
| `GEONODE_DATABASE_PASSWORD` | `fix: preservar GEONODE_DATABASE_PASSWORD y GEONODE_GEODATABASE_PASSWORD` |
| `GEONODE_GEODATABASE_PASSWORD` | ídem |

#### Desincronización entre `DATABASE_URL` y `GEONODE_DATABASE_PASSWORD`

`create-envfile.py` genera `DATABASE_URL` y `GEONODE_DATABASE_PASSWORD` como variables
**independientes**, cada una con su propio password aleatorio. Django usa `DATABASE_URL`
para la conexión principal a la DB, mientras que `geodata_conn.py` usa `GEONODE_GEODATABASE_PASSWORD`.
Si se preserva una y no la otra, el contenedor queda con passwords inconsistentes.

**Fix definitivo:** En lugar de intentar preservar `DATABASE_URL` y `GEODATABASE_URL` como
strings (lo cual era frágil y propenso a fallas del `sed`), se reconstruyen a partir de los
passwords individuales que sí se preservan correctamente:

```bash
# Después del loop de preservación de variables individuales:
DB_USER=$(grep "^GEONODE_DATABASE_USER=" .env | cut -d= -f2)
DB_PASS=$(grep "^GEONODE_DATABASE_PASSWORD=" .env | cut -d= -f2)
DB_NAME=$(grep "^GEONODE_DATABASE=" .env | cut -d= -f2)
GEO_USER=$(grep "^GEONODE_GEODATABASE_USER=" .env | cut -d= -f2)
GEO_PASS=$(grep "^GEONODE_GEODATABASE_PASSWORD=" .env | cut -d= -f2)
GEO_NAME=$(grep "^GEONODE_GEODATABASE=" .env | cut -d= -f2)

if [ -n "$DB_USER" ] && [ -n "$DB_PASS" ] && [ -n "$DB_NAME" ]; then
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgis://${DB_USER}:${DB_PASS}@db:5432/${DB_NAME}|" .env
fi
if [ -n "$GEO_USER" ] && [ -n "$GEO_PASS" ] && [ -n "$GEO_NAME" ]; then
  sed -i "s|^GEODATABASE_URL=.*|GEODATABASE_URL=postgis://${GEO_USER}:${GEO_PASS}@db:5432/${GEO_NAME}|" .env
fi
```

Commit: `fix: reconstruir DATABASE_URL y GEODATABASE_URL desde passwords preservados en reinstalls`

---

### 2.5 `geodata_conn.py` — conexión a PostgreSQL a nivel de módulo

**Síntoma:** Django crasheaba en el arranque con `FATAL: password authentication failed for user "sigic_geonode_data"`,
incluso cuando `DATABASE_URL` era correcto.

**Causa:** `sigic_geonode/utils/geodata_conn.py` abre una conexión `psycopg2` a PostgreSQL
**a nivel de módulo** (línea 5, fuera de cualquier función). Este código se ejecuta cuando
Django carga sus URLs, usando `GEONODE_GEODATABASE_PASSWORD`. Si ese password no coincide
con el de PostgreSQL, Django no puede siquiera iniciar.

**Workaround aplicado:** Sincronizar manualmente el password del usuario `sigic_geonode_data`
en PostgreSQL para que coincida con `GEONODE_GEODATABASE_PASSWORD` del contenedor:

```bash
PGPWD=$(grep "^POSTGRES_PASSWORD=" .env.sedema-qa | cut -d= -f2)
docker exec -e PGPASSWORD="$PGPWD" db4sedema-qa psql -U postgres \
  -c "ALTER USER sigic_geonode_data WITH PASSWORD '<password>';"
```

**Fix permanente pendiente:** Modificar `geodata_conn.py` en el submodule `sigic-geonode-wrapper`
para que la conexión se abra dentro de una función (lazy) en lugar de al importar el módulo.

---

## 3. Estado final — Primer reinstall exitoso

**Fecha:** 9 de julio de 2026  
**Stack:** `sedema-qa`  
**Rama:** `main`

Secuencia del deploy exitoso (sin intervención manual):

```
✅ git pull → sigic_install.sh actualizado
✅ create-envfile.py → .env generado con passwords nuevos
✅ Loop de preservación → GEONODE_DATABASE_PASSWORD y GEONODE_GEODATABASE_PASSWORD restaurados
✅ Reconstrucción de URLs → DATABASE_URL y GEODATABASE_URL consistentes con passwords preservados
✅ docker compose up → todos los contenedores recreados
✅ db4sedema-qa → Healthy
✅ django4sedema-qa → Healthy (sin crash de passwords)
✅ Keycloak configurado → clientes importados
✅ Fixture socialaccount → cargado
✅ Stack levantado: 12 contenedores, todos healthy o running
```

---

## 4. Pendientes (tras primer reinstall)

| Ítem | Descripción | Prioridad |
|------|-------------|-----------|
| CI/CD auto en develop | Trigger automático en push a `develop` para despliegue a ambiente dev | Baja |

---

## 5. Primer fresh install — `sedema-dev` (15 de julio de 2026)

Tras el primer reinstall exitoso de `sedema-qa`, se probó el flujo de **fresh install** completo
disparando CI/CD para `sedema-dev`, un ambiente que no existía previamente en Nimbus.

### 5.1 Fix: `npm ERESOLVE` en build del frontend

**Síntoma:** El build de la imagen `sigic-frontend-admin:sedema-dev` fallaba con:

```
npm error code ERESOLVE
npm error ERESOLVE could not resolve
npm error While resolving: @nuxt/test-utils@3.23.0
npm error Found: vitest@4.1.2
npm error peerOptional vitest@"^3.2.0" from @nuxt/test-utils@3.23.0
```

**Causa:** `vitest@4.1.2` (instalado como peer) no satisface el rango `^3.2.0` que requiere
`@nuxt/test-utils@3.23.0`. El conflicto bloqueaba el `npm install` estricto.

**Por qué no afectó a sedema-qa:** Las imágenes de sedema-qa se habían construido antes de que
se introdujera la versión conflictiva de vitest, y quedaron cacheadas en Docker. En sedema-dev,
al ser fresh install, no había caché y el build partía de cero.

**Fix:** Agregar `--legacy-peer-deps` al `npm install` del Dockerfile del frontend:

```dockerfile
# overrides/frontend/Dockerfile
RUN npm install --include=dev --legacy-peer-deps
```

Commit: `fix: --legacy-peer-deps en Dockerfile y pre-build de imágenes frontend en fresh install`

---

### 5.2 Fix: chars problemáticos en `SECRET_KEY`

**Síntoma:** Django fallaba al iniciar con `unterminated quoted value` al leer `.env`. El
`SECRET_KEY` generado contenía caracteres que rompen el parser de archivos `.env`:

```
SECRET_KEY=??gS*#/QJI)T]^T~}EbvN=q8rCym6VNwjY(biUA_G{WzLn@wl\
```

- `#` → interpretado como inicio de comentario
- `\` al final de línea → interpretado como continuación de línea
- `$` → interpolación de variable de shell
- `=` → puede romper parsers de `.env` en ciertos contextos

**Fix:** Eliminar esos caracteres del conjunto `_strong_chars` en `create-envfile.py`:

```python
_strong_chars = shuffle(
    string.ascii_letters
    + string.digits
    + string.punctuation.replace('"', "").replace("'", "").replace("`", "")
    .replace("#", "").replace("\\", "").replace("$", "").replace("=", "")
)
```

**Fix adicional:** Agregar `SECRET_KEY` a la lista de variables preservadas en reinstalls dentro
de `sigic_install.sh`, para que Django no reciba un `SECRET_KEY` distinto en cada deploy
(lo cual invalida sesiones activas y cookies):

```bash
for VAR in POSTGRES_PASSWORD KC_DB_PASSWORD GEONODE_DATABASE_PASSWORD \
           GEONODE_GEODATABASE_PASSWORD GEOSERVER_ADMIN_PASSWORD ADMIN_PASSWORD SECRET_KEY; do
```

Commit: `fix: preservar SECRET_KEY en reinstalls y eliminar chars problemáticos en passwords`

---

### 5.3 Fix: race condition en healthcheck de DB en fresh install

**Síntoma:** En fresh install, `init-keycloak-db` terminaba con exit code != 0 porque
PostgreSQL aún estaba inicializando la base de datos de Keycloak cuando el healthcheck
(`pg_isready`) ya reportaba éxito. Esto dejaba `keycloak`, `django`, `celery` y `geoserver`
en estado `Created` (nunca iniciados) porque sus dependencias de salud no se satisfacían.

**Fix:** Añadir lógica de reintento para `init-keycloak-db` en `sigic_install.sh`:

```bash
INIT_CONTAINER="${COMPOSE_PROJECT_NAME}-init-keycloak-db-1"
for attempt in 1 2 3; do
  for i in $(seq 1 18); do
    STATUS=$(docker inspect --format='{{.State.Status}}' "$INIT_CONTAINER" 2>/dev/null || echo "missing")
    [ "$STATUS" = "exited" ] || [ "$STATUS" = "missing" ] && break
    sleep 10
  done
  EXIT_CODE=$(docker inspect --format='{{.State.ExitCode}}' "$INIT_CONTAINER" 2>/dev/null || echo "0")
  [ "$EXIT_CODE" = "0" ] && break
  echo "⚠️  init-keycloak-db falló (intento $attempt/3) — reintentando en 20s..."
  docker rm "$INIT_CONTAINER" 2>/dev/null || true
  sleep 20
  COMPOSE_PROFILES=$PROFILES docker compose --env-file "$ENV_ACTIVE" \
    -f docker-compose.yml -f docker-compose.platform.yml up -d || true
done
```

---

### 5.4 Fix: segunda pasada de `docker compose up` al final del script

**Síntoma:** En fresh install, `celery` y `geoserver` quedaban en `Created` porque sus
dependencias incluían `django` con condición `service_healthy`, y Django tardaba varios minutos
en completar las migraciones. El script avanzaba antes de que Django estuviera healthy, así que
esos servicios nunca arrancaban.

**Fix:** Añadir una segunda llamada a `docker compose up -d` al final del script, después de
que Django ya quedó healthy y se cargó el fixture:

```bash
if [ "$PLATFORM_MODE" = true ]; then
  COMPOSE_PROFILES=$PROFILES docker compose --env-file "$ENV_ACTIVE" \
    -f docker-compose.yml -f docker-compose.platform.yml up -d || true
fi
```

---

### 5.5 Fix: `externalhttps` en ambientes dev

**Síntoma:** Los archivos `platforms/*/env/dev.env` tenían `https_mode=http`, pero el servidor
Apache (`10.2.7.26`) termina TLS externamente y reenvía HTTP plano a nginx-proxy. Con `http`,
el `SITEURL` generado era `http://...` y GeoNode rechazaba cookies seguras.

**Fix:** Cambiar a `https_mode=externalhttps` en todos los `dev.env`:

```
# platforms/sedema/env/dev.env
https_mode=externalhttps

# platforms/idegeo/env/dev.env
https_mode=externalhttps

# platforms/conafor/env/dev.env
https_mode=externalhttps
```

Commit: `fix: cambiar https_mode a externalhttps en ambientes dev de todas las plataformas`

---

### 5.6 Proxy conf huérfano bloqueaba reload de nginx-proxy

**Síntoma:** Después de completar el setup manual de sedema-dev, `docker exec nginx-proxy nginx -s reload`
fallaba con:

```
host not found in upstream "nginx4sedema-prd" in /etc/nginx/conf.d/sedema-prd.conf:8
```

**Causa:** Un archivo `proxy/conf.d/sedema-prd.conf` quedó de una instalación previa de `sedema-prd`
que nunca se terminó de desplegar. nginx no permite recargar config con upstreams que no resuelven.

**Fix:** Eliminar el archivo huérfano:

```bash
rm proxy/conf.d/sedema-prd.conf
docker exec nginx-proxy nginx -s reload
```

**Lección:** `sigic_delete.sh` ya elimina `proxy/conf.d/<project>.conf` automáticamente (línea 53).
El problema ocurrió porque `sedema-prd` se creó sin haberse dado de baja con `sigic_delete.sh`.
Siempre usar ese script para teardown en lugar de bajar contenedores manualmente.

---

### 5.7 Estado final — sedema-dev operativo

**Fecha:** 15 de julio de 2026  
**Stack:** `sedema-dev`  
**Tipo:** Fresh install (primer deploy)

```
✅ db4sedema-dev            → Healthy
✅ django4sedema-dev        → Healthy (migraciones completas, ~20 min)
✅ celery4sedema-dev        → Running
✅ geoserver4sedema-dev     → Running
✅ keycloak4sedema-dev      → Running
✅ rabbitmq4sedema-dev      → Running
✅ memcached4sedema-dev     → Healthy
✅ frontendadmin4sedema-dev → Running
✅ frontendapp4sedema-dev   → Running
✅ nginx4sedema-dev         → Running
✅ Keycloak: realm sedema + 3 clientes importados
✅ Django fixture socialaccount → cargado
✅ https://sedema-dev.geosuitemp.centrogeo.org.mx → accesible
```

---

---

## 6. Segundo deploy automatizado — `idegeo-dev` (20 de julio de 2026)

Se disparó un nuevo deploy de `idegeo-dev` para validar que todos los fixes funcionan
end-to-end sin intervención manual.

### 6.1 Fix: SECRET_KEY con chars problemáticos (recurrente)

En el primer intento del fresh install de `idegeo-dev`, el `.env.idegeo-dev` generado tenía
un `SECRET_KEY` con `&`, `{`, `}`, `*`, `[`, `]` que rompían el parser de `docker compose --env-file`:

```
failed to read .env.idegeo-dev: line 204: unexpected character "&" in variable name
```

Nuestro fix anterior (eliminar `#`, `\`, `$`, `=` de `_strong_chars`) no era suficiente.

**Fix definitivo:** Usar `secrets.token_urlsafe(50)` para generar el SECRET_KEY — produce
solo letras, dígitos, `-` y `_`, completamente seguros en archivos `.env`:

```python
_vals_to_replace["secret_key"] = _jsfile.get(
    "secret_key", args.secret_key
) or secrets.token_urlsafe(50)
```

`secrets` ya estaba importado en el módulo (se usaba para `ia_django_secret_key`).

Commit: `fix: usar token_urlsafe para SECRET_KEY y ampliar timeout de Keycloak a 30 min`

---

### 6.2 Fix: timeout de Keycloak insuficiente en fresh install

**Síntoma:** En el fresh install, Keycloak tardó ~10 minutos solo en la fase de augmentation
de Quarkus (`547236ms`). El script esperaba 60 intentos × 15s = **15 minutos**, lo cual
no era suficiente margen.

**Fix:** Aumentar el loop de espera de Keycloak a 120 intentos (30 minutos):

```bash
for i in $(seq 1 120); do
```

En reinstalls, Keycloak arranca en ~2 minutos porque la augmentation ya está cacheada.
El timeout de 30 minutos solo importa en fresh installs.

---

### 6.3 Estado final — deploy automatizado exitoso

**Fecha:** 20 de julio de 2026
**Stack:** `idegeo-dev`
**Tipo:** Reinstall (validación de fixes)

```
✅ 12 contenedores levantados sin intervención manual
✅ Keycloak listo en ~2 minutos (reinstall con caché)
✅ Keycloak: realm idegeo + 3 clientes importados automáticamente
✅ Django fixture socialaccount cargado automáticamente
✅ celery y geoserver levantados en segunda pasada
✅ https://idegeo-dev.geosuitemp.centrogeo.org.mx accesible
```

| | Antes | Después |
|--|-------|---------|
| SECRET_KEY | Chars problemáticos rompen `.env` | `token_urlsafe` — siempre seguro |
| Keycloak timeout | 15 min (insuficiente en fresh install) | 30 min |
| Intervención manual | Necesaria | No requerida |

---

## 7. Pendientes actuales

| Ítem | Descripción | Prioridad |
|------|-------------|-----------|
| CI/CD auto en develop | Trigger automático en push a `develop` para despliegue a ambiente dev | Baja |
| `conafor-dev` | Verificar fresh install con todos los fixes aplicados | Baja |
