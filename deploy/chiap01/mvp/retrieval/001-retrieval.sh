#!/bin/sh
set -eu

{
  sed '/^-- sklegal:down$/,$d' \
    /opt/sklegal/retrieval-migrations/0001_governed_corpus_projection.sql
} | psql --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB"

psql --set=ON_ERROR_STOP=1 \
  --set=app_password="$SKLEGAL_APP_PASSWORD" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --file /docker-entrypoint-initdb.d/retrieval.sql.tmpl
