#!/usr/bin/env bash
# Per-boot infrastructure for the Kiln dev environment.
# Starts Postgres, Redis, and Qdrant, ensures the database exists, and applies
# migrations. Idempotent and safe to re-run. Returns once infra is ready; the
# application dev servers (agent/api/web) run as terminals.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PGPASS="kiln_dev_password"
QDRANT_HOME="${HOME}/.local/share/qdrant"

# --- PostgreSQL ---
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for i in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then break; fi
  sleep 1
done
# Role + database (idempotent)
sudo -u postgres psql -v ON_ERROR_STOP=1 -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname='kiln'" | grep -q 1 \
  || sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
     "CREATE ROLE kiln LOGIN PASSWORD '${PGPASS}'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='kiln'" | grep -q 1 \
  || sudo -u postgres createdb -O kiln kiln
echo "start: postgres ready"

# --- Schema + migrations (idempotent; all guarded with IF NOT EXISTS) ---
export PGPASSWORD="$PGPASS"
psql -h localhost -U kiln -d kiln -v ON_ERROR_STOP=1 -q -f infra/postgres/init.sql
for f in infra/postgres/migrations/*.sql; do
  [ -f "$f" ] || continue
  psql -h localhost -U kiln -d kiln -v ON_ERROR_STOP=1 -q -f "$f"
done
unset PGPASSWORD
echo "start: migrations applied"

# --- Redis ---
if ! redis-cli ping >/dev/null 2>&1; then
  sudo redis-server /etc/redis/redis.conf --daemonize yes
  for i in $(seq 1 15); do redis-cli ping >/dev/null 2>&1 && break; sleep 1; done
fi
echo "start: redis ready ($(redis-cli ping 2>/dev/null))"

# --- Qdrant (background daemon; corpus is re-ingested by the agent on boot) ---
mkdir -p "${QDRANT_HOME}/storage" "${QDRANT_HOME}/snapshots"
if ! curl -fsS http://localhost:6333/healthz >/dev/null 2>&1; then
  QDRANT__STORAGE__STORAGE_PATH="${QDRANT_HOME}/storage" \
  QDRANT__STORAGE__SNAPSHOTS_PATH="${QDRANT_HOME}/snapshots" \
    nohup qdrant >/tmp/qdrant.log 2>&1 &
  for i in $(seq 1 30); do
    curl -fsS http://localhost:6333/healthz >/dev/null 2>&1 && break
    sleep 1
  done
fi
echo "start: qdrant ready ($(curl -fsS http://localhost:6333/healthz 2>/dev/null))"

echo "start: infrastructure up"
