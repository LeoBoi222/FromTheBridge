# Agent Team Design — FromTheBridge

**Date:** 2026-03-06
**Status:** Approved
**Scope:** Project-level agent team, enforcement hooks, MCP integration, structured reporting

---

## Overview

Four specialized agents with project-level enforcement hooks, PostgreSQL MCP access, agent-to-agent delegation, background execution, and structured JSON reporting. Designed to enforce the FromTheBridge 9-layer architecture rules mechanically — not just review them.

## Design Principles

1. **Enforce, don't just check.** Project-level hooks make the 3 hard rules actually hard — commands that violate them don't execute.
2. **Verify live state, don't infer from files.** PostgreSQL MCP gives agents read-only database access for real-time schema verification.
3. **Self-organizing reviews.** Code reviewer auto-spawns security agent when it detects credential/auth patterns.
4. **Non-blocking where possible.** Read-only agents run in background.
5. **Audit trail.** All agents write JSON reports for phase gate certification.
6. **CLAUDE.md is the shared truth.** No separate skill files — agents inherit project rules from CLAUDE.md.

---

## A. Project-Level Hooks

Three PreToolUse hooks on Bash in `settings.json`. Fire on ALL tool use in the project.

### Hook 1 — ClickHouse Read Gate (Rule 2)

**File:** `.claude/hooks/guard-clickhouse-reads.sh`
**Trigger:** PreToolUse on Bash
**Behavior:** Parses command for ClickHouse read patterns (`SELECT FROM forge.observations`, `clickhouse-client` with SELECT). Allows only if command matches the export job context. Exit 2 = blocked with explanation.

### Hook 2 — DDL Gate (Schema Immutability)

**File:** `.claude/hooks/guard-ddl.sh`
**Trigger:** PreToolUse on Bash
**Behavior:** Blocks `CREATE TABLE`, `ALTER TABLE`, `DROP TABLE` targeting forge schema. Only `INSERT INTO forge.metric_catalog` / `forge.source_catalog` allowed. Gate controlled by flag file `.claude/phase0-open` — while file exists, DDL is permitted. Delete file after Phase 0 gate passes to activate enforcement.

### Hook 3 — Forbidden Target Gate

**File:** `.claude/hooks/guard-forbidden-targets.sh`
**Trigger:** PreToolUse on Bash
**Behavior:** Blocks commands containing write operations to `192.168.68.91` (NAS) or `192.168.68.12` (Server2).

### Hook Scripts Location

```
.claude/hooks/
├── guard-clickhouse-reads.sh
├── guard-ddl.sh
└── guard-forbidden-targets.sh
```

---

## B. MCP Server

**PostgreSQL read-only** — Configured at project level. Connects to `empire_postgres:5433` as `forge_reader`. Available to ftb-preflight and ftb-code-reviewer. Enables live schema verification, catalog lookups, and current-state checks without SSH/docker exec chains.

---

## C. Agents

### Agent 1 — ftb-preflight

| Field | Value |
|-------|-------|
| Model | haiku |
| Tools | Read, Grep, Glob, Bash + PostgreSQL MCP |
| Permission mode | plan (read-only) |
| Memory | project |
| Background | true |
| maxTurns | 8 |

**Purpose:** Pre-change verification. Run before modifying schemas, infrastructure, or database operations.

**Checklist:**
- Query live database to verify current state
- Confirm target database is correct for the operation
- Verify no DDL changes violate schema immutability
- Validate docker-compose changes against infrastructure spec
- Confirm no hardcoded IPs
- Output: JSON report to `.claude/reports/preflight-{timestamp}.json`

### Agent 2 — ftb-code-reviewer

| Field | Value |
|-------|-------|
| Model | sonnet |
| Tools | Read, Grep, Glob, Bash, Agent(ftb-security) + PostgreSQL MCP |
| Permission mode | plan (read-only) |
| Memory | project |
| Background | false |
| maxTurns | 12 |

**Purpose:** Post-change architecture enforcement. Run after code is written or modified.

**Delegation:** Automatically spawns ftb-security when it encounters credential, auth, API key, or Docker security patterns.

**Checklist:**
- Verify data flow direction (no upward reads across layers)
- Check credential usage matches database targeting rules
- Validate layer boundaries in imports and function calls
- Check for Rule 2 violations (ClickHouse reads outside export)
- Check for Rule 3 violations (time-series patterns in PostgreSQL)
- Flag adjacent improvements without implementing
- Update memory with recurring patterns
- Output: JSON report to `.claude/reports/review-{timestamp}.json` (BLOCKING / WARNING / NOTE)

### Agent 3 — ftb-security

| Field | Value |
|-------|-------|
| Model | sonnet |
| Tools | Read, Grep, Glob, Bash |
| Permission mode | plan (read-only) |
| Memory | project |
| Background | true |
| maxTurns | 10 |

**Purpose:** Credential, API, and deployment security. Spawned by ftb-code-reviewer or invoked manually.

**Checklist:**
- Scan for hardcoded credentials, API keys, passwords
- Validate docker-compose doesn't expose internal ports
- Verify ClickHouse user separation (forge_writer vs forge_reader)
- Check redistribution flags before data output code
- Validate environment variable usage (no hardcoded IPs)
- Verify Cloudflare tunnel config
- Output: JSON report to `.claude/reports/security-{timestamp}.json` (CRITICAL / HIGH / MEDIUM / LOW)

### Agent 4 — ftb-adapter-validator

| Field | Value |
|-------|-------|
| Model | sonnet |
| Tools | Read, Grep, Glob, Bash + PostgreSQL MCP |
| Permission mode | plan (read-only) |
| Memory | project |
| Background | false |
| maxTurns | 10 |
| Stop hook | Verifies all 10 responsibilities addressed before returning |

**Purpose:** Validate adapter code against the 10-responsibility contract.

**Checklist (the 10 responsibilities):**
1. Fetch data from source API (auth, rate limiting, pagination)
2. Write raw payload to Bronze Iceberg (append-only, partitioned by source/date/metric)
3. Map source-specific field names to canonical metric names
4. Convert units to canonical units
5. Resolve source instrument identifiers to canonical instrument_id
6. Resolve source metric identifiers to canonical metric_id
7. Validate values against metric catalog definitions (range, type, nullability)
8. Write validated observations to ClickHouse forge.observations
9. Write rejected observations to ClickHouse forge.dead_letter with rejection code + raw payload
10. Write run record to agent_runs on completion

**Additional checks:**
- Per-observation validation independence (one bad value doesn't fail batch)
- Query catalog via MCP to verify metric/instrument IDs resolve
- Output: JSON report to `.claude/reports/adapter-{timestamp}.json` — 10-item PASS/FAIL/MISSING

---

## D. Report Schema

All agents write to `.claude/reports/` (gitignored). Standard envelope:

```json
{
  "agent": "ftb-preflight",
  "timestamp": "2026-03-06T14:30:00Z",
  "phase": "1",
  "target": "adapters/coinalyze.py",
  "findings": [
    {
      "check": "database-target",
      "status": "PASS",
      "detail": "Silver write correctly targets ClickHouse forge_writer"
    }
  ],
  "summary": { "pass": 5, "fail": 0, "warning": 1, "blocked": 0 }
}
```

Phase gate certification references accumulated reports.

---

## E. Database Targeting Table

Added to CLAUDE.md DATABASE RULES section:

| Operation | Container | Port | User | Schema | Notes |
|-----------|-----------|------|------|--------|-------|
| Catalog read | empire_postgres | 5433 | forge_reader | forge | MCP server uses this |
| Catalog write | empire_postgres | 5433 | forge_writer | forge | |
| Silver write | empire_clickhouse | 9000 | forge_writer | forge | |
| Silver read (export only) | empire_clickhouse | 9000 | forge_reader | forge | Rule 2 |
| Dead letter write | empire_clickhouse | 9000 | forge_writer | forge | |
| Bronze write | empire_minio | 9001 | — | bronze/ | |
| Gold write | empire_minio | 9001 | — | gold/ | |
| Legacy Forge read | empire_forge_db | 5435 | forge_reader | forge | Decommission after Phase 1 + 90d |
| Pipeline items | empire_postgres | 5433 | crypto_user | bridge | |
| Never write | — | — | — | — | 192.168.68.91 (NAS), 192.168.68.12 (Server2) |

---

## F. GSD Integration

| GSD Command | Agents | Mode |
|-------------|--------|------|
| /gsd:plan-phase | ftb-preflight | background |
| /gsd:execute-phase | ftb-code-reviewer (spawns ftb-security as needed) | foreground |
| /gsd:quick | ftb-preflight (background) then ftb-code-reviewer (foreground) | mixed |
| /gsd:debug | ftb-preflight | background |
| /gsd:verify-work | ftb-code-reviewer | foreground |
| Adapter work | ftb-adapter-validator (in addition to above) | foreground |

---

## G. File Layout

```
.claude/
├── agents/
│   ├── ftb-preflight.md
│   ├── ftb-code-reviewer.md
│   ├── ftb-security.md
│   └── ftb-adapter-validator.md
├── hooks/
│   ├── guard-clickhouse-reads.sh
│   ├── guard-ddl.sh
│   └── guard-forbidden-targets.sh
├── reports/                          # gitignored, agent output accumulates here
├── agent-memory/                     # project-level, per-agent persistent memory
│   ├── ftb-preflight/
│   ├── ftb-code-reviewer/
│   ├── ftb-security/
│   └── ftb-adapter-validator/
└── phase0-open                       # delete to activate DDL gate
```

---

## H. Future Additions

| Trigger | Addition |
|---------|----------|
| Testing framework chosen | ftb-test-writer agent |
| Phase 1 Dagster deployed | ftb-dagster-checker agent |
| Phase 2 features | ftb-feature-rules added to code-reviewer memory |
| ClickHouse MCP available | Add to ftb-preflight for Silver verification |
