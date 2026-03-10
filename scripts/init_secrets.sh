#!/bin/bash
# Initialize secrets directory with generated credentials.
# Run once on proxmox during first deployment. Idempotent — skips existing files.
# Usage: bash scripts/init_secrets.sh
set -euo pipefail

SECRETS_DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
mkdir -p "$SECRETS_DIR/external_apis"
chmod 700 "$SECRETS_DIR" "$SECRETS_DIR/external_apis"

gen_secret() {
    local file="$1"
    if [ -f "$file" ]; then
        echo "  skip (exists): $file"
        return
    fi
    openssl rand -base64 32 | tr -d '\n' > "$file"
    chmod 600 "$file"
    echo "  created: $file"
}

echo "Initializing secrets in $SECRETS_DIR"

# MinIO root (operator only — never mounted in app containers)
gen_secret "$SECRETS_DIR/minio_root_key.txt"
gen_secret "$SECRETS_DIR/minio_root_secret.txt"

# MinIO service accounts (created inside MinIO after first boot)
gen_secret "$SECRETS_DIR/minio_bronze_key.txt"
gen_secret "$SECRETS_DIR/minio_bronze_secret.txt"
gen_secret "$SECRETS_DIR/minio_gold_key.txt"
gen_secret "$SECRETS_DIR/minio_gold_secret.txt"
gen_secret "$SECRETS_DIR/minio_export_key.txt"
gen_secret "$SECRETS_DIR/minio_export_secret.txt"
gen_secret "$SECRETS_DIR/minio_marts_key.txt"
gen_secret "$SECRETS_DIR/minio_marts_secret.txt"

# Dagster metadata DB
gen_secret "$SECRETS_DIR/dagster_pg_password.txt"

# PostgreSQL forge credentials (already exist if Phase 0 deployed)
gen_secret "$SECRETS_DIR/pg_forge_user.txt"
gen_secret "$SECRETS_DIR/pg_forge_reader.txt"

# External API keys — create empty placeholders
for source in tiingo coinalyze sosovalue etherscan coinpaprika coinmetrics bgeometrics defillama; do
    file="$SECRETS_DIR/external_apis/${source}.txt"
    if [ ! -f "$file" ]; then
        touch "$file"
        chmod 600 "$file"
        echo "  placeholder: $file"
    fi
done

echo ""
echo "Done. Next steps:"
echo "  1. Set MinIO root key to a memorable username: echo -n 'minio_admin' > $SECRETS_DIR/minio_root_key.txt"
echo "  2. Add real API keys to secrets/external_apis/*.txt"
echo "  3. Run the Dagster DB migration: scripts/init_dagster_db.sh"
