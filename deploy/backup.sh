#!/usr/bin/env bash
# Daily backup (cron as seyalini):  30 2 * * * /opt/seyalini/app/deploy/backup.sh
# Keeps 14 days of database copies + the tenants folder. Approved videos stay in data/media.
set -euo pipefail
DEST=/opt/seyalini/backups/$(date +%F)
mkdir -p "$DEST"
sqlite3 /opt/seyalini/data/db.sqlite3 ".backup '$DEST/db.sqlite3'"
tar -czf "$DEST/tenants.tgz" -C /opt/seyalini/app tenants
find /opt/seyalini/backups -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
