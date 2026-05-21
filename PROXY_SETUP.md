# Configuración del Reverse Proxy — Servidor Dev/QA

Documento de referencia para la reunión de configuración en servidor `10.2.102.228`.

---

## Arquitectura

```
Internet (HTTPS 443)
    │
    ▼
Apache de CentroGeo  ← SSL termination (certificado aquí)
    │ reenvía por HTTP interno
    ▼
10.2.102.228:80  (servidor Nimbus — nuestro servidor)
    │
    ▼
Nginx de sistema  ← reverse proxy por subdominio
    ├── geosuite-dev.centrogeo.org.mx  → localhost:8001  (stack IDEGEO)
    ├── conafor-dev.centrogeo.org.mx   → localhost:8002  (stack CONAFOR, futuro)
    └── sedema-dev.centrogeo.org.mx    → localhost:8003  (stack SEDEMA, futuro)
```

Cada stack de plataforma corre dentro de Docker. El Nginx de Docker de cada stack escucha en su puerto asignado del host (8001, 8002, 8003).

---

## Por qué el proxy vive en el mismo servidor que las plataformas

Para los ambientes **dev y QA**, donde las tres plataformas (IDEGEO, CONAFOR, SEDEMA) comparten un mismo servidor Nimbus, es mejor práctica tener el reverse proxy en esa misma máquina por las siguientes razones:

### 1. No hay tráfico de red entre máquinas
El proxy reenvía las peticiones a `localhost:8001`, `localhost:8002`, `localhost:8003` — comunicación dentro de la misma máquina por la interfaz loopback. Esto es más rápido, más simple y sin latencia de red.

### 2. Una instancia de Nimbus menos que administrar
Si el proxy viviera en una instancia separada, habría que gestionar, parchear y monitorear esa instancia adicional — costo y complejidad innecesarios cuando todas las plataformas ya están en el mismo servidor.

### 3. Es el patrón estándar para ambientes compartidos
Un servidor con múltiples aplicaciones + un Nginx de sistema como enrutador es la arquitectura estándar en Linux. Es exactamente cómo funciona cualquier VPS con múltiples sitios. La instancia separada solo tiene sentido cuando las aplicaciones están en máquinas distintas.

### 4. Diferencia con producción
En **producción**, cada plataforma tiene su propio servidor dedicado — ahí sí puede tener sentido un proxy dedicado o usar directamente el Apache de CentroGeo como entrada. Para dev/QA el modelo compartido es el correcto.

---

## Por qué no necesitamos certbot / Let's Encrypt en nuestro servidor

El Apache de CentroGeo (administrado por Mario) ya tiene el certificado SSL para `geosuite-dev.centrogeo.org.mx` y hace la **terminación SSL**. Esto significa:

- El tráfico del usuario al servidor de CentroGeo viaja cifrado (HTTPS)
- El tráfico de CentroGeo a nuestro servidor `10.2.102.228` viaja por HTTP en la red interna

Nuestro Nginx de sistema **nunca ve HTTPS** — recibe peticiones HTTP desde CentroGeo y las reenvía a los stacks de Docker. No hay ningún punto en nuestro servidor donde sea necesario un certificado.

Instalar certbot en nuestro servidor sería:
- Innecesario (el certificado ya existe en CentroGeo)
- Conflictivo (intentaría obtener un certificado para un dominio que ya tiene uno)
- Duplicar responsabilidades que ya maneja Mario

---

## Pasos de configuración

### Prerrequisito
El servidor ya tiene el código actualizado en `/opt/sigic-bundle` (rama `develop`).

### Paso 1 — Bajar el stack actual

El stack actual fue deployado en modo clásico con nombre `sigic`. Hay que bajarlo antes de instalar Nginx de sistema (ambos compiten por el puerto 80).

```bash
cd /opt/sigic-bundle
COMPOSE_PROJECT_NAME=sigic docker compose down
```

### Paso 2 — Instalar Nginx de sistema

```bash
sudo apt update && sudo apt install -y nginx
```

### Paso 3 — Crear configuración para IDEGEO y habilitar

```bash
sudo tee /etc/nginx/sites-available/idegeo-dev << 'EOF'
server {
    listen 80;
    server_name geosuite-dev.centrogeo.org.mx;

    location / {
        proxy_pass http://localhost:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/idegeo-dev /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl enable nginx && sudo systemctl start nginx
```

### Paso 4 — Redeployar IDEGEO en modo plataforma

```bash
cd /opt/sigic-bundle
./sigic_install.sh idegeo dev
```

Esto levantará el stack con:
- Contenedores con prefijo `idegeo` (ej. `django4idegeo`, `nginx4idegeo`)
- Nginx de Docker escuchando en el puerto **8001** del host
- Frontend admin en puerto **3001**, frontend app en puerto **3002**

### Paso 5 — Verificar

```bash
# verificar que Docker nginx está en puerto 8001
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"

# verificar que Nginx de sistema enruta correctamente
curl -I http://localhost:8001
curl -H "Host: geosuite-dev.centrogeo.org.mx" http://localhost/
```

---

## Lo que necesitamos de Mario (infra CentroGeo)

El dominio `geosuite-dev.centrogeo.org.mx` ya está configurado y apuntando al servidor. No se requiere ningún cambio para IDEGEO dev.

Cuando agreguemos CONAFOR y SEDEMA en el futuro:

| Lo que necesitamos de Mario | Detalle |
|-----------------------------|---------|
| Registro DNS | `conafor-dev.centrogeo.org.mx` → `10.2.102.228` |
| Registro DNS | `sedema-dev.centrogeo.org.mx` → `10.2.102.228` |
| VirtualHost Apache | Uno nuevo por cada subdominio (misma estructura que el actual) |

Del lado de nuestro servidor, nosotros agregamos el archivo de configuración de Nginx correspondiente y deployamos la plataforma. No se requiere intervención de Mario para nuestro servidor.

---

## Puertos asignados por plataforma

| Plataforma | Nginx (host) | Frontend admin | Frontend app |
|------------|:------------:|:--------------:|:------------:|
| idegeo     | 8001         | 3001           | 3002         |
| conafor    | 8002         | 3011           | 3012         |
| sedema     | 8003         | 3021           | 3022         |
