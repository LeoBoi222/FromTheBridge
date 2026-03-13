#!/usr/bin/env bash
# setup_minio_service_accounts.sh — Create MinIO service accounts with isolated policies
#
# Per v4.0 §MinIO service accounts (lines 4731-4742):
#   bronze_writer:          PutObject on bronze-hot/* only
#   bronze_archive_writer:  PutObject + GetObject + ListBucket on bronze-archive/* only
#   export_writer:          PutObject + GetObject + ListBucket + DeleteObject on gold/*
#   gold_reader:            GetObject + ListBucket on gold/* + marts/*  (Phase 2+)
#   marts_writer:           PutObject + GetObject + ListBucket on marts/* only (Phase 2+)
#
# Note: GetBucketLocation and GetObject+ListBucket added beyond v4.0 spec where
# PyIceberg requires them for metadata discovery on append operations.
#
# Usage: Run inside the MinIO container or via docker exec.
#   docker exec -i empire_minio bash < scripts/setup_minio_service_accounts.sh
#
# Prerequisites:
#   - mc alias "local" configured (docker entrypoint does this)
#   - Secrets files exist in /opt/empire/FromTheBridge/secrets/

set -euo pipefail

SECRETS_DIR="/opt/empire/FromTheBridge/secrets"

# --- bronze_writer: bronze-hot only ---
cat <<'POLICY' | mc admin policy create local bronze-writer /dev/stdin
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::bronze-hot",
        "arn:aws:s3:::bronze-hot/*"
      ]
    }
  ]
}
POLICY
echo "Policy bronze-writer created"

# --- bronze_archive_writer: bronze-archive only ---
cat <<'POLICY' | mc admin policy create local bronze-archive-rw /dev/stdin
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::bronze-archive",
        "arn:aws:s3:::bronze-archive/*"
      ]
    }
  ]
}
POLICY
echo "Policy bronze-archive-rw created"

# --- export_writer: gold only ---
cat <<'POLICY' | mc admin policy create local gold-rw /dev/stdin
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::gold",
        "arn:aws:s3:::gold/*"
      ]
    }
  ]
}
POLICY
echo "Policy gold-rw created"

echo ""
echo "=== Verification ==="
echo "Listing all users and policies:"
mc admin user list local
echo ""
echo "Run bidirectional isolation tests manually to confirm."
echo "See CLAUDE.md or runbook FTB-04 for test procedure."
