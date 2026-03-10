#!/bin/bash
# Create the Dagster metadata database and user in empire_postgres.
# Run once on proxmox. Idempotent.
# Usage: bash scripts/init_dagster_db.sh
set -euo pipefail

SECRETS_DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
DAGSTER_PASS=$(cat "$SECRETS_DIR/dagster_pg_password.txt")

docker exec -i empire_postgres psql -U crypto_user -d crypto_structured <<SQL
-- Create dagster database if not exists
SELECT 'CREATE DATABASE dagster' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dagster')\gexec

-- Create dagster_user role if not exists
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dagster_user') THEN
        CREATE ROLE dagster_user WITH LOGIN PASSWORD '${DAGSTER_PASS}';
    ELSE
        ALTER ROLE dagster_user WITH PASSWORD '${DAGSTER_PASS}';
    END IF;
END
\$\$;
SQL

# Grant ownership of dagster database to dagster_user
docker exec -i empire_postgres psql -U crypto_user -d dagster <<SQL
GRANT ALL PRIVILEGES ON DATABASE dagster TO dagster_user;
GRANT ALL PRIVILEGES ON SCHEMA public TO dagster_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO dagster_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO dagster_user;
SQL

echo "Dagster database ready. User: dagster_user, DB: dagster"
