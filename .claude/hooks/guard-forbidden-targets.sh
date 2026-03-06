#!/usr/bin/env bash
# guard-forbidden-targets.sh — Block writes to NAS and Server2.
# NAS (192.168.68.91) is backup-only. Server2 (192.168.68.12) is Binance Collector only.
# Exit 0 = allow, Exit 2 = block with feedback

set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

[ -z "$COMMAND" ] && exit 0

NAS_IP="192.168.68.91"
SERVER2_IP="192.168.68.12"

# Block any SSH/rsync/scp writes to NAS
if echo "$COMMAND" | grep -q "$NAS_IP"; then
  if echo "$COMMAND" | grep -qPi "(rsync|scp|ssh.*rm|ssh.*mv|ssh.*cp|ssh.*tee|ssh.*dd|ssh.*write|ssh.*mkdir)"; then
    echo "BLOCKED: NAS ($NAS_IP) is backup destination only. No write operations permitted. See CLAUDE.md FORBIDDEN ACTIONS." >&2
    exit 2
  fi
fi

# Block any writes to Server2
if echo "$COMMAND" | grep -q "$SERVER2_IP"; then
  if echo "$COMMAND" | grep -qPi "(rsync|scp|ssh.*rm|ssh.*mv|ssh.*cp|ssh.*tee|ssh.*dd|ssh.*write|ssh.*mkdir|ssh.*docker)"; then
    echo "BLOCKED: Server2 ($SERVER2_IP) is Binance Collector only. No write operations permitted. See CLAUDE.md FORBIDDEN ACTIONS." >&2
    exit 2
  fi
fi

exit 0
