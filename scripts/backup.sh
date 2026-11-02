#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p runtime
out="runtime/paperbot_backup_$(date -u +%Y%m%dT%H%M%SZ).sql"
if command -v docker-compose >/dev/null 2>&1; then
  docker-compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$out"
else
  docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$out"
fi
chmod 600 "$out"
printf 'Backup written to %s (keep private)\n' "$out"
