#!/usr/bin/env bash
# Encrypted PostgreSQL consistent backup, never writes plaintext dump to disk.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
if ! command -v age >/dev/null 2>&1; then echo "STOP: age CLI required" >&2; exit 2; fi
if ! command -v docker-compose >/dev/null 2>&1; then echo "STOP: Docker Compose required" >&2; exit 2; fi
if [[ ! -f secrets/backup_recipient.pub ]]; then echo "STOP: recipient not initialized" >&2; exit 2; fi
recipient="$(cat secrets/backup_recipient.pub)"
if [[ ! "$recipient" =~ ^age1[a-z0-9]{30,}$ ]]; then echo "STOP: invalid public recipient" >&2; exit 2; fi
mkdir -p backups
chmod 700 backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="backups/paperbot_${stamp}.dump.age"
temp="${out}.partial"
if [[ -e "$out" || -e "$temp" ]]; then echo "STOP: backup name collision" >&2; exit 2; fi
trap 'rm -f "$temp"' EXIT
docker-compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' | age -r "$recipient" -o "$temp"
test -s "$temp"
chmod 600 "$temp"
mv "$temp" "$out"
trap - EXIT
shasum -a 256 "$out" > "${out}.sha256"
chmod 600 "${out}.sha256"
echo "ENCRYPTED_BACKUP_OK $out (off-VPS copy is a separate required step)"
