#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.yml"
SERVICE="db"
HOST_PORT="5432"
AUTO_STOP_LOCAL_POSTGRES="${AUTO_STOP_LOCAL_POSTGRES:-1}"
ALLOW_SUDO_PROMPT="${ALLOW_SUDO_PROMPT:-1}"

if [[ -x ./election_data/bin/python ]]; then
  PYTHON=./election_data/bin/python
else
  PYTHON=python3
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker command not found. Install Docker Engine/Compose first."
  exit 1
fi

is_port_in_use() {
  ss -ltnH "( sport = :$HOST_PORT )" | grep -q .
}

run_sudo_if_possible() {
  if ! command -v sudo >/dev/null 2>&1; then
    return 1
  fi

  if [[ "$ALLOW_SUDO_PROMPT" == "1" && -t 0 ]]; then
    sudo "$@"
  else
    sudo -n "$@"
  fi
}

if [[ "$HOST_PORT" == "5432" ]] && is_port_in_use; then
  if [[ "$AUTO_STOP_LOCAL_POSTGRES" != "1" ]]; then
    echo "Error: port 5432 is already in use and AUTO_STOP_LOCAL_POSTGRES is disabled."
    echo "Set AUTO_STOP_LOCAL_POSTGRES=1 or stop local PostgreSQL, then retry."
    exit 1
  fi

  echo "Port 5432 is in use; attempting to stop local PostgreSQL services..."

  if command -v systemctl >/dev/null 2>&1; then
    run_sudo_if_possible systemctl stop postgresql >/dev/null 2>&1 || true
    while IFS= read -r unit; do
      [[ -n "$unit" ]] && run_sudo_if_possible systemctl stop "$unit" >/dev/null 2>&1 || true
    done < <(systemctl list-units --type=service --all 'postgresql@*.service' --no-legend 2>/dev/null | awk '{print $1}')
  fi

  if command -v pg_lsclusters >/dev/null 2>&1 && command -v pg_ctlcluster >/dev/null 2>&1; then
    while read -r version cluster _; do
      [[ -n "$version" && -n "$cluster" ]] || continue
      if run_sudo_if_possible pg_ctlcluster "$version" "$cluster" stop >/dev/null 2>&1; then
        true
      else
        pg_ctlcluster "$version" "$cluster" stop >/dev/null 2>&1 || true
      fi
    done < <(pg_lsclusters --no-header 2>/dev/null || true)
  fi

  if is_port_in_use; then
    echo "Error: port 5432 is still in use after stop attempts."
    echo "Try running this once: sudo systemctl stop postgresql"
    echo "Then re-run ./start_db.sh"
    exit 1
  fi

  echo "Local PostgreSQL listener on 5432 has been stopped."
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