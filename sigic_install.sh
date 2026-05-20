#!/bin/bash
set -e

ARG1=${1:-default}
ARG2=$2

# =========================
# 🔹 detectar modo: platform o flavor clásico
# =========================

if [ -d "platforms/$ARG1" ]; then
  # --- modo platform: ./sigic_install.sh <platform> <environment> ---
  PLATFORM=$ARG1
  ENVIRONMENT=$ARG2

  if [ -z "$ENVIRONMENT" ]; then
    echo "Uso: ./sigic_install.sh <platform> <environment>"
    echo "  platform:     carpeta en platforms/ (ej: idegeo, conafor, sedema)"
    echo "  environment:  dev | qa | prd"
    exit 1
  fi

  PLATFORM_FILE="platforms/$PLATFORM/platform.json"
  ENV_FILE="platforms/$PLATFORM/env/$ENVIRONMENT.env"

  if [ ! -f "$PLATFORM_FILE" ]; then
    echo "No existe: $PLATFORM_FILE"
    exit 1
  fi

  if [ ! -f "$ENV_FILE" ]; then
    echo "No existe: $ENV_FILE"
    echo "Environments disponibles: $(ls platforms/$PLATFORM/env/ 2>/dev/null | sed 's/\.env//' | tr '\n' ' ')"
    exit 1
  fi
  # HERENCIA
  BASE_FLAVOR=$(jq -r '.extends' "$PLATFORM_FILE")
  FLAVOR_FILE="sigic-mixins/$BASE_FLAVOR.json"

  if [ ! -f "$FLAVOR_FILE" ]; then
    echo "El platform '$PLATFORM' extiende '$BASE_FLAVOR' pero no existe: $FLAVOR_FILE"
    exit 1
  fi

  echo "Platform: $PLATFORM | Environment: $ENVIRONMENT | Base flavor: $BASE_FLAVOR"

  # leer env file (hostname, env_type, oidc_provider_url, https_mode)
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    case "$key" in
      hostname)          HOSTNAME="$value" ;;
      env_type)          ENV_TYPE="$value" ;;
      oidc_provider_url) OIDC_URL="$value" ;;
      https_mode)        HTTPS_MODE="$value" ;;
    esac
  done < "$ENV_FILE"

  # email y homepath: platform.json overrides > flavor base
  EMAIL=$(jq -r '.overrides.email // empty' "$PLATFORM_FILE")
  [ -z "$EMAIL" ] && EMAIL=$(jq -r '.email // empty' "$FLAVOR_FILE")

  HOMEPATH=$(jq -r '.overrides.homepath // empty' "$PLATFORM_FILE")
  [ -z "$HOMEPATH" ] && HOMEPATH=$(jq -r '.homepath // empty' "$FLAVOR_FILE")

  # puertos por plataforma
  NGINX_PORT=$(jq -r '.ports.nginx' "$PLATFORM_FILE")
  ADMIN_PORT=$(jq -r '.ports.frontend_admin' "$PLATFORM_FILE")
  APP_PORT=$(jq -r '.ports.frontend_app' "$PLATFORM_FILE")

  export COMPOSE_PROJECT_NAME=$PLATFORM
  export HTTP_PORT=$NGINX_PORT
  export FRONTEND_ADMIN_PORT=$ADMIN_PORT
  export FRONTEND_APP_PORT=$APP_PORT

  PLATFORM_MODE=true

else
  # --- modo clásico: ./sigic_install.sh <flavor> <https_mode> ---
  FLAVOR=$ARG1
  HTTPS_MODE=$ARG2

  if [ -z "$HTTPS_MODE" ]; then
    echo "Uso: ./sigic_install.sh <sabor> [http|https|externalhttps]"
    echo "El valor de <sabor> debe corresponder a un archivo JSON en sigic-mixins/ (ej: default.json)"
    exit 1
  fi

  FLAVOR_FILE="sigic-mixins/$FLAVOR.json"

  if [ ! -f "$FLAVOR_FILE" ]; then
    echo "No existe flavor: $FLAVOR_FILE"
    exit 1
  fi

  echo "Flavor: $FLAVOR"

  ENV_TYPE=$(jq -r '.env_type' "$FLAVOR_FILE")
  HOSTNAME=$(jq -r '.hostname' "$FLAVOR_FILE")
  EMAIL=$(jq -r '.email' "$FLAVOR_FILE")
  OIDC_URL=$(jq -r '.oidc_provider_url' "$FLAVOR_FILE")
  HOMEPATH=$(jq -r '.homepath' "$FLAVOR_FILE")

  PLATFORM_MODE=false
fi

# =========================
# 🔹 HTTPS runtime
# =========================

HTTPS_FLAG=""

case "$HTTPS_MODE" in
  https)
    HTTPS_FLAG="--https"
    ;;
  externalhttps)
    HTTPS_FLAG="--externalhttps"
    ;;
  http|"")
    HTTPS_FLAG=""
    ;;
  *)
    echo "Modo HTTPS inválido: $HTTPS_MODE"
    echo "Usa: http | https | externalhttps"
    exit 1
    ;;
esac

echo "Modo HTTPS: ${HTTPS_MODE:-http}"

# =========================
# 🔹 convertir JSON a flags
# =========================

FLAGS=""

# boolean flags: platform overrides > base flavor
for key in useoidc usefrontendadmin usefrontendapp enableiaproxy enableiadb enablelevantamientoproxy enablelevantamientodb; do
  if [ "$PLATFORM_MODE" = true ]; then
    val=$(jq -r ".overrides.$key // empty" "$PLATFORM_FILE")
    [ -z "$val" ] && val=$(jq -r ".$key // empty" "$FLAVOR_FILE")
  else
    val=$(jq -r ".$key" "$FLAVOR_FILE")
  fi
  if [ "$val" = "true" ]; then
    FLAGS="$FLAGS --$key"
  fi
done

# =========================
# 🔹 ejecutar script real
# =========================

python3 create-envfile.py \
  --env_type="$ENV_TYPE" \
  --hostname="$HOSTNAME" \
  --email="$EMAIL" \
  --oidc_provider_url="$OIDC_URL" \
  --homepath="$HOMEPATH" \
  $FLAGS \
  $HTTPS_FLAG

# =========================
# 🔹 profiles
# =========================

PROFILES=$(jq -r '.profiles | join(",")' "$FLAVOR_FILE")

echo "🚀 Profiles: $PROFILES"

COMPOSE_PROFILES=$PROFILES docker compose up -d


# =========================
# 🔹 importar keycloak (si aplica)
# =========================

if echo "$PROFILES" | grep -q "oidc"; then
  echo "🔐 Detectado profile oidc → importando configuración de Keycloak..."

  # esperar a que keycloak esté listo
  echo "⏳ Esperando Keycloak..."
  sleep 30

  echo "🚀 Ejecutando import de clientes..."

  docker exec keycloak4sigic bash -c "/scripts/import-keycloak-clients.sh"

  echo "✅ Keycloak configurado"

  cat .env | grep -E '^(GEOSERVER_ADMIN_PASSWORD|ADMIN_PASSWORD)='
fi


# =========================
# 🔹 importar fixtures geonode (si aplica)
# =========================

if echo "$PROFILES" | grep -q "geonode"; then
  echo "📦 Detectado profile geonode → cargando fixtures Django..."

  python3 create-socialaccount-fixture.py

  echo "⏳ Esperando Django..."
  sleep 20

  echo "🚀 Cargando fixture socialaccount..."

  docker exec django4sigic bash -c "python manage.py loaddata /usr/src/sigic_geonode/fixtures/socialaccount.json" || true

  echo "✅ Fixture cargado"
fi

echo "🎉 SIGIC instalado con éxito!"
cat .env | grep -E '^(GEOSERVER_ADMIN_PASSWORD|ADMIN_PASSWORD)='