#!/usr/bin/env bash
# guard-sql-in-files.sh (FTB version) — Block Rule 2 violations and forge.* DDL in file content.
# PreToolUse on Write + Edit tools.
# Exit 0 = allow, Exit 2 = block with feedback

set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only check Write and Edit tools
case "$TOOL" in
  Write|Edit) ;;
  *) exit 0 ;;
esac

# Get the file path and content
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')

[ -z "$FILE_PATH" ] && exit 0
[ -z "$CONTENT" ] && exit 0

# Only check relevant file types
case "$FILE_PATH" in
  *.sql|*.py|*.txt) ;;
  *) exit 0 ;;
esac

# Block SELECT from forge.observations/current_values (Rule 2)
# Exception: files in export/ or ops/ directories (Dagster export + ops assets)
case "$FILE_PATH" in
  */export/*|*/ops/*) ;;
  *)
    RULE2_PATTERN='\bSELECT\b.*\bFROM\s+forge\.(observations|current_values)\b'
    if echo "$CONTENT" | grep -qPi "$RULE2_PATTERN"; then
      echo "BLOCKED by Rule 2: File contains SELECT from forge.observations or forge.current_values. Only Dagster export/ops assets may read Silver. Files in export/ or ops/ directories are exempt." >&2
      exit 2
    fi
    ;;
esac

# Block forge.* DDL in files unless phase0 gate is open
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -f "$SCRIPT_DIR/phase0-open" ]; then
  DDL_PATTERN='\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|VIEW|MATERIALIZED\s+VIEW|SCHEMA|DATABASE)\b.*\bforge\.'
  if echo "$CONTENT" | grep -qPi "$DDL_PATTERN"; then
    echo "BLOCKED: File contains DDL targeting forge.* schema. Schema immutability enforced after Phase 0 gate. Create .claude/phase0-open to override (requires architect approval)." >&2
    exit 2
  fi
fi

exit 0
