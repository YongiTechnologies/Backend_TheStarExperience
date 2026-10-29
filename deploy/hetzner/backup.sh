#!/usr/bin/env bash
# Nightly backup of The Star's database. Keeps 14 days in /opt/the-star/backups.
# Installed in the deploy user's crontab by setup (07:00, after the night's service):
#   0 7 * * * /opt/the-star/backup.sh >> /opt/the-star/backups/backup.log 2>&1
#
# Restore a backup (replaces the current data; stop the backend first):
#   docker compose stop backend
#   docker compose exec -T db psql -U postgres -d the_star -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION star_app;"
#   docker compose exec -T db pg_restore -U star_app -d the_star --no-owner < backups/the-star-YYYYMMDD-HHMM.dump
#   docker compose start backend
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups && chmod 700 backups

FILE="backups/the-star-$(date +%Y%m%d-%H%M).dump"
docker compose exec -T db pg_dump -U star_app -d the_star --format=custom > "$FILE.partial"
mv "$FILE.partial" "$FILE"
chmod 600 "$FILE"
find backups -name 'the-star-*.dump' -mtime +14 -delete
echo "$(date -Is) backed up $FILE ($(du -h "$FILE" | cut -f1))"
