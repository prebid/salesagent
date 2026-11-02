#!/usr/bin/env bash
# Logical backup of the sales agent database into ./backups, keeping KEEP_DAYS days (default 14).
# Guide: docs/deployment/vm.md. Restore:
#   docker compose stop app
#   gunzip -c backups/salesagent-<UTC>.sql.gz | docker compose exec -T postgres psql -q -U salesagent -d salesagent
#   docker compose start app
# A dump is useless without the .env that encrypted it: keep a copy of .env apart from the dumps.
set -euo pipefail
cd "$(dirname "$0")"
umask 077
mkdir -p backups
f="backups/salesagent-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
docker compose exec -T postgres pg_dump -U salesagent -d salesagent --no-owner --clean --if-exists | gzip > "$f"
find backups -name 'salesagent-*.sql.gz' -mtime +"${KEEP_DAYS:-14}" -delete
echo "$f"
