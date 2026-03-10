# CLAUDE.md — FromTheBridge Project Rules

## PROJECT IDENTITY

FromTheBridge is a data lakehouse for crypto market intelligence — a consumer-first 9-layer stack that transforms data into signals.

**FTB does not collect data.** EDS (EmpireDataServices) is the primary data provider. EDS collects from external sources → writes to `empire.observations` → FTB consumes via `empire_to_forge_sync` bridge → `forge.observations`. FTB owns everything from Silver (Layer 4) upward: storage, features, signals, serving.

**Two repos, one system:**
- **EDS** (`/var/home/stephen/Projects/EmpireDataServices/`) — data collection, adapters, on-chain derivation. Owns `empire.*` ClickHouse database.
- **FTB** (this repo) — lakehouse, features, signals, serving. Owns `forge.*` ClickHouse database + `forge` PG schema + MinIO buckets.
- **Nexus-Council** — legacy monolith. Manages shared infra containers (empire_postgres, empire_clickhouse, empire_forge_db). Not for EDS or FTB design authority.

**Design document:** `docs/design/FromTheBridge_design_v4.0.md` (SSOT). `design_index.md` for navigation only.

---

## DATA COLLECTION BOUNDARY

**This is the most important section. Read it before building anything in Layer 0–2.**

EDS collects all external data. FTB consumes it. The bridge is `empire_to_forge_sync`.

```
External APIs → EDS adapters → empire.observations → empire_to_forge_sync → forge.observations
                                                            ↑
                                                     FTB owns this asset
```

**EDS source ownership (per EDS_design_v1.1.md):**

| EDS Track | Sources | Status |
|-----------|---------|--------|
| Track 1 (Nodes) | BGeometrics replacements, CoinMetrics replacements, Etherscan replacements | Node infra building |
| Track 2 (Exchange APIs) | Coinalyze replacements, Tiingo replacements (OHLCV direct) | Planned |
| Track 3 (Public Feeds) | FRED, DeFiLlama, CFTC COT, CoinPaprika, SoSoValue → SEC EDGAR | FRED + DeFiLlama deployed |

**FTB builds NO source adapters.** If a source needs collecting, it belongs in EDS. FTB's responsibilities at the data boundary:
1. `empire_to_forge_sync` — the Dagster asset that reads `empire.observations` and writes to `forge.observations` with `source_id='eds_derived'`
2. `forge.metric_catalog` rows — manual promotion of EDS metrics into the FTB catalog
3. Validation + dead letter at the sync boundary
4. BLC-01 rsync pull (Server2 → proxmox landing directory) — ops task, not an adapter

**If you think FTB needs to build an adapter: STOP. Check EDS first.**

---

## ARCHITECTURE — 9-LAYER STACK (summary — see v4.0 for canonical)

Data flows downward only. No layer reads a layer above itself.

| Layer | Name | Technology | What lives here |
|-------|------|------------|-----------------|
| 8 | Serving | FastAPI + DuckDB + Arrow Flight | `/v1/signals`, `/v1/timeseries`, webhooks, Telegram. Reads Gold + Marts only. Phase 5. |
| 7 | Catalog | PostgreSQL (`forge` schema) | 12 relational tables. No time series — ever. |
| 6 | Marts | dbt (SQL) + forge_compute (Python) | Feature store. PIT enforced. Reads Gold via DuckDB. |
| 5 | Gold | Iceberg on MinIO | Analytical layer. DuckDB reads here. Hybrid export from Silver. |
| 4 | Silver | ClickHouse (ReplacingMergeTree) | Observation store. EAV. Write-only except export job. |
| 3 | Bronze | Iceberg on MinIO | Raw landing. `bronze-hot` (90-day) + `bronze-archive` (indefinite). |
| 2 | Sync Bridge | `empire_to_forge_sync` | Reads `empire.observations`, validates, writes `forge.observations`. This is NOT a per-source adapter layer — EDS handles that. |
| 1 | Orchestration | Dagster (Docker service) | Software-Defined Assets. `AutomationCondition` for scheduling — NOT `@multi_asset_sensor`. |
| 0 | Sources | EDS | Data arrives via `empire_to_forge_sync`, not direct API calls. |

### Three Hard Rules

**Rule 1 — One-way gate:** Data flows down only. Feature compute reads Gold (Layer 5), never Silver (Layer 4). Serving reads Marts (Layer 6). No exceptions.

**Rule 2 — No application service reads forge.* in ClickHouse:** Only Dagster assets read `forge` in ClickHouse — export asset (`ch_export_reader`) and ops health (`ch_ops_reader`). The `empire.*` database is governed by EDS rules.

**Rule 3 — No time series in forge.* PostgreSQL tables:** The `forge` schema holds relational integrity only. No `observed_at + value` columns.

### Two Signal Tracks

**EDSx (deterministic):** Five pillars × 3 horizons. Rule-based scoring from feature layer.

**ML (probabilistic):** Five LightGBM domain models. 14-day horizon. Walk-forward training. Independent of EDSx.

**Synthesis:** `final_score = 0.5 × edsx + 0.5 × ml`. ML must graduate (30-day shadow, 5 hard criteria) before entering composite.

---

## CURRENT STATE

**As of:** 2026-03-10 (verified against live infrastructure)

| Phase | Description | Status |
|-------|-------------|--------|
| Design | All 7 thread files complete | ✅ Complete |
| Phase 0 | Schema Foundation | ✅ Complete |
| Phase 1 | Data Collection + Sync | 🔧 In progress — early |
| Phase 2–6 | Features → Productization | ❌ Not started |

**Deployed on proxmox (verified):**
- PostgreSQL `forge` schema: 74 metrics, 10 sources, 4 instruments, 3 instrument_source_map rows, 3 metric_lineage rows
- ClickHouse `forge`: 3 tables (observations, dead_letter, current_values MV)
- MinIO: bronze-hot, bronze-archive, gold buckets exist
- Dagster: 4 containers running (webserver, daemon, code_ftb, code_eds)
- Tiingo adapter: running in FTB (needs migration to EDS)
  - 2,633 collection events, 6h schedule active
  - Shared writers in `src/ftb/writers/` — these stay for sync bridge use
- EDS adapters in shared Dagster: FRED (3,463 obs in empire.*), DeFiLlama (936 obs in empire.*)

**Built in code (`src/ftb/`):**
- `adapters/tiingo.py` + `tiingo_asset.py` — to be migrated to EDS
- `writers/silver.py`, `bronze.py`, `collection.py` — shared write utilities
- `validation/core.py` — observation validation
- `resources.py` — Dagster resources (ClickHouse, PostgreSQL, MinIO, API keys)
- `definitions.py` — Dagster entry point with Tiingo schedule

**Phase 1 gate progress (40 criteria in v4.0 §Phase Gates):**
- ✅ Dagster services healthy (3 services + EDS code server)
- ✅ Dagster in docker-compose
- ✅ MinIO buckets created with lifecycle
- ⚠️ Tiingo collecting but in wrong repo (FTB, not EDS)
- ❌ `empire_to_forge_sync` — not built (primary Phase 1 deliverable)
- ❌ Great Expectations — not configured
- ❌ Bronze archive job — not built
- ❌ Export round-trip (Silver → Gold → DuckDB) — not built
- ❌ Ops assets (adapter_health, export_health, sync_health) — not built
- ❌ Runbooks FTB-01 through FTB-08 — not written
- ❌ Ops credentials (calendar_writer, risk_writer, ch_ops_reader) — not created
- ❌ Most collection sources — waiting on EDS adapters + sync bridge

---

## NEXT ACTIONS (Phase 1)

1. Build `empire_to_forge_sync` Dagster asset — v4.0 §Sync Layer
2. Build Bronze archive job (`bronze_cold_archive`) — v4.0 §Bronze Archive
3. Build export round-trip (Silver → Gold via DuckDB Iceberg write) — v4.0 §Gold Layer
4. Build ops assets (adapter_health, export_health, sync_health) — v4.0 §Solo Operator Operations
5. Create ops credentials + calendar schema — v4.0 §Solo Operator Operations

---

## v1 DATA SOURCES (11 target)

All sources are collected by EDS adapters and flow to FTB via `empire_to_forge_sync`. FTB does not build source adapters.

| Source | Data | Cadence | Collected By | FTB Receives Via |
|--------|------|---------|--------------|------------------|
| Coinalyze | Funding, OI, liquidations, L/S ratio | 8h | EDS Track 2 | empire_to_forge_sync |
| DeFiLlama | TVL, DEX volume, stablecoins, lending | 12h | EDS Track 3 | empire_to_forge_sync |
| FRED | 23 macro series | 24h+ | EDS Track 3 | empire_to_forge_sync |
| Tiingo | OHLCV spot prices | 6h | EDS Track 2 (transitional) | empire_to_forge_sync |
| SoSoValue | ETF flows | 24h | EDS Track 3 (→ SEC EDGAR) | empire_to_forge_sync |
| Etherscan | Exchange flows (ETH + Arbitrum) | 8h | EDS Track 1 | empire_to_forge_sync |
| CoinPaprika | Market cap, metadata | 24h | EDS Track 3 | empire_to_forge_sync |
| CoinMetrics | On-chain transfer volume | 24h | EDS Track 1 | empire_to_forge_sync |
| BGeometrics | MVRV, SOPR, NUPL, Puell | 24h | EDS Track 1 | empire_to_forge_sync |
| Binance BLC-01 | Tick liquidations | Real-time | EDS (Server2) | BLC-01 rsync + sync |
| CFTC COT | COT positioning | Weekly | EDS Track 3 | empire_to_forge_sync |

**Redistribution blocked:** SoSoValue, CoinMetrics. Excluded from external products until flags changed.

---

## INFRASTRUCTURE

| Machine | IP | Role |
|---------|-----|------|
| proxmox | 192.168.68.11 | Production. All services. GPU: RTX 3090. |
| Server2 | 192.168.68.12 | EDS only: BLC-01 collector (LXC 203 + VPN). |
| bluefin | 192.168.68.64 | Development |
| NAS | 192.168.68.91 | Backup destination only |

**Domain:** `fromthebridge.net` (Cloudflare tunnel → proxmox). **API:** `192.168.68.11:8000`.

**Cloudflare:** `cloudflared` is a systemd service (not Docker). Routes: `fromthebridge.net` → `:3002` (landing page), `/api/*` → `:8000`.

**Storage:**
| Mount | Capacity | Contents |
|-------|----------|---------|
| `/` | 4TB NVMe | OS, Docker engine, container layers |
| `/mnt/empire-db` | 2TB SSD | PostgreSQL, ClickHouse, Dagster metadata |
| `/mnt/empire-data` | 4TB SSD | MinIO (Bronze + Gold Iceberg), Prometheus, Grafana, Gitea |

**Docker compose split:**
- **Nexus-Council** compose manages shared infra: empire_postgres, empire_clickhouse, empire_forge_db
- **FTB** compose (`docker-compose.yml`) manages: MinIO + Dagster (4 containers). All on shared `empire_network` (external: true).

**FTB Docker services:**

| Service | Container | Port |
|---------|-----------|------|
| MinIO | empire_minio | 9001 (API), 9002 (console) |
| Dagster webserver | empire_dagster_webserver | 3010 |
| Dagster daemon | empire_dagster_daemon | — |
| Dagster code server (FTB) | empire_dagster_code | — |
| Dagster code server (EDS) | empire_dagster_code_eds | — |

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

**DB migration (ClickHouse):** `cat <file>.sql | ssh root@192.168.68.11 "docker exec -i empire_clickhouse clickhouse-client --multiquery"`

---

## DATABASE RULES

| Database | Container | Port | Contents |
|----------|-----------|------|----------|
| PostgreSQL | empire_postgres | 5433 | `forge` schema: catalog tables. `empire_utxo` schema (EDS). `dagster` DB. |
| ClickHouse | empire_clickhouse | 8123/9000 | `forge` DB: observations, dead_letter, current_values. `empire` DB (EDS). |
| MinIO | empire_minio | 9001/9002 | Bronze (Iceberg) + Gold (Iceberg) object storage |
| Forge DB (legacy) | empire_forge_db | 5435 | Legacy raw data. Read-only. Decommission after Phase 1 + 90d. |

**Schema immutability:** No DDL changes after Phase 0 gate. New metrics/sources add catalog rows only.

`archive` schema = frozen. Do not read/write.

### Database Targeting Reference

| Operation | Container | Port | User | Schema | Notes |
|-----------|-----------|------|------|--------|-------|
| Catalog read | empire_postgres | 5433 | forge_reader | forge | MCP server uses this |
| Catalog write | empire_postgres | 5433 | forge_user | forge | |
| Silver write (sync) | empire_clickhouse | 9000 | ch_writer | forge | INSERT-only, via empire_to_forge_sync |
| Silver read (export) | empire_clickhouse | 9000 | ch_export_reader | forge | Rule 2 — export job only |
| Silver read (ops) | empire_clickhouse | 9000 | ch_ops_reader | forge | Rule 2 — Dagster health only |
| Dead letter write | empire_clickhouse | 9000 | ch_writer | forge | INSERT-only |
| Bronze write | empire_minio | 9001 | bronze_writer | bronze-hot/ | 90-day lifecycle |
| Gold write | empire_minio | 9001 | export_writer | gold/ | |
| Pipeline items | empire_postgres | 5433 | crypto_user | bridge | |
| Never write | — | — | — | — | 192.168.68.91 (NAS) |
| No FTB services | — | — | — | — | 192.168.68.12 (Server2, EDS only) |

---

## DATA SOURCE LEGAL COMPLIANCE

**Policy:** Any source whose ToS prohibits commercial use must be upgraded or excluded before Phase 6 gate.

**Redistribution enforcement:** Sources marked `redistribution = false` in `forge.source_catalog` are excluded from all external data products. Currently blocked: SoSoValue, CoinMetrics.

**LH-06 trigger:** Tiingo redistribution clause must be verified before live collection.

---

## INFRASTRUCTURE BLOCKERS

Work blocked on physical infrastructure. When a blocker clears, delete the row and build.

| Blocker | What's Waiting | Unblocks |
|---------|---------------|----------|
| Server2 OS upgrade (5800X + 64GB + 2TB NVMe) | Pruned ETH failover, expanded BLC-01 storage | EDS-0 gate |
| ai-srv-01 hardware verification (mobo M.2 slots, RAM, SSD TBW) | UTXO backfill, ML training infrastructure | Phase 4 gate |
| Tiingo migration to EDS | FTB adapter cleanup, `empire_to_forge_sync` for Tiingo data | Phase 1 (sync bridge) |

**Pipeline retired (2026-03-10).** EDS/LH/ML items cancelled from `bridge.pipeline_items` — tracked by design docs (v4.0 + EDS_design_v1.1). Nexus-Council items (B*, PL*, V*, R2) remain in pipeline for that project's use.

---

## CODE DISCIPLINE

- State plan in 3 bullets BEFORE coding
- **Pre-flight checks required.** Verify current schema/signature before modifying.
- Build only what the prompt specifies. Flag adjacent improvements — do not implement.
- Service modules: 800 lines max. Routers: 200 lines max.

**Python-specific:** `uv` + `pyproject.toml` + `uv.lock`. Testing: `pytest`. Linting: `ruff`.

---

## PHASE GATES

Phase gates are hard pass/fail. Architect confirms. Full criteria in v4.0 §Phase Gates.

| Phase | Key Gate Criteria |
|-------|-------------------|
| 0 — Schema | 12 PG tables, CH Silver schema, MinIO buckets, ≥50 metrics seeded, PIT query, redistribution flags |
| 1 — Collection | Dagster healthy, `empire_to_forge_sync` flowing data, all 11 sources have Silver rows (via EDS sync), Bronze exists, GE checkpoint, dead letter captures, BLC-01 rsync, credential isolation |
| 2 — Features | Gold Iceberg readable by DuckDB, dbt models pass, forge_compute produces features, PIT audit |
| 3 — EDSx | All 5 pillars scoring, confidence, regime classification |
| 4 — ML | 5 models trained (walk-forward), graduation criteria, ≥30 day shadow |
| 5 — Serving | FastAPI, API key auth, entitlement tiers, redistribution filter, Arrow Flight |
| 6 — Product | Health monitoring, methodology docs, ToS audit, first customer |

---

## EDS CROSS-REFERENCE

**EDS CLAUDE.md:** bluefin: `/var/home/stephen/Projects/EmpireDataServices/CLAUDE.md`

**EDS owns:** All data collection adapters, on-chain node infrastructure, `empire.*` ClickHouse database, `empire_utxo` PG schema.

**FTB owns:** `forge.*` ClickHouse database, `forge` PG schema, MinIO buckets, lakehouse layers 2–8, feature engineering, EDSx, ML pipeline, serving.

**The bridge:** `empire_to_forge_sync` is an FTB Dagster asset that reads `empire.observations` and writes promoted metrics to `forge.observations` with `source_id='eds_derived'`. One-directional: empire → forge. EDS never reads `forge.*`. FTB never reads `empire.*`. Credential isolation enforces both.

**Metric promotion:** Manual operation — add row to `forge.metric_catalog` via SQL migration, verify `eds_derived` in `source_catalog`, sync asset picks it up. Requires architect approval + 7-day EDS freshness + <1% dead-letter rate.

**Shared Dagster instance:** FTB and EDS code servers both run in FTB's Dagster deployment (separate code locations: `ftb` and `eds`). The webserver and daemon are FTB-owned containers.

**Nexus-Council:** Legacy monolith. Manages shared infrastructure containers. Not design authority for EDS or FTB. MAE, CAA, W6, Content Engine, Bridge UI, Contracts remain there.

---

## FORBIDDEN ACTIONS

- Deploy without local build + test on bluefin
- Edit code on proxmox
- Target NAS (192.168.68.91) for any writes
- Deploy FTB services to Server2 (192.168.68.12) — EDS only
- Build source adapters in FTB — they belong in EDS
- Create tables that duplicate existing data
- Self-certify gates
- Read/write `archive` schema
- Read forge.* in ClickHouse except via Dagster export/ops assets (Rule 2)
- Store time series in forge.* PostgreSQL tables (Rule 3)
- Modify DDL after Phase 0 gate
- Hardcode IPs in application code

---

## MIGRATION NOTES

**Tiingo adapter (built in FTB, needs to move to EDS):**
The Tiingo OHLCV adapter was built in `src/ftb/adapters/` but should live in EDS per the data collection boundary. Current state: 29k Silver observations in `forge.observations`, 6h schedule running. Migration plan tracked separately. The shared writers (`src/ftb/writers/`) and validation (`src/ftb/validation/`) may remain in FTB for the sync bridge.

---

## AGENT DELEGATION (MANDATORY)

| Task | Agent | Model |
|------|-------|-------|
| Pre-change schema/infra verification | `ftb-preflight` | haiku |
| Post-change architecture enforcement | `ftb-code-reviewer` | sonnet |
| Security scan (APIs, Docker, DB, creds) | `ftb-security` | sonnet |

Agents in `.claude/agents/`. Hooks in `.claude/hooks/`: `guard-clickhouse-reads.sh` (Rule 2), `guard-ddl.sh` (schema immutability), `guard-forbidden-targets.sh` (NAS/Server2).

**MCP:** PostgreSQL read-only via `.mcp.json` (forge_reader → empire_postgres:5433).

---

## GSD + SUPERPOWERS (MANDATORY)

| GSD Command | Required Superpowers |
|-------------|---------------------|
| `/gsd:plan-phase` | `brainstorming` before plan |
| `/gsd:execute-phase` | `brainstorming` + `test-driven-development` + `verification-before-completion` |
| `/gsd:quick` | All three above |
| `/gsd:debug` | `systematic-debugging` |
| `/gsd:verify-work` | `verification-before-completion` |
| Completion points | `requesting-code-review` |

---

## WHEN STUCK

Say "I need to stop and re-plan." State what's broken in 1 sentence. Propose 2-3 options. Wait for Stephen's choice.
