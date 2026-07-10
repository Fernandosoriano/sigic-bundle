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

## 4. Pendientes

| Ítem | Descripción | Prioridad |
|------|-------------|-----------|
| `geodata_conn.py` | Mover conexión psycopg2 fuera del nivel de módulo en `sigic-geonode-wrapper` | Media |
| `vitest` ERESOLVE | `vitest@^4.0.17` incompatible con `@nuxt/test-utils@3.23.0` en `sigic-nuxt-frontend` — bloquea fresh installs del frontend | Media |
| CI/CD auto en develop | Trigger automático en push a `develop` para despliegue a ambiente dev | Baja |
| Fresh install test | Probar CI/CD con stack limpio (sin volúmenes existentes) | Baja |
