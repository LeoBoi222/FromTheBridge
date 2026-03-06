#!/usr/bin/env bash
# guard-ddl.sh — Enforce schema immutability after Phase 0.
# DDL (CREATE/ALTER/DROP TABLE) on forge schema is blocked unless
# .claude/phase0-open exists (gate flag).
# INSERT INTO forge.metric_catalog / forge.source_catalog always allowed.
# Exit 0 = allow, Exit 2 = block with feedback

set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

[ -z "$COMMAND" ] && exit 0

# If Phase 0 gate is still open, allow all DDL
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$SCRIPT_DIR/phase0-open" ]; then
  exit 0
fi

# Block DDL on forge schema
DDL_PATTERN='\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|VIEW|MATERIALIZED\s+VIEW|SCHEMA|DATABASE)\b'

if echo "$COMMAND" | grep -qPi "$DDL_PATTERN"; then
  # Check if it targets forge schema
  if echo "$COMMAND" | grep -qi "forge\."; then
    echo "BLOCKED: Schema immutability enforced. DDL on forge schema is not permitted after Phase 0 gate. New metrics/sources add catalog rows only (INSERT INTO forge.metric_catalog / forge.source_catalog). To reopen DDL, create .claude/phase0-open (requires architect approval)." >&2
    exit 2
  fi
fi

exit 0
