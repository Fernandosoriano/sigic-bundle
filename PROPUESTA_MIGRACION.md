# Propuesta de Migración: sigic-bundle como Generador Multi-Plataforma

## Contexto

sigic-bundle actualmente orquesta múltiples submódulos (frontend, geonode, ia-engine, etc.)
a través de Docker Compose, y de momento pensamos usarlo como base para tres plataformas: idegeo, 
sedema y conafor. Cada plataforma tendrá sus propios ambientes: desarrollo (dev), pruebas (qa) y
producción (prd).

Nuestra infraestructura actual consiste en tres servidores:

| Servidor | Uso actual |
|---|---|
| test-sigic | Ambiente dev/qa para todas las plataformas |
| idegeo | Producción de la plataforma idegeo |
| sedema | Producción de la plataforma sedema |

---

## Problemas Identificados

**1. Escalabilidad de submódulos**
Cuando una plataforma necesita personalizar un submódulo (por ejemplo, la landing page del
frontend), es necesario hacer un fork de ese submódulo, modificar el build del bundle y
configurar su propio CI/CD. Con más de 10 plataformas esto se vuelve inmanejable.

**2. Despliegue manual**
Una vez que algo está listo en test-sigic, el código se copia manualmente al servidor de la
plataforma correspondiente. No existe un pipeline real: es lento, propenso a errores y no
deja trazabilidad.
<!-- 
**3. Un servidor por plataforma**
El modelo actual implica un servidor dedicado por plataforma en producción. Escalar a 10 o
más plataformas significaría mantener 10 o más servidores, lo cual es costoso e ineficiente.

--- -->

## Objetivo

Convertir sigic-bundle en el **repositorio generador multi-plataforma**: un solo repositorio
que, únicamente a través de configuración, pueda construir y desplegar cualquier plataforma
en cualquier ambiente — sin forks por plataforma y sin copiar código manualmente.

---

## Principio Fundamental: Agregar, No Reemplazar

La propuesta es **completamente aditiva**. Todo lo que existe hoy en sigic-bundle permanece
intacto:

| Archivo / Directorio | Estado |
|---|---|
| `sigic-mixins/` | Sin cambios |
| `create-envfile.py` | Se extiende, no se reemplaza |
| `sigic_install.sh` | Se extiende, no se reemplaza |
| `docker-compose.yml` | Adiciones menores |
| `.env.sample` | Sin cambios |
| Todos los submódulos | Sin forks |

Lo que se **agrega**:

- `platforms/` — directorio con configuración por plataforma
- `.github/workflows/` — pipeline de CI/CD

---

## Arquitectura de Configuración

### Nueva estructura del repositorio

```
sigic-bundle/
├── platforms/                          ← NUEVO
│   ├── idegeo/
│   │   ├── platform.json
│   │   ├── env/
│   │   │   ├── dev.env
│   │   │   ├── qa.env
│   │   │   └── prd.env
│   │   └── overrides/
│   │       └── frontend/
│   │           ├── pages/index.vue
│   │           └── assets/
│   ├── sedema/
│   │   └── ...
│   └── conafor/
│       └── ...
├── .github/                            ← NUEVO
│   └── workflows/
│       ├── validate.yml
│       └── deploy.yml
├── sigic-mixins/                       ← sin cambios
├── create-envfile.py                   ← se extiende
├── sigic_install.sh                    ← se extiende
├── docker-compose.yml                  ← adiciones menores
└── ...                                 ← todo lo demás sin cambios
```

### platform.json — cada plataforma extiende un flavor existente

En lugar de duplicar configuración, cada plataforma declara qué flavor base usa y solo
sobreescribe lo que es diferente:

```json
{
  "platform": "idegeo",
  "extends": "geonode-frontend-keycloak",
  "overrides": {
    "hostname": "idegeo.example.mx",
    "email": "admin@idegeo.example.mx",
    "oidc_provider_url": "https://idegeo.example.mx/iam/realms/sigic"
  }
}
```

### env files — solo el delta

Los archivos de ambiente contienen únicamente las variables que difieren del flavor base.
No se repiten las 340+ variables de `.env.sample`.

```ini
# platforms/idegeo/env/prd.env
SITEURL=https://idegeo.example.mx/
DJANGO_ALLOWED_HOSTS=idegeo.example.mx
```

### Capas de configuración

La generación del `.env` final sigue este orden de prioridad:

```
.env.sample  (plantilla completa con todos los campos)
    ↓ se aplica el flavor base desde sigic-mixins/
    ↓ se aplican los overrides de platform.json
    ↓ se aplica el env file del ambiente (dev / qa / prd)
    → se escribe .env
```

### Personalización de submódulos sin forks

Para el caso del frontend (landing pages personalizadas por plataforma), en lugar de hacer
un fork del submódulo, se usa una capa de overlays:

- Los archivos personalizados viven en `platforms/<nombre>/overrides/frontend/`
- Docker BuildKit los inyecta sobre el código del submódulo al momento del build
- El submódulo en sí no se toca; todos los cambios viven en el bundle

Esto requiere una sola línea adicional en el `Dockerfile` del frontend, que se propondría
como PR al repositorio upstream. Si en el futuro otra plataforma necesita personalizar
otro submódulo (geonode, ia-engine, etc.), se aplica el mismo mecanismo — solo cuando
la necesidad exista, no de forma preventiva.

---

## Modelo de Servidores

### Problema con el modelo actual

Un servidor por plataforma en producción no escala: 10 plataformas implicarían 10 servidores
independientes con toda la carga operativa que eso conlleva.

### Solución: proxy reverso a nivel de host

Se agrega un proxy reverso (Traefik o Nginx) en el servidor host que escucha en los puertos
80/443 y enruta el tráfico por nombre de dominio al stack de Docker Compose correcto:

```
Internet
    ↓
Proxy reverso del host  (puerto 80 / 443)
    ├── idegeo.example.mx  → stack idegeo  (puerto interno 8001)
    ├── sedema.example.mx  → stack sedema  (puerto interno 8002)
    └── conafor.example.mx → stack conafor (puerto interno 8003)
```

Cada stack de Docker Compose deja de ocupar el puerto 80/443 directamente y expone un
puerto interno único, configurado por variable de ambiente. El proxy del host maneja TLS
y el enrutamiento por dominio.

### Modelo de servidores resultante

| Servidor | Rol | Plataformas |
|---|---|---|
| test-sigic | dev + qa | todas las plataformas |
| servidor-prd | prd | todas las plataformas |

Solo dos servidores para comenzar, usando la infraestructura que ya existe. Cuando la
capacidad del servidor de producción se sature, se agrega otro servidor y se migran algunas
plataformas — sin cambios en la arquitectura del bundle.

---

## CI/CD

### Estrategia de ramas

```
main      → despliegue a prd  (requiere aprobación manual)
staging   → despliegue a qa   (automático al hacer push)
develop   → despliegue a dev  (automático al hacer push)
```

### Flujo de despliegue

```
Push a develop
    ↓
GitHub Actions detecta qué plataformas cambiaron
    ↓
Construye imágenes Docker → las sube al registro (GHCR)
    ↓
SSH al servidor → docker compose pull && docker compose up -d
```

### Detección inteligente de cambios

- Si cambia `platforms/idegeo/` → solo se reconstruye idegeo
- Si cambia un submódulo (geonode, frontend, etc.) → se reconstruyen todas las plataformas
- También se puede lanzar un despliegue manual a una plataforma específica

### Secretos y configuración en GitHub

Cada servidor se registra como un secreto en GitHub por plataforma y ambiente:

```
IDEGEO_DEV_HOST, IDEGEO_PRD_HOST
SEDEMA_DEV_HOST, SEDEMA_PRD_HOST
DEPLOY_SSH_KEY  (clave SSH compartida del usuario deploy)
```

Las contraseñas y secrets específicos de cada plataforma se manejan como GitHub Environment
secrets y se inyectan al generar el `.env`.

---

## Plan de Migración

### Fase 1 — Consolidación de configuración (1-2 semanas, riesgo cero)

**Objetivo**: sigic-bundle como fuente única de verdad para la configuración de todas las
plataformas. Los servidores actuales siguen funcionando sin cambios durante esta fase.

- Crear el directorio `platforms/` con subdirectorios para cada plataforma
- Extraer las diferencias de configuración de cada servidor y volcarlas en `platform.json`
  y en los archivos `env/dev.env`, `env/qa.env`, `env/prd.env`
- Extender `create-envfile.py` para aceptar los flags `--platform` y `--environment`
- Verificar que el `.env` generado para cada plataforma coincide con el que hay actualmente
  en cada servidor

### Fase 2 — Eliminar forks del frontend (1-2 semanas, complejidad media)

**Objetivo**: cero forks de submódulos.

- Identificar los archivos personalizados en cada repo forkeado del frontend
- Moverlos a `platforms/<nombre>/overrides/frontend/`
- Agregar soporte de overlays al `Dockerfile` del frontend via Docker BuildKit
  (se propone como PR al repo upstream — es una sola línea)
- Construir y verificar que la landing page personalizada aparece correctamente
- Archivar los repos forkeados

### Fase 3 — Pipeline de CI/CD (2-3 semanas)

**Objetivo**: un push a `develop` dispara automáticamente el despliegue correcto.

- Configurar GitHub Actions con el workflow de despliegue
- Configurar GHCR como registro de imágenes Docker
- Configurar GitHub Environments (dev, qa, prd) con regla de aprobación en prd
- Agregar el proxy reverso a nivel de host en los servidores
- Probar el pipeline completo con una sola plataforma primero (idegeo/dev)
- Extender a todas las plataformas una vez validado

### Fase 4 — Descomisionar los forks del bundle (semana 4+)

**Objetivo**: todos los servidores usan el repo canónico de sigic-bundle.

- Reemplazar el fork local de sigic-bundle en cada servidor por un clone del repo principal
- Verificar que CI/CD despliega correctamente en todos los casos
- Archivar los forks antiguos del bundle

### Agregar una nueva plataforma (después de la fase 4)

El proceso completo para incorporar una nueva plataforma toma aproximadamente 30 minutos:

```bash
mkdir -p platforms/nueva-plataforma/overrides
mkdir -p platforms/nueva-plataforma/env
# crear platform.json con el flavor base y los overrides propios
# crear dev.env, qa.env, prd.env con el delta de variables
# agregar el secreto del servidor en GitHub
# abrir PR a develop — listo
```

Sin forks, sin repos separados, sin modificaciones al código base del bundle.

---

## Resumen

| Problema | Solución |
|---|---|
| Forks de submódulos por plataforma | Overlays de archivos vía Docker BuildKit |
| Configuración manual por servidor | `platform.json` + `env/*.env` en capas |
| Despliegue manual sin trazabilidad | GitHub Actions + SSH al servidor |
| Un servidor por plataforma | Proxy reverso a nivel de host, N plataformas por servidor |
| Escala a 10+ plataformas | Agregar `platforms/<nombre>/` es suficiente, sin tocar el bundle |
