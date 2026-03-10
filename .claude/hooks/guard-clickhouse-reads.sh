#!/usr/bin/env bash
# guard-clickhouse-reads.sh — Enforce Rule 2: ClickHouse is write-only
# except for the Silver -> Gold export job.
# Exit 0 = allow, Exit 2 = block with feedback

set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# If no command, allow
[ -z "$COMMAND" ] && exit 0

# Patterns that indicate a ClickHouse READ (not write)
# We block: SELECT from forge.observations, forge.current_values
# We allow: SELECT from forge.dead_letter (monitoring), INSERT, CREATE, system queries
CH_READ_PATTERN='clickhouse-client.*(-q|--query)\s.*SELECT\s.*FROM\s+forge\.(observations|current_values)'

# Also catch HTTP API reads
CH_HTTP_READ='curl.*8123.*SELECT.*forge\.(observations|current_values)'

if echo "$COMMAND" | grep -qPi "$CH_READ_PATTERN"; then
  # Check if this is the export job or ops health context (Rule 2 exemptions)
  if echo "$COMMAND" | grep -qi "export\|silver.*gold\|gold.*export\|ch_ops_reader\|ops.health\|smoke.test\|verification"; then
    exit 0  # Allow export job + ops health reads
  fi
  echo "BLOCKED by Rule 2: ClickHouse Silver is write-only. Only the Dagster export asset (Silver -> Gold) may read forge.observations or forge.current_values. If this IS the export job, include 'export' in the command context." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qPi "$CH_HTTP_READ"; then
  echo "BLOCKED by Rule 2: ClickHouse Silver is write-only via HTTP API. See thread_infrastructure.md Rule 2." >&2
  exit 2
fi

exit 0
