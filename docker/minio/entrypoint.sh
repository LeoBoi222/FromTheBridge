#!/bin/sh
# D1-compliant MinIO entrypoint — reads credentials from bind-mounted secrets.
# No credentials in environment variables, docker-compose.yml, or docker inspect output.
set -eu

MINIO_ROOT_USER=$(cat /run/secrets/minio_root_key)
MINIO_ROOT_PASSWORD=$(cat /run/secrets/minio_root_secret)
MINIO_DOMAIN=minio
export MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_DOMAIN

exec minio server /data --address ":9001" --console-address ":9002"
