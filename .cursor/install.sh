#!/usr/bin/env bash
# Idempotent bootstrap for the Kiln dev environment.
# Runs after checkout. Installs system services (Postgres, Redis, Qdrant),
# refreshes Python + Node dependencies, and ensures a dev .env.
# Self-contained so it reproduces on the default base image without a snapshot.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

QDRANT_VERSION="v1.13.2"

# --- System packages (Postgres + Redis + Python venv toolchain) ---
# The default base image ships python3 without the venv module, so install it here.
if ! command -v pg_ctlcluster >/dev/null 2>&1 \
   || ! command -v redis-server >/dev/null 2>&1 \
   || ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  echo "install: installing system packages (postgres, redis, python venv)"
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    postgresql postgresql-client redis-server \
    python3-venv python3-dev build-essential
else
  echo "install: system packages already present"
fi

# --- Qdrant binary ---
if ! command -v qdrant >/dev/null 2>&1; then
  echo "install: downloading qdrant ${QDRANT_VERSION}"
  tmp="$(mktemp -d)"
  for asset in qdrant-x86_64-unknown-linux-musl.tar.gz qdrant-x86_64-unknown-linux-gnu.tar.gz; do
    if curl -fsSL -o "$tmp/qdrant.tar.gz" \
        "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/${asset}"; then
      break
    fi
  done
  tar xzf "$tmp/qdrant.tar.gz" -C "$tmp"
  sudo mv "$tmp/qdrant" /usr/local/bin/qdrant
  sudo chmod +x /usr/local/bin/qdrant
  rm -rf "$tmp"
else
  echo "install: qdrant already present"
fi

# --- Dev .env (never overwrite an existing one) ---
# Note: AGENT_SHARED_KEY is intentionally left empty here. It is injected into
# the running agent/api processes via the environment.json terminals instead, so
# that the agent test suite (which reads this same .env) does not require the
# X-Agent-Key header on its in-process TestClient calls.
if [ ! -f .env ]; then
  cp .env.example .env
  echo "install: created .env from .env.example (dev mode)"
else
  echo "install: .env already present, leaving it untouched"
fi

# --- Python agent (editable install into a repo-local venv) ---
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -e "apps/agent[dev]"
echo "install: agent Python deps ready"

# --- Node workspaces ---
npm_ci() {
  local dir="$1"
  ( cd "$dir" && ( npm ci || npm install ) )
  echo "install: npm deps ready ($dir)"
}
npm_ci packages/contracts
npm_ci apps/api
npm_ci apps/web

echo "install: complete"
