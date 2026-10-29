#!/usr/bin/env bash
# Copy The Star's data from Supabase into this server's database (the "db" service).
# Run on the server from /opt/the-star:
#
#   ./copy-from-supabase.sh /path/to/supabase-url.txt
#
# The file holds the Supabase Postgres URL (it is never printed). The app's tables in
# the target are REPLACED, so stop the backend first at go-live:
#   docker compose stop backend
# Supabase itself is only read, never changed. A copy of the dump is kept in backups/.
set -euo pipefail
cd "$(dirname "$0")"

URL_FILE="${1:?usage: ./copy-from-supabase.sh /path/to/supabase-url.txt}"
mkdir -p backups && chmod 700 backups
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="backups/supabase-$STAMP.dump"
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
chmod 600 "$ENV_FILE"
# pg_dump wants a plain postgresql:// URL (the app's settings use postgresql+psycopg2://).
printf 'SRC=%s\n' "$(sed -e 's#^postgresql+psycopg2://#postgresql://#' -e 's#^postgres://#postgresql://#' "$URL_FILE" | tr -d '\r\n')" > "$ENV_FILE"

echo "Dumping The Star's tables from Supabase (read only)..."
docker run --rm --env-file "$ENV_FILE" postgres:17-alpine \
  sh -c 'pg_dump "$SRC" --schema=public --no-owner --no-privileges --format=custom' > "$DUMP"
chmod 600 "$DUMP"
echo "  saved $DUMP ($(du -h "$DUMP" | cut -f1))"

echo "Loading into this server's database..."
# The dump recreates the public schema itself (as star_app, which owns the database).
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d the_star -q -c "DROP SCHEMA IF EXISTS public CASCADE;"
docker compose exec -T db pg_restore -U star_app -d the_star --no-owner --no-privileges --exit-on-error < "$DUMP"

echo "Comparing row counts..."
COUNT_SQL="select string_agg(t, ' ' order by t) from (select relname || '=' || (xpath('/row/c/text()', query_to_xml('select count(*) as c from public.' || quote_ident(relname), false, true, '')))[1]::text as t from pg_class where relkind = 'r' and relnamespace = 'public'::regnamespace) s"
SOURCE=$(docker run --rm --env-file "$ENV_FILE" postgres:17-alpine sh -c "psql \"\$SRC\" -Atc \"$COUNT_SQL\"")
TARGET=$(docker compose exec -T db psql -U star_app -d the_star -Atc "$COUNT_SQL")
echo "  Supabase: $SOURCE"
echo "  Hetzner:  $TARGET"
if [ "$SOURCE" = "$TARGET" ]; then echo "All tables match."; else echo "MISMATCH: check before switching over." >&2; exit 1; fi
