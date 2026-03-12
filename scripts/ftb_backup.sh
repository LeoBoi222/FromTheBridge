#!/bin/bash
# =============================================================================
# FTB Backup Script
# Backs up FTB-owned data to NAS: Dagster metadata DB, MinIO (Bronze + Gold)
# Runs on proxmox, stores to NAS via NFS mount
#
# Schedule: daily 04:00 UTC (after Nexus-Council backup at 03:00)
# Retention: 30 days rolling
# Encryption: GPG symmetric AES-256
# =============================================================================
set -euo pipefail

BACKUP_ROOT="/mnt/nas/empire/backups/ftb"
RETENTION_DAYS=30
DATE=$(date +%Y-%m-%d_%H%M)
LOG_FILE="/var/log/ftb-backup.log"
GPG_PASSPHRASE_FILE="/opt/empire/FromTheBridge/secrets/backup_gpg_passphrase"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

log "=== FTB backup started ==="

# --- Pre-flight checks ---

if ! mountpoint -q /mnt/nas/empire; then
    log "ERROR: NFS mount /mnt/nas/empire not available"
    exit 1
fi

if [ ! -f "$GPG_PASSPHRASE_FILE" ]; then
    log "ERROR: GPG passphrase file not found at $GPG_PASSPHRASE_FILE"
    log "Create it: openssl rand -base64 32 > $GPG_PASSPHRASE_FILE && chmod 600 $GPG_PASSPHRASE_FILE"
    exit 1
fi

mkdir -p "$BACKUP_ROOT"/{dagster,minio}

# --- Dagster metadata DB ---
DAGSTER_DIR="$BACKUP_ROOT/dagster"
DAGSTER_DUMP="$DAGSTER_DIR/dagster_$DATE.dump"
log "Dumping Dagster metadata DB..."
if docker exec empire_postgres pg_dump -U dagster_user -d dagster -Fc \
    > "$DAGSTER_DUMP" 2>>"$LOG_FILE"; then
    # Encrypt
    if gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-file "$GPG_PASSPHRASE_FILE" \
        --output "${DAGSTER_DUMP}.gpg" "$DAGSTER_DUMP" 2>>"$LOG_FILE"; then
        rm -f "$DAGSTER_DUMP"
        SIZE=$(du -h "${DAGSTER_DUMP}.gpg" | cut -f1)
        log "Dagster dump OK ($SIZE, encrypted)"
    else
        log "ERROR: GPG encryption failed for Dagster dump"
        rm -f "$DAGSTER_DUMP"
    fi
else
    log "ERROR: Dagster dump failed"
    rm -f "$DAGSTER_DUMP"
fi

# --- MinIO: Bronze + Gold ---
MINIO_DIR="$BACKUP_ROOT/minio/$DATE"
mkdir -p "$MINIO_DIR"

# Mirror bronze-hot
log "Mirroring MinIO bronze-hot..."
if docker exec empire_minio mc mirror --quiet local/bronze-hot /tmp/bronze-hot-backup 2>>"$LOG_FILE"; then
    # Copy out of container and encrypt
    if docker cp empire_minio:/tmp/bronze-hot-backup - 2>>"$LOG_FILE" | \
        gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-file "$GPG_PASSPHRASE_FILE" \
        --output "$MINIO_DIR/bronze-hot.tar.gpg" 2>>"$LOG_FILE"; then
        SIZE=$(du -h "$MINIO_DIR/bronze-hot.tar.gpg" | cut -f1)
        log "Bronze-hot mirror OK ($SIZE, encrypted)"
    else
        log "ERROR: Bronze-hot encryption/copy failed"
    fi
    docker exec empire_minio rm -rf /tmp/bronze-hot-backup 2>/dev/null
else
    log "ERROR: Bronze-hot mirror failed"
fi

# Mirror gold
log "Mirroring MinIO gold..."
if docker exec empire_minio mc mirror --quiet local/gold /tmp/gold-backup 2>>"$LOG_FILE"; then
    if docker cp empire_minio:/tmp/gold-backup - 2>>"$LOG_FILE" | \
        gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-file "$GPG_PASSPHRASE_FILE" \
        --output "$MINIO_DIR/gold.tar.gpg" 2>>"$LOG_FILE"; then
        SIZE=$(du -h "$MINIO_DIR/gold.tar.gpg" | cut -f1)
        log "Gold mirror OK ($SIZE, encrypted)"
    else
        log "ERROR: Gold encryption/copy failed"
    fi
    docker exec empire_minio rm -rf /tmp/gold-backup 2>/dev/null
else
    log "ERROR: Gold mirror failed"
fi

# Mirror bronze-archive (may be empty)
log "Mirroring MinIO bronze-archive..."
ARCHIVE_COUNT=$(docker exec empire_minio mc ls local/bronze-archive/ 2>/dev/null | wc -l)
if [ "$ARCHIVE_COUNT" -gt 0 ]; then
    if docker exec empire_minio mc mirror --quiet local/bronze-archive /tmp/bronze-archive-backup 2>>"$LOG_FILE"; then
        if docker cp empire_minio:/tmp/bronze-archive-backup - 2>>"$LOG_FILE" | \
            gpg --batch --yes --symmetric --cipher-algo AES256 \
            --passphrase-file "$GPG_PASSPHRASE_FILE" \
            --output "$MINIO_DIR/bronze-archive.tar.gpg" 2>>"$LOG_FILE"; then
            SIZE=$(du -h "$MINIO_DIR/bronze-archive.tar.gpg" | cut -f1)
            log "Bronze-archive mirror OK ($SIZE, encrypted)"
        else
            log "ERROR: Bronze-archive encryption/copy failed"
        fi
        docker exec empire_minio rm -rf /tmp/bronze-archive-backup 2>/dev/null
    else
        log "ERROR: Bronze-archive mirror failed"
    fi
else
    log "Bronze-archive empty, skipping"
fi

# --- Retention cleanup ---
log "Cleaning backups older than $RETENTION_DAYS days..."
find "$BACKUP_ROOT/dagster" -name "dagster_*.gpg" -mtime +$RETENTION_DAYS -delete -print 2>>"$LOG_FILE" | \
    while read f; do log "Deleted: $f"; done
find "$BACKUP_ROOT/minio" -mindepth 1 -maxdepth 1 -type d -mtime +$RETENTION_DAYS -exec rm -rf {} \; -print 2>>"$LOG_FILE" | \
    while read f; do log "Deleted: $f"; done

log "=== FTB backup complete ==="
