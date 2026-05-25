# Arquitectura del Reverse Proxy — Documentación Técnica

Este documento explica el diseño, la motivación y el funcionamiento del reverse proxy
containerizado que forma parte de la arquitectura multi-plataforma de sigic-bundle.

---

## Contexto: por qué necesitamos un proxy propio

La arquitectura de red tiene tres capas:

```
Internet (HTTPS)
    → Mario / Apache CentroGeo  (10.2.7.26)
        SSL termination, wildcard *.geosuite-dev.centrogeo.org.mx
        ↓ HTTP interno
    → Nuestro servidor  (10.2.102.228)
        nginx-proxy (Docker)
        ↓ HTTP interno Docker
    → Stack de plataforma
        nginx4idegeo, nginx4conafor, nginx4sedema...
```

Mario reenvía todo el tráfico `*.geosuite-dev.centrogeo.org.mx` a nuestro servidor.
Nuestro proxy es quien distingue qué subdominio corresponde a qué plataforma y enruta
al stack correcto dentro de Docker.

Sin este proxy, todas las plataformas tendrían que competir por el puerto 80 del host,
lo que causa colisiones. Con el proxy, cada plataforma vive en su propia red Docker
interna y el proxy es el único punto de entrada.

---

## Por qué el proxy es un contenedor y no un servicio del sistema

La alternativa sería instalar nginx directamente en el sistema operativo del servidor
(como hizo Jaime en debian@10.2.102.238). El problema con esa aproximación es el
"snowflake server": configuraciones manuales en `/etc/nginx/`, paquetes instalados
a mano, estado que no está en git y que nadie sabe reproducir exactamente.

Con el proxy containerizado:
- Toda la configuración vive en el repositorio
- Reproducible en cualquier servidor con `docker compose up`
- Migrar a otro servidor es copiar el repo y correr un comando

---

## El problema del DNS interno (hairpin) y por qué se necesita extra_hosts

### El problema

Cuando los contenedores del stack (Django, Keycloak, Celery) necesitan comunicarse
entre sí usando el dominio público, ocurre lo siguiente:

```
Contenedor Django en 10.2.102.228
    → intenta llamar a http://idegeo.geosuite-dev.centrogeo.org.mx/iam/...
    → pregunta al DNS de Nimbus quién es ese dominio
    → Nimbus no conoce ese dominio (está detrás del proxy de Mario)
    → el router devuelve una IP incorrecta o no responde
    → la autenticación OIDC falla
```

Esto es exactamente lo que encontró Jaime cuando levantó sigic en su servidor
(ubuntu@10.2.102.71): Keycloak fallaba al autenticar porque el servidor no sabía
resolver su propio dominio público.

La solución de Jaime fue agregar en `/etc/hosts` del servidor del bundle una entrada
que apuntara el dominio a la IP del proxy de Mario (`10.2.7.26`), de modo que el
tráfico saliera del servidor, pasara por Mario y por el proxy de Jaime (con SSL)
y regresara al servidor. Funciona, pero es ineficiente: el tráfico sale y vuelve
por red externa.

### Nuestra solución: extra_hosts en docker-compose

Como nuestro proxy vive en el **mismo servidor** que el bundle, podemos resolver el
dominio directamente a `127.0.0.1` (localhost), sin salir del servidor:

```
Contenedor Django en 10.2.102.228
    → intenta llamar a http://idegeo.geosuite-dev.centrogeo.org.mx/iam/...
    → extra_hosts en docker-compose: ese dominio = host-gateway (IP del host)
    → llega a nginx-proxy en el mismo servidor (puerto 80)
    → nginx-proxy enruta a nginx4idegeo
    → la petición nunca sale del servidor
```

`host-gateway` es un valor especial de Docker que resuelve a la IP del host desde
dentro de un contenedor, sin necesidad de hardcodear IPs.

En `docker-compose.platform.yml` esto se configura así:

```yaml
services:
  django:
    extra_hosts:
      - "${PLATFORM_HOST}:host-gateway"
  celery:
    extra_hosts:
      - "${PLATFORM_HOST}:host-gateway"
  keycloak:
    extra_hosts:
      - "${PLATFORM_HOST}:host-gateway"
```

`PLATFORM_HOST` es la variable con el hostname de la plataforma (ej.
`idegeo.geosuite-dev.centrogeo.org.mx`), que `sigic_install.sh` escribe en `.env`
antes de levantar el stack.

---

## Por qué el proxy debe estar en la red sigic-proxy

Los contenedores nginx4idegeo, nginx4conafor, etc. no exponen puertos al host.
Solo son accesibles dentro de la red Docker `sigic-proxy`. El nginx-proxy también
está en esa red, por lo que puede llegar a ellos por nombre de contenedor:

```
proxy_pass http://nginx4idegeo;   ← Docker DNS resuelve el nombre
```

Sin la red compartida, el proxy no podría encontrar los contenedores de cada stack.

---

## Configuración del proxy: qué tomamos de Jaime

Jaime configuró su proxy (debian@10.2.102.238) con nginx instalado en el sistema
operativo. Sus archivos de configuración revelaron el patrón que nosotros replicamos:

### Buffer settings — críticos para Keycloak

Los tokens JWT de Keycloak son muy grandes. Sin estos parámetros, nginx devuelve
errores 502 o trunca las cabeceras de autenticación:

```nginx
proxy_buffer_size          128k;
proxy_buffers              4 256k;
proxy_busy_buffers_size    256k;
large_client_header_buffers 4 16k;
```

### X-Forwarded-Proto

Este header le dice a Django si la petición original vino por HTTP o HTTPS.
GeoNode lo usa para generar URLs correctas y para validar CSRF:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

En el bloque de puerto 80: `$scheme = http`
En el bloque de puerto 443: `$scheme = https`

### Estructura de config — lo que Jaime hizo y nosotros replicamos

```nginx
# puerto 80 — siempre presente
server {
    listen 80;
    server_name idegeo.geosuite-dev.centrogeo.org.mx;

    location ^~ /.well-known/acme-challenge/ {
        alias /var/www/acme-challenge/;
    }

    location / {
        proxy_pass http://nginx4idegeo;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffer_size          128k;
        proxy_buffers              4 256k;
        proxy_busy_buffers_size    256k;
        large_client_header_buffers 4 16k;
    }
}

# puerto 443 — solo si https_mode=externalhttps
server {
    listen 443 ssl;
    server_name idegeo.geosuite-dev.centrogeo.org.mx;

    ssl_certificate     /etc/letsencrypt/live/idegeo.geosuite-dev.centrogeo.org.mx/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/idegeo.geosuite-dev.centrogeo.org.mx/privkey.pem;

    location / {
        proxy_pass http://nginx4idegeo;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffer_size          128k;
        proxy_buffers              4 256k;
        proxy_busy_buffers_size    256k;
        large_client_header_buffers 4 16k;
    }
}
```

La diferencia respecto a Jaime: `proxy_pass` apunta al nombre del contenedor Docker
(`nginx4idegeo`) en lugar de a una IP fija (`http://10.2.102.71`).

---

## SSL y certbot: solo necesario con https_mode=externalhttps

### Por qué no se necesita en dev/qa (https_mode=http)

Con `https_mode=http` en el env file de la plataforma:
- `SITEURL=http://idegeo.geosuite-dev.centrogeo.org.mx/`
- `oidc_provider_url=http://...` — Keycloak usa HTTP
- Django no espera HTTPS en ningún punto
- El proxy solo necesita el bloque de puerto 80

No hay certificado que obtener, no hay certbot que correr.

### Por qué sí se necesita en producción (https_mode=externalhttps)

Con `https_mode=externalhttps`:
- `SITEURL=https://idegeo.centrogeo.org.mx/`
- `oidc_provider_url=https://...` — Keycloak requiere HTTPS
- Los contenedores hacen llamadas internas a `https://...`
- El proxy necesita el bloque de puerto 443 con un certificado válido
- Sin el certificado, el handshake SSL falla y la autenticación se rompe

### Cómo funciona la validación HTTP-01 (certbot)

```
certbot corre en nuestro servidor
    → genera token de desafío
    → lo coloca en /var/www/acme-challenge/TOKEN
    → Let's Encrypt hace GET http://idegeo.centrogeo.org.mx/.well-known/acme-challenge/TOKEN
    → Mario reenvía esa petición a nuestro servidor (igual que cualquier otra)
    → nginx-proxy sirve el archivo desde /var/www/acme-challenge/
    → Let's Encrypt valida → emite el certificado
```

**Prerequisito con Mario:** confirmar que su Apache reenvía las peticiones
`.well-known/acme-challenge/` a nuestro servidor. Sin esta regla, certbot no puede
validar y no emite el certificado.

### Por qué no se usa wildcard para los certs del proxy

Mario tiene el wildcard `*.geosuite-dev.centrogeo.org.mx` en su Apache — ese cert
cubre el tráfico externo (internet → Mario). Nuestro proxy necesita su propio cert
para el tráfico interno (contenedores → proxy via hairpin). Se usa HTTP-01 por
subdominio específico, igual que Jaime lo hizo para `sigic.geoweb.centrogeo.org.mx`.

---

## Cómo la configuración es dinámica

Los archivos de configuración del proxy **no se escriben a mano ni se versionan**.
Se generan automáticamente cada vez que se corre `sigic_install.sh`:

```bash
./sigic_install.sh idegeo dev   → genera proxy/conf.d/idegeo-dev.conf  (solo port 80)
./sigic_install.sh idegeo prd   → genera proxy/conf.d/idegeo-prd.conf  (port 80 + 443)
./sigic_install.sh conafor dev  → genera proxy/conf.d/conafor-dev.conf (solo port 80)
```

El script detecta `https_mode` del env file y decide si agrega el bloque 443.
Al final del deploy, recarga el proxy con `docker exec nginx-proxy nginx -s reload`.

---

## Comparación con el setup de Jaime

| Aspecto | Jaime (debian@10.2.102.238) | Nuestro setup (ubuntu@10.2.102.228) |
|---------|----------------------------|--------------------------------------|
| Nginx | Instalado en el OS | Contenedor Docker |
| Certbot | Systemd timer en el OS | Contenedor Docker (solo prd) |
| Proxy y bundle | Servidores separados | Mismo servidor |
| Hairpin DNS | `/etc/hosts` en el bundle server, apunta a Mario | `extra_hosts` en docker-compose, apunta a host-gateway |
| `proxy_pass` | IP fija del bundle server | Nombre de contenedor Docker |
| Config en git | No (en `/etc/nginx/` del OS) | Sí (generada por sigic_install.sh) |

---

## Tabla resumen por ambiente

| Ambiente | https_mode | Port proxy | SSL/certbot | extra_hosts |
|----------|------------|:----------:|:-----------:|:-----------:|
| dev | http | 80 | No | Sí |
| qa | http | 80 | No | Sí |
| prd | externalhttps | 80 + 443 | Sí | Sí |

---

## Pasos de implementación

### Cambios al bundle (código)

1. `proxy/docker-compose.yml` — servicio nginx-proxy con volúmenes para conf.d,
   acme-challenge y letsencrypt
2. `proxy/conf.d/` — directorio vacío en git (los confs se generan en deploy)
3. `docker-compose.platform.yml` — override que elimina port bindings al host,
   agrega red sigic-proxy al nginx y extra_hosts a django/celery/keycloak
4. `sigic_install.sh` — genera el conf del proxy, crea la red, usa el override,
   recarga el proxy
5. `platforms/idegeo/env/dev.env` — actualizar hostname al patrón correcto:
   `idegeo.geosuite-dev.centrogeo.org.mx`

### Setup único en el servidor

```bash
# bajar stack viejo
COMPOSE_PROJECT_NAME=sigic docker compose down

# jalar código nuevo
git pull origin develop

# crear red compartida
docker network create sigic-proxy

# levantar proxy
docker compose -f proxy/docker-compose.yml up -d

# desplegar idegeo dev
./sigic_install.sh idegeo dev
```

### Para producción (la primera vez)

```bash
# obtener certificado antes del primer deploy
docker compose -f proxy/docker-compose.yml run --rm certbot certonly \
  --webroot -w /var/www/acme-challenge \
  -d idegeo.centrogeo.org.mx

# desplegar en modo externalhttps
./sigic_install.sh idegeo prd
```

### Agregar una plataforma nueva

```bash
# en el servidor, sin tocar nada del proxy manualmente
./sigic_install.sh conafor dev
# el script genera proxy/conf.d/conafor-dev.conf y recarga el proxy
```

---

## Lo que Mario necesita configurar

| Qué | Para qué |
|-----|----------|
| Reenviar `*.geosuite-dev.centrogeo.org.mx` → `10.2.102.228:80` | Ya confirmado |
| Confirmar que `.well-known/acme-challenge/` también se reenvía | Necesario para certbot en prd |
| DNS para nuevos subdominios de qa/prd cuando se definan | Para futuros ambientes |
