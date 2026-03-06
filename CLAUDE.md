# CLAUDE.md — FromTheBridge Project Rules

## PROJECT IDENTITY

FromTheBridge is a next-generation data lakehouse for crypto market intelligence. It is **not** the legacy Empire system (Nexus-Council). It is a peer project that replaces Empire's bottom-up Forge architecture with a top-down, consumer-first 9-layer stack.

**Relationship to Empire:** Shared infrastructure (same machines, same PostgreSQL). Forge DB (`empire_forge_db`, port 5435) is the shared boundary — FromTheBridge reads it via `forge_reader` during migration, then decommissions it. Empire's EDS, MAE, CAA, W6, Content Engine, Bridge, and Contracts are documented in the Empire CLAUDE.md, not here.

**Design documents:** All authoritative. Located in `docs/design/`.

| Document | Scope |
|----------|-------|
| `FromTheBridge_design_v2.md` | Canonical synthesis — all layers, all threads |
| `thread_infrastructure.md` | ADRs, deployment topology, hard rules |
| `thread_2_signal.md` | EDSx + ML signal architecture |
| `thread_3_features.md` | Feature engineering, PIT, null states |
| `thread_4_data_universe.md` | PostgreSQL catalog + ClickHouse Silver DDL |
| `thread_5_collection.md` | Adapters, Dagster, GE, BLC-01, migration |
| `thread_6_build_plan.md` | Phase gates, scope, duration estimates |
| `thread_7_output_delivery.md` | API, tiers, SLAs, first customer |
| `design_index.md` | Navigation and cross-reference only |

---

## ARCHITECTURE — 9-LAYER STACK

Data flows downward only. No layer reads a layer above itself.

| Layer | Name | Technology | What lives here |
|-------|------|------------|-----------------|
| 8 | Serving | FastAPI + DuckDB + Arrow Flight | `/v1/signals`, `/v1/timeseries`, webhooks, Telegram. Reads Gold + Marts only. Phase 6. |
| 7 | Catalog | PostgreSQL (`forge` schema) | 12 relational tables: assets, instruments, venues, metric_catalog, source_catalog, metric_lineage, etc. No time series — ever. |
| 6 | Marts | dbt (SQL) + forge_compute (Python) | Feature store. Rolling window, cross-sectional, breadth scores. PIT enforced. Reads Gold via DuckDB. |
| 5 | Gold | Iceberg on MinIO | Analytical layer. DuckDB reads here. Populated by 6h Silver → Gold export. |
| 4 | Silver | ClickHouse (ReplacingMergeTree) | Observation store. EAV: `(metric_id, instrument_id, observed_at, value)`. Bitemporal. Write-only except export job. |
| 3 | Bronze | Iceberg on MinIO | Raw landing. Append-only, 90-day retention. Partitioned by `(source_id, date, metric_id)`. |
| 2 | Adapters | Python (per-source) | 10-responsibility contract: auth, rate limiting, normalization, validation, Bronze write, Silver write, dead letter. |
| 1 | Orchestration | Dagster (Docker service) | Software-Defined Assets. One asset per `(metric_id, source_id)`. Freshness from `cadence_hours`. |
| 0 | Sources | External APIs | 10 v1 sources. See Data Sources section. |

### Three Hard Rules

**Rule 1 — One-way gate:** Data flows down only. Feature compute reads Gold (Layer 5), never Silver (Layer 4). Serving reads Marts (Layer 6). No exceptions. Enforced by Dagster asset dependency graph + credential isolation.

**Rule 2 — ClickHouse write-only:** The only process that reads ClickHouse is the Dagster export asset (Silver → Gold, every 6h). No other service has ClickHouse credentials. Unmerged `ReplacingMergeTree` rows would produce silently incorrect results.

**Rule 3 — No time series in PostgreSQL:** The catalog holds relational integrity only. No `observed_at + value` columns in any PostgreSQL table. No metric observations, no derived computations, no feature values.

### Two Signal Tracks

**EDSx (deterministic):** Five pillars (Trend/Structure, Liquidity/Flow, Valuation, Structural Risk, Tactical Macro) × 3 horizons. Rule-based scoring from feature layer.

**ML (probabilistic):** Five LightGBM domain models (Derivatives Pressure, Capital Flow Direction, Macro Regime, DeFi Stress, Volatility Regime). 14-day horizon. Walk-forward training. Independent of EDSx — no cross-contamination.

**Synthesis:** `final_score = 0.5 × edsx + 0.5 × ml`. Recalibrated quarterly. ML must graduate (30-day shadow, 5 hard criteria) before entering composite.

---

## CURRENT STATE

**As of:** 2026-03-06

| Phase | Description | Status | Gate Reference |
|-------|-------------|--------|----------------|
| Design | All 7 thread files complete | ✅ Complete | Architect confirmed 2026-03-05 |
| Phase 0 | Schema Foundation | ✅ Complete | thread_6 §Phase 0 Gate (13 criteria) |
| Phase 1 | Data Collection | ❌ Not started | thread_6 §Phase 1 Gate |
| Phase 2 | Feature Engineering | ❌ Not started | thread_6 §Phase 2 Gate |
| Phase 3 | EDSx Signal | ❌ Not started | thread_6 §Phase 3 Gate |
| Phase 4 | ML Track (Shadow) | ❌ Not started | thread_6 §Phase 4 Gate |
| Phase 5 | Serving | ❌ Not started | thread_6 §Phase 5 Gate |
| Phase 6 | Productization | ❌ Not started | thread_6 §Phase 6 Gate |

**What exists:**
- Forge DB schema deployed (`db/migrations/0001_phase0_schema.sql`)
- 74 metrics in catalog across 9 domains
- 14 sources in source catalog (10 v1 active + 4 reference/fallback)
- 12 PostgreSQL catalog tables created and seeded
- ClickHouse Silver schema deployed (observations, dead_letter, current_values)
- MinIO bronze + gold buckets initialized
- `docker-compose.yml` with Forge DB + ClickHouse services

**Known gaps (Phase 0 corrective):**
- ClickHouse DDL migration file location: `db/migrations/clickhouse/0001_silver_schema.sql`
- Dagster service definition not yet in docker-compose.yml (Phase 1)
- Great Expectations not yet configured (Phase 1)

**Blocking:** Polygon.io integration design session — must complete before Phase 1 build prompt.

---

## INFRASTRUCTURE

| Machine | IP | Role |
|---------|-----|------|
| proxmox | 192.168.68.11 | Production. All new-architecture services. GPU: RTX 3090 (24GB). |
| Server2 | 192.168.68.12 | Binance Collector only (LXC 203 + VPN). Single-purpose. |
| bluefin | 192.168.68.64 | Development |
| NAS | 192.168.68.91 | Backup destination only |

**Domain:** `fromthebridge.net` (Cloudflare tunnel → proxmox). **API:** `192.168.68.11:8000`.

**Cloudflare:** `cloudflared` is a systemd service (not Docker). Routes: `fromthebridge.net` → `:3002` (landing page), `/api/*` → `:8000`. Bridge behind Zero Trust — not public. Public bypasses: `/briefs`, `/launch`, forge/content APIs.

**Storage:**
| Mount | Capacity | Contents |
|-------|----------|---------|
| `/` | 4TB NVMe | OS, Docker engine, container layers |
| `/mnt/empire-db` | 2TB SSD | PostgreSQL, ClickHouse, Dagster metadata, Redis |
| `/mnt/empire-data` | 4TB SSD | MinIO (Bronze Iceberg + Gold Iceberg), Prometheus, Grafana, Gitea |

NFS mounts to NAS for bronze archives + backups (read-only — backup destination only).

**GPU:** RTX 3090, NVIDIA 580.126.18, CUDA 13.0. Container Toolkit installed (`--gpus all`). Nouveau blacklisted. Shutdown script: `scripts/proxmox_shutdown.sh`.

**New Docker services (this repo):**

| Service | Container | Port | Volume |
|---------|-----------|------|--------|
| Forge DB (legacy, read-only after Phase 1) | empire_forge_db | 5435 | forge_data |
| ClickHouse | empire_clickhouse | 8123 (HTTP), 9000 (native) | /mnt/empire-db/clickhouse |
| MinIO | empire_minio | 9001 (API), 9002 (console) | /mnt/empire-data/minio |
| Dagster webserver | empire_dagster_webserver | 3010 | /mnt/empire-db/dagster |
| Dagster daemon | empire_dagster_daemon | — | /mnt/empire-db/dagster |
| Dagster code server | empire_dagster_code | — | /opt/empire/pipeline |

---

## DEVELOPMENT WORKFLOW

```
bluefin (develop + test) → rsync → proxmox (rebuild + deploy)
```

**NEVER** edit on proxmox. **NEVER** deploy without local test.

**SSH:** `ssh root@192.168.68.11` (key auth)
**SSH:** `ssh root@192.168.68.12` (key auth)

**Sync:** `rsync -av --exclude='node_modules' --exclude='.next' --exclude='.git' <src>/ root@192.168.68.11:/opt/empire/FromTheBridge/<src>/`

**Rebuild:** `ssh root@192.168.68.11 'cd /opt/empire/FromTheBridge && docker compose build <service> && docker compose up -d <service>'`

**Verify tunnel:** `curl -s https://fromthebridge.net` — if down: `systemctl restart cloudflared`

**DB migration (PG):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_postgres psql -U crypto_user -d crypto_structured"`

**DB migration (TS):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_timescaledb psql -U crypto_user -d crypto_timeseries"`

**DB migration (ClickHouse):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"`

---

## DATABASE RULES

| Database | Container | Port | Contents |
|----------|-----------|------|----------|
| PostgreSQL | empire_postgres | 5433 | App state, instruments, catalog tables (`forge.*` schema) |
| TimescaleDB | empire_timescaledb | 5434 | Legacy time-series (Empire EDS) |
| Forge DB (legacy) | empire_forge_db | 5435 | Legacy raw data. Read-only during migration. Decommissioned after Phase 1 + 90 days. |
| ClickHouse | empire_clickhouse | 8123/9000 | Silver observation store: `forge.observations`, `forge.dead_letter`, `forge.current_values` |
| MinIO | empire_minio | 9001/9002 | Bronze (Iceberg) + Gold (Iceberg) object storage |

**Future:** ClickHouse Cloud, MinIO → S3, Dagster Cloud, PostgreSQL → RDS. All zero-code-change migrations (endpoint config swap). Triggers defined in `thread_infrastructure.md`.

`archive` schema = frozen. Do not read/write. Before creating any table: check if data exists elsewhere first.

**Forge catalog tables (PostgreSQL):** assets, asset_aliases, venues, instruments, source_catalog, metric_catalog, metric_lineage, event_calendar, supply_events, adjustment_factors, collection_events, instrument_metric_coverage. Write: `forge_user`. Read: `forge_reader`.

**ClickHouse Silver:** `forge.observations` (ReplacingMergeTree, ordering key `metric_id, instrument_id, observed_at`) · `forge.dead_letter` (MergeTree, TTL 90 days) · `forge.current_values` (AggregatingMergeTree, argMaxState — incremental on insert).

**Schema immutability:** No DDL changes after Phase 0 gate passes. New metrics and sources add catalog rows, not columns or tables.

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

---

## DATA SOURCES

### v1 Sources (10 active)

| Source | Tier | Data | Cadence | Cost |
|--------|------|------|---------|------|
| Coinalyze | T1 | Funding, OI, liquidations, L/S ratio (121 instruments) | 8h | Free |
| DeFiLlama | T1 | Stablecoins, DeFi protocols, TVL, DEX volume, lending | 12h | Free |
| FRED | T1 | 23 macro series (rates, FX, inflation, liquidity, commodities) | 24h+ | Free |
| Tiingo | T1 | OHLCV spot prices (dependency for unit normalization) | 6h | Paid |
| SoSoValue | T1 | ETF flows (BTC, ETH, SOL) | 24h | Free (non-commercial ToS) |
| Etherscan/Explorer | T2 | ETH + Arbitrum exchange flows (9 exchanges, 18 instruments) | 8h | Freemium |
| CoinPaprika | T1 | Market cap, sector, category metadata | 24h | Free |
| CoinMetrics | T2 | On-chain transfer volume (GitHub CSVs) | 24h | Free (redistribution blocked) |
| BGeometrics | T2 | MVRV, SOPR, NUPL, Puell Multiple | 24h | Free |
| Binance BLC-01 | T2 | Tick liquidations (WebSocket, ~65-72k events/day) | Real-time | Free |

**Reference/fallback (in catalog, not v1 active):** Glassnode, CoinGecko, KuCoin, Explorer (separate from Etherscan).

**Redistribution blocked:** SoSoValue (`redistribution = false`), CoinMetrics (`redistribution = false`). Excluded from all external data products until flags changed.

**Permanently excluded:** Santiment, BSCScan, Solscan (deprecated).

**Parked (paid, not in budget):** CoinGlass, CryptoQuant, CoinMarketCap.

---

## PIPELINE DISCIPLINE

- Every CC prompt includes "Pipeline Update" as final step
- Completion reports identify ALL resolved pipeline items + list every file created/modified/deleted
- Format: `UPDATE bridge.pipeline_items SET status = 'complete', completed_at = NOW(), decision_notes = '[what]' WHERE id = 'XX';`
- Every deferral gets a pipeline item with trigger condition. No unnamed deferrals.

**ID prefix conventions:**
| Prefix | Scope |
|--------|-------|
| `FRG-*` | Forge / data collection |
| `ML-*` | ML track |
| `LH-*` | Lakehouse infrastructure |
| `EDSx-*` | EDSx signal track |

Pipeline items live in `bridge.pipeline_items` in `empire_postgres`. Use `system_ids` array column to tag items for filtering (e.g., `'{fromthebridge}'`).

---

## CODE DISCIPLINE

- State plan in 3 bullets BEFORE coding
- **Pre-flight checks required.** Verify current schema/signature before modifying. Do not assume.
- Replace entire functions, never patch blocks. Minimal diffs.
- Build only what the prompt specifies. Flag adjacent improvements — do not implement.
- Service modules: 800 lines max. Routers: 200 lines max. Re-plan if exceeding.

**Python-specific:**
- Package management: `pyproject.toml` (tooling TBD — not yet chosen)
- Testing framework: TBD
- Linting: TBD

---

## PHASE GATES

Phase gates are hard pass/fail. No phase begins until the previous gate passes and the architect confirms. Self-certification is not permitted. Full gate criteria in `thread_6_build_plan.md`.

| Phase | Key Gate Criteria | Duration Est. |
|-------|-------------------|---------------|
| 0 — Schema | 13 criteria: 12 PG tables, 10 source rows, ≥50 metrics, Rule 3 check, CH 3 objects, write/read round-trip, dead_letter, current_values, PIT revision, MinIO buckets | 3–5 days |
| 1 — Collection | 3 Dagster services healthy, ≥1 asset/source, Tiingo Silver rows, all 10 sources Silver rows, Bronze Iceberg exists, GE checkpoint, dead letter captures, BLC-01 rsync, NAS backups (×2), CH credential isolation, full round-trip, `macro.credit.hy_oas` in FRED | 2–3 weeks |
| 2 — Features | Gold Iceberg readable by DuckDB, all dbt models pass, forge_compute produces features, null states tested, PIT audit passes, breadth scores verified | 2–3 weeks |
| 3 — EDSx | All 5 pillars scoring, confidence computation, regime classification, output contract conformant | 1–2 weeks |
| 4 — ML | All 5 models trained (walk-forward), graduation criteria on OOS, shadow mode deployed, ≥30 day shadow | 3–4 weeks |
| 5 — Serving | FastAPI endpoints, API key auth + tiers, redistribution filter, Arrow Flight, webhook + Telegram, provenance trace, staleness propagation | 1–2 weeks |
| 6 — Product | Health monitoring, methodology docs, ToS audit (all 10 sources), redistribution verified, first customer delivery | 1–2 weeks |

**Total estimate:** 13–20 weeks. Shadow period (Phase 4) is a floor.

---

## EMPIRE CROSS-REFERENCE

**Empire CLAUDE.md locations:**
- bluefin: `/var/home/stephen/Projects/Nexus-Council/CLAUDE.md`
- proxmox: `/opt/empire/Nexus-Council/CLAUDE.md`

**What lives in Empire (not here):** EDS, MAE, CAA, W6, Signal Gates, Reconciliation, Content Engine, Hunt Gates, Bridge UI, Contracts (`contract.*`, `metadata.*`), DisconnectDetector.

**What lives here (not Empire):** 9-layer lakehouse architecture, ClickHouse Silver, Iceberg Bronze/Gold, Dagster orchestration, dbt Marts, DuckDB serving, adapter contracts, feature engineering, EDSx v2, ML training pipeline.

**Shared boundary:** Forge DB (`empire_forge_db`, port 5435) via `forge_reader` role. FromTheBridge reads Forge during Phase 1 migration. After migration + 90-day window, Forge DB is decommissioned.

**Empire consumers of FromTheBridge data (future):** EDS (reads features), EDSx (reads features), ML (reads features), Reconciliation (reads signal outcomes). These consumers are documented in Empire's CLAUDE.md. FromTheBridge builds for all of them — null handling, not omission.

---

## FORBIDDEN ACTIONS

- Deploy without local build + test on bluefin
- Edit code on proxmox
- Target NAS (192.168.68.91) for any writes — NAS is backup storage only
- Target Server2 (192.168.68.12) for any writes — Server2 is Binance Collector only
- Build without stating plan first
- Create tables that duplicate existing data
- Self-certify gates that don't work end-to-end
- Read/write `archive` schema
- Read ClickHouse (Silver) from any service except the export Dagster asset (Rule 2)
- Store time series data in PostgreSQL catalog tables (Rule 3)
- Modify DDL after Phase 0 gate passes — new metrics/sources add catalog rows only
- Hardcode IPs — use environment variables

---

## GSD + SUPERPOWERS (MANDATORY)

Every GSD operation triggers corresponding superpowers. No exceptions.

| GSD Command | Required Superpowers |
|-------------|---------------------|
| `/gsd:plan-phase` | `brainstorming` before plan |
| `/gsd:execute-phase` | `brainstorming` + `test-driven-development` + `verification-before-completion` |
| `/gsd:quick` | All three above |
| `/gsd:debug` | `systematic-debugging` |
| `/gsd:verify-work` | `verification-before-completion` |
| Completion points | `requesting-code-review` |

---

## AGENT DELEGATION (MANDATORY)

| Task | Agent | Model |
|------|-------|-------|
| Pre-change schema/infra verification | `ftb-preflight` | haiku |
| Post-change architecture enforcement | `ftb-code-reviewer` | sonnet |
| Security scan (APIs, Docker, DB, creds) | `ftb-security` | sonnet |
| Adapter contract validation | `ftb-adapter-validator` | sonnet |

Agents are defined in `.claude/agents/`. All are read-only (`permissionMode: plan`), use project memory, and write JSON reports to `.claude/reports/`.

**Enforcement hooks** (`.claude/hooks/`): `guard-clickhouse-reads.sh` (Rule 2), `guard-ddl.sh` (schema immutability), `guard-forbidden-targets.sh` (NAS/Server2). Registered in `.claude/settings.json`.

**MCP:** PostgreSQL read-only via `.mcp.json` (forge_reader → empire_postgres:5433). Available to ftb-preflight and ftb-code-reviewer for live schema verification.

**Delegation chain:** ftb-code-reviewer auto-spawns ftb-security when it detects credential/auth patterns.

**GSD integration:**

| GSD Command | Agents | Mode |
|-------------|--------|------|
| `/gsd:plan-phase` | ftb-preflight | background |
| `/gsd:execute-phase` | ftb-code-reviewer (spawns ftb-security) | foreground |
| `/gsd:quick` | ftb-preflight (bg) + ftb-code-reviewer (fg) | mixed |
| `/gsd:debug` | ftb-preflight | background |
| `/gsd:verify-work` | ftb-code-reviewer | foreground |
| Adapter work | ftb-adapter-validator (additional) | foreground |

**Future agents (add when triggered):** ftb-test-writer (testing framework chosen), ftb-dagster-checker (Phase 1 Dagster deployed).

---

## AUDIT-BEFORE-FIX

Modifying existing systems (not greenfield): read-only audit first → architect reviews → then build with pre-flight checks.

---

## MIGRATION DISCIPLINE

Every new table must be justified. Migration files must match what was built. Time-series → ClickHouse Silver. Catalog → PostgreSQL. Raw → Bronze (Iceberg/MinIO). Analytical → Gold (Iceberg/MinIO). Never mix.

---

## WHEN STUCK

Say "I need to stop and re-plan." State what's broken in 1 sentence. Propose 2-3 options. Wait for Stephen's choice.
