#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.yml"
SERVICE="db"

if [[ -x ../election_data/bin/python ]]; then
  PYTHON=../election_data/bin/python
else
  PYTHON=python3
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker command not found. Install Docker Engine/Compose first."
  exit 1
fi

echo "Starting PostgreSQL service ($SERVICE) using $COMPOSE_FILE..."
docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"

echo "Waiting for database readiness..."
ready=false
for _ in {1..30}; do
  if docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" pg_isready -U election_maps -d election_maps >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

if [[ "$ready" != "true" ]]; then
  echo "Database is not ready yet. Check logs with: docker compose -f $COMPOSE_FILE logs -f $SERVICE"
  exit 1
fi

echo "Database is up."
echo "Host: localhost"
echo "Port: 5432"
echo "Database: election_maps"
echo "User: election_maps"
echo "Password: election_maps_dev"
echo "Run importer: $PYTHON old_data/import_topojson.py"