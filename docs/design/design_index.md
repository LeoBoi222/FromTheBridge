# FromTheBridge — Design Index
## Lakehouse Architecture v3.1

**Date:** 2026-03-06
**Status:** `FromTheBridge_design_v3.1.md` is the single canonical design document. This file is navigation and cross-reference only.
**Owner:** Stephen (architect, sole operator)

---

## DOCUMENT AUTHORITY ORDER

As of 2026-03-06, the consolidated design document is authoritative. Individual thread
files are historical inputs that were merged into v3.1. The review handoff documents
record the edit history.

```
1. FromTheBridge_design_v3.1.md (canonical — all layers, all threads)
2. design_index.md (navigation and cross-reference only)
3. V3.1_REVIEW_HANDOFF_SESSION2.md (edit history — 35 items across 5 batches + 5 architectural gaps)
```

**Superseded documents (historical only):**
- `thread_infrastructure.md` → merged into v3.1 §Infrastructure
- `thread_2_signal.md` → merged into v3.1 §Thread 2
- `thread_3_features.md` → merged into v3.1 §Thread 3
- `thread_4_data_universe.md` → merged into v3.1 §Thread 4
- `thread_5_collection.md` → merged into v3.1 §Thread 5
- `thread_6_build_plan.md` → merged into v3.1 §Thread 6
- `thread_7_output_delivery.md` → merged into v3.1 §Thread 7
- `FromTheBridge_design_v1_1.md` → baseline, superseded by v3.1

---

## ARCHITECTURE STATUS

| Document | Status | Date |
|----------|--------|------|
| `FromTheBridge_design_v3.1.md` | **Canonical** | 2026-03-06 |
| `design_index.md` | Navigation only | 2026-03-06 |
| `V3.1_REVIEW_HANDOFF_SESSION2.md` | Edit history | 2026-03-06 |

**Current phase:** Phase 0 complete — gate passed 2026-03-06, architect confirmed.
Phase 1 — Data Collection not yet started. Blocking: Polygon.io integration design
session must complete before Phase 1 build prompt.

---

## LAYER STACK (INFRASTRUCTURE VIEW)

```
Layer 8: Serving
  Decoupled API process. DuckDB reads Gold + Marts.
  Arrow Flight (bulk timeseries), REST JSON (signals).
  /v1/signals, /v1/timeseries, webhooks, Telegram.
  Never reads ClickHouse or PostgreSQL directly.
  Phase 6 scope — not built until all prior layers verified.

Layer 7: Catalog
  PostgreSQL. Relational integrity only.
  metric_catalog, source_catalog, instruments, assets,
  asset_aliases, venues, metric_lineage, event_calendar,
  supply_events, adjustment_factors, collection_events,
  instrument_metric_coverage.
  No time series data here — ever.

Layer 6: Marts
  Feature Store. dbt (SQL transforms) + forge_compute (Python).
  forge_compute runs as Dagster assets inside empire_dagster_code.
  DuckDB embedded. gold_reader credential. PIT enforced.
  Feature catalog entry required before compute.

Layer 5: Gold
  Analytical Layer. Iceberg tables on MinIO.
  DuckDB reads here. Feature compute reads here ONLY.
  Never reads ClickHouse directly — hard rule.
  Populated by event-triggered hybrid export (1h fallback).

Layer 4: Silver
  Observation Store. ClickHouse.
  EAV: (metric_id, instrument_id, observed_at, value).
  ReplacingMergeTree. Bitemporal: observed_at + ingested_at.
  dead_letter table here. current_values materialized view.
  Write-only except for the export Dagster asset (event-triggered + hourly fallback).

Layer 3: Bronze
  Raw Landing. Apache Iceberg tables on MinIO.
  Partitioned by (source_id, date, metric_id).
  Two-bucket: bronze-hot (90-day lifecycle) + bronze-archive (indefinite).
  Great Expectations validation at Bronze → Silver boundary.

Layer 2: Adapters
  Per-source. 10-responsibility contract.
  Auth, rate limiting, pagination, schema normalization,
  timestamp normalization, unit normalization, validation,
  extreme value handling, idempotency, observability.

Layer 1: Orchestration
  Dagster (dedicated Docker service).
  Software-Defined Assets. One asset per (metric_id, source_id).
  Asset graph mirrors metric_catalog + metric_lineage.
  Freshness from cadence_hours. Retry, backoff, alerting
  as framework primitives. BLC-01: file-sensor trigger.

Layer 0: Sources
  Coinalyze, CFTC COT, DeFiLlama, FRED, Tiingo, SoSoValue,
  Etherscan/Explorer, CoinPaprika, BGeometrics, CoinMetrics,
  Binance (BLC-01). 11 sources at v1.
```

---

## THREE HARD RULES

### Rule 1: Layer boundary is a one-way gate

Data flows down only. Nothing reads a layer above its own. Feature compute reads
Gold (Layer 5), never Silver (Layer 4). Serving reads Marts (Layer 6) via DuckDB.
No exceptions.

**Enforcement:** Dagster asset dependency graph. A cycle-detecting violation fails
at pipeline definition time. Credential isolation prevents workarounds at runtime.

### Rule 2: ClickHouse is write-only except for the export job

The only process that reads ClickHouse is the Dagster Software-Defined Asset that
runs the incremental Silver → Gold export. The export fires on two triggers:
(1) `multi_asset_sensor` polling 11 collection asset keys at 30s intervals, and
(2) `@hourly` fallback schedule as a safety net. All analytical workloads go
through Gold (Iceberg on MinIO, read by DuckDB).

**Enforcement:** ClickHouse credentials issued exclusively to the export asset's
environment (`ch_export_reader` — SELECT on `forge.observations FINAL` only). No
other Docker service has a ClickHouse connection string.

### Rule 3: PostgreSQL holds no time series data

The catalog layer holds relational integrity only. No `observed_at + value` columns
exist in any PostgreSQL table. No metric observations, no derived computations, no
feature values in PostgreSQL.

**Enforcement:** Structural DDL. No time series tables exist in the schema.

---

## TECHNOLOGY DECISIONS

Seven ADRs. Full alternatives analysis in v3.1 §Architecture Decision Records.

| Layer | Component | Technology | ADR | Decision rationale |
|-------|-----------|------------|-----|---------------------|
| 4 — Silver | Observation store | ClickHouse (ReplacingMergeTree) | ADR-001 | Columnar compression (10-20x), export-optimized scan, idempotent revision via data_version |
| 3 — Bronze, 5 — Gold | Storage format | Apache Iceberg on MinIO | ADR-002 | ACID writes, time travel, schema evolution, DuckDB native read, S3-portable |
| 3 — Bronze, 5 — Gold | Object storage | MinIO (self-hosted) | ADR-003 | S3-compatible API — cloud migration is config swap, zero code changes |
| 6 — Marts, 8 — Serving | Query engine | DuckDB | ADR-004 | Embedded columnar engine with Iceberg and Arrow Flight support |
| 1 — Orchestration | Orchestration | Dagster (Docker service) | ADR-005 | SDA model mirrors metric_catalog + metric_lineage; freshness from cadence_hours |
| 6 — Marts | Feature compute | dbt + forge_compute (Python) | ADR-006 | dbt for SQL transforms; forge_compute for rolling window/cross-sectional Python features |
| 7 — Catalog | Catalog store | PostgreSQL | ADR-007 | FK integrity across 12 catalog + 12 entitlement tables (Phase 5). Rule 3 verified. |

**Managed service migration paths** (all zero code changes):

| Component | Trigger | Target |
|-----------|---------|--------|
| MinIO → AWS S3 | 50 paying customers OR /mnt/empire-data > 80% | AWS S3 |
| ClickHouse → Cloud | 50 paying customers OR merge queue > 100 parts for 7+ days | ClickHouse Cloud |
| Dagster → Cloud | > 4 hours/month operator time on infra | Dagster Cloud |
| PostgreSQL → RDS | 50 paying customers | AWS RDS |

---

## PHASE READING MAP

All content is in `FromTheBridge_design_v3.1.md`. Section references below guide
which parts to load for each phase.

| Phase | Required sections in v3.1 |
|---|---|
| Phase 0 — Schema Foundation | Three Hard Rules · §Thread 4 (all catalog DDL, ClickHouse schema, PIT model) · §Cold-Start Sequence · §Phase 0 gate |
| Phase 1 — Data Collection | §Thread 5 (adapter contract, per-source specs, GE, BLC-01, migration plan) · §Thread 4 (source_catalog, metric_catalog, collection_events) · §Silver → Gold Export · §Phase 1 gate · §Disaster Recovery Objectives |
| Phase 2 — Feature Engineering | §Thread 3 (PIT, null states, computation order, breadth scores, feature catalog) · §Thread 4 (instrument_metric_coverage) · ADR-004 (DuckDB), ADR-006 (dbt + forge_compute) · §Phase 2 gate |
| Phase 3 — EDSx Signal | §Thread 2 (Regime Engine, Five-Pillar Framework, EDSx v2.2, Synthesis) · §Thread 3 (per-pillar feature requirements) · §Phase 3 gate · §Decision Gate DG-R1 |
| Phase 4 — ML Track (Shadow) | §Thread 2 (ML Track, Graduation Criteria, Synthesis) · §Thread 3 (ML feature requirements) · §48h Preview Implementation Spec · §Phase 4 gate |
| Phase 5 — Serving | §Thread 7 (API Surface, Redistribution, Performance History B3, Signal Snapshot Cache C3, SLA Definitions) · §Customer Identity Model · §DuckDB Concurrency Model · §Credential Inventory · §Phase 5 gate |
| Phase 6 — Productization | §Thread 7 (Methodology Documentation, First Customer Onboarding) · §Disaster Recovery (restore drill verification) · §Phase 6 gate |

---

## LOCKED DECISIONS

### Infrastructure

**Physical deployment topology:**

| Machine | IP | Role |
|---------|-----|------|
| proxmox | 192.168.68.11 | Production. All new-architecture services. GPU: RTX 3090 (24GB). |
| Server2 | 192.168.68.12 | Binance Collector only (LXC 203 + VPN). Single-purpose. |
| bluefin | 192.168.68.64 | Development. Build and test here. Never edit on proxmox. |
| NAS | 192.168.68.91 | Backup destination only. No service, agent, or data writes. |

**Storage mounts on proxmox:**

| Mount | Capacity | Contents |
|-------|----------|---------|
| `/` | 4TB NVMe | OS, Docker engine, container layers |
| `/mnt/empire-db` | 2TB SSD | PostgreSQL, ClickHouse, Dagster metadata, Redis |
| `/mnt/empire-data` | 4TB SSD | MinIO (bronze-hot + bronze-archive + gold Iceberg), Prometheus, Grafana |

**New Docker services:**

| Service | Container | Port | Volume |
|---------|-----------|------|--------|
| Forge DB (legacy) | empire_forge_db | 5435 | forge_data |
| ClickHouse | empire_clickhouse | 8123 (HTTP), 9000 (native) | /mnt/empire-db/clickhouse |
| MinIO | empire_minio | 9001 (API), 9002 (console) | /mnt/empire-data/minio |
| Dagster webserver | empire_dagster_webserver | 3010 | /mnt/empire-db/dagster |
| Dagster daemon | empire_dagster_daemon | — | /mnt/empire-db/dagster |
| Dagster code server | empire_dagster_code | — | /opt/empire/pipeline |

**ClickHouse credential isolation:**

| User | Access | Mounted On |
|---|---|---|
| `ch_writer` | INSERT on `forge.observations` + `forge.dead_letter` only. No SELECT. | `empire_dagster_code` only |
| `ch_export_reader` | SELECT on `forge.observations`, `forge.dead_letter`, `forge.current_values`. No INSERT. | `empire_dagster_export` only |
| `ch_admin` | All (DDL + admin) | Never mounted — operator terminal only |

---

### Signal Architecture (Thread 2)

**Two independent tracks:** EDSx (deterministic) and ML (LightGBM). No cross-contamination.

**Synthesis:** `final_score = 0.5 × edsx + 0.5 × ml`. Recalibrated quarterly.

**Five EDSx pillars:** trend_structure, liquidity_flow, valuation, structural_risk, tactical_macro.

**Live pillars:** trend_structure, liquidity_flow.
**Planned pillars:** valuation, structural_risk, tactical_macro (null states with reason codes).

**Five ML models:** Derivatives Pressure, Capital Flow Direction, Macro Regime, DeFi Stress, Volatility Regime. All LightGBM, 14-day horizon, walk-forward.

**ML graduation:** 5 hard criteria, minimum 30-day shadow, no self-certification.

**Neutral threshold:** Fixed ±0.10. Noise floor at 2/5 pillars. Not configurable per-request.

---

### Data Universe (Thread 4)

**PostgreSQL catalog — 12 tables:**
`assets` → `asset_aliases` → `venues` → `instruments` → `source_catalog` →
`metric_catalog` → `metric_lineage` → `event_calendar` → `supply_events` →
`adjustment_factors` → `collection_events` → `instrument_metric_coverage`

**Phase 5 entitlement tables — 12 (additive):** 9 core (`customers`, `api_keys`,
`plans`, `rate_limit_policies`, `subscriptions`, `plan_endpoint_access`,
`plan_field_access`, `plan_lookback_config`, `plan_instrument_access`) + 3 supporting
(`customer_instrument_overrides`, `metric_redistribution_tags`, `audit_access_log`).

**ClickHouse Silver — 3 objects:**
- `forge.observations` (ReplacingMergeTree, ordered by `metric_id, instrument_id, observed_at`)
- `forge.dead_letter` (MergeTree, TTL 90 days)
- `forge.current_values` (AggregatingMergeTree materialized view, argMaxState)

**Metric counts:** 74 Phase 0 seed, +8 Phase 1 additions = 82 total.

**Schema immutability:** No DDL after Phase 0 gate. New metrics/sources = catalog rows only.

**Instrument tiers:** `collection` → `scoring` → `signal_eligible`. Rule-driven promotion.
`signal_eligible` is the `instruments.collection_tier` column (not a metric_catalog flag).

---

### Collection (Thread 5)

**10-responsibility adapter contract.** Per-observation validation independence.
Dead letter with rejection codes. Nothing silently dropped.

**Dagster asset graph:** ~65 assets at Phase 1 launch.
Collection AssetKeys: `collect_{source_id}` convention.

**Silver → Gold export:** Asset `gold_observations`. Event-triggered hybrid
(`multi_asset_sensor` at 30s polling + `@hourly` fallback). Worst-case 44min lag.

**`marts.signals_history`:** `forge_compute` Python asset (Dagster SDA, not dbt).
Writes to Gold (Iceberg on MinIO).

**GE suites:** `bronze_core` (universal, 7 expectations) + `bronze_{source_id}`
(per-adapter additive).

**Forge decommission:** `empire_forge_db` read-only 90 days after Phase 1 gate.

---

### Build Plan (Thread 6)

**Phase gate model:** Hard pass/fail. No self-certification. Every criterion is
runnable by someone who has never seen the system.

| Phase | Key deliverables | Duration |
|---|---|---|
| Phase 0 — Schema | 12 PG catalog tables + seed, ClickHouse 3 objects, MinIO buckets | 3–5 days |
| Phase 1 — Collection | Dagster services, all 11 source adapters, Bronze/Silver writes, GE, BLC-01 rsync, NAS backup | 2–3 weeks |
| Phase 2 — Features | Gold Iceberg readable, dbt models pass, forge_compute features, breadth scores, PIT audit | 2–3 weeks |
| Phase 3 — EDSx | 2 live pillars + 3 null-state, regime classification, ±0.10 neutral threshold, §L2.8 output schema | 1–2 weeks |
| Phase 4 — ML (Shadow) | 5 models trained, graduation criteria on OOS, shadow ≥30d, shadow artifacts, 48h preview | 3–4 weeks |
| Phase 5 — Serving | Synthesis, FastAPI, 12 entitlement tables, API key auth (argon2id), redistribution filter, Arrow Flight, F1 go-live | 1–2 weeks |
| Phase 6 — Product | Health monitoring, methodology docs, ToS audit, first customer delivery | 1–2 weeks |

**Total:** 13–18 weeks. ML shadow period is a floor.

---

### Output Delivery (Thread 7)

**4-tier plan matrix:** Free, Pro ($199/month), Protocol ($4,500–$18,000), Institutional ($2,500/month).

**Authentication:** API key required on ALL endpoints. argon2id hashed. Manual issuance in v1.

**Redistribution:** Three-state enum (`allowed`/`pending`/`blocked`). Option C propagation.
Null-with-flag response — never silent omission.

**48h-delayed public preview:** Top-10 composite (top 5/bottom 5) on `fromthebridge.net`.
No account. No pillar/ML/confidence detail. Live at shadow week 2.
Endpoint: `GET /v1/preview` (unauthenticated, rate-limited).

**Performance history (B3):** `GET /v1/signals/performance`. Two dbt marts:
`signal_outcomes` (PIT boundary) + `performance_metrics` (rolling aggregations).
DuckDB SQL uses `arg_min()` (not `FIRST()`).

**`pillar_attribution` Protocol scoping:** Scoped to pillars with ≥1 metric sourced from
instruments in customer's `customer_instrument_overrides` coverage set.

**Four SLAs:** Signal freshness (90min), API uptime (99.5%), staleness notification (60min),
methodology change notice (14 days).

---

### Architectural Additions (v3.1 Review)

**Disaster Recovery (A-1):** RPO/RTO per component. Proxmox host failure scenario
(2–4h recovery). Quarterly restore drills. Phase 1 gate: backups configured.
Phase 5 gate: restore drill completed.

**DuckDB Concurrency Model (A-2):** Single DuckDB connection per FastAPI worker
(2 workers v1). Iceberg snapshot consistency. Cache invalidation event-driven.
Bottleneck: `performance_metrics` query at 4 concurrent/worker.

**Credential Inventory (A-3):** 10 credentials enumerated. File-based (`secrets/`
directory, chmod 600). Annual rotation (March). Future: Vault at 10+ customers.

**Customer Identity Model (A-4):** Flat entity model. Manual management. No org
hierarchy in v1. Override scopes for Protocol tier. Revocation: key permanent,
account suspension reversible, closure permanent.

**Decision Gate DG-R1 (A-5):** Redistribution impact on product quality. Both live
EDSx pillars affected by `pending`/`blocked` sources at launch. Three options
presented. **Decision required before Phase 5 build prompt.**

---

## KNOWN GAPS REGISTER

| Gap | V1 handling | Resolution trigger | Status |
|---|---|---|---|
| `defi.lending.utilization_rate` | Proxy: borrow/supply TVL ratio from DeFiLlama | v1.1 — Aave/Compound subgraph adapter | Open |
| Options data (Deribit) | Null-propagate. Pillar confidence decreases. | v1.1 — Deribit adapter | Open |
| Exchange flows beyond 18 instruments | Accept for v1 | v1.1 — additional on-chain sources | Open |
| BTC directional exchange flows | Null-propagates in Capital Flows pillar | v1.1 — CryptoQuant (parked) | Open |
| `macro.credit.hy_oas` FRED adapter | Metric in catalog (BAMLH0A0HYM2) | Phase 1 pre-condition | **Catalog resolved Phase 0. Adapter pending Phase 1.** |
| `defi.protocol.fees_usd_24h` / `revenue_usd_24h` | Both in metric catalog | Phase 1 | **Catalog resolved Phase 0. Adapter pending Phase 1.** |
| FRED metric catalog expansion | All FRED series in catalog | Phase 1 | **Catalog resolved Phase 0. Collection pending Phase 1.** |
| BLC-01 rsync routine | Unbuilt | Phase 1 | Open |
| `macro.cb.boj_total_assets` PBOC equivalent | BOJ confirmed. PBOC: evaluate during FRED build. | Phase 1 | Open |
| BGeometrics signal_pillar assignment | `signal_pillar = NULL` | Structural Risk pillar design (REM-24) | Open |
| CoinMetrics redistribution | `redistribution_status = 'blocked'` | Phase 6 ToS audit | Open |
| SoSoValue redistribution | `redistribution_status = 'blocked'` | Phase 6 ToS audit or paid tier | Open |
| Index / benchmark licensing | Deferred | v2 — methodology documented + ToS audited | Open |
| BLC-01 ToS audit | Internal use only | Phase 6 ToS audit | Open |
| forge_agent_explorer DOWN | Backfill from Etherscan V2 in Phase 1 | Phase 1 | Open |
| ClickHouse DDL migration file | Deployed | Phase 0 corrective | **Resolved Phase 0.** |
| `forge.current_values` DDL | DDL in design doc, not yet in migration file | Phase 0 corrective or Phase 1 | Open |
| MinIO bucket initialization | Not yet created | Phase 0 corrective | Open |
| Dagster service definition | Not yet in docker-compose.yml | Phase 1 | Open |
| Great Expectations setup | Not installed | Phase 1 | Open |
| Silver → Gold export asset (`gold_observations`) | Not yet written | Phase 1 | Open |
| NAS backup job for MinIO | Not configured | Phase 1, before live collection | Open |
| Dagster metadata DB backup | Not configured | Phase 1 | Open |
| `macro.employment.mfg_employment` | Not in metric_catalog | Phase 1 pre-condition | Open |
| thread_3 §9 ClickHouse reference | Legacy language — must correct to Gold/DuckDB path | Before Phase 2 build prompt | Open |
| DG-R1 redistribution decision | Open — required before Phase 5 | Phase 5 pre-condition | **Open — escalated from assumptions** |
| Polygon.io integration design | Must complete before Phase 1 build prompt | Phase 1 pre-condition | **Blocking** |

---

## SOURCES CATALOG SUMMARY

11 sources at v1. All rows verified against `forge.source_catalog` seed data in v3.1.

| canonical_name | display_name | tier | redistribution_status | cost_tier |
|---|---|---|---|---|
| `coinalyze` | Coinalyze | 1 | pending | free |
| `cftc_cot` | CFTC COT | 2 | allowed | free |
| `defillama` | DeFiLlama | 1 | allowed | free |
| `fred` | Federal Reserve (FRED) | 1 | allowed | free |
| `tiingo` | Tiingo | 1 | allowed | paid |
| `sosovalue` | SoSoValue | 1 | **blocked** | free |
| `etherscan` | Etherscan V2 / Explorer | 2 | pending | freemium |
| `coinpaprika` | CoinPaprika | 1 | allowed | free |
| `coinmetrics` | CoinMetrics | 2 | **blocked** | free |
| `bgeometrics` | BGeometrics | 2 | pending | free |
| `binance_blc01` | Binance (BLC-01) | 2 | pending | free |

**Redistribution hard blocks:** `sosovalue` and `coinmetrics`. Excluded from all
external data products until `redistribution_status` changed.

**Pending (unaudited):** `coinalyze`, `etherscan`, `bgeometrics`, `binance_blc01`.
Treated as blocked by serving layer until Phase 6 ToS audit clears.

**Excluded permanently:** Santiment, Glassnode, BSCScan, Solscan

**Parked (paid, not in budget):** CoinGlass, CryptoQuant, CoinMarketCap

**Reference/fallback (in catalog, not v1 active):** CoinGecko, KuCoin, Explorer (separate)

---

## CONVENTIONS (from v3.1 review)

| Convention | Rule |
|------------|------|
| Dagster asset naming | Noun-form (`gold_observations`, `signal_snapshot_writer`) — not verb-form |
| Collection asset keys | `collect_{source_id}` where source_id is the slug from `source_catalog` |
| `forge_compute` location | Dagster assets inside `empire_dagster_code`, not a separate container |
| GE suite naming | `bronze_core` (universal) + `bronze_{source_id}` (per-adapter) |
| `marts.signals_history` | `forge_compute` Python asset (not dbt). Writes to Gold. |
| `signal_eligible` | `instruments.collection_tier` column (not a metric_catalog flag) |
| Decision gates | `DG-*` prefix. Formal decision points that block phase build prompts. |
| DuckDB SQL | `arg_min()` / `arg_max()` — not `FIRST()` / `LAST()` |

---

*design_index.md — navigation and cross-reference only. Not authoritative on any
specific decision. Full specification in `FromTheBridge_design_v3.1.md`. Changes to
locked decisions require architect approval and a design doc revision.*
