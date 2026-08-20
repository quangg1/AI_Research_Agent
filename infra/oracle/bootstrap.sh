#!/usr/bin/env bash
# Run once on a fresh Oracle Ubuntu ARM VM (as a sudo-capable user).
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run as ubuntu (or another sudo user), not as root."
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl git

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. Log out and back in (or: newgrp docker), then re-run compose."
fi

docker compose version
echo "Ready. Next: clone the repo, copy .env.oracle.example to .env, then:"
echo "  docker compose -f docker-compose.yml -f docker-compose.oracle.yml up -d --build"
