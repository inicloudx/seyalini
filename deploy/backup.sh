#!/usr/bin/env bash
# Daily database backup (server-setup.sh adds it to cron: 03:15). Keeps 14 days of copies in
# ~/seyalini-backups, outside Docker. Videos are not copied: approved ones are already on YouTube.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=$HOME/seyalini-backups
mkdir -p "$DEST"
docker compose exec -T web python -c "
import sqlite3
src, dst = sqlite3.connect('/app/data/db.sqlite3'), sqlite3.connect('/app/data/backup.sqlite3')
src.backup(dst); dst.close(); src.close()"
docker cp seyalini_web:/app/data/backup.sqlite3 "$DEST/db-$(date +%F).sqlite3"
docker compose exec -T web tar -czf /app/data/tenants-backup.tgz -C /app/data tenants
docker cp seyalini_web:/app/data/tenants-backup.tgz "$DEST/tenants-$(date +%F).tgz"
find "$DEST" -name 'db-*.sqlite3' -mtime +14 -delete
find "$DEST" -name 'tenants-*.tgz' -mtime +14 -delete
