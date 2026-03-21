#!/usr/bin/env bash
# Migrate local Docker PostgreSQL database to Supabase.
#
# Run AFTER archive_old_model_runs.py so old simulation data is already
# in model_uns.db and won't be transferred to Supabase.
#
# Prerequisites:
#   - Local Docker DB must be running (./data/start_db.sh)
#   - SUPABASE_DB_URL must be set to the Supabase connection string, e.g.:
#       export SUPABASE_DB_URL="postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
#   - PostGIS extension must be enabled in Supabase (dashboard → Extensions → postgis)
#   - psql and pg_dump must be available on PATH
#
# Usage:
#   export SUPABASE_DB_URL="postgresql://..."
#   ./data/scripts/migrate_to_supabase.sh

set -euo pipefail

LOCAL_DB_URL="postgresql://election_maps:election_maps_dev@localhost:5432/election_maps"
DOCKER_CONTAINER="election_maps_db"
DUMP_DIR="$(mktemp -d)"
SCHEMA_DUMP="$DUMP_DIR/schema.sql"
DATA_DUMP="$DUMP_DIR/data.sql"

if [[ -z "${SUPABASE_DB_URL:-}" ]]; then
    echo "ERROR: SUPABASE_DB_URL is not set."
    echo "  export SUPABASE_DB_URL=\"postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres\""
    exit 1
fi

if [[ "${SUPABASE_DB_URL}" != postgresql://* && "${SUPABASE_DB_URL}" != postgres://* ]]; then
    echo "ERROR: SUPABASE_DB_URL does not look like a PostgreSQL URL: ${SUPABASE_DB_URL}"
    echo "  Expected: postgresql://user:password@host:port/database"
    exit 1
fi

# Parse connection components from URI for use in key=value format.
# hostaddr in a URI query string is silently ignored by libpq; key=value format
# is required for hostaddr to take effect (needed for WSL2 IPv6 bypass).
_sb_user="$(echo "$SUPABASE_DB_URL" | sed 's|.*://||; s|:.*||')"
_sb_password="$(echo "$SUPABASE_DB_URL" | sed 's|.*://[^:]*:||; s|@.*||')"
_sb_host="$(echo "$SUPABASE_DB_URL" | sed 's|.*@||; s|[:/].*||')"
_sb_port="$(echo "$SUPABASE_DB_URL" | sed 's|.*@[^:]*:||; s|/.*||')"
_sb_dbname="$(echo "$SUPABASE_DB_URL" | sed 's|.*/||; s|?.*||')"

# WSL2 often lacks IPv6 routing. Resolve to IPv4 so libpq uses IPv4.
_sb_ipv4="$(getent ahostsv4 "$_sb_host" 2>/dev/null | awk 'NR==1{print $1}')"
if [[ -n "$_sb_ipv4" ]]; then
    echo "==> Resolved $_sb_host to IPv4 $_sb_ipv4 (forcing IPv4 routing with SSL)"
else
    _sb_ipv4="$_sb_host"
fi

# key=value connection string used for psql calls (hostaddr + sslmode honoured here)
SUPABASE_CONN="host=${_sb_host} hostaddr=${_sb_ipv4} port=${_sb_port} dbname=${_sb_dbname} user=${_sb_user} password=${_sb_password} sslmode=require"

echo "==> Dumping schema from local DB (via Docker container to match server version)..."
docker exec "$DOCKER_CONTAINER" pg_dump \
    "$LOCAL_DB_URL" \
    --schema-only \
    --no-owner \
    --no-privileges \
    --no-acl \
    > "$SCHEMA_DUMP"

# Supabase installs PostGIS in the extensions schema; strip the public. qualifier
# so geometry columns resolve correctly on the Supabase side.
sed -i 's/public\.geometry/geometry/g' "$SCHEMA_DUMP"

echo "==> Dumping data from local DB..."
docker exec "$DOCKER_CONTAINER" pg_dump \
    "$LOCAL_DB_URL" \
    --data-only \
    --no-owner \
    --no-privileges \
    > "$DATA_DUMP"

echo "==> Applying schema to Supabase..."
echo "    (Errors about existing types/extensions from Supabase defaults can be ignored)"
psql "$SUPABASE_CONN" \
    --file="$SCHEMA_DUMP" \
    2>&1 | grep -v "^SET$" | grep -v "^$" || true

echo "==> Importing data into Supabase..."
psql "$SUPABASE_CONN" \
    --file="$DATA_DUMP"

echo ""
echo "==> Migration complete."
echo "    Dump files retained at: $DUMP_DIR"
echo ""
echo "Next steps:"
echo "  1. Set DATABASE_URL in your environment (or .env file):"
echo "       export DATABASE_URL=\"\$SUPABASE_DB_URL\""
echo "  2. Verify the connection:"
echo "       cd data && python -c \"from db import Database; db = Database(); print('OK')\""
echo "  3. Run the Flask server and check the election maps load correctly."
