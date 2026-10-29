#!/bin/sh
# Runs once, when the database volume is first created (Postgres image initdb hook).
# The app gets its own login that owns the app database, but is not a superuser.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}';
	ALTER DATABASE ${POSTGRES_DB} OWNER TO ${APP_DB_USER};
	ALTER SCHEMA public OWNER TO ${APP_DB_USER};
EOSQL
