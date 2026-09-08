#!/usr/bin/env bash
# Idempotent repository bootstrap for the Kiln dev environment.
# Runs after checkout. Refreshes Python + Node dependencies and ensures a dev .env.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Dev .env (never overwrite an existing one) ---
if [ ! -f .env ]; then
  cp .env.example .env
  # Dev shared key so kiln-api can authenticate to kiln-agent (/ready turns green).
  sed -i 's/^AGENT_SHARED_KEY=.*/AGENT_SHARED_KEY=kiln_dev_agent_key/' .env
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
