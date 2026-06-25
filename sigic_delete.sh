#!/bin/bash
set -e

ARG1=${1:-}
ARG2=${2:-}

# =========================
# 🔹 detectar modo: platform o clásico
# =========================

if [ -n "$ARG1" ] && [ -d "platforms/$ARG1" ]; then
  # --- modo platform: ./sigic_delete.sh <platform> <environment> ---
  PLATFORM=$ARG1
  ENVIRONMENT=$ARG2

  if [ -z "$ENVIRONMENT" ]; then
    echo "Uso: ./sigic_delete.sh <platform> <environment>"
    echo "  platform:     carpeta en platforms/ (ej: idegeo, conafor, sedema)"
    echo "  environment:  dev | qa | prd"
    exit 1
  fi

  PROJECT="${PLATFORM}-${ENVIRONMENT}"
  ENV_ACTIVE=".env.${PROJECT}"
  PROXY_CONF="proxy/conf.d/${PROJECT}.conf"
  KC_SUBDIR="overrides/keycloak/${PROJECT}"

  echo "🗑️  Eliminando plataforma: $PROJECT"

  # bajar contenedores
  CONTAINERS=$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" -q)
  if [ -n "$CONTAINERS" ]; then
    echo "🛑 Deteniendo contenedores..."
    docker stop $CONTAINERS 2>/dev/null || true
    docker rm $CONTAINERS 2>/dev/null || true
  else
    echo "  Sin contenedores activos para $PROJECT"
  fi

  # eliminar volúmenes
  VOLUMES=$(docker volume ls --filter "label=com.docker.compose.project=$PROJECT" -q)
  if [ -n "$VOLUMES" ]; then
    echo "🗄️  Eliminando volúmenes..."
    docker volume rm $VOLUMES 2>/dev/null || true
  else
    echo "  Sin volúmenes para $PROJECT"
  fi

  # eliminar red del proyecto
  docker network rm "${PROJECT}_sigicnetwork" 2>/dev/null || true

  # eliminar proxy conf y recargar nginx-proxy
  if [ -f "$PROXY_CONF" ]; then
    echo "🔧 Eliminando proxy config: $PROXY_CONF"
    rm -f "$PROXY_CONF"
    docker exec nginx-proxy nginx -s reload 2>/dev/null || true
  fi

  # eliminar env file
  if [ -f "$ENV_ACTIVE" ]; then
    echo "🗂️  Eliminando $ENV_ACTIVE"
    rm -f "$ENV_ACTIVE"
  fi

  # eliminar JSONs de keycloak del subdirectorio de plataforma
  if [ -d "$KC_SUBDIR" ]; then
    echo "🔑 Eliminando JSONs de Keycloak: $KC_SUBDIR"
    rm -rf "$KC_SUBDIR"
  fi

  echo "✅ Plataforma $PROJECT eliminada"

else
  # --- modo clásico ---
  sh geonode/docker-purge.sh
fi
