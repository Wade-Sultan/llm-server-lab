#! /usr/bin/env bash

# Capture a Cloud SQL snapshot into the local seed directory, for
# seed-local-db.sh to restore into the Minikube cluster.
#
# Run from the repo root:  ./scripts/dump-prod-db.sh
#
# Dumps everything rather than excluding the user tables. The catalog you
# actually want is entangled with them — reference_builds.pc_build_id ->
# pc_builds.id, and pc_builds.owner_id -> users.id with no ON DELETE clause —
# so excluding users' data would only fail at the very end of the restore, when
# pg_restore recreates the foreign keys. seed-local-db.sh strips the PII
# immediately after loading instead.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SEED_DIR="${PALLADIUM_SEED_DIR:-$REPO_ROOT/.local-seed}"
DUMP_FILE="${PALLADIUM_SEED_DUMP:-$SEED_DIR/palladium.dump}"
PROXY_PORT="${PROXY_PORT:-5434}"

# .env is not sourceable — EMAIL_FROM contains unquoted <> that the shell would
# read as redirection. Pull the two values out individually instead.
env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

INSTANCE="$(env_value CLOUD_SQL_INSTANCE)"
PROD_PASSWORD="$(env_value POSTGRES_PASSWORD)"
DB_USER="$(env_value POSTGRES_USER)"
DB_NAME="$(env_value POSTGRES_DB)"

if [ -z "$INSTANCE" ]; then
  echo "ERROR: CLOUD_SQL_INSTANCE not found in .env" >&2
  exit 1
fi

# Ubuntu 22.04's default client is 14, which refuses to dump a 15+ server.
DUMP_MAJOR="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
if [ "$DUMP_MAJOR" -lt 17 ]; then
  echo "ERROR: pg_dump is $DUMP_MAJOR; need 17+ to dump this server." >&2
  echo "       sudo apt-get install -y postgresql-client-17" >&2
  exit 1
fi

mkdir -p "$SEED_DIR"

export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$REPO_ROOT/.gcloud/application_default_credentials.json}"

echo "Starting cloud-sql-proxy on 127.0.0.1:$PROXY_PORT ..."
./cloud-sql-proxy --port "$PROXY_PORT" "$INSTANCE" &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  if pg_isready -h 127.0.0.1 -p "$PROXY_PORT" -q 2>/dev/null; then break; fi
  # If the proxy died (bad instance name, expired ADC) fail now rather than
  # spending 30s waiting on a port that will never open.
  kill -0 "$PROXY_PID" 2>/dev/null || { echo "ERROR: proxy exited; see its output above." >&2; exit 1; }
  sleep 1
done

export PGPASSWORD="$PROD_PASSWORD"

echo "Source server: $(psql -h 127.0.0.1 -p "$PROXY_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc 'SHOW server_version')"

echo "Dumping to $DUMP_FILE ..."
pg_dump -h 127.0.0.1 -p "$PROXY_PORT" -U "$DB_USER" -d "$DB_NAME" \
  --no-owner --no-privileges --format=custom --file="$DUMP_FILE"

echo
echo "Wrote $(du -h "$DUMP_FILE" | cut -f1) to $DUMP_FILE"
echo "Restore happens automatically on the next 'tilt up', or now with:"
echo "    ./scripts/seed-local-db.sh --force"
