#!/usr/bin/env bash
# Pull latest main and rebuild the full Kiln stack on Oracle VM.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.oracle.yml)

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.oracle.example and fill DOMAIN, ACME_EMAIL, keys."
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

missing=()
[[ -z "${DOMAIN:-}" || "${DOMAIN}" == "yourname.io.vn" ]] && missing+=("DOMAIN")
[[ -z "${ACME_EMAIL:-}" || "${ACME_EMAIL}" == "you@example.com" ]] && missing+=("ACME_EMAIL")
[[ -z "${AGENT_SHARED_KEY:-}" ]] && missing+=("AGENT_SHARED_KEY")
if ((${#missing[@]})); then
  echo "Set in .env before deploy: ${missing[*]}"
  exit 1
fi

if [[ "${API_TO_AGENT_KEY:-}" != "${AGENT_SHARED_KEY}" ]]; then
  echo "API_TO_AGENT_KEY must match AGENT_SHARED_KEY (or leave API_TO_AGENT_KEY empty)."
  exit 1
fi

echo "==> git pull"
git pull --ff-only origin main

echo "==> docker compose up --build"
"${COMPOSE[@]}" up -d --build

echo "==> status"
"${COMPOSE[@]}" ps

echo ""
echo "Logs: ${COMPOSE[*]} logs -f caddy web api agent"
echo "Open: https://${DOMAIN}"
