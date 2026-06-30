# Operación Multi-Plataforma — Guía Técnica

Este documento describe el estado actual del modo multi-plataforma de `sigic-bundle`: cómo instalar, borrar y agregar nuevas plataformas, junto con la arquitectura de componentes clave.

---

## Arquitectura general

```
Internet
  └── Apache (CentroGeo, 10.2.7.26 — termina SSL)
        └── Servidor Nimbus (10.2.102.228)
              └── nginx-proxy (contenedor, puerto 80)
                    ├── nginx4idegeo-qa
                    ├── nginx4sedema-qa
                    └── nginx4conafor-qa
                          └── Servicios del stack (django, keycloak, frontends, etc.)
```

Cada plataforma corre como un stack Docker Compose aislado con `COMPOSE_PROJECT_NAME=<plataforma>-<ambiente>`. Los contenedores no exponen puertos al host — el nginx-proxy los alcanza por nombre de contenedor dentro de la red `sigic-proxy`.

---

## Estructura de archivos por plataforma

```
platforms/
├── <plataforma>/
│   ├── platform.json                    # identidad y overrides de servicios
│   ├── env/
│   │   ├── dev.env
│   │   ├── qa.env
│   │   └── prd.env
│   └── overrides/
│       └── frontend/
│           └── pages/
│               └── index.vue            # overlay de landing page
```

### `platform.json`

```json
{
  "platform": "conafor",
  "description": "CONAFOR - Comisión Nacional Forestal",
  "extends": "geonode-frontend-keycloak",
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

### `env/qa.env`

```ini
hostname=conafor-qa.geosuitemp.centrogeo.org.mx
env_type=test
oidc_provider_url=https://conafor-qa.geosuitemp.centrogeo.org.mx/iam/realms/conafor
https_mode=externalhttps
```

> **Importante:** para ambientes qa y prd usar siempre `https_mode=externalhttps` y `oidc_provider_url` con `https://`.

---

## Instalar una plataforma

```bash
./sigic_install.sh <plataforma> <ambiente>
```

**Ejemplo:**
```bash
./sigic_install.sh conafor qa
```

El script realiza automáticamente:

1. Genera `.env` a partir de `platform.json` + `env/<ambiente>.env`
2. Genera los JSONs de Keycloak con las URLs correctas de la plataforma en `overrides/keycloak/<plataforma>-<ambiente>/`
3. Copia `keycloak-realm-sigic.json` al subdirectorio de la plataforma
4. Levanta todos los servicios del stack con Docker Compose
5. Genera la configuración del nginx-proxy para el hostname de la plataforma
6. Espera a que Keycloak inicialice el master realm (hasta 15 min)
7. Importa el realm y los 3 clientes OIDC en Keycloak (`sigic-admin`, `sigic-app`, `sigic-geonode`)
8. Espera a que Django esté healthy (migraciones pueden tardar varios minutos en install fresh)
9. Carga el fixture de socialaccount (configuración OIDC de GeoNode)

Al finalizar imprime las contraseñas generadas para GeoServer y el admin de GeoNode.

### Tiempo estimado

| Condición | Tiempo aproximado |
|-----------|------------------|
| Install fresh (sin caché Docker) | 45–60 minutos |
| Reinstall (con caché de imágenes) | 10–20 minutos |

El mayor consumo de tiempo es el build de las imágenes de frontend (Node.js + Nuxt). En reinstalls Docker reutiliza las capas cacheadas.

---

## Borrar una plataforma

```bash
./sigic_delete.sh <plataforma> <ambiente>
```

**Ejemplo:**
```bash
./sigic_delete.sh sedema qa
```

El script elimina automáticamente:

- Todos los contenedores del stack
- Todos los volúmenes (base de datos, GeoServer, etc.)
- La red Docker del proyecto
- La configuración del nginx-proxy (`proxy/conf.d/<plataforma>-<ambiente>.conf`)
- El env file (`.env.<plataforma>-<ambiente>`)
- Los JSONs de Keycloak del subdirectorio de la plataforma (`overrides/keycloak/<plataforma>-<ambiente>/`)

> **Advertencia:** la eliminación incluye volúmenes. Todos los datos de la plataforma se pierden de forma permanente.

---

## Agregar una nueva plataforma

1. Crear la estructura de directorios:
```bash
mkdir -p platforms/<nueva>/env
mkdir -p platforms/<nueva>/overrides/frontend/pages
```

2. Crear `platforms/<nueva>/platform.json` (copiar de conafor o sedema y ajustar descripción)

3. Crear `platforms/<nueva>/env/qa.env` con el hostname, realm y `https_mode=externalhttps`

4. Crear `platforms/<nueva>/overrides/frontend/pages/index.vue` con el overlay de landing page:
```vue
<template>
  <div style="display:flex;align-items:center;justify-content:center;height:100vh;font-size:2rem;">
    Hola mundo desde <nueva>
  </div>
</template>

<script setup>
definePageMeta({ auth: false });
</script>
```

5. Ejecutar el install:
```bash
./sigic_install.sh <nueva> qa
```

---

## Plataformas actualmente operativas

| Plataforma | Ambiente | URL |
|------------|----------|-----|
| idegeo | qa | https://idegeo-qa.geosuitemp.centrogeo.org.mx |
| sedema | qa | https://sedema-qa.geosuitemp.centrogeo.org.mx |
| conafor | qa | https://conafor-qa.geosuitemp.centrogeo.org.mx |

---

## Componentes clave

### nginx-proxy

Contenedor nginx compartido entre todas las plataformas. Lee los archivos en `proxy/conf.d/` para enrutar por hostname. El install genera el conf de cada plataforma automáticamente y recarga nginx-proxy al finalizar.

### Keycloak por plataforma

Cada plataforma tiene su propio realm en Keycloak (nombrado igual que la plataforma: `idegeo`, `sedema`, `conafor`). Los 3 clientes OIDC (`sigic-admin`, `sigic-app`, `sigic-geonode`) se crean con las redirect URIs correctas del hostname de la plataforma.

Los JSONs de Keycloak se generan en `overrides/keycloak/<plataforma>-<ambiente>/` y se montan dentro del contenedor vía `KEYCLOAK_IMPORT_SUBDIR`.

### Overlays de frontend

El Dockerfile del frontend copia `platforms/<plataforma>/overrides/frontend/` sobre el código base del submodulo al momento del build. Permite personalizar páginas, assets y componentes sin modificar el submodulo ni crear forks.

### `NUXT_AUTH_ORIGIN`

Esta variable debe estar vacía (no definida). Si se le asigna la URL externa de la plataforma, Nuxt detecta same-origin en las llamadas a `/session` durante SSR y entra en recursión, causando OOM. El install no la genera — el valor vacío es el comportamiento correcto.

---

## Notas operativas

- El DNS wildcard `*.geosuitemp.centrogeo.org.mx` cubre automáticamente cualquier nuevo subdominio de plataforma en el servidor de dev/qa.
- Las contraseñas de base de datos se preservan en reinstalls para no romper volúmenes existentes.
- Si un install falla y se vuelve a correr, el script detecta el env file existente y preserva las contraseñas.
