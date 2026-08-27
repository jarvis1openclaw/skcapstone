#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 \
  --set=app_password="$SKLEGAL_APP_PASSWORD" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --file /docker-entrypoint-initdb.d/core.sql.tmpl
