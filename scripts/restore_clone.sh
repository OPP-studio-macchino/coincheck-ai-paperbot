#!/usr/bin/env bash
# Restore encrypted archive into a NEW, empty clone DB only. Never overwrite paperbot.
set -euo pipefail
cd "$(dirname "$0")/.."
if (( $# != 2 )); then echo "Usage: $0 backups/file.dump.age paperbot_restore_<id>" >&2; exit 2; fi
archive="$1"; clone="$2"
if [[ ! "$clone" =~ ^paperbot_restore_[a-z0-9_]{1,24}$ ]]; then
  echo "STOP: target must be a separate paperbot_restore_* database" >&2; exit 2
fi
if [[ ! -s "$archive" || ! -f "${archive}.sha256" ]]; then
  echo "STOP: missing archive/checksum" >&2; exit 2
fi
identity="${BACKUP_IDENTITY_FILE:-}"
if [[ -z "$identity" || ! -r "$identity" ]]; then echo "STOP: offline restore identity path required" >&2; exit 2; fi
if [[ "$identity" == "$(pwd)"/* ]]; then echo "STOP: restore key must be outside project tree" >&2; exit 2; fi
shasum -a 256 -c "${archive}.sha256" >/dev/null || { echo "STOP: integrity mismatch" >&2; exit 2; }
docker-compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$clone"
age -d -i "$identity" "$archive" |
  docker-compose exec -T db sh -c 'pg_restore --no-owner --no-acl -U "$POSTGRES_USER" -d "$1"' sh "$clone"
docker-compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$1" -Atc "SELECT count(*) FROM portfolios"' sh "$clone"
echo "CLONE_RESTORE_DONE target=$clone (existing primary DB untouched)"
