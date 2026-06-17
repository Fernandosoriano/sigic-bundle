# Autenticación OIDC — Documentación de referencia

Este documento describe el funcionamiento de la autenticación OIDC en SIGIC Bundle, cómo se configura automáticamente al instalar una plataforma, y las soluciones a los problemas encontrados durante la implementación.

---

## Arquitectura de autenticación

```
Usuario (browser)
    │ HTTPS
    ▼
Apache CentroGeo (10.2.7.26, puerto 443)  ← SSL termination
    │ HTTP interno  +  X-Forwarded-Proto: https
    ▼
nginx-proxy (Docker, puerto 80)  ← reverse proxy por hostname
    │ HTTP  +  X-Forwarded-Proto: https  (propagado)
    ▼
nginx4<plataforma>-<ambiente>  ← enruta por path
    │ HTTP  +  X-Forwarded-Proto: https  (propagado)
    ▼
frontendadmin4<plataforma>-<ambiente>  (Nuxt, puerto 3000)
    │
    │ (login) redirect a Keycloak
    ▼
keycloak4<plataforma>-<ambiente>  (/iam/realms/<plataforma>)
    │ JWT
    ▼
django4<plataforma>-<ambiente>  (GeoNode, valida JWT vía OIDC)
```

El Apache de CentroGeo termina el SSL y reenvía por HTTP plano a nuestro servidor. El header `X-Forwarded-Proto: https` viaja por toda la cadena para que cada servicio sepa que el protocolo original del usuario fue HTTPS.

---

## Cómo funciona el login (flujo completo)

1. El usuario accede a `https://<hostname>/admin/`
2. El frontend Nuxt detecta que no hay sesión y redirige a `/admin/api/auth/signin`
3. `NuxtAuthHandler` construye la URL de callback usando el header `X-Forwarded-Proto` recibido → `https://<hostname>/admin/api/auth/callback/keycloak`
4. El browser redirige al login de Keycloak (`/iam/realms/<plataforma>/protocol/openid-connect/auth`)
5. El usuario introduce sus credenciales en Keycloak
6. Keycloak valida que el `redirect_uri` del callback coincide con lo configurado en el cliente OIDC
7. Keycloak emite un JWT y redirige de vuelta al frontend
8. El frontend almacena la sesión y el usuario queda autenticado

---

## Configuración automática al instalar

Al correr `./sigic_install.sh <plataforma> <ambiente>`, el script:

1. **Genera `.env`** con `create-envfile.py` — incluye `SOCIALACCOUNT_OIDC_*` para GeoNode y las URLs del frontend Nuxt
2. **Genera los JSONs de clientes Keycloak** con `create-keycloak-jsons.py` — produce `keycloak-client-sigic-admin.json`, `keycloak-client-sigic-app.json` y `keycloak-client-sigic-geonode.json` con las `redirectUris` correctas para el hostname del ambiente
3. **Levanta los contenedores** incluido `keycloak4<plataforma>-<ambiente>`
4. **Espera a Keycloak** — poll en el puerto 8080 cada 15 segundos, hasta 15 minutos (cold start puede tardar 6–10 min)
5. **Importa realm y clientes** vía `kcadm.sh` dentro del contenedor Keycloak
6. **Carga el fixture de GeoNode** — registra el proveedor OIDC en Django

Todo esto ocurre sin intervención manual.

---

## Realm dinámico por plataforma

Cada plataforma tiene su propio realm de Keycloak con el nombre de la plataforma. El nombre se extrae automáticamente desde `KEYCLOAK_ISSUER`:

```bash
# En scripts/import-keycloak-clients.sh
REALM=$(echo "${KEYCLOAK_ISSUER:-}" | sed 's|.*/realms/||' | sed 's|/.*||')
REALM=${REALM:-sigic}
```

**Ejemplos:**

| `KEYCLOAK_ISSUER` | Realm resultante |
|-------------------|-----------------|
| `https://idegeo-qa.../iam/realms/idegeo` | `idegeo` |
| `https://sedema-qa.../iam/realms/sedema` | `sedema` |
| (no definido) | `sigic` (fallback para modo clásico) |

El `KEYCLOAK_ISSUER` se pasa explícitamente al contenedor desde `sigic_install.sh`:

```bash
docker exec -e KEYCLOAK_ISSUER="$OIDC_URL" keycloak4${COMPOSE_PROJECT_NAME} \
  bash -c "/scripts/import-keycloak-clients.sh"
```

Esto es necesario porque el contenedor Keycloak no tiene esa variable en su propio env.

---

## UPSERT de realm y clientes

El script `import-keycloak-clients.sh` es idempotente — puede correrse múltiples veces sin errores.

### Realm

```bash
if kcadm.sh get realms/$REALM > /dev/null 2>&1; then
  # Realm existe: actualizar configuración
  kcadm.sh update realms/$REALM -f keycloak-realm-sigic.json -s realm=$REALM -s displayName=$REALM
else
  # Realm nuevo: crear mínimo primero (Keycloak asigna su propio UUID)
  kcadm.sh create realms -s realm=$REALM -s enabled=true
  # Luego aplicar configuración completa
  kcadm.sh update realms/$REALM -f keycloak-realm-sigic.json -s realm=$REALM -s displayName=$REALM
fi
```

> **Por qué dos pasos en el CREATE:** el archivo `keycloak-realm-sigic.json` tiene un UUID hardcodeado (`"id": "2011f0f4-..."`). Si se pasa directamente al crear, Keycloak falla con "Duplicate resource error" cuando ese UUID ya existe de un intento anterior. Creando primero el realm mínimo (sin JSON), Keycloak asigna su propio UUID libre de conflictos.

### Clientes

```bash
CID=$(kcadm.sh get clients -r $REALM -q clientId=$CLIENT_ID --fields id --format csv | tail -n 1 | tr -d '"')
if [ -z "$CID" ]; then
  kcadm.sh create clients -r $REALM -f $PATH_JSON
else
  kcadm.sh update clients/$CID -r $REALM -f $PATH_JSON
fi
```

---

## Archivos clave

| Archivo | Función |
|---------|---------|
| `scripts/import-keycloak-clients.sh` | Crea o actualiza realm y clientes en Keycloak vía `kcadm.sh` |
| `overrides/keycloak/keycloak-realm-sigic.json` | Template del realm (políticas de contraseñas, tokens, etc.) |
| `overrides/keycloak/keycloak-client-*.json.template` | Templates de clientes OIDC |
| `create-keycloak-jsons.py` | Genera los JSONs de clientes con URLs correctas desde `.env` |
| `create-socialaccount-fixture.py` | Genera el fixture que registra el proveedor OIDC en Django |
| `overrides/nginx/z-frontend-admin.conf` | Config nginx del frontend admin (incluye `X-Forwarded-Proto`) |
| `overrides/nginx/z-frontend-app.conf` | Config nginx del frontend público (incluye `X-Forwarded-Proto`) |
| `platforms/<plataforma>/env/<ambiente>.env` | Define `oidc_provider_url` y `https_mode` por ambiente |

---

## Variables de entorno relevantes

| Variable | Dónde se usa | Para qué |
|----------|-------------|---------|
| `KEYCLOAK_ISSUER` | Nuxt frontend, Django | URL base del realm OIDC |
| `SOCIALACCOUNT_OIDC_*` | Django / GeoNode | Configuración del proveedor OIDC en GeoNode |
| `NUXT_PUBLIC_KEYCLOAK_URL` | Nuxt frontend | URL de Keycloak visible desde el browser |
| `oidc_provider_url` | `platforms/*/env/*.env` | Fuente de verdad para el ambiente; determina nombre del realm |

---

## Problemas encontrados y soluciones

### 1. Error `OAuthSignin` en el frontend

**Síntoma:** Login redirige a `/admin/api/auth/signin?error=OAuthSignin`.

**Causa:** nginx interno usaba `proxy_set_header X-Forwarded-Proto $scheme`. La variable `$scheme` siempre vale `http` porque nginx recibe la conexión en HTTP plano desde nginx-proxy. `NuxtAuthHandler` (con `trustHost: true`) calculaba la URL de callback como `http://...` en lugar de `https://...`, y Keycloak rechazaba el `redirect_uri`.

**Solución** en `overrides/nginx/z-frontend-admin.conf` y `z-frontend-app.conf`:
```nginx
# Antes (incorrecto en cadena de proxies):
proxy_set_header X-Forwarded-Proto $scheme;

# Después (propaga el protocolo original del usuario):
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
```

---

### 2. Realm creado con nombre incorrecto

**Síntoma:** `[OPError: Realm does not exist]` — el realm `idegeo` (o `sedema`) no existe en Keycloak.

**Causa:** El script usaba el nombre hardcodeado `sigic` del archivo `keycloak-realm-sigic.json`, sin importar para qué plataforma se instalaba.

**Solución:** Extraer el nombre del realm dinámicamente desde `KEYCLOAK_ISSUER` (ver sección *Realm dinámico por plataforma*).

---

### 3. "Duplicate resource error" al crear realm

**Síntoma:** `kcadm.sh create realms -f keycloak-realm-sigic.json` falla con "Duplicate resource error".

**Causa:** El JSON de template tiene un UUID hardcodeado. En reinstalaciones o intentos parciales previos, ese UUID ya existe en la base de datos.

**Solución:** Separar el CREATE en dos pasos (ver sección *UPSERT de realm y clientes*).

---

### 4. Timeout en cold start de Keycloak

**Síntoma:** El import falla con "Connection refused" — Keycloak no terminó de iniciar.

**Causa:** El script tenía `sleep 30` fijo; Keycloak puede tardar 6–10 minutos en cold start.

**Solución:** Loop de polling que prueba el puerto 8080 cada 15 segundos, hasta 15 minutos:

```bash
for i in $(seq 1 60); do
  if docker exec "$KEYCLOAK_CONTAINER" bash -c "exec 3<>/dev/tcp/localhost/8080" 2>/dev/null; then
    echo "Keycloak listo"
    break
  fi
  echo "  intento $i/60..."
  sleep 15
done
```

---

## Plataformas actualmente configuradas

| Plataforma | Ambiente | Hostname | Realm Keycloak |
|------------|----------|----------|----------------|
| idegeo | qa | idegeo-qa.geosuitemp.centrogeo.org.mx | idegeo |
| sedema | dev | sedema-dev.geosuitemp.centrogeo.org.mx | sedema |
| sedema | qa | sedema-qa.geosuitemp.centrogeo.org.mx | sedema |
| conafor | dev | conafor-dev.geosuitemp.centrogeo.org.mx | conafor |

---

## Agregar una nueva plataforma con OIDC

1. Crear `platforms/<plataforma>/env/<ambiente>.env` con:
```ini
hostname=<hostname>
env_type=<dev|test|prod>
oidc_provider_url=https://<hostname>/iam/realms/<plataforma>
https_mode=externalhttps
```

2. Asegurarse de que `platform.json` incluye `"useoidc": true` en overrides.

3. Correr:
```bash
./sigic_install.sh <plataforma> <ambiente>
```

El script genera los JSONs de Keycloak, levanta los contenedores, espera el cold start y configura el realm y clientes automáticamente.
