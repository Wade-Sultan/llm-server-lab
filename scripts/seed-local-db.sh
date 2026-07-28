#! /usr/bin/env bash

# Restore the Cloud SQL snapshot into the cluster's Postgres, then strip the
# production PII. Run by Tilt after the migrate Job, so a fresh
# `minikube delete && tilt up` comes back with a populated catalog.
#
# Idempotent: skips when the catalog already has rows, so it costs nothing on
# every subsequent `tilt up`. Pass --force to restore over existing data.
#
# Talks to Postgres through Tilt's 5433 port-forward rather than exec'ing into
# the pod, so the host's pg_restore does the work and the dump never has to be
# copied into the container.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SEED_DIR="${PALLADIUM_SEED_DIR:-$REPO_ROOT/.local-seed}"
DUMP_FILE="${PALLADIUM_SEED_DUMP:-$SEED_DIR/palladium.dump}"
PG_PORT="${PALLADIUM_LOCAL_PG_PORT:-5433}"
FORCE="${PALLADIUM_SEED_FORCE:-0}"
[ "${1:-}" = "--force" ] && FORCE=1

if [ ! -f "$DUMP_FILE" ]; then
  echo "No seed dump at $DUMP_FILE — starting with an empty schema."
  echo "Create one with: ./scripts/dump-prod-db.sh"
  # Exit 0 on purpose: an empty database is a working cluster, and failing here
  # would turn a missing convenience into a red Tilt resource.
  exit 0
fi

LOCAL_PW="$(grep -E '^POSTGRES_PASSWORD=' deploy/overlays/local/.env.local | head -1 | cut -d= -f2-)"
if [ -z "$LOCAL_PW" ]; then
  echo "ERROR: POSTGRES_PASSWORD missing from deploy/overlays/local/.env.local" >&2
  exit 1
fi
export PGPASSWORD="$LOCAL_PW"

PSQL=(psql -h 127.0.0.1 -p "$PG_PORT" -U palladium_app -d palladium)

# Tilt starts this once the migrate Job finishes, but the port-forward is
# established independently — wait rather than race it.
echo "Waiting for Postgres on 127.0.0.1:$PG_PORT ..."
for _ in $(seq 1 60); do
  pg_isready -h 127.0.0.1 -p "$PG_PORT" -q 2>/dev/null && break
  sleep 1
done
if ! pg_isready -h 127.0.0.1 -p "$PG_PORT" -q 2>/dev/null; then
  echo "ERROR: Postgres never became reachable on $PG_PORT." >&2
  echo "       Is the 'postgres' resource green in Tilt?" >&2
  exit 1
fi

if [ "$FORCE" != "1" ]; then
  EXISTING="$("${PSQL[@]}" -tAc "SELECT count(*) FROM pc_parts" 2>/dev/null || echo 0)"
  if [ "${EXISTING:-0}" -gt 0 ]; then
    echo "Catalog already has $EXISTING pc_parts rows — skipping restore (--force to override)."
    exit 0
  fi
fi

echo "Restoring $DUMP_FILE ..."
# --clean drops objects the migrate Job just created; the DROPs for objects that
# do not exist yet are expected noise, so a non-zero exit here is not fatal on
# its own. The verification below is what actually decides.
pg_restore -h 127.0.0.1 -p "$PG_PORT" -U palladium_app -d palladium \
  --no-owner --no-privileges --clean --if-exists "$DUMP_FILE" \
  2> >(grep -v 'does not exist, skipping' >&2) || true

echo "Stripping production PII ..."
# Children before parents, so this holds regardless of each FK's ON DELETE.
# pc_builds rows are kept but un-owned: reference_builds.pc_build_id points at
# them, so deleting them would take curated reference builds with them.
"${PSQL[@]}" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
UPDATE pc_builds SET owner_id = NULL;
DELETE FROM module_decisions;
DELETE FROM build_sessions;
DELETE FROM messages;
DELETE FROM conversations;
DELETE FROM users;
COMMIT;
SQL

PARTS="$("${PSQL[@]}" -tAc 'SELECT count(*) FROM pc_parts')"
LISTINGS="$("${PSQL[@]}" -tAc 'SELECT count(*) FROM listings')"
REFS="$("${PSQL[@]}" -tAc 'SELECT count(*) FROM reference_builds')"
USERS="$("${PSQL[@]}" -tAc 'SELECT count(*) FROM users')"

echo "  pc_parts=$PARTS  listings=$LISTINGS  reference_builds=$REFS  users=$USERS"

if [ "$PARTS" -eq 0 ]; then
  echo "ERROR: restore finished but pc_parts is empty — check the dump." >&2
  exit 1
fi
if [ "$USERS" -ne 0 ]; then
  echo "ERROR: users table still populated after scrub." >&2
  exit 1
fi

echo "Seed complete. Restart pods to refresh their pools:"
echo "    kubectl rollout restart deployment/builder deployment/commerce deployment/admin"
