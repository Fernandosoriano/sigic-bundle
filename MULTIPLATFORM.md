# Generador Multi-Plataforma — Documentación

Este documento describe los cambios realizados al repositorio `sigic-bundle` para convertirlo en un generador multi-plataforma. Los cambios son **completamente aditivos** — el funcionamiento original del bundle no fue modificado.

---

## Contexto

El `sigic-bundle` original permite desplegar una única instancia de la plataforma GeoNode. El objetivo de esta adaptación es permitir desplegar múltiples plataformas (IDEGEO, CONAFOR, SEDEMA, y futuras) en distintos ambientes (dev, qa, prd) con un solo comando, sin duplicar código ni repositorios.

---

## Alcance por fase

| Capacidad | Mecanismo | Estado |
|-----------|-----------|--------|
| Un solo bundle para todas las plataformas (sin forks del bundle) | `platforms/` + `sigic_install.sh` | ✅ Fase 1 |
| Despliegue con un solo comando por plataforma+ambiente | `./sigic_install.sh <plataforma> <ambiente>` | ✅ Fase 1 |
| Aislamiento de contenedores y puertos por plataforma | `COMPOSE_PROJECT_NAME` + puertos en `platform.json` | ✅ Fase 1 |
| Configuración distinta por plataforma (hostname, OIDC, flags) | `platform.json` + `env/*.env` | ✅ Fase 1 |
| Archivos específicos por plataforma sin forks (landing pages, templates, assets, vistas) | `platforms/<nombre>/overrides/<submodulo>/` + Docker BuildKit | ⏳ Fase 2 |
| Lógica de negocio fundamentalmente distinta entre plataformas | Overlays cubre la mayoría; branches por submodulo solo si hay divergencia extrema | Por definir |

El mecanismo de overlays (Fase 2) permite que archivos específicos de cada plataforma — páginas Nuxt, templates Django, configuraciones de IA, assets — vivan en `platforms/<nombre>/overrides/` dentro del bundle. Docker BuildKit los inyecta sobre el código del submodulo al momento del build. **El submodulo no se toca y no hay forks.**

---

## Arquitectura general

```
Internet
  └── Apache (CentroGeo, puerto 443)
        └── 10.2.102.228 (servidor Nimbus)
              ├── Traefik (reverse proxy, pendiente)
              │     ├── Stack IDEGEO   (nginx: 8001, admin: 3001, app: 3002)
              │     ├── Stack CONAFOR  (nginx: 8002, admin: 3011, app: 3012)
              │     └── Stack SEDEMA   (nginx: 8003, admin: 3021, app: 3022)
              └── Servidor producción por plataforma (prd)
```

---

## Archivos nuevos

### `platforms/`

Directorio raíz de la configuración multi-plataforma. Contiene una subcarpeta por cada plataforma.

```
platforms/
├── idegeo/
│   ├── platform.json
│   └── env/
│       ├── dev.env
│       ├── qa.env
│       └── prd.env
├── conafor/
│   └── ...
└── sedema/
    └── ...
```

---

### `platforms/<plataforma>/platform.json`

Define la identidad de una plataforma: sobre qué flavor base se construye, qué servicios activa o desactiva, y en qué puertos corre.

**Ejemplo — `platforms/idegeo/platform.json`:**
```json
{
  "platform": "idegeo",
  "description": "IDEGEO - Infraestructura de Datos Espaciales de México",
  "extends": "geonode-frontend-keycloak",
  "ports": {
    "nginx": 8001,
    "frontend_admin": 3001,
    "frontend_app": 3002
  },
  "overrides": {
    "homepath": "app",
    "useoidc": true,
    "usefrontendadmin": true,
    "usefrontendapp": true,
    "enableiaproxy": false,
    "enableiadb": false,
    "enablelevantamientoproxy": false,
    "enablelevantamientodb": false
  }
}
```

| Campo | Para qué sirve |
|-------|---------------|
| `extends` | Flavor base de `sigic-mixins/` del que hereda la topología |
| `ports` | Puertos que expone esta plataforma en el servidor (evita conflictos entre plataformas) |
| `overrides` | Flags que sobreescriben al flavor base (qué servicios activar o desactivar) |

---

### `platforms/<plataforma>/env/<ambiente>.env`

Contiene solo los valores que cambian por ambiente: hostname, tipo de ambiente, URL del proveedor OIDC y modo HTTPS. Nada más.

**Ejemplo — `platforms/idegeo/env/dev.env`:**
```ini
hostname=geosuite-dev.centrogeo.org.mx
env_type=dev
oidc_provider_url=http://geosuite-dev.centrogeo.org.mx/iam/realms/idegeo
https_mode=http
```

**Ejemplo — `platforms/idegeo/env/prd.env`:**
```ini
hostname=idegeo.centrogeo.org.mx
env_type=prod
oidc_provider_url=https://idegeo.centrogeo.org.mx/iam/realms/idegeo
https_mode=externalhttps
```

| Variable | Para qué sirve |
|----------|---------------|
| `hostname` | URL base de la plataforma en ese ambiente |
| `env_type` | Tipo de ambiente (dev / test / prod) |
| `oidc_provider_url` | URL del realm Keycloak específico de la plataforma |
| `https_mode` | Modo de HTTPS (http / https / externalhttps) |

---

### `apache-vhosts-multiplatform.conf`

Archivo de configuración Apache con los tres bloques VirtualHost para IDEGEO, CONAFOR y SEDEMA. Generado para compartir con el equipo de infraestructura de CentroGeo.

**No se instala en el bundle.** Es un archivo de referencia para el equipo de infraestructura que configura el servidor Apache externo.

| Plataforma | nginx | admin | app |
|------------|-------|-------|-----|
| idegeo | 8001 | 3001 | 3002 |
| conafor | 8002 | 3011 | 3012 |
| sedema | 8003 | 3021 | 3022 |

---

## Archivos modificados

### `sigic_install.sh`

**Cambio:** Se agregó detección automática del modo de instalación al inicio del script.

**Comportamiento nuevo:**
```bash
# modo plataforma (nuevo)
./sigic_install.sh idegeo dev
./sigic_install.sh conafor prd

# modo clásico (sin cambios)
./sigic_install.sh geonode-frontend-keycloak externalhttps
```

Si el primer argumento coincide con una carpeta en `platforms/`, el script entra en modo plataforma y lee los archivos correspondientes. Si no, cae al comportamiento original sin ningún cambio.

**En modo plataforma el script:**
1. Lee `platform.json` → obtiene flavor base, overrides y puertos
2. Lee `env/<ambiente>.env` → obtiene hostname, env_type, oidc_provider_url, https_mode
3. Exporta `COMPOSE_PROJECT_NAME=$PLATFORM` → namespacing de contenedores por plataforma
4. Exporta `HTTP_PORT`, `FRONTEND_ADMIN_PORT`, `FRONTEND_APP_PORT` → puertos correctos por plataforma
5. Llama a `create-envfile.py` con los valores combinados
6. Ejecuta `docker compose up -d` con los perfiles del flavor base

---

### `docker-compose.yml`

**Cambio mínimo:** Los puertos de los contenedores frontend ahora son configurables via variable de entorno. Si no se define la variable, el valor por defecto es el mismo que antes — sin impacto en el uso original.

```yaml
# antes
- "3001:3000"
- "3002:3000"

# después (backward compatible)
- "${FRONTEND_ADMIN_PORT:-3001}:3000"
- "${FRONTEND_APP_PORT:-3002}:3000"
```

---

### `.gitignore`

**Dos correcciones:**

1. Los archivos `platforms/*/env/*.env` estaban bloqueados por la regla `env/` del gitignore original. Se agregó una excepción para que sean rastreados por git:
```
!platforms/*/env/
!platforms/*/env/*.env
```

2. Los JSONs generados de Keycloak (`overrides/keycloak/keycloak-client-*.json`) contenían secretos autogenerados y no estaban ignorados. Se agregaron al gitignore:
```
overrides/keycloak/keycloak-client-*.json
```

---

## Puertos asignados por plataforma

| Plataforma | nginx (host) | frontend admin | frontend app |
|------------|-------------|----------------|--------------|
| idegeo | 8001 | 3001 | 3002 |
| conafor | 8002 | 3011 | 3012 |
| sedema | 8003 | 3021 | 3022 |

---

## Uso

```bash
# desplegar idegeo en desarrollo
./sigic_install.sh idegeo dev

# desplegar conafor en QA
./sigic_install.sh conafor qa

# desplegar sedema en producción
./sigic_install.sh sedema prd

# uso original (sin cambios)
./sigic_install.sh geonode-frontend-keycloak externalhttps
```

---

## Estado actual

### Fase 1 — Orquestación y configuración

| Tarea | Estado |
|-------|--------|
| Estructura `platforms/` | Completado |
| `sigic_install.sh` extendido | Completado |
| `.gitignore` corregido | Completado |
| Puertos por plataforma en `platform.json` | Completado |
| Puertos configurables en `docker-compose.yml` | Completado |
| Prueba end-to-end idegeo dev en servidor | Completado |
| Nginx de sistema como reverse proxy en 10.2.102.228 | Pendiente |
| Redeployar idegeo en modo plataforma (puerto 8001) | Pendiente |
| Configuración Apache/DNS por infra CentroGeo (subdominos adicionales) | Pendiente |
| Prueba multi-plataforma simultánea (idegeo + conafor en mismo servidor) | Pendiente |
| Verificar el correcto funcionamiento de externalhttps en modo plataforma | Pendiente |
| Convertir `platforms/` en submodulo git | Pendiente |

### Fase 2 — Overlays de código por plataforma (sin forks de submodulos)

| Tarea | Estado |
|-------|--------|
| Crear estructura `platforms/<nombre>/overrides/<submodulo>/` | Pendiente |
| Agregar soporte de overlays vía Docker BuildKit al `Dockerfile` del frontend | Pendiente |
| Migrar archivos personalizados del frontend de repos forkeados a `overrides/frontend/` | Pendiente |
| Verificar que la landing page personalizada aparece correctamente | Pendiente |
| Extender el mecanismo de overlays a geonode e ia-engine si se necesita | Pendiente |
| Archivar repos forkeados de submódulos | Pendiente |


