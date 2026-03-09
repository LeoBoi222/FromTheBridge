# RESULT: D1 — Security Posture Baseline
## FromTheBridge — Empire Architecture v2.0

**Date:** 2026-03-06
**Status:** LOCKED — Architect Approved
**Phase gate:** Phase 0 corrective action (deploy before Phase 1 begins)
**Depends on:** `thread_infrastructure.md`, D2 Entitlement Model
**Feeds into:** `db/migrations/clickhouse/0002_credential_isolation.sql`, `docker-compose.yml` secrets mounts, Phase 5 serving layer (API key lifecycle), Phase 6 ToS audit gate

---

## SCOPE

This document is the minimum viable security implementation required before any external customer API key is issued. It is a practical, implementable specification for a solo operator on self-hosted infrastructure. It covers eight domains: secrets management, credential isolation, encryption posture, customer API key lifecycle, external API key management, rotation policy, incident response playbooks, and Cloudflare Zero Trust configuration.

All decisions in this document are locked. The thirteen open assumptions from the draft review are resolved below. Implementation sessions reference section numbers here.

---

## RESOLVED DECISIONS (from Draft Review)

| # | Assumption | Resolution |
|---|------------|------------|
| 1 | `forge.customers` table existence | Assume Phase 5 D2 defines it. `customer_id` column is `TEXT NOT NULL` with no FK at v1. FK added as non-destructive `ALTER TABLE` migration when D2's `forge.customers` is confirmed deployed. |
| 2 | ClickHouse `default` user | Suspend. Empire stack uses named users per `thread_infrastructure.md`. |
| 3 | `crypto_user` vs. `forge_user` | `crypto_user` is operator-only for migration DDL runs. Never mounted in any service container. |
| 4 | `argon2-cffi` availability | Add `argon2-cffi>=23.1.0` to `pyproject.toml` for both FastAPI serving layer and Dagster code server. |
| 5 | MinIO service accounts | Implement per-policy model: `bronze_writer` and `gold_reader` service accounts. Root key never mounted in service containers. |
| 6 | GPG passphrase storage | Confirmed: 1Password. Passphrase never written to any machine in the infrastructure. |
| 7 | Dagster port 3010 host binding | Internal-only. Not published to host interface. Add Cloudflare Zero Trust for Dagster if remote access is needed (Phase 6 operational improvement, not v1 gate). |
| 8 | MinIO console port 9002 host binding | Internal-only. Not published to host interface. All MinIO administration via `mc` CLI over SSH. |
| 9 | `forge_user` write scope | Single role with full schema ownership is acceptable at v1. Add separate `forge_migrator` role (DDL rights only, used for migration runs) at Phase 6. |
| 10 | API key delivery channel | 1Password secure share (one-time-view link). Never email plaintext. |
| 11 | `key_prefix` length | **12 characters** (e.g., `ftb_a8Xk2mPq`). Stores first 12 chars of plaintext key — `ftb_` prefix plus 8 chars of token entropy — for log identification without brute-force risk. |
| 12 | `ch_admin` password handling | Interactive terminal only. Password pasted at the terminal during migration window. Never written to disk on proxmox. |
| 13 | Annual rotation window | March (last week of Q1). First rotation: March 2027. |

---

## 1. Secrets Management

### Philosophy

v1 secrets management uses three primitives only: a restricted `secrets/` directory for sensitive credentials injected as Docker bind mounts, an `.env` file for non-sensitive configuration, and no secrets anywhere in `docker-compose.yml`. This is intentionally cloud-migration compatible — when managed services trigger, the `secrets/` directory is replaced by provider-specific secret injection (AWS Secrets Manager, HashiCorp Vault) with zero application code changes.

### Directory Structure and File Permissions

```
/opt/empire/FromTheBridge/
├── docker-compose.yml           # No secrets. Service topology and volume mounts only.
├── .env                         # chmod 644. Non-sensitive config only. No credentials.
├── secrets/                     # chmod 700, chown root:root
│   ├── pg_forge_user.txt        # PostgreSQL forge_user password
│   ├── pg_forge_reader.txt      # PostgreSQL forge_reader password
│   ├── ch_writer.txt            # ClickHouse ch_writer password
│   ├── ch_export_reader.txt     # ClickHouse ch_export_reader password
│   ├── minio_root_key.txt       # MinIO root access key (admin — operator use only)
│   ├── minio_root_secret.txt    # MinIO root secret key (admin — operator use only)
│   ├── minio_bronze_key.txt     # MinIO bronze_writer service account access key
│   ├── minio_bronze_secret.txt  # MinIO bronze_writer service account secret key
│   ├── minio_gold_key.txt       # MinIO gold_reader service account access key
│   ├── minio_gold_secret.txt    # MinIO gold_reader service account secret key
│   ├── minio_export_key.txt     # MinIO export_writer service account access key
│   ├── minio_export_secret.txt  # MinIO export_writer service account secret key
│   ├── cf_tunnel_token.txt      # Cloudflare tunnel token
│   └── external_apis/           # chmod 700, chown root:root
│       ├── tiingo.txt           # Tiingo API key
│       ├── coinalyze.txt        # Coinalyze API key
│       ├── sosovalve.txt        # SoSoValue API key
│       ├── etherscan.txt        # Etherscan API key
│       ├── coinpaprika.txt      # CoinPaprika API key
│       ├── coinmetrics.txt      # CoinMetrics API key (GitHub token if applicable)
│       ├── bgeometrics.txt      # BGeometrics API key
│       └── defillama.txt        # DeFiLlama (public API — empty file, present for uniformity)
```

**Per-file rules:**
- `chmod 600`, `chown root:root`
- Single line, raw credential value only
- No `KEY=VALUE` formatting, no comments, no trailing newline
- `secrets/` directory itself: `chmod 700`
- `secrets/external_apis/` subdirectory: `chmod 700`

### `.env` Contents (non-sensitive only)

```dotenv
# Service ports
POSTGRES_PORT=5433
CLICKHOUSE_HTTP_PORT=8123
CLICKHOUSE_NATIVE_PORT=9000
MINIO_API_PORT=9001
MINIO_CONSOLE_PORT=9002
DAGSTER_WEBSERVER_PORT=3010

# Database and schema names
POSTGRES_DB=crypto_structured
FORGE_SCHEMA=forge
CLICKHOUSE_DB=forge

# Service hostnames (Docker internal DNS)
POSTGRES_HOST=empire_postgres
CLICKHOUSE_HOST=empire_clickhouse
MINIO_HOST=empire_minio
DAGSTER_HOST=empire_dagster_webserver

# MinIO endpoint (no credentials here)
MINIO_ENDPOINT=http://empire_minio:9001

# Log levels
LOG_LEVEL=INFO
DAGSTER_LOG_LEVEL=INFO

# Feature flags
ENABLE_DEAD_LETTER_LOGGING=true
SILVER_EXPORT_INTERVAL_HOURS=6
BRONZE_RETENTION_DAYS=90
```

### Secrets Directory Initialization Script

Run once on proxmox before any service is started. Creates the directory structure with correct permissions and populates placeholder files. The operator replaces placeholder values before the first `docker compose up`.

```bash
#!/bin/bash
# scripts/init_secrets.sh
# Run as root on proxmox. Execute once before first docker compose up.
# Replaces all placeholder values with real credentials before use.

set -euo pipefail

SECRETS_DIR="/opt/empire/FromTheBridge/secrets"

# Create directories
mkdir -p "${SECRETS_DIR}/external_apis"
chmod 700 "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}/external_apis"
chown -R root:root "${SECRETS_DIR}"

# Create placeholder files
files=(
  "pg_forge_user.txt"
  "pg_forge_reader.txt"
  "ch_writer.txt"
  "ch_export_reader.txt"
  "minio_root_key.txt"
  "minio_root_secret.txt"
  "minio_bronze_key.txt"
  "minio_bronze_secret.txt"
  "minio_gold_key.txt"
  "minio_gold_secret.txt"
  "minio_export_key.txt"
  "minio_export_secret.txt"
  "cf_tunnel_token.txt"
  "external_apis/tiingo.txt"
  "external_apis/coinalyze.txt"
  "external_apis/sosovalve.txt"
  "external_apis/etherscan.txt"
  "external_apis/coinpaprika.txt"
  "external_apis/coinmetrics.txt"
  "external_apis/bgeometrics.txt"
  "external_apis/defillama.txt"
)

for f in "${files[@]}"; do
  if [ ! -f "${SECRETS_DIR}/${f}" ]; then
    echo -n "REPLACE_ME" > "${SECRETS_DIR}/${f}"
    chmod 600 "${SECRETS_DIR}/${f}"
    chown root:root "${SECRETS_DIR}/${f}"
    echo "Created: ${SECRETS_DIR}/${f}"
  else
    echo "Skipped (exists): ${SECRETS_DIR}/${f}"
  fi
done

# Verify
echo ""
echo "Secrets directory initialized. Replace all REPLACE_ME values before docker compose up."
echo "Run: grep -r 'REPLACE_ME' ${SECRETS_DIR} to confirm no placeholders remain."
```

### Docker Compose Injection Pattern

Secrets are injected as read-only bind mounts. Services read credential values from mounted file paths at startup via `read_secret()`. Credentials never appear in environment variables, `docker inspect` output, Dagster's environment logging, or process environment dumps.

**docker-compose.yml pattern (excerpt — full file built in Phase 1):**

```yaml
services:
  empire_dagster_code:
    # Adapters: ch_writer (INSERT only), pg_forge_reader (SELECT only),
    #           bronze_writer MinIO, external API keys
    volumes:
      - /opt/empire/FromTheBridge/secrets/ch_writer.txt:/run/secrets/ch_writer:ro
      - /opt/empire/FromTheBridge/secrets/pg_forge_reader.txt:/run/secrets/pg_forge_reader:ro
      - /opt/empire/FromTheBridge/secrets/minio_bronze_key.txt:/run/secrets/minio_bronze_key:ro
      - /opt/empire/FromTheBridge/secrets/minio_bronze_secret.txt:/run/secrets/minio_bronze_secret:ro
      - /opt/empire/FromTheBridge/secrets/external_apis:/run/secrets/external_apis:ro
    # Note: ch_export_reader and minio_export_* are NOT mounted here.
    # They are mounted only on the export asset's dedicated config.
    # See: dagster_export_asset service definition.

  empire_dagster_export:
    # Export asset only: ch_export_reader (SELECT only), export_writer MinIO
    volumes:
      - /opt/empire/FromTheBridge/secrets/ch_export_reader.txt:/run/secrets/ch_export_reader:ro
      - /opt/empire/FromTheBridge/secrets/minio_export_key.txt:/run/secrets/minio_export_key:ro
      - /opt/empire/FromTheBridge/secrets/minio_export_secret.txt:/run/secrets/minio_export_secret:ro
      - /opt/empire/FromTheBridge/secrets/pg_forge_reader.txt:/run/secrets/pg_forge_reader:ro
    # Note: ch_writer is NOT mounted here. Export asset reads Silver, never writes it.

  # Dagster webserver and daemon: pg_forge_reader only (metadata + catalog reads)
  empire_dagster_webserver:
    ports: []  # Not published to host. Internal Docker network only.
    volumes:
      - /opt/empire/FromTheBridge/secrets/pg_forge_reader.txt:/run/secrets/pg_forge_reader:ro

  empire_dagster_daemon:
    volumes:
      - /opt/empire/FromTheBridge/secrets/pg_forge_reader.txt:/run/secrets/pg_forge_reader:ro

  empire_minio:
    ports: []  # 9001 and 9002 NOT published to host. Internal Docker network only.
    environment:
      # MinIO root credentials are an exception: MinIO requires env vars at init.
      # These are the ONLY credentials that go in environment, not bind mounts.
      # MinIO reads these at first-run only to initialize the root account.
      MINIO_ROOT_USER_FILE: /run/secrets/minio_root_key
      MINIO_ROOT_PASSWORD_FILE: /run/secrets/minio_root_secret
    volumes:
      - /opt/empire/FromTheBridge/secrets/minio_root_key.txt:/run/secrets/minio_root_key:ro
      - /opt/empire/FromTheBridge/secrets/minio_root_secret.txt:/run/secrets/minio_root_secret:ro
      - /mnt/empire-data/minio:/data
```

**Application secret reader utility (shared across all Python services):**

```python
# src/fromthebridge/core/secrets.py
from functools import lru_cache
from pathlib import Path

_SECRETS_BASE = Path("/run/secrets")

@lru_cache(maxsize=64)
def read_secret(name: str) -> str:
    """
    Read a credential from the Docker secrets mount.
    Results are cached — reads the file once per process lifetime.
    Raises FileNotFoundError if the secret is not mounted (fail-fast at startup).
    """
    path = _SECRETS_BASE / name
    return path.read_text().strip()

def read_external_api_key(source_name: str) -> str:
    """Convenience wrapper for external API keys."""
    return read_secret(f"external_apis/{source_name}")
```

### Backup and Recovery

Secrets backup is run by the operator after any credential rotation and on the first-of-month schedule.

```bash
#!/bin/bash
# scripts/backup_secrets.sh
# Run as root on proxmox. Requires gpg and ssh access to NAS.
# GPG passphrase stored in 1Password only — never written to any machine.

set -euo pipefail

SECRETS_DIR="/opt/empire/FromTheBridge/secrets"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE="/tmp/ftb_secrets_${TIMESTAMP}.tar.gz"
ENCRYPTED="${ARCHIVE}.gpg"
NAS_DEST="root@192.168.68.91:/backups/fromthebridge/secrets/"

# Compress
tar -czf "${ARCHIVE}" -C "$(dirname ${SECRETS_DIR})" "$(basename ${SECRETS_DIR})"

# Encrypt with GPG symmetric (AES256). Operator enters passphrase from 1Password.
gpg --symmetric \
    --cipher-algo AES256 \
    --batch \
    --passphrase-fd 0 \
    --output "${ENCRYPTED}" \
    "${ARCHIVE}" <<< "$(read -rsp 'GPG passphrase: ' p; echo $p)"

# Transfer to NAS (rsync over SSH, key auth only)
rsync -av \
    -e "ssh -i /root/.ssh/nas_backup_key -o StrictHostKeyChecking=yes" \
    "${ENCRYPTED}" "${NAS_DEST}"

# Clean up plaintext archive
rm "${ARCHIVE}" "${ENCRYPTED}"

echo "Secrets backup complete: ${TIMESTAMP}"
```

Recovery:
```bash
# Retrieve from NAS, decrypt, extract
scp -i /root/.ssh/nas_backup_key \
    root@192.168.68.91:/backups/fromthebridge/secrets/ftb_secrets_<TIMESTAMP>.tar.gz.gpg \
    /tmp/

gpg --decrypt /tmp/ftb_secrets_<TIMESTAMP>.tar.gz.gpg | tar -xzf - \
    -C /opt/empire/FromTheBridge/
```

---

## 2. Credential Isolation Matrix

### Complete Service Credential Map

| Service | Credential | Target | Access Level | Injection Method |
|---------|-----------|--------|--------------|-----------------|
| empire_dagster_code (adapters) | `ch_writer` | ClickHouse `forge.*` | INSERT on `observations`, `dead_letter` only. No SELECT. | Bind mount `/run/secrets/ch_writer` |
| empire_dagster_code (adapters) | `pg_forge_reader` | PostgreSQL `forge.*` | SELECT only (metric_catalog, source_catalog, instruments, metric_lineage) | Bind mount `/run/secrets/pg_forge_reader` |
| empire_dagster_code (adapters) | `minio_bronze_key/secret` | MinIO `bronze` bucket | PutObject only | Bind mount `/run/secrets/minio_bronze_*` |
| empire_dagster_code (adapters) | External API keys | Tiingo, Coinalyze, etc. | Per-provider read-only API access | Bind mount `/run/secrets/external_apis/` |
| empire_dagster_export (export asset) | `ch_export_reader` | ClickHouse `forge.*` | SELECT on `observations`, `dead_letter`, `current_values`. No INSERT. | Bind mount `/run/secrets/ch_export_reader` — this service only |
| empire_dagster_export (export asset) | `minio_export_key/secret` | MinIO `gold` bucket | PutObject + GetObject + ListBucket | Bind mount `/run/secrets/minio_export_*` |
| empire_dagster_export (export asset) | `pg_forge_reader` | PostgreSQL `forge.*` | SELECT only | Bind mount `/run/secrets/pg_forge_reader` |
| forge_compute / dbt | `minio_gold_key/secret` | MinIO `gold` bucket | GetObject + ListBucket only. No PutObject. | Bind mount `/run/secrets/minio_gold_*` |
| forge_compute / dbt | `pg_forge_reader` | PostgreSQL `forge.*` | SELECT only (feature catalog) | Bind mount `/run/secrets/pg_forge_reader` |
| empire_dagster_webserver | `pg_forge_reader` | PostgreSQL `forge.*` | SELECT only (run history, catalog display) | Bind mount `/run/secrets/pg_forge_reader` |
| empire_dagster_daemon | `pg_forge_reader` | PostgreSQL `forge.*` | SELECT only (schedule state) | Bind mount `/run/secrets/pg_forge_reader` |
| empire_api (Phase 5+) | `pg_forge_reader` | PostgreSQL `forge.*` | SELECT only (api_keys, customer tier) | Bind mount `/run/secrets/pg_forge_reader` |
| empire_api (Phase 5+) | `minio_gold_key/secret` | MinIO `gold` + `marts` | GetObject + ListBucket only | Bind mount `/run/secrets/minio_gold_*` |
| empire_minio (init) | `minio_root_key/secret` | MinIO root admin | Admin (bucket + service account management) | Env var file reference (MinIO requirement) |
| empire_clickhouse (user management) | `ch_admin` | ClickHouse | DDL + admin — operator terminal only | Never mounted in any container |
| empire_postgres (init) | `pg_forge_user` | PostgreSQL | Owner of `forge` schema | Mounted only for migration runs |
| cloudflared | `cf_tunnel_token` | Cloudflare edge | Tunnel authentication only | Bind mount `/run/secrets/cf_tunnel_token` |

**Critical isolation enforcement summary:**
- `ch_export_reader` is mounted in `empire_dagster_export` only. No other service has ClickHouse SELECT credentials.
- `ch_writer` is not mounted in `empire_dagster_export`. The export asset reads Silver and writes Gold via MinIO — it never writes back to ClickHouse.
- MinIO root credentials are not mounted in any adapter or compute service.
- `ch_admin` never touches the filesystem on proxmox — operator terminal use only.

### ClickHouse Credential Isolation DDL

**File:** `db/migrations/clickhouse/0002_credential_isolation.sql`

```sql
-- ============================================================
-- ClickHouse Credential Isolation
-- FromTheBridge — Phase 0 Corrective Action
-- File: db/migrations/clickhouse/0002_credential_isolation.sql
-- Run AFTER 0001_silver_schema.sql
-- Run AS: ch_admin (operator terminal — password from 1Password)
-- ============================================================

-- Step 1: Suspend the default user.
-- Empire stack confirmed to use named users per thread_infrastructure.md.
-- If this breaks any existing connection, stop immediately and investigate
-- before proceeding.
ALTER USER default ACCOUNT SUSPEND;

-- Step 2: ch_writer — collection adapters (all Dagster assets except export)
-- INSERT-only on observation tables. Zero SELECT. Zero DDL.
CREATE USER IF NOT EXISTS ch_writer
    IDENTIFIED WITH sha256_password BY '${CH_WRITER_PASSWORD}';

-- Grant INSERT on Silver observation tables only
GRANT INSERT ON forge.observations TO ch_writer;
GRANT INSERT ON forge.dead_letter TO ch_writer;

-- Explicitly revoke SELECT to make intent unambiguous
-- (ClickHouse denies by default, but explicit state is documentation)
REVOKE SELECT ON forge.observations FROM ch_writer;
REVOKE SELECT ON forge.dead_letter FROM ch_writer;
REVOKE SELECT ON forge.current_values FROM ch_writer;

-- Step 3: ch_export_reader — Silver→Gold export asset ONLY
-- SELECT-only on all forge tables. Zero INSERT. Zero DDL.
CREATE USER IF NOT EXISTS ch_export_reader
    IDENTIFIED WITH sha256_password BY '${CH_EXPORT_READER_PASSWORD}';

GRANT SELECT ON forge.observations TO ch_export_reader;
GRANT SELECT ON forge.dead_letter TO ch_export_reader;
GRANT SELECT ON forge.current_values TO ch_export_reader;

REVOKE INSERT ON forge.observations FROM ch_export_reader;
REVOKE INSERT ON forge.dead_letter FROM ch_export_reader;

-- Step 4: ch_admin — operator maintenance only, never mounted in containers
CREATE USER IF NOT EXISTS ch_admin
    IDENTIFIED WITH sha256_password BY '${CH_ADMIN_PASSWORD}';

GRANT ALL ON forge.* TO ch_admin WITH GRANT OPTION;
GRANT SYSTEM ON *.* TO ch_admin;

-- ============================================================
-- POST-DEPLOY VERIFICATION CHECKLIST
-- Run after migration. All assertions must pass before Phase 0 closes.
-- ============================================================

-- Assert 1: ch_writer has INSERT only (must return INSERT grants for observations, dead_letter)
SHOW GRANTS FOR ch_writer;
-- Expected: GRANT INSERT ON forge.observations, GRANT INSERT ON forge.dead_letter
-- Must NOT show: SELECT on any table

-- Assert 2: ch_export_reader has SELECT only (must return SELECT grants for 3 tables)
SHOW GRANTS FOR ch_export_reader;
-- Expected: GRANT SELECT ON forge.observations, forge.dead_letter, forge.current_values
-- Must NOT show: INSERT on any table

-- Assert 3: default user is suspended
SELECT name, is_active FROM system.users WHERE name = 'default';
-- Expected: is_active = 0

-- Assert 4: ch_writer cannot SELECT (must return error, not rows)
-- Run as ch_writer user:
-- SELECT count() FROM forge.observations; → Must fail with ACCESS_DENIED

-- Assert 5: ch_export_reader cannot INSERT (must return error)
-- Run as ch_export_reader user:
-- INSERT INTO forge.observations VALUES (...); → Must fail with ACCESS_DENIED
-- ============================================================
```

**Deployment procedure:**

```bash
# On proxmox — substitute actual passwords from 1Password before running
# DO NOT commit this command to git history

export CH_WRITER_PASSWORD="$(cat /opt/empire/FromTheBridge/secrets/ch_writer.txt)"
export CH_EXPORT_READER_PASSWORD="$(cat /opt/empire/FromTheBridge/secrets/ch_export_reader.txt)"
# CH_ADMIN_PASSWORD is entered interactively — not read from disk

cat db/migrations/clickhouse/0002_credential_isolation.sql \
  | envsubst '${CH_WRITER_PASSWORD} ${CH_EXPORT_READER_PASSWORD}' \
  | docker exec -i empire_clickhouse clickhouse-client \
      --user ch_admin \
      --password  \  # operator types admin password interactively
      --multiquery
```

### MinIO Service Account Setup

Run once after MinIO initialization. Creates the three service accounts with bucket-scoped policies, replacing any use of the root key for service-to-service access.

```bash
#!/bin/bash
# scripts/setup_minio_service_accounts.sh
# Run as root on proxmox after MinIO is healthy.

set -euo pipefail

MINIO_ALIAS="local"
MINIO_ENDPOINT="http://localhost:9001"
ROOT_KEY=$(cat /opt/empire/FromTheBridge/secrets/minio_root_key.txt)
ROOT_SECRET=$(cat /opt/empire/FromTheBridge/secrets/minio_root_secret.txt)

BRONZE_KEY=$(cat /opt/empire/FromTheBridge/secrets/minio_bronze_key.txt)
BRONZE_SECRET=$(cat /opt/empire/FromTheBridge/secrets/minio_bronze_secret.txt)
GOLD_KEY=$(cat /opt/empire/FromTheBridge/secrets/minio_gold_key.txt)
GOLD_SECRET=$(cat /opt/empire/FromTheBridge/secrets/minio_gold_secret.txt)
EXPORT_KEY=$(cat /opt/empire/FromTheBridge/secrets/minio_export_key.txt)
EXPORT_SECRET=$(cat /opt/empire/FromTheBridge/secrets/minio_export_secret.txt)

# Configure mc alias
mc alias set ${MINIO_ALIAS} ${MINIO_ENDPOINT} ${ROOT_KEY} ${ROOT_SECRET}

# Create buckets
mc mb --ignore-existing ${MINIO_ALIAS}/bronze
mc mb --ignore-existing ${MINIO_ALIAS}/gold
mc mb --ignore-existing ${MINIO_ALIAS}/marts

# bronze_writer: PutObject on bronze only
cat > /tmp/bronze_write_policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject"],
    "Resource": ["arn:aws:s3:::bronze/*"]
  }]
}
EOF
mc admin policy create ${MINIO_ALIAS} bronze-write-only /tmp/bronze_write_policy.json
mc admin user add ${MINIO_ALIAS} ${BRONZE_KEY} ${BRONZE_SECRET}
mc admin policy attach ${MINIO_ALIAS} bronze-write-only --user ${BRONZE_KEY}

# gold_reader: GetObject + ListBucket on gold and marts (for DuckDB and serving layer)
cat > /tmp/gold_read_policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::gold",
      "arn:aws:s3:::gold/*",
      "arn:aws:s3:::marts",
      "arn:aws:s3:::marts/*"
    ]
  }]
}
EOF
mc admin policy create ${MINIO_ALIAS} gold-read-only /tmp/gold_read_policy.json
mc admin user add ${MINIO_ALIAS} ${GOLD_KEY} ${GOLD_SECRET}
mc admin policy attach ${MINIO_ALIAS} gold-read-only --user ${GOLD_KEY}

# export_writer: PutObject + GetObject on gold (Silver→Gold export asset)
cat > /tmp/export_write_policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"],
    "Resource": [
      "arn:aws:s3:::gold",
      "arn:aws:s3:::gold/*"
    ]
  }]
}
EOF
mc admin policy create ${MINIO_ALIAS} export-write /tmp/export_write_policy.json
mc admin user add ${MINIO_ALIAS} ${EXPORT_KEY} ${EXPORT_SECRET}
mc admin policy attach ${MINIO_ALIAS} export-write --user ${EXPORT_KEY}

# Clean up policy temp files
rm /tmp/bronze_write_policy.json /tmp/gold_read_policy.json /tmp/export_write_policy.json

echo "MinIO service accounts created. Root key is now unused for service-to-service access."
echo "Verify: mc admin user list ${MINIO_ALIAS}"
```

---

## 3. Encryption Posture

### In Transit

**External traffic: client → Cloudflare → FastAPI**

TLS 1.3 enforced at Cloudflare edge. The `cloudflared` daemon establishes an encrypted outbound tunnel to Cloudflare's network — no inbound port exposure. All clients communicate over HTTPS with Cloudflare managing certificate lifecycle (Universal SSL, auto-renewed). No TLS certificate management required on proxmox.

The tunnel segment (Cloudflare edge → proxmox) traverses Cloudflare's internal network, not the public internet. This is an accepted trust boundary. If end-to-end TLS is required in a future audit, add a self-signed cert on `empire_api` behind the tunnel — no client-facing changes required.

**Inter-container traffic: Dagster → ClickHouse, DuckDB → MinIO, adapters → PostgreSQL**

Not encrypted at v1.

Risk acceptance: All Docker services run on a single Docker bridge network on a single physical host. Container-to-container traffic never traverses the public network or a network interface observable from outside the host. The realistic attack surface is host-level compromise — an attacker with host access has access to all inter-container traffic regardless of encryption. In-transit encryption between containers on the same host does not materially reduce this risk.

Effective control: SSH key-only authentication on proxmox, fail2ban, no password auth, no exposed Docker socket. The host-level access control is the meaningful security boundary, not inter-container TLS.

Cloud migration trigger that adds inter-service TLS: first paying customer AND inter-service traffic classified as carrying customer-identifiable data. At that point, services migrate to managed cloud with VPC-level network controls and optional service mesh (AWS App Mesh, Consul Connect).

**Proxmox → NAS backup traffic**

Encrypted in transit via SSH. NAS accepts rsync over SSH only, key-based authentication from proxmox only.

### At Rest

| Storage | Encrypted at v1 | Rationale | Cloud Migration Default |
|---------|----------------|-----------|------------------------|
| PostgreSQL `/mnt/empire-db/postgresql/` | No | Data is catalog metadata only. API keys stored as argon2id hashes — plaintext never persisted. Not PII. Physical theft of a homelab SSD is accepted residual risk. | RDS: AES-256 via KMS, enabled by default. Zero action required. |
| ClickHouse `/mnt/empire-db/clickhouse/` | No | Raw market data. Not PII, not regulated. Same physical risk rationale. | ClickHouse Cloud: storage encryption managed by provider. Zero action required. |
| MinIO `/mnt/empire-data/minio/` | No | Processed market intelligence. Not PII, not regulated. | S3: SSE-S3 (AES-256) enabled as a one-line config change. |
| NAS backup files | **Yes — GPG AES-256** | Backups leave the proxmox security boundary and land on NAS with broader local network access. GPG encryption at rest before transfer. Passphrase in 1Password only. | Backups to S3: SSE-S3 replaces GPG. |

---

## 4. API Key Lifecycle (Customer Keys)

### Dependencies

```
pyproject.toml (FastAPI serving layer and Dagster code server):
  argon2-cffi>=23.1.0
```

### Schema

```sql
-- forge.api_keys
-- Deploy as part of Phase 5 schema migration.
-- Note: customer_id is TEXT NOT NULL (no FK) at v1.
-- FK to forge.customers added as ALTER TABLE when D2 customers table is confirmed.

CREATE TABLE IF NOT EXISTS forge.api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     TEXT NOT NULL,
    -- FK deferred: REFERENCES forge.customers(id) added in D2 migration
    key_hash        TEXT NOT NULL,
    -- argon2id hash of the plaintext key. Plaintext never stored.
    key_prefix      TEXT NOT NULL,
    -- First 12 characters of the plaintext key (e.g., "ftb_a8Xk2mPq").
    -- Used for log identification. Cannot reconstruct the key.
    tier            TEXT NOT NULL CHECK (tier IN ('standard', 'pro', 'institutional')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    -- NULL = no expiry (institutional tier).
    -- Standard/pro: created_at + 365 days.
    revoked_at      TIMESTAMPTZ,
    -- NULL = active. Non-null = revoked. Revocation is immediate on write.
    last_used_at    TIMESTAMPTZ,
    -- Updated on each successfully verified request.
    rotation_of     UUID REFERENCES forge.api_keys(id)
    -- Links a rotated key to its predecessor for audit chain.
);

CREATE INDEX idx_api_keys_prefix
    ON forge.api_keys(key_prefix)
    WHERE revoked_at IS NULL;
-- Enables O(1) lookup during request verification without full-table argon2id scan.

COMMENT ON TABLE forge.api_keys IS
    'Customer API keys. Plaintext stored nowhere — only argon2id hash. '
    'key_prefix (12 chars) used for log identification only.';
```

### Key Generation

```python
# src/fromthebridge/core/api_keys.py
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Argon2id parameters — tuned for ~100ms verification on proxmox hardware.
# Retuning required if verification latency exceeds 200ms at Phase 5 load.
_ph = PasswordHasher(
    time_cost=2,        # Iterations
    memory_cost=65536,  # 64 MB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

_KEY_PREFIX_LENGTH = 12  # Chars stored in key_prefix column for log ID

def generate_api_key() -> str:
    """
    Generate a new customer API key.
    Format: ftb_ + 40 URL-safe base64 chars = 240 bits entropy.
    Called once per customer onboarding. Result delivered to customer exactly once.
    """
    token = secrets.token_urlsafe(30)  # 30 bytes → 40 base64 chars
    return f"ftb_{token}"


def hash_api_key(plaintext_key: str) -> str:
    """Hash a plaintext API key for storage. Call once at issuance."""
    return _ph.hash(plaintext_key)


def verify_api_key(plaintext_key: str, stored_hash: str) -> bool:
    """
    Verify a presented API key against the stored hash.
    Called on every authenticated API request.
    Returns False on any mismatch — never raises.
    """
    try:
        return _ph.verify(stored_hash, plaintext_key)
    except VerifyMismatchError:
        return False
    except Exception:
        # Log and fail closed on unexpected errors (e.g., malformed hash)
        return False


def onboard_customer(
    customer_id: str,
    tier: str,
    conn,
    *,
    expires_days: Optional[int] = 365,
) -> dict:
    """
    Issue a new API key for a customer.

    Returns the plaintext key exactly once for operator delivery via
    1Password secure share. After this function returns, the plaintext
    key is unrecoverable from the database.

    Args:
        customer_id: Customer identifier (TEXT).
        tier: One of 'standard', 'pro', 'institutional'.
        conn: Active psycopg2 connection.
        expires_days: Days until expiry. None = no expiry (institutional).
    """
    if tier not in ("standard", "pro", "institutional"):
        raise ValueError(f"Invalid tier: {tier}")

    plaintext = generate_api_key()
    key_hash = hash_api_key(plaintext)
    key_prefix = plaintext[:_KEY_PREFIX_LENGTH]

    expires_at = None
    if expires_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO forge.api_keys
                (customer_id, key_hash, key_prefix, tier, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (customer_id, key_hash, key_prefix, tier, expires_at),
        )
        key_id = cur.fetchone()[0]
    conn.commit()

    # Audit log: key_id and prefix only — never the plaintext key
    print(
        f"[AUDIT] API key issued: id={key_id}, customer={customer_id}, "
        f"tier={tier}, prefix={key_prefix}, expires={expires_at}"
    )

    return {
        "key_id": str(key_id),
        "api_key": plaintext,  # Operator delivers via 1Password secure share. Never email.
        "key_prefix": key_prefix,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "_note": "This is the only time this key will be shown. Deliver via 1Password secure share.",
    }


def rotate_api_key(old_key_id: str, conn) -> dict:
    """
    Rotate a customer API key.
    Old key is revoked immediately. New key linked via rotation_of FK.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT customer_id, tier
            FROM forge.api_keys
            WHERE id = %s AND revoked_at IS NULL
            """,
            (old_key_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Key {old_key_id} not found or already revoked")
        customer_id, tier = row

        # Revoke old key immediately
        cur.execute(
            "UPDATE forge.api_keys SET revoked_at = NOW() WHERE id = %s",
            (old_key_id,),
        )
    conn.commit()

    # Issue new key
    result = onboard_customer(customer_id, tier, conn)

    # Link new key to predecessor
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE forge.api_keys SET rotation_of = %s WHERE id = %s",
            (old_key_id, result["key_id"]),
        )
    conn.commit()

    return result


def revoke_api_key(key_id: str, conn) -> None:
    """Immediately revoke an API key. No grace period."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE forge.api_keys SET revoked_at = NOW() WHERE id = %s AND revoked_at IS NULL",
            (key_id,),
        )
        if cur.rowcount == 0:
            raise ValueError(f"Key {key_id} not found or already revoked")
    conn.commit()
    print(f"[AUDIT] API key revoked: id={key_id}")
```

### Request Verification Query

```sql
-- Called on every authenticated API request.
-- Lookup by key_prefix first (indexed) → then argon2id verify in application.
-- Rejects: revoked keys, expired keys.
SELECT id, key_hash, tier, customer_id
FROM forge.api_keys
WHERE key_prefix = $1
  AND revoked_at IS NULL
  AND (expires_at IS NULL OR expires_at > NOW());
-- If row returned: verify presented key against key_hash in application code.
-- On successful verify: UPDATE forge.api_keys SET last_used_at = NOW() WHERE id = $2
```

### Expiry Policy

| Tier | Default Expiry | Rationale |
|------|---------------|-----------|
| standard | 365 days | Aligns with annual subscription cycle |
| pro | 365 days | Same |
| institutional | None (NULL) | Enterprise contracts manage rotation independently |

### Transmission

Plaintext key delivered exactly once via 1Password secure share (one-time-view link). Never email, never SMS, never Slack. After the secure share is viewed, the plaintext is gone — not recoverable without a rotation.

---

## 5. External API Key Management

### Storage

All external API keys stored in `/opt/empire/FromTheBridge/secrets/external_apis/`, one file per source, `chmod 600`, `chown root:root`, raw key value only. FRED and DeFiLlama have no keys — their files are empty placeholders maintained for uniformity.

Adapters read keys at startup via `read_external_api_key("tiingo")` — cached per process lifetime. No runtime re-reads.

### Key Compromise Response

If an external API key is suspected or confirmed compromised:

```
1. ROTATE AT PROVIDER (< 2 minutes)
   Log into the provider's API management console immediately.
   Revoke the compromised key and generate a replacement.
   Do not wait — automated credential scrapers act within minutes.

2. UPDATE ON PROXMOX
   echo -n 'NEW_KEY_VALUE' > /opt/empire/FromTheBridge/secrets/external_apis/<source>.txt
   chmod 600 /opt/empire/FromTheBridge/secrets/external_apis/<source>.txt

3. RESTART DAGSTER CODE SERVER
   docker compose restart empire_dagster_code
   # The read_secret() LRU cache is invalidated on restart.
   # Verify collection resumes in Dagster UI within one cadence window.

4. GIT HISTORY AUDIT
   git log -p --all | grep -c '<first-8-chars-of-old-key>'
   # If found in any commit:
   a. Rotate ALL credentials (assume repo was scanned by automated scrapers).
   b. Use git filter-repo to purge the key from all commits.
   c. Force-push to Gitea remote.
   d. Notify: this is now a multi-credential rotation event.
```

### Monitoring Patterns

These patterns in Dagster asset observation logs indicate a key problem:

| Pattern | Duration | Likely Cause | Action |
|---------|----------|-------------|--------|
| Asset fails with HTTP 401/403 | ≥2 consecutive runs | Key revoked or expired at provider | Rotate key |
| Asset fails with HTTP 429 | ≥3 consecutive runs | Rate limit exceeded | Investigate whether rate limit was increased recently; possible key sharing if not |
| Sudden asset staleness after stable period | >2× cadence window | Key problem or provider outage | Check provider status page first, then key validity |
| Dead letter volume spike for one source | Any | Data format change or auth degradation | Review dead letter table for rejection codes |

Dagster's native freshness policy (from `cadence_hours` in `metric_catalog`) surfaces the first and fourth patterns as stale asset alerts without additional instrumentation.

---

## 6. Rotation Policy

| Credential Type | Rotation Trigger | Procedure | Downtime? | Recovery Time |
|----------------|-----------------|-----------|-----------|---------------|
| PostgreSQL `forge_user` | Compromise; annual (March) | `ALTER USER forge_user PASSWORD 'new';` → update `secrets/pg_forge_user.txt` → `docker compose restart empire_dagster_code empire_dagster_webserver empire_dagster_daemon` | ~30s (service restart) | < 2 min |
| PostgreSQL `forge_reader` | Compromise; annual | Same pattern. Restart: all services using forge_reader. | ~30s | < 2 min |
| ClickHouse `ch_writer` | Compromise; annual | `ALTER USER ch_writer IDENTIFIED WITH sha256_password BY 'new';` → update `secrets/ch_writer.txt` → restart `empire_dagster_code` | ~30s | < 2 min |
| ClickHouse `ch_export_reader` | Compromise; annual | Same pattern. Restart `empire_dagster_export`. | ~30s | < 2 min |
| MinIO `bronze_writer` service account | Compromise; annual | Create new service account via `mc admin user add` → apply bronze-write-only policy → update `secrets/minio_bronze_*` → restart `empire_dagster_code` → delete old account | ~30s | < 3 min |
| MinIO `gold_reader` service account | Compromise; annual | Same pattern for gold-read-only policy. Restart forge_compute and empire_api. | ~30s | < 3 min |
| MinIO `export_writer` service account | Compromise; annual | Same pattern for export-write policy. Restart `empire_dagster_export`. | ~30s | < 3 min |
| Customer API keys (standard/pro) | Customer request; compromise; 365-day expiry | `rotate_api_key(old_key_id, conn)` → deliver new key via 1Password share | None (old key revoked on rotate, not before) | < 5 min |
| Customer API keys (institutional) | Customer request; compromise | `rotate_api_key()` same procedure. No expiry-based trigger. | None | < 5 min |
| External vendor API keys | Compromise; annual (March) | Rotate at vendor console → update `secrets/external_apis/<source>.txt` → restart `empire_dagster_code` | ~30s (one missed collection window) | < 5 min |
| Cloudflare tunnel token | Compromise; annual | Delete tunnel in Cloudflare dashboard → create new tunnel → update `secrets/cf_tunnel_token.txt` → `systemctl restart cloudflared` | ~60s (tunnel reconnect) | < 5 min |

**Annual rotation window:** Last week of March. All non-compromised credentials rotated in a single window per year. First annual rotation: March 2027.

**Rotation runbook template (for each annual rotation):**
```
□ PostgreSQL forge_user rotated
□ PostgreSQL forge_reader rotated
□ ClickHouse ch_writer rotated
□ ClickHouse ch_export_reader rotated
□ MinIO bronze_writer rotated
□ MinIO gold_reader rotated
□ MinIO export_writer rotated
□ External API keys checked (rotate any approaching vendor-recommended interval)
□ Cloudflare tunnel token rotated
□ secrets/ backup created and transferred to NAS (encrypted)
□ All services verified healthy post-rotation
□ Rotation date logged in ops journal
```

---

## 7. Incident Response Playbooks

### Scenario A: Customer API Key Suspected Compromised

```
DETECTION
  Customer reports unexpected access, or API logs show requests from
  unusual IPs for a known key prefix.

1. CONTAINMENT (< 5 minutes)
   UPDATE forge.api_keys
   SET revoked_at = NOW()
   WHERE key_prefix = '<prefix>' AND revoked_at IS NULL;

   Verify:
   SELECT key_prefix, revoked_at FROM forge.api_keys
   WHERE key_prefix = '<prefix>';
   -- revoked_at must be non-null. Key is now dead. No restart required.

2. INVESTIGATION
   SELECT requested_at, endpoint, source_ip, response_code
   FROM forge.api_access_log
   WHERE key_prefix = '<prefix>'
   ORDER BY requested_at DESC
   LIMIT 100;
   -- Identify: when anomaly started, what was accessed, source IPs.

3. ROTATION
   Contact customer via verified channel (not a reply-to email from an unknown address).
   Execute: rotate_api_key('<old_key_id>', conn)
   Deliver new key via 1Password secure share.

4. VERIFICATION
   curl -H "X-API-Key: ftb_<old-key>" https://api.fromthebridge.net/v1/signals
   -- Must return: 401 Unauthorized

   curl -H "X-API-Key: ftb_<new-key>" https://api.fromthebridge.net/v1/signals
   -- Must return: 200 OK

5. CUSTOMER NOTIFICATION
   Notify: key revoked at [timestamp], reason: suspected unauthorized access.
   Advise customer to audit their own systems for the compromised credential.
   Document rotation chain in forge.api_keys.rotation_of.
```

### Scenario B: External API Key Confirmed Leaked (e.g., Tiingo committed to git)

```
DETECTION
  Key found in public repository, paste site, or provider alerts on anomalous usage.

1. CONTAINMENT (< 2 minutes)
   Open provider console (Tiingo, Coinalyze, etc.) immediately.
   Revoke the leaked key. Do not wait.

2. ROTATION
   Generate new key at provider.
   echo -n '<NEW_KEY>' > /opt/empire/FromTheBridge/secrets/external_apis/<source>.txt
   chmod 600 /opt/empire/FromTheBridge/secrets/external_apis/<source>.txt
   docker compose restart empire_dagster_code
   Verify: Dagster asset for that source materializes cleanly within one cadence window.

3. GIT HISTORY AUDIT
   git log -p --all | grep '<first-8-chars-of-leaked-key>'

   IF FOUND IN HISTORY:
   a. Assume repo was scanned by automated credential scrapers within minutes of the commit.
   b. Treat as ALL-CREDENTIALS COMPROMISED. Execute full rotation across:
      - All PostgreSQL service accounts
      - All ClickHouse service accounts
      - All MinIO service accounts
      - All external API keys
      - Cloudflare tunnel token
   c. Remove from git history:
      git filter-repo --path-glob '*.txt' --invert-paths  # if key was in a file
      # or for inline secret:
      git filter-repo --replace-text <(echo '<LEAKED_KEY>==>REDACTED')
   d. Force-push to Gitea: git push --force-with-lease origin main
   e. Rotate secrets backup on NAS (old backup may contain the key in cleartext).

4. VERIFICATION
   Old key returns 401/403 from provider: confirm via provider's API.
   New key resumes collection: verify Dagster Tiingo asset is non-stale.

5. CUSTOMER NOTIFICATION
   Not required unless data delivery was disrupted > 24 hours (Phase 6+: notify affected customers).
```

### Scenario C: Cloudflare Tunnel Token Exposed

```
DETECTION
  Token found in git history, process environment dump, or Cloudflare audit
  logs show unexpected tunnel connections from an unrecognized origin.

1. CONTAINMENT (< 5 minutes)
   Log into Cloudflare dashboard.
   Zero Trust → Networks → Tunnels.
   DELETE the exposed tunnel entirely (not just rotate token).
   Deleting the tunnel invalidates all tokens for that tunnel instance.
   fromthebridge.net is now unreachable. Acceptable — no paying customers in v1.

2. ROTATION
   Create a new tunnel in Cloudflare dashboard.
   echo -n '<NEW_TUNNEL_TOKEN>' > /opt/empire/FromTheBridge/secrets/cf_tunnel_token.txt
   chmod 600 /opt/empire/FromTheBridge/secrets/cf_tunnel_token.txt
   Update cloudflared service to use the new token:
   # If cloudflared is started with --token flag via systemd:
   systemctl edit cloudflared  # update ExecStart with new --token value
   systemctl daemon-reload
   systemctl restart cloudflared

3. ROUTE VERIFICATION
   curl -s https://fromthebridge.net  → expect: 200 (landing page)
   curl -s https://fromthebridge.net/api/health  → expect: 200 (FastAPI Phase 5+)
   Access https://fromthebridge.net/bridge/* → must redirect to Cloudflare Access login.

4. CUSTOMER NOTIFICATION
   If API was unreachable > 5 minutes during rotation and customers are active (Phase 6+):
   Notify via out-of-band channel (email) of a brief maintenance window.
```

### Scenario D: PostgreSQL `forge_user` Password Compromised

```
DETECTION
  Unexpected connections in pg_stat_activity from unrecognized IPs or process names.
  Audit log shows DML not attributable to known services.

1. CONTAINMENT (< 5 minutes)
   forge_user has WRITE access to the forge catalog schema.
   This is the highest-sensitivity internal credential. Act immediately.

   Terminate all active forge_user connections:
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE usename = 'forge_user';

   Revoke login privilege:
   ALTER USER forge_user NOLOGIN;
   -- All services using forge_user now fail. Collection stops. Accept this.

2. DAMAGE ASSESSMENT
   Review recent DML via pg_stat_statements:
   SELECT query, calls, total_exec_time
   FROM pg_stat_statements
   WHERE userid = (SELECT oid FROM pg_roles WHERE rolname = 'forge_user')
   ORDER BY total_exec_time DESC LIMIT 50;

   Verify catalog table row counts match known-good values:
   SELECT
     (SELECT COUNT(*) FROM forge.metric_catalog)   AS metric_catalog,
     (SELECT COUNT(*) FROM forge.source_catalog)   AS source_catalog,
     (SELECT COUNT(*) FROM forge.instruments)      AS instruments,
     (SELECT COUNT(*) FROM forge.assets)           AS assets;
   -- Compare against Phase 0 completion report counts.

3. ROTATION
   Generate new password:
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"

   Set new password and re-enable login:
   ALTER USER forge_user PASSWORD '<NEW_PASSWORD>' LOGIN;

   Update secrets file:
   echo -n '<NEW_PASSWORD>' > /opt/empire/FromTheBridge/secrets/pg_forge_user.txt
   chmod 600 /opt/empire/FromTheBridge/secrets/pg_forge_user.txt

   Restart affected services:
   docker compose restart empire_dagster_code empire_dagster_webserver empire_dagster_daemon

4. VERIFICATION
   Verify forge_user reconnects from Dagster:
   docker compose logs empire_dagster_code | grep -i "postgres\|connected\|error" | tail -20
   -- Expect: successful connection messages, no auth errors.

   Re-run catalog row count verification from step 2.
   Counts must match Phase 0 baseline.

5. CUSTOMER NOTIFICATION
   Internal credential only — no customer notification required.
   If collection gaps exceed 24 hours and customers are active (Phase 6+):
   Notify affected customers of a data quality gap window.
   Document incident in private ops log.
```

---

## 8. Cloudflare Zero Trust Configuration

### Route Map

| Route | Destination | Protection Level |
|-------|------------|-----------------|
| `fromthebridge.net` | `:3002` (landing page) | Public |
| `fromthebridge.net/briefs` | `:3002` | Public |
| `fromthebridge.net/launch` | `:3002` | Public |
| `fromthebridge.net/api/*` | `:8000` (FastAPI) | Public route — API key auth enforced by FastAPI, not Cloudflare |
| `fromthebridge.net/bridge/*` | `:3002` (Bridge UI) | **Cloudflare Zero Trust — operator only** |
| `dagster.fromthebridge.net` | `:3010` (Dagster webserver) | **Cloudflare Zero Trust — operator only** (Phase 6 addition) |

### Cloudflare Access Policy — Bridge UI

```
Application name:    FTB Bridge UI
Application domain:  fromthebridge.net/bridge/*
Action:              Allow
Identity provider:   Cloudflare Access (one-time PIN or Google OAuth — operator choice)
Session duration:    24 hours

Include rule:
  Selector: Emails
  Value: <operator-email>

Additional rule:
  Selector: IP ranges (optional hardening)
  Value: 192.168.68.0/24  ← adds local-network-only restriction if operator always accesses from home
```

### Dagster Webserver (:3010)

Dagster port 3010 is **not published to the proxmox host interface** in `docker-compose.yml`. The `ports` mapping for `empire_dagster_webserver` is omitted (internal Docker network only).

v1 access method: SSH port forward.
```bash
ssh -L 3010:localhost:3010 root@192.168.68.11
# Then access: http://localhost:3010 in browser on bluefin
```

Phase 6 upgrade path: Add `dagster.fromthebridge.net → :3010` to the Cloudflare tunnel, behind the same Zero Trust operator-email policy as the Bridge UI. This eliminates the SSH tunnel requirement for remote monitoring without exposing the Dagster port to the internet.

### MinIO Console (:9002)

MinIO console port 9002 is **not published to the proxmox host interface**. All MinIO administration is performed via `mc` CLI over SSH.

```bash
# All MinIO administration performed as:
ssh root@192.168.68.11
mc alias set local http://localhost:9001 $(cat secrets/minio_root_key.txt) $(cat secrets/minio_root_secret.txt)
mc ls local/
```

MinIO console exposure via Cloudflare tunnel is not recommended for v1 — the `mc` CLI provides all required administration capabilities without a persistent open route.

---

## 9. Implementation Checklist — Phase 0 Corrective Actions

These items must be completed before Phase 1 begins. They close the security gaps identified in `thread_infrastructure.md` and this document.

```
PHASE 0 SECURITY CORRECTIVE ACTIONS

□ SEC-01: Initialize secrets directory
   Run: scripts/init_secrets.sh
   Populate all REPLACE_ME placeholders with real credentials from 1Password.
   Verify: grep -r 'REPLACE_ME' /opt/empire/FromTheBridge/secrets/ → zero results

□ SEC-02: Deploy ClickHouse credential isolation DDL
   File: db/migrations/clickhouse/0002_credential_isolation.sql
   Run as ch_admin (interactive terminal — password from 1Password).
   Post-deploy: run all five assertions in the verification checklist.
   Verify: SHOW GRANTS FOR ch_writer → INSERT only.
   Verify: SHOW GRANTS FOR ch_export_reader → SELECT only.
   Verify: default user ACCOUNT SUSPENDED.

□ SEC-03: Set up MinIO service accounts
   Run: scripts/setup_minio_service_accounts.sh
   Verify: mc admin user list local → three service accounts present.
   Verify: bronze_writer cannot GetObject from bronze bucket.
   Verify: gold_reader cannot PutObject to gold bucket.

□ SEC-04: Update docker-compose.yml with per-service secret mounts
   Ensure no service has access to secrets it does not need.
   Confirm: ch_export_reader not mounted on empire_dagster_code.
   Confirm: ch_writer not mounted on empire_dagster_export.
   Confirm: MinIO root key not mounted on any adapter or compute service.
   Confirm: Dagster port 3010 NOT in ports: mapping.
   Confirm: MinIO console port 9002 NOT in ports: mapping.

□ SEC-05: Perform initial secrets backup to NAS
   Run: scripts/backup_secrets.sh
   Verify: encrypted archive exists on NAS at /backups/fromthebridge/secrets/.

□ SEC-06: Verify Cloudflare Zero Trust on /bridge/* routes
   Access fromthebridge.net/bridge/ from an unauthenticated browser.
   Must redirect to Cloudflare Access login — must not serve content.
```

---

## 10. Cloud Migration Compatibility Summary

All security controls in this document are designed for zero-code-change migration when managed service triggers activate.

| Control | v1 Implementation | Cloud Migration Equivalent | Change Required |
|---------|-----------------|--------------------------|----------------|
| Secrets management | `secrets/` bind mounts | AWS Secrets Manager / HashiCorp Vault | Provider swap via environment config |
| At-rest encryption (PG) | Not encrypted | RDS: AES-256 via KMS (default) | Zero — RDS default |
| At-rest encryption (ClickHouse) | Not encrypted | ClickHouse Cloud: provider-managed | Zero — provider default |
| At-rest encryption (MinIO) | Not encrypted | S3 SSE-S3: one-line config | Config only |
| In-transit (inter-service) | Unencrypted Docker bridge | VPC private subnets + optional service mesh | Network topology change |
| ClickHouse credential isolation | Database-level user grants | ClickHouse Cloud: same DDL | Zero — schema-compatible |
| Customer API key hashing | argon2id in application | Unchanged | Zero |
| Cloudflare Zero Trust | Existing | Unchanged (Cloudflare is already cloud) | Zero |

---

*Document status: LOCKED — Architect Approved 2026-03-06.
All thirteen draft assumptions resolved.
Implementation sessions reference section numbers in this document.
Changes to any locked decision require architect approval and a new RESULT document version.*
