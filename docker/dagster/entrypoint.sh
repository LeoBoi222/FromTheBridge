#!/bin/sh
# D1-compliant Dagster entrypoint — builds PG URL from bind-mounted secrets.
# No credentials in environment variables, docker-compose.yml, or docker inspect output.
set -eu

DAGSTER_PG_PASS=$(cat /run/secrets/dagster_pg_password)
export DAGSTER_PG_URL="postgresql://dagster_user:${DAGSTER_PG_PASS}@empire_postgres:5432/dagster"

exec "$@"
