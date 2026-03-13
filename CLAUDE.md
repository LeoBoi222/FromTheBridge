# CLAUDE.md — FromTheBridge Project Rules

## PROJECT IDENTITY

FromTheBridge is a data lakehouse for crypto market intelligence — a 9-layer stack that transforms data into signals.

**FTB does not collect data.** EDS collects from external sources → `empire.observations` → FTB consumes via `empire_to_forge_sync` → `forge.observations`. FTB owns Silver (Layer 4) upward. **If you think FTB needs a source adapter: STOP. It belongs in EDS.**

**Two repos, one system:**
- **EDS** (`/var/home/stephen/Projects/EmpireDataServices/`) — collection, adapters, on-chain. Owns `empire.*`.
- **FTB** (this repo) — lakehouse, features, signals, serving. Owns `forge.*` + `forge` PG schema + MinIO.
- **Nexus-Council** — legacy monolith. Manages shared infra containers. Not design authority.

**Design document:** `docs/design/FromTheBridge_design_v4.0.md` is the SSOT. Architecture, phase gates, source list, and signal design live there — not here.

---

## THREE HARD RULES

**Rule 1 — One-way gate:** Data flows down only. Feature compute reads Gold, never Silver. Serving reads Marts. No exceptions.

**Rule 2 — No application service reads forge.* in ClickHouse.** Only Dagster assets: `ch_export_reader` (export) and `ch_ops_reader` (health). Enforced by `guard-clickhouse-reads.sh` hook.

**Rule 3 — No time series in forge.* PostgreSQL.** The `forge` schema holds relational integrity only. No `observed_at + value` columns.

---

## GOVERNANCE SEQUENCE

These steps are non-negotiable. No rationalizing past them. No "this case is simple enough to skip." Every task, regardless of apparent simplicity:

1. **Read SSOT** — find the relevant v4.0 section. If it doesn't exist, flag it.
2. **State 3-bullet plan** — what you'll do, in what order. Wait for confirmation when touching credentials, Docker networking, database targeting, or changes spanning 3+ systems.
3. **Pre-flight** — verify current state before modifying. Do not assume.
4. **Execute** — build only what the prompt specifies. Flag adjacent improvements — do not implement.
5. **Verify against SSOT** — confirm the result matches v4.0, not just "it works."
6. **Update state records** — any commit that changes phase status, deployed services, or clears a blocker must update `.claude/state/phase-status.md` in the same commit. Not a follow-up commit — the same one. Gate criteria pass/fail updates go in v4.0 §Phase Gates at gate passage events. If you can't update state in the same commit, stop and flag.

**Build from the design doc, not plan files.** v4.0 is the plan. Do not create `docs/plans/` files. If v4.0 is missing detail, amend v4.0.

**Code discipline:** Service modules: 800 lines max. Routers: 200 lines max. Re-plan if exceeding.

---

## FORBIDDEN ACTIONS

- Deploy without local build + test on bluefin
- Edit code on proxmox
- Target NAS (192.168.68.91) for any writes
- Deploy FTB services to Server2 (192.168.68.12) — EDS only
- Build source adapters in FTB — they belong in EDS
- Create tables that duplicate existing data
- Self-certify phase gates
- Read/write `archive` schema
- Modify DDL after Phase 0 gate (new metrics/sources = catalog rows only)
- Hardcode IPs in application code

Rules 2 and 3 (ClickHouse read isolation, no PG time series) are enforced in §THREE HARD RULES above — not repeated here.

---

## DATABASE TARGETING

| Operation | Container | Port | User | Schema |
|-----------|-----------|------|------|--------|
| Catalog read | empire_postgres | 5433 | forge_reader | forge |
| Catalog write | empire_postgres | 5433 | forge_user | forge |
| Silver write (sync) | empire_clickhouse | 8123 | ch_writer | forge |
| Silver read (export) | empire_clickhouse | 8123 | ch_export_reader | forge |
| Silver read (ops) | empire_clickhouse | 8123 | ch_ops_reader | forge |
| Dead letter write | empire_clickhouse | 8123 | ch_writer | forge |
| Bronze write | empire_minio | 9001 | bronze_writer | bronze-hot/ |
| Gold write | empire_minio | 9001 | export_writer | gold/ |
| **Never** | NAS (192.168.68.91) | — | — | — |
| **No FTB** | Server2 (192.168.68.12) | — | — | — |

Schema immutability: No DDL after Phase 0 gate. `archive` schema = frozen.

---

## DEVELOPMENT WORKFLOW

```
bluefin (develop + test) → rsync → proxmox (rebuild + deploy)
```

**NEVER** edit on proxmox. **NEVER** deploy without local test.

**SSH:** `ssh root@192.168.68.11` (key auth). `ssh root@192.168.68.12` (key auth).

**Sync:** `rsync -av --exclude='__pycache__' --exclude='.git' <src>/ root@192.168.68.11:/opt/empire/FromTheBridge/<src>/`

**Rebuild:** `ssh root@192.168.68.11 'cd /opt/empire/FromTheBridge && docker compose build <service> && docker compose up -d <service>'`

**DB migration (PG):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U forge_user -d crypto_structured"`

**DB migration (CH):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"`

**Python:** `uv` + `pyproject.toml` + `uv.lock`. Testing: `pytest`. Linting: `ruff`.

---

## LEGAL COMPLIANCE

Redistribution is a first-class constraint in export and serving code. Sources with `redistribution = false` in `forge.source_catalog` are excluded from all external data products — null values with flag, not omitted rows. Currently blocked: CoinMetrics. Tiingo clause must be verified before Phase 6 gate. See v4.0 §Redistribution for the three-state enum and response patterns.

---

## AGENTS

Defined in `.claude/agents/`. All read-only, write reports to `.claude/reports/`.

| Agent | Invoke when |
|-------|------------|
| `ftb-preflight` | Deploying new services, changing credentials, modifying Docker networking |
| `ftb-code-reviewer` | Multi-file changes that cross layer boundaries |
| `ftb-security` | Any change touching credentials, API keys, or Docker config |
| `ftb-sync-validator` | Modifying `empire_to_forge_sync`, writers, or validation logic |

Hooks (`.claude/hooks/`) fire automatically — no invocation needed. They enforce Rule 2, schema immutability, and forbidden targets.

---

## CURRENT STATE

Phase status, deployed infrastructure, and blockers: `.claude/state/phase-status.md` (version controlled, updated per §GOVERNANCE SEQUENCE step 6). Detailed gate criteria: v4.0 §Phase Gates.

---

## WHEN STUCK

Say "I need to stop and re-plan." State what's broken in 1 sentence. Propose 2-3 options. Wait for Stephen's choice.
