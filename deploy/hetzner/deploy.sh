#!/usr/bin/env bash
# Deploy The Star Experience to the Hetzner server. Run from Git Bash on the dev PC:
#
#   bash deploy/hetzner/deploy.sh
#
# Packs the backend and the frontend (sibling folder ../TheStarExperience), uploads them,
# swaps them into /opt/the-star/src, then rebuilds and restarts The Star's containers.
# Only /opt/the-star and the "the-star" compose project are touched; other apps on the
# server are left alone. Secrets (.env, backend.env) must already exist on the server.
set -euo pipefail

HOST="${STAR_HOST:-deploy@2.29.117.211}"
APP=/opt/the-star
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
FRONTEND="$(cd "$BACKEND/../TheStarExperience" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Packing code..."
tar -czf "$WORK/backend.tgz" -C "$BACKEND" --exclude=__pycache__ \
  Dockerfile .dockerignore requirements.txt app create_admin.py seed.py
tar -czf "$WORK/frontend.tgz" -C "$FRONTEND" \
  --exclude=./node_modules --exclude=./.next --exclude='./.env*' --exclude='*.tsbuildinfo' .
cp "$HERE/docker-compose.yml" "$HERE/Caddyfile" "$HERE/db-init.sh" "$HERE/backup.sh" "$HERE/copy-from-supabase.sh" "$WORK/"

echo "Uploading to $HOST..."
ssh "$HOST" "rm -rf /tmp/the-star-upload && mkdir -p /tmp/the-star-upload"
scp -q "$WORK"/*.tgz "$WORK/docker-compose.yml" "$WORK/Caddyfile" "$WORK"/*.sh "$HOST:/tmp/the-star-upload/"

echo "Building and starting on the server..."
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
APP=/opt/the-star
UP=/tmp/the-star-upload
[ -d "$APP" ] || { sudo mkdir -p "$APP" && sudo chown "$(id -u):$(id -g)" "$APP" && chmod 750 "$APP"; }
for f in .env backend.env; do
  [ -f "$APP/$f" ] || { echo "Missing $APP/$f: create it first (see deploy/hetzner/*.example)"; exit 1; }
done
for v in STAR_DOMAIN TUNNEL_TOKEN POSTGRES_PASSWORD APP_DB_PASSWORD; do
  grep -Eq "^$v=.+" "$APP/.env" || { echo "Set $v in $APP/.env before deploying"; exit 1; }
done

rm -rf "$APP/src.new" && mkdir -p "$APP/src.new/backend" "$APP/src.new/frontend"
tar -xzf "$UP/backend.tgz" -C "$APP/src.new/backend"
tar -xzf "$UP/frontend.tgz" -C "$APP/src.new/frontend"
rm -rf "$APP/src.old"
[ -d "$APP/src" ] && mv "$APP/src" "$APP/src.old"
mv "$APP/src.new" "$APP/src"
cp "$UP/docker-compose.yml" "$UP/Caddyfile" "$UP/db-init.sh" "$UP/backup.sh" "$UP/copy-from-supabase.sh" "$APP/"
chmod 755 "$APP/db-init.sh" "$APP/backup.sh" "$APP/copy-from-supabase.sh"
rm -rf "$UP"

cd "$APP"
docker compose build
docker compose up -d --remove-orphans
sleep 5
docker compose ps
REMOTE

echo "Done."
