#!/usr/bin/env bash
# Kiln / AI_Research_Agent â€” zero-clone quickstart
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/quangg1/AI_Research_Agent/<ref>/scripts/quickstart.sh | bash
# Overrides: KILN_REF, KILN_HOME, KILN_REPO, KILN_USE_GHCR=0
set -euo pipefail

REPO_URL="${KILN_REPO:-https://github.com/quangg1/AI_Research_Agent.git}"
REF="${KILN_REF:-main}"
INSTALL_DIR="${KILN_HOME:-${HOME}/.kiln/AI_Research_Agent}"

echo "==> Kiln quickstart"
echo "    repo: ${REPO_URL}"
echo "    ref:  ${REF}"
echo "    dir:  ${INSTALL_DIR}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is required. Install Docker Desktop, then re-run." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose v2 is required (docker compose)." >&2
  exit 1
fi

mkdir -p "$(dirname "${INSTALL_DIR}")"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  echo "==> Updating existing checkout..."
  git -C "${INSTALL_DIR}" fetch --depth 1 origin "${REF}"
  git -C "${INSTALL_DIR}" checkout -B "${REF}" "FETCH_HEAD"
else
  echo "==> Shallow-cloning into ${INSTALL_DIR} (managed for you)..."
  rm -rf "${INSTALL_DIR}"
  if ! git clone --depth 1 --branch "${REF}" "${REPO_URL}" "${INSTALL_DIR}"; then
    git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" checkout "${REF}" 2>/dev/null || true
  fi
fi

cd "${INSTALL_DIR}"

if [[ ! -f .env ]]; then
  echo "==> Creating .env from .env.example (dev auth defaults)..."
  cp .env.example .env
else
  echo "==> Keeping existing .env"
fi

USE_GHCR=0
if [[ -f docker-compose.ghcr.yml ]] && [[ "${KILN_USE_GHCR:-auto}" != "0" ]]; then
  if docker compose -f docker-compose.yml -f docker-compose.ghcr.yml -f docker-compose.dev.yml pull 2>/dev/null; then
    USE_GHCR=1
  fi
fi

if [[ "${USE_GHCR}" -eq 1 ]]; then
  echo "==> Using prebuilt GHCR images"
  docker compose -f docker-compose.yml -f docker-compose.ghcr.yml -f docker-compose.dev.yml up -d
else
  echo "==> Building from source (first run can take several minutes)..."
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
fi

WEB_PORT="$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2- || true)"
WEB_PORT="${WEB_PORT:-5173}"

echo ""
echo "==> Kiln is starting."
echo "    Web UI:        http://localhost:${WEB_PORT}"
echo "    API health:    http://localhost:3000/health"
echo "    Agent health:  http://localhost:8000/health"
echo ""
echo "    Dev auth is on (AUTH_MODE=dev). Open the UI and submit a research question."
echo "    Add at least one LLM key to ${INSTALL_DIR}/.env (GOOGLE_API_KEY recommended),"
echo "    then:  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate agent api"
echo ""
echo "    Install dir: ${INSTALL_DIR}"
echo "    Logs:        docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f"