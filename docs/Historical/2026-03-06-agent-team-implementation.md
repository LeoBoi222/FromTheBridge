# Agent Team Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy 4 project-level agents, 3 enforcement hooks, PostgreSQL MCP, structured reporting, and database targeting table for FromTheBridge.

**Architecture:** Agents as `.claude/agents/*.md` with YAML frontmatter. Hooks as `.claude/hooks/*.sh` registered in `settings.json`. Reports to `.claude/reports/` (gitignored). MCP configured project-level. CLAUDE.md updated with database targeting table. All agents inherit CLAUDE.md as shared truth.

**Tech Stack:** Claude Code subagent system, bash hook scripts, PostgreSQL MCP (`@anthropic-ai/dbhub` or equivalent), JSON reporting.

**Design doc:** `docs/plans/2026-03-06-agent-team-design.md`

---

## Task 1: Directory Structure + Gitignore

**Files:**
- Create: `.claude/agents/` (directory)
- Create: `.claude/hooks/` (directory)
- Create: `.claude/reports/.gitkeep`
- Create: `.gitignore`
- Create: `.claude/phase0-open`

**Step 1: Create directory structure**

```bash
mkdir -p .claude/agents .claude/hooks .claude/reports
```

**Step 2: Create .gitignore with reports excluded**

```
# Agent reports (accumulated, not versioned)
.claude/reports/*.json

# Editor locks
.~lock.*

# Python
__pycache__/
*.pyc
.venv/
*.egg-info/

# Environment
.env
.env.local
```

**Step 3: Create gitkeep so reports dir is tracked**

```bash
touch .claude/reports/.gitkeep
```

**Step 4: Create Phase 0 gate flag**

Phase 0 is complete but we keep the flag so the DDL hook can be tested. Delete manually to activate the DDL gate.

```bash
touch .claude/phase0-open
```

**Step 5: Commit**

```bash
git add .gitignore .claude/reports/.gitkeep .claude/phase0-open
git commit -m "chore: scaffold agent team directory structure"
```

---

## Task 2: Hook — guard-clickhouse-reads.sh (Rule 2)

**Files:**
- Create: `.claude/hooks/guard-clickhouse-reads.sh`

**Step 1: Write the hook script**

The hook receives JSON on stdin with `{"tool_name": "Bash", "tool_input": {"command": "..."}}`. It must parse the command and block ClickHouse read patterns unless the command is part of the export job.

```bash
#!/usr/bin/env bash
# guard-clickhouse-reads.sh — Enforce Rule 2: ClickHouse is write-only
# except for the Silver → Gold export job.
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
  # Check if this is the export job context
  if echo "$COMMAND" | grep -qi "export\|silver.*gold\|gold.*export"; then
    exit 0  # Allow export job
  fi
  echo "BLOCKED by Rule 2: ClickHouse Silver is write-only. Only the Dagster export asset (Silver -> Gold) may read forge.observations or forge.current_values. If this IS the export job, include 'export' in the command context." >&2
  exit 2
fi

if echo "$COMMAND" | grep -qPi "$CH_HTTP_READ"; then
  echo "BLOCKED by Rule 2: ClickHouse Silver is write-only via HTTP API. See thread_infrastructure.md Rule 2." >&2
  exit 2
fi

exit 0
```

**Step 2: Make executable**

```bash
chmod +x .claude/hooks/guard-clickhouse-reads.sh
```

**Step 3: Test — verify it blocks a ClickHouse read**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"docker exec empire_clickhouse clickhouse-client -q \"SELECT * FROM forge.observations LIMIT 10\""}}' | .claude/hooks/guard-clickhouse-reads.sh
echo "Exit code: $?"
```

Expected: Exit code 2, stderr shows "BLOCKED by Rule 2"

**Step 4: Test — verify it allows a ClickHouse write**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"docker exec empire_clickhouse clickhouse-client -q \"INSERT INTO forge.observations VALUES ...\""}}' | .claude/hooks/guard-clickhouse-reads.sh
echo "Exit code: $?"
```

Expected: Exit code 0 (allowed)

**Step 5: Test — verify it allows export job reads**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"# Silver to Gold export job\ndocker exec empire_clickhouse clickhouse-client -q \"SELECT * FROM forge.observations WHERE observed_at > ...\""}}' | .claude/hooks/guard-clickhouse-reads.sh
echo "Exit code: $?"
```

Expected: Exit code 0 (allowed — contains "export" context)

**Step 6: Commit**

```bash
git add .claude/hooks/guard-clickhouse-reads.sh
git commit -m "feat: add Rule 2 enforcement hook — ClickHouse write-only gate"
```

---

## Task 3: Hook — guard-ddl.sh (Schema Immutability)

**Files:**
- Create: `.claude/hooks/guard-ddl.sh`

**Step 1: Write the hook script**

```bash
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
```

**Step 2: Make executable**

```bash
chmod +x .claude/hooks/guard-ddl.sh
```

**Step 3: Test — verify it blocks DDL when gate closed**

```bash
# Temporarily remove gate flag
mv .claude/phase0-open .claude/phase0-open.bak 2>/dev/null || true
echo '{"tool_name":"Bash","tool_input":{"command":"psql -c \"CREATE TABLE forge.bad_table (id int)\""}}' | .claude/hooks/guard-ddl.sh
echo "Exit code: $?"
mv .claude/phase0-open.bak .claude/phase0-open 2>/dev/null || true
```

Expected: Exit code 2, "BLOCKED: Schema immutability enforced"

**Step 4: Test — verify it allows DDL when gate open**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"psql -c \"CREATE TABLE forge.test_table (id int)\""}}' | .claude/hooks/guard-ddl.sh
echo "Exit code: $?"
```

Expected: Exit code 0 (gate file exists)

**Step 5: Test — verify catalog inserts always allowed**

```bash
mv .claude/phase0-open .claude/phase0-open.bak 2>/dev/null || true
echo '{"tool_name":"Bash","tool_input":{"command":"psql -c \"INSERT INTO forge.metric_catalog (canonical_name) VALUES ('\''test'\'')\""}}' | .claude/hooks/guard-ddl.sh
echo "Exit code: $?"
mv .claude/phase0-open.bak .claude/phase0-open 2>/dev/null || true
```

Expected: Exit code 0 (INSERT is not DDL)

**Step 6: Commit**

```bash
git add .claude/hooks/guard-ddl.sh
git commit -m "feat: add schema immutability enforcement hook — DDL gate"
```

---

## Task 4: Hook — guard-forbidden-targets.sh

**Files:**
- Create: `.claude/hooks/guard-forbidden-targets.sh`

**Step 1: Write the hook script**

```bash
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
  # Allow read-only commands (ls, cat, du, df, mount, stat)
  if echo "$COMMAND" | grep -qPi "(rsync|scp|ssh.*rm|ssh.*mv|ssh.*cp|ssh.*tee|ssh.*dd|ssh.*write|ssh.*mkdir)"; then
    echo "BLOCKED: NAS ($NAS_IP) is backup destination only. No write operations permitted. See CLAUDE.md FORBIDDEN ACTIONS." >&2
    exit 2
  fi
fi

# Block any writes to Server2
if echo "$COMMAND" | grep -q "$SERVER2_IP"; then
  # Allow read-only SSH (status checks)
  if echo "$COMMAND" | grep -qPi "(rsync|scp|ssh.*rm|ssh.*mv|ssh.*cp|ssh.*tee|ssh.*dd|ssh.*write|ssh.*mkdir|ssh.*docker)"; then
    echo "BLOCKED: Server2 ($SERVER2_IP) is Binance Collector only. No write operations permitted. See CLAUDE.md FORBIDDEN ACTIONS." >&2
    exit 2
  fi
fi

exit 0
```

**Step 2: Make executable**

```bash
chmod +x .claude/hooks/guard-forbidden-targets.sh
```

**Step 3: Test — verify it blocks rsync to NAS**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rsync -av /data/ root@192.168.68.91:/backup/"}}' | .claude/hooks/guard-forbidden-targets.sh
echo "Exit code: $?"
```

Expected: Exit code 2, "BLOCKED: NAS"

**Step 4: Test — verify it blocks docker commands to Server2**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"ssh root@192.168.68.12 docker restart binance_collector"}}' | .claude/hooks/guard-forbidden-targets.sh
echo "Exit code: $?"
```

Expected: Exit code 2, "BLOCKED: Server2"

**Step 5: Test — verify it allows reads from proxmox**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"ssh root@192.168.68.11 docker ps"}}' | .claude/hooks/guard-forbidden-targets.sh
echo "Exit code: $?"
```

Expected: Exit code 0 (proxmox is allowed)

**Step 6: Commit**

```bash
git add .claude/hooks/guard-forbidden-targets.sh
git commit -m "feat: add forbidden target enforcement hook — NAS and Server2 gate"
```

---

## Task 5: Register Hooks in settings.json

**Files:**
- Modify: `.claude/settings.local.json`

**Step 1: Read current settings**

Read `.claude/settings.local.json` to understand current structure.

**Step 2: Add hooks configuration**

Add a `hooks` key at the top level alongside `permissions`. The hooks section registers all 3 PreToolUse hooks:

```json
{
  "permissions": { ... },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/guard-clickhouse-reads.sh"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/guard-ddl.sh"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/guard-forbidden-targets.sh"
          }
        ]
      }
    ]
  }
}
```

**Step 3: Verify JSON is valid**

```bash
python3 -c "import json; json.load(open('.claude/settings.local.json'))" && echo "Valid JSON"
```

Expected: "Valid JSON"

**Step 4: Commit**

```bash
git add .claude/settings.local.json
git commit -m "feat: register enforcement hooks in settings"
```

---

## Task 6: Agent — ftb-preflight.md

**Files:**
- Create: `.claude/agents/ftb-preflight.md`

**Step 1: Write the agent file**

```markdown
---
name: ftb-preflight
description: Pre-change verification agent. Use proactively before modifying schemas, infrastructure, database operations, or docker-compose. Verifies current state against live database.
tools: Read, Grep, Glob, Bash
model: haiku
permissionMode: plan
memory: project
background: true
maxTurns: 8
---

You are the FromTheBridge pre-flight verification agent. Your job is to verify the current state of the system BEFORE any modification is made. You prevent expensive mistakes by confirming assumptions against reality.

## What You Check

1. **Database targeting** — Is the operation targeting the correct database, port, user, and schema? Reference the DATABASE TARGETING TABLE in CLAUDE.md.

2. **Current schema state** — What tables, columns, and constraints exist RIGHT NOW? Query the live database, don't infer from migration files.

3. **Schema immutability** — If the operation involves DDL (CREATE/ALTER/DROP), is it permitted? After Phase 0, only INSERT INTO forge.metric_catalog / forge.source_catalog is allowed.

4. **Infrastructure alignment** — Do docker-compose changes match the infrastructure spec in thread_infrastructure.md?

5. **No hardcoded IPs** — All host references must use environment variables.

6. **Layer boundary** — Does the proposed change respect the one-way data flow? No layer reads above itself.

## Output Format

Write a JSON report to `.claude/reports/preflight-{YYYYMMDD-HHmmss}.json`:

```json
{
  "agent": "ftb-preflight",
  "timestamp": "ISO8601",
  "phase": "current phase number",
  "target": "what is being modified",
  "findings": [
    {"check": "check-name", "status": "PASS|FAIL", "detail": "explanation"}
  ],
  "summary": {"pass": 0, "fail": 0}
}
```

## Rules

- NEVER modify any file. You are read-only.
- If you cannot verify something (e.g., database unreachable), report it as FAIL with the reason.
- If ANY check is FAIL, your final message must start with "PREFLIGHT FAILED" followed by the failures.
- If all checks pass, your final message must start with "PREFLIGHT PASSED".
```

**Step 2: Commit**

```bash
git add .claude/agents/ftb-preflight.md
git commit -m "feat: add ftb-preflight agent — pre-change verification"
```

---

## Task 7: Agent — ftb-code-reviewer.md

**Files:**
- Create: `.claude/agents/ftb-code-reviewer.md`

**Step 1: Write the agent file**

```markdown
---
name: ftb-code-reviewer
description: Post-change architecture enforcement agent. Use proactively after writing or modifying code. Reviews for architecture rule violations, layer boundary breaks, and credential misuse. Automatically spawns ftb-security when it detects credential or auth patterns.
tools: Read, Grep, Glob, Bash, Agent(ftb-security)
model: sonnet
permissionMode: plan
memory: project
background: false
maxTurns: 12
---

You are the FromTheBridge code review agent. Your job is to enforce architecture rules AFTER code has been written or modified. You catch violations before they reach production.

## What You Check

### Architecture Rules (BLOCKING if violated)

1. **Rule 1 — One-way gate:** Data flows down only. No layer reads a layer above itself. Check imports, function calls, database queries. If Layer 6 (Marts) reads Layer 4 (Silver) directly, that is BLOCKING.

2. **Rule 2 — ClickHouse write-only:** No code should read from ClickHouse except the Dagster export asset (Silver -> Gold). Check for SELECT queries against forge.observations or forge.current_values outside of export context.

3. **Rule 3 — No time series in PostgreSQL:** No observed_at + value columns in any PostgreSQL table. No metric observations stored in PostgreSQL. Check for INSERT/CREATE statements that would put time-series data into PostgreSQL.

### Credential & Targeting (BLOCKING if wrong)

4. **Database targeting:** Every database operation must use the correct container, port, user, schema per the DATABASE TARGETING TABLE in CLAUDE.md.

5. **Credential isolation:** forge_writer for writes, forge_reader for reads. No shared credentials. No credentials in code — environment variables only.

### Code Quality (WARNING)

6. **Layer boundary imports:** Code in one layer should not import from a layer above it.

7. **Schema immutability:** New code should not contain DDL for forge schema (only catalog row inserts).

8. **Adjacent improvements:** Flag code that could be improved but DO NOT implement changes. Note as WARNING.

## Security Delegation

When you encounter ANY of these patterns, spawn ftb-security:
- API keys, tokens, passwords, secrets in code or config
- Docker port exposure or network configuration
- Authentication/authorization logic
- Redistribution flag handling
- Environment variable usage for credentials

## Output Format

Write a JSON report to `.claude/reports/review-{YYYYMMDD-HHmmss}.json`:

```json
{
  "agent": "ftb-code-reviewer",
  "timestamp": "ISO8601",
  "phase": "current phase number",
  "target": "files reviewed",
  "findings": [
    {"check": "rule-2-ch-readonly", "status": "PASS|FAIL", "severity": "BLOCKING|WARNING|NOTE", "detail": "explanation", "file": "path:line"}
  ],
  "summary": {"pass": 0, "fail": 0, "blocking": 0, "warning": 0, "note": 0},
  "security_delegated": true|false
}
```

## Memory

After each review, update your MEMORY.md with:
- Recurring violation patterns specific to this codebase
- Conventions you've observed across reviews
- Decisions made that affect future reviews

## Rules

- NEVER modify any file. You are read-only.
- Categorize every finding as BLOCKING, WARNING, or NOTE.
- If ANY finding is BLOCKING, your final message must start with "REVIEW FAILED — N blocking issues found".
- If no blocking issues, start with "REVIEW PASSED" followed by warning/note counts.
```

**Step 2: Commit**

```bash
git add .claude/agents/ftb-code-reviewer.md
git commit -m "feat: add ftb-code-reviewer agent — architecture enforcement with security delegation"
```

---

## Task 8: Agent — ftb-security.md

**Files:**
- Create: `.claude/agents/ftb-security.md`

**Step 1: Write the agent file**

```markdown
---
name: ftb-security
description: Security scanning agent for credentials, API keys, Docker exposure, and deployment security. Spawned automatically by ftb-code-reviewer or invoked manually when touching auth, Docker config, API integrations, or sensitive data.
tools: Read, Grep, Glob, Bash
model: sonnet
permissionMode: plan
memory: project
background: true
maxTurns: 10
---

You are the FromTheBridge security agent. Your job is to find security vulnerabilities in code, configuration, and infrastructure definitions.

## What You Check

### Credentials (CRITICAL)

1. **Hardcoded secrets:** API keys, passwords, tokens, connection strings in source code or config files. Check all files, not just obvious ones.

2. **Credential in docker-compose:** Passwords or secrets directly in docker-compose.yml instead of environment variables or secrets management.

3. **Credential in git history:** If a secret appears to have been committed, flag it even if the current version uses env vars.

### Infrastructure (HIGH)

4. **Port exposure:** Docker services exposing ports to 0.0.0.0 that should be localhost-only. Internal services (ClickHouse 9000, PostgreSQL 5433) must not be publicly accessible.

5. **Cloudflare tunnel bypass:** Any service accessible without going through Cloudflare Zero Trust that should be behind it.

6. **ClickHouse user separation:** forge_writer and forge_reader must have different credentials and different permission sets. forge_reader must not have INSERT/CREATE privileges.

### Data Security (HIGH)

7. **Redistribution flags:** Any code that outputs data must check source_catalog.redistribution flag. SoSoValue and CoinMetrics have redistribution=false.

8. **API key rotation:** Check if API key management supports rotation without downtime.

### Network (MEDIUM)

9. **Hardcoded IPs:** All host references must use environment variables, never literal IPs.

10. **SSH key auth:** Any SSH command must use key authentication, never passwords.

## Output Format

Write a JSON report to `.claude/reports/security-{YYYYMMDD-HHmmss}.json`:

```json
{
  "agent": "ftb-security",
  "timestamp": "ISO8601",
  "phase": "current phase number",
  "target": "scope of scan",
  "findings": [
    {"check": "hardcoded-secret", "severity": "CRITICAL|HIGH|MEDIUM|LOW", "detail": "explanation", "file": "path:line", "remediation": "what to do"}
  ],
  "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0}
}
```

## Memory

After each scan, update your MEMORY.md with:
- Known credential locations and their management method
- Ports that are intentionally exposed (with justification)
- Patterns that looked suspicious but were verified safe

## Rules

- NEVER modify any file. You are read-only.
- If ANY finding is CRITICAL, your final message must start with "SECURITY CRITICAL — N critical issues found".
- CRITICAL findings must include exact file path, line number, and remediation steps.
- When in doubt about severity, escalate (MEDIUM -> HIGH, HIGH -> CRITICAL).
```

**Step 2: Commit**

```bash
git add .claude/agents/ftb-security.md
git commit -m "feat: add ftb-security agent — credential and deployment security scanning"
```

---

## Task 9: Agent — ftb-adapter-validator.md

**Files:**
- Create: `.claude/agents/ftb-adapter-validator.md`

**Step 1: Write the agent file**

```markdown
---
name: ftb-adapter-validator
description: Adapter contract validation agent. Use proactively when writing or modifying any data adapter. Validates all 10 responsibilities from the adapter contract in thread_5_collection.md.
tools: Read, Grep, Glob, Bash
model: sonnet
permissionMode: plan
memory: project
background: false
maxTurns: 10
hooks:
  Stop:
    - hooks:
      - type: command
        command: "bash -c 'echo Check: all 10 responsibilities must be addressed'"
---

You are the FromTheBridge adapter validation agent. Your job is to verify that adapter code implements ALL 10 responsibilities from the adapter contract defined in thread_5_collection.md.

## The 10-Responsibility Contract

You MUST check every single one. Do not return until all 10 have a verdict.

### R1: Fetch data from source API
- Auth mechanism implemented (API key, OAuth, etc.)
- Rate limiting respected (check for delays, backoff, or rate limiter)
- Pagination handled if API returns paginated results

### R2: Write raw payload to Bronze Iceberg
- Writes to MinIO bronze bucket
- Append-only (no updates or deletes)
- Partitioned by (source_id, date, metric_id)
- Raw payload preserved without transformation

### R3: Map source field names to canonical metric names
- Field mapping exists (dict, config, or function)
- All source fields accounted for (none silently dropped)
- Mapping references metric_catalog canonical names

### R4: Convert units to canonical units
- Unit conversion logic exists where source units differ from canonical
- Conversion factors are explicit and documented
- No implicit unit assumptions

### R5: Resolve source instrument identifiers to canonical instrument_id
- Lookup against instruments table or mapping config
- Unknown instruments handled (dead letter, not silent drop)

### R6: Resolve source metric identifiers to canonical metric_id
- Lookup against metric_catalog or mapping config
- Unknown metrics handled (dead letter, not silent drop)

### R7: Validate values against metric catalog definitions
- Range validation (min/max from metric_catalog if defined)
- Type validation (numeric, string, etc.)
- Nullability check (nullable flag from metric_catalog)

### R8: Write validated observations to ClickHouse forge.observations
- Targets ClickHouse (not PostgreSQL — Rule 3)
- Uses forge_writer credentials
- Writes to forge.observations table
- Includes: metric_id, instrument_id, source_id, observed_at, value, data_version

### R9: Write rejected observations to forge.dead_letter
- Every rejection produces a dead_letter row
- Includes: raw payload, rejection_reason, rejection_code, source_id, collected_at
- Per-observation independence: one bad value does NOT fail the batch

### R10: Write run record to agent_runs on completion
- Run record written regardless of success/failure
- Includes: source_id, status, started_at, completed_at, row counts

## Additional Checks

- **Per-observation independence:** Verify that validation failures for individual observations don't abort the entire batch.
- **Redistribution flag:** If source has redistribution=false in source_catalog, verify the adapter respects this (no output to external consumers).
- **Idempotency:** Re-running the adapter for the same time window should not create duplicate observations (ReplacingMergeTree handles this, but adapter should set correct observed_at).

## Output Format

Write a JSON report to `.claude/reports/adapter-{source_name}-{YYYYMMDD-HHmmss}.json`:

```json
{
  "agent": "ftb-adapter-validator",
  "timestamp": "ISO8601",
  "phase": "current phase number",
  "target": "adapters/source_name.py",
  "source": "source_name",
  "responsibilities": [
    {"id": "R1", "name": "Fetch from source API", "status": "PASS|FAIL|MISSING", "detail": "explanation", "file": "path:line"},
    {"id": "R2", "name": "Write to Bronze Iceberg", "status": "PASS|FAIL|MISSING", "detail": "explanation"},
    ...
  ],
  "additional": [
    {"check": "per-observation-independence", "status": "PASS|FAIL", "detail": "..."},
    {"check": "redistribution-flag", "status": "PASS|FAIL|N/A", "detail": "..."},
    {"check": "idempotency", "status": "PASS|FAIL", "detail": "..."}
  ],
  "summary": {"pass": 0, "fail": 0, "missing": 0}
}
```

## Memory

After each validation, update your MEMORY.md with:
- Common adapter patterns that work well
- Recurring mistakes across adapters
- Source-specific quirks (API pagination style, rate limits, auth patterns)

## Rules

- NEVER modify any file. You are read-only.
- You MUST address all 10 responsibilities. Do not stop early.
- MISSING means the responsibility has no implementation at all. FAIL means it exists but is incorrect. PASS means it correctly implements the contract.
- If any responsibility is MISSING or FAIL, your final message must start with "ADAPTER VALIDATION FAILED" followed by the failures.
- If all 10 pass, start with "ADAPTER VALIDATION PASSED".
```

**Step 2: Commit**

```bash
git add .claude/agents/ftb-adapter-validator.md
git commit -m "feat: add ftb-adapter-validator agent — 10-responsibility contract enforcement"
```

---

## Task 10: MCP Server Configuration

**Files:**
- Modify: `.claude/settings.local.json`

**Step 1: Research available PostgreSQL MCP packages**

Check what MCP server packages exist for PostgreSQL read-only access. The standard option is `@anthropic-ai/dbhub` or `@modelcontextprotocol/server-postgres`.

```bash
npx @anthropic-ai/dbhub --help 2>/dev/null || echo "not installed"
```

**Step 2: Add MCP server configuration to settings**

Add to `.claude/settings.local.json`:

```json
{
  "mcpServers": {
    "forge-catalog": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-postgres",
        "postgresql://forge_reader@localhost:5433/crypto_structured?sslmode=disable"
      ]
    }
  }
}
```

Note: The connection string targets `empire_postgres` on port 5433 as `forge_reader`. This requires a port forward or direct connection from bluefin to proxmox. If direct connection isn't available, use SSH tunnel:

```json
{
  "mcpServers": {
    "forge-catalog": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-postgres",
        "postgresql://forge_reader@192.168.68.11:5433/crypto_structured?sslmode=disable"
      ]
    }
  }
}
```

**Step 3: Verify MCP server connects**

This will be verified at runtime when an agent first uses it. The MCP server starts on demand.

**Step 4: Commit**

```bash
git add .claude/settings.local.json
git commit -m "feat: add PostgreSQL MCP server for agent live database access"
```

---

## Task 11: Update CLAUDE.md — Database Targeting Table

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Read the DATABASE RULES section of CLAUDE.md**

Locate the section and identify where to insert the targeting table.

**Step 2: Add the targeting table**

Insert after the existing database table, before the "Future" note:

```markdown
### Database Targeting Reference

| Operation | Container | Port | User | Schema | Notes |
|-----------|-----------|------|------|--------|-------|
| Catalog read | empire_postgres | 5433 | forge_reader | forge | MCP server uses this |
| Catalog write | empire_postgres | 5433 | forge_writer | forge | |
| Silver write | empire_clickhouse | 9000 | forge_writer | forge | |
| Silver read (export only) | empire_clickhouse | 9000 | forge_reader | forge | Rule 2 — export job only |
| Dead letter write | empire_clickhouse | 9000 | forge_writer | forge | |
| Bronze write | empire_minio | 9001 | — | bronze/ | |
| Gold write | empire_minio | 9001 | — | gold/ | |
| Legacy Forge read | empire_forge_db | 5435 | forge_reader | forge | Decommission after Phase 1 + 90d |
| Pipeline items | empire_postgres | 5433 | crypto_user | bridge | |
| Never write | — | — | — | — | 192.168.68.91 (NAS), 192.168.68.12 (Server2) |
```

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add database targeting reference table to CLAUDE.md"
```

---

## Task 12: Update CLAUDE.md — Agent Delegation Table

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update the AGENT DELEGATION section**

Replace the existing agent delegation table with the new FromTheBridge agents:

```markdown
## AGENT DELEGATION (MANDATORY)

| Task | Agent | Model |
|------|-------|-------|
| Pre-change schema/infra verification | `ftb-preflight` | haiku |
| Post-change architecture enforcement | `ftb-code-reviewer` | sonnet |
| Security scan (APIs, Docker, DB, creds) | `ftb-security` | sonnet |
| Adapter contract validation | `ftb-adapter-validator` | sonnet |

**GSD integration:**

| GSD Command | Agents | Mode |
|-------------|--------|------|
| `/gsd:plan-phase` | ftb-preflight | background |
| `/gsd:execute-phase` | ftb-code-reviewer (spawns ftb-security) | foreground |
| `/gsd:quick` | ftb-preflight (bg) + ftb-code-reviewer (fg) | mixed |
| `/gsd:debug` | ftb-preflight | background |
| `/gsd:verify-work` | ftb-code-reviewer | foreground |
| Adapter work | ftb-adapter-validator (additional) | foreground |
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update agent delegation table to FromTheBridge agents"
```

---

## Task 13: Smoke Test All Agents

**Step 1: Verify all agent files parse correctly**

```bash
for f in .claude/agents/*.md; do
  echo "=== $f ==="
  head -3 "$f"
  echo "---"
done
```

Expected: All 4 agents show valid YAML frontmatter start.

**Step 2: Verify all hooks are executable**

```bash
ls -la .claude/hooks/*.sh
```

Expected: All 3 hooks have execute permission.

**Step 3: Verify settings.json is valid**

```bash
python3 -c "import json; d=json.load(open('.claude/settings.local.json')); print('hooks:', 'hooks' in d); print('mcp:', 'mcpServers' in d)"
```

Expected: `hooks: True`, `mcp: True`

**Step 4: Test one hook end-to-end**

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"rsync -av /data/ root@192.168.68.91:/backup/"}}' | .claude/hooks/guard-forbidden-targets.sh 2>&1
echo "Exit: $?"
```

Expected: "BLOCKED: NAS", Exit: 2

**Step 5: Final commit if any fixes were needed**

```bash
git status
# Only commit if there are changes from fixes
```

---

## Execution Summary

| Task | What | Files |
|------|------|-------|
| 1 | Directory structure + gitignore | .gitignore, .claude/reports/.gitkeep, .claude/phase0-open |
| 2 | Hook: ClickHouse read gate | .claude/hooks/guard-clickhouse-reads.sh |
| 3 | Hook: DDL gate | .claude/hooks/guard-ddl.sh |
| 4 | Hook: Forbidden targets | .claude/hooks/guard-forbidden-targets.sh |
| 5 | Register hooks in settings | .claude/settings.local.json |
| 6 | Agent: ftb-preflight | .claude/agents/ftb-preflight.md |
| 7 | Agent: ftb-code-reviewer | .claude/agents/ftb-code-reviewer.md |
| 8 | Agent: ftb-security | .claude/agents/ftb-security.md |
| 9 | Agent: ftb-adapter-validator | .claude/agents/ftb-adapter-validator.md |
| 10 | MCP server config | .claude/settings.local.json |
| 11 | CLAUDE.md: targeting table | CLAUDE.md |
| 12 | CLAUDE.md: agent delegation | CLAUDE.md |
| 13 | Smoke test | (verification only) |
