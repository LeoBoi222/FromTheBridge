#!/usr/bin/env bash
# post-commit-reminder.sh — Advisory reminders after git commit.
# PostToolUse on Bash. Cannot block (exit 0 always).

set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

[ -z "$COMMAND" ] && exit 0

# Only fire on git commit commands
echo "$COMMAND" | grep -qPi '^\s*git\s+commit\b' || exit 0

# Get the files in the last commit
COMMITTED_FILES=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || echo "")
[ -z "$COMMITTED_FILES" ] && exit 0

# Check 1: Did commit touch .py or .sql files without CLAUDE.md?
HAS_CODE=$(echo "$COMMITTED_FILES" | grep -cPi '\.(py|sql)$' || true)
HAS_CLAUDE=$(echo "$COMMITTED_FILES" | grep -c 'CLAUDE.md' || true)

if [ "$HAS_CODE" -gt 0 ] && [ "$HAS_CLAUDE" -eq 0 ]; then
  echo "ADVISORY: Commit includes .py/.sql changes but CLAUDE.md was not updated. If this commit adds/modifies a Dagster asset, deployed service, or phase gate item, update CURRENT STATE in CLAUDE.md." >&2
fi

# Check 2: Large commit — suggest review
FILE_COUNT=$(echo "$COMMITTED_FILES" | wc -l)
if [ "$FILE_COUNT" -gt 3 ]; then
  echo "ADVISORY: Commit touched $FILE_COUNT files. Consider running code review agent before pushing." >&2
fi

# Check 3: If commit message contains deploy/rsync/rebuild keywords, remind about deploys.md
COMMIT_MSG=$(git log -1 --format=%s 2>/dev/null || echo "")
if echo "$COMMIT_MSG" | grep -qPi '(deploy|rsync|rebuild|proxmox)'; then
  echo "ADVISORY: Deploy-related commit detected. Update deploys.md in memory files." >&2
fi

exit 0
