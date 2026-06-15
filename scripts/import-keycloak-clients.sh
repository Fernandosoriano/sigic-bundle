#!/bin/bash
set -e

SERVER="http://localhost:8080/iam"
ADMIN_REALM="master"
USER="kadmin"
PASS="kadmin"

REALM=$(echo "${KEYCLOAK_ISSUER:-}" | sed 's|.*/realms/||' | sed 's|/.*||')
REALM=${REALM:-sigic}
IMPORT_DIR="/opt/keycloak/data/import"

# 🔐 LOGIN
/opt/keycloak/bin/kcadm.sh config credentials \
  --server $SERVER \
  --realm $ADMIN_REALM \
  --user $USER \
  --password $PASS \
  --client admin-cli

# =========================
# 🔥 REALM (UPSERT)
# =========================

echo "Checking realm $REALM..."

REALM_EXISTS=$(/opt/keycloak/bin/kcadm.sh get realms/$REALM 2>/dev/null || true)

if [ -z "$REALM_EXISTS" ]; then
  echo "Creating realm $REALM"
  /opt/keycloak/bin/kcadm.sh create realms \
    -f $IMPORT_DIR/keycloak-realm-sigic.json \
    -s realm=$REALM -s displayName=$REALM
else
  echo "Updating realm $REALM"
  /opt/keycloak/bin/kcadm.sh update realms/$REALM \
    -f $IMPORT_DIR/keycloak-realm-sigic.json \
    -s realm=$REALM -s displayName=$REALM
fi

echo "Realm OK"

# =========================
# 🔥 CLIENTS (UPSERT)
# =========================

FILES=(
  keycloak-client-sigic-admin.json
  keycloak-client-sigic-app.json
  keycloak-client-sigic-geonode.json
)

for FILE in "${FILES[@]}"; do
  PATH_JSON="$IMPORT_DIR/$FILE"

  # sacar clientId sin jq (como ya lo haces)
  CLIENT_ID=$(grep '"clientId"' $PATH_JSON | head -1 | sed -E 's/.*"clientId": *"([^"]+)".*/\1/')

  echo "Processing $CLIENT_ID..."

  CID=$(/opt/keycloak/bin/kcadm.sh get clients \
    -r $REALM \
    -q clientId=$CLIENT_ID \
    --fields id \
    --format csv | tail -n 1 | tr -d '"')

  if [ -z "$CID" ]; then
    echo "Creating $CLIENT_ID"
    /opt/keycloak/bin/kcadm.sh create clients \
      -r $REALM \
      -f $PATH_JSON
  else
    echo "Updating $CLIENT_ID"
    /opt/keycloak/bin/kcadm.sh update clients/$CID \
      -r $REALM \
      -f $PATH_JSON
  fi

done

echo "All clients synced"
