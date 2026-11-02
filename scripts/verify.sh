#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -q
if docker compose version >/dev/null 2>&1; then
  docker compose config --quiet
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose config --quiet
else
  echo 'Docker Compose unavailable: source tests only' >&2
fi
