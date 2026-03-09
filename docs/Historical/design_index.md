# FromTheBridge — Design Index
## Empire Architecture v2.0

**Date:** 2026-03-05 | **Status:** Architecture pivot complete. Design rewrite in progress.
**Full specs:** `docs/design/thread_*.md` — load only what the active phase requires.

---

## ⚠ ARCHITECTURE PIVOT — 2026-03-05

The infrastructure architecture was redesigned in full on 2026-03-05.

**Stale documents — do not treat as authoritative on infrastructure:**
- thread_4_data_universe.md — describes TimescaleDB observation store,
  wrong storage targets, wrong DDL
- thread_5_collection.md — missing orchestration layer, wrong storage
  targets, incomplete collector inventory
- thread_6_build_plan.md — all phase gates written against wrong stack

**Authoritative until thread files are rewritten:**
- SESSION_HANDOFF_2026-03-05_v2.md — complete pivot record, architecture
  locked decisions, Phase 0 precise scope
- thread_2_signal.md — not stale, carries forward unchanged
- thread_3_features.md — not stale, carries forward unchanged

**Rewrite status:**

| Document | Status |
|----------|--------|
| thread_infrastructure.md | Not yet written — written first |
| thread_4_data_universe.md | Stale — rewrite pending |
| thread_5_collection.md | Stale — rewrite pending |
| thread_6_build_plan.md | Stale — rewrite pending |
| design_index.md (this file) | Partially updated — full rewrite after threads complete |

---

## LAYER STACK

```
Layer 8: Serving               — Decoupled API process. DuckDB reads Gold + Marts.
                                  Arrow Flight (bulk timeseries), REST JSON (signals).
                                  /v1/signals, /v1/timeseries, webhooks, Telegram.
                                  Never reads ClickHouse or PostgreSQL directly.

Layer 7: Catalog               — PostgreSQL. Relational integrity only.
                                  metric_catalog, source_catalog, instruments,
                                  assets, asset_aliases, venues, metric_lineage,
                                  event_calendar, supply_events, adjustment_factors.
                                  No time series data here — ever.

Layer 6: Marts                 — Feature Store. dbt (SQL transforms) +
                                  Python (rolling window, cross-sectional).
                                  forge_compute lives here. PIT enforced.
                                  Feature catalog entry required before compute.

Layer 5: Gold                  — Analytical Layer. Iceberg tables on MinIO.
                                  DuckDB reads here. Feature compute reads here ONLY.
                                  Never reads ClickHouse directly — hard rule.
                                  Populated by incremental export from Silver every 6h.

Layer 4: Silver                — Observation Store. ClickHouse.
                                  EAV: (metric_id, instrument_id, observed_at, value).
                                  ReplacingMergeTree. Bitemporal: observed_at + ingested_at.
                                  dead_letter table here. current_values materialized view.
                                  Write-only except for export job.

Layer 3: Bronze                — Raw Landing. Apache Iceberg tables on MinIO.
                                  Partitioned by (source_id, date, metric_id).
                                  ACID, schema evolution, time travel native.
                                  Append-only, 90-day retention. Raw payload preserved.
                                  Great Expectations validation at Bronze → Silver boundary.

Layer 2: Adapters              — Per-source. 10-responsibility contract.
                                  Auth, rate limiting, pagination, schema normalization,
                                  timestamp normalization, unit normalization, validation,
                                  extreme value handling, idempotency, observability.

Layer 1: Orchestration         — Dagster (dedicated LXC). Software-Defined Assets.
                                  One asset per (metric_id, source_id).
                                  Asset graph mirrors metric_catalog + metric_lineage.
                                  Freshness from cadence_hours. Retry, backoff, alerting
                                  as framework primitives. BLC-01: file-sensor trigger.

Layer 0: Sources               — Coinalyze, DeFiLlama, FRED, Tiingo, SoSoValue,
                                  Etherscan, CoinPaprika, BGeometrics, CoinMetrics,
                                  Binance (BLC-01)
```

### Three Hard Rules

**Rule 1: Layer boundary is a one-way gate.**
Data flows down only. Nothing reads a layer above its own. Feature compute
reads Gold (Layer 5), never Silver (Layer 4). Serving reads Marts (Layer 6)
via DuckDB. No exceptions.

**Rule 2: ClickHouse is write-only except for the export job.**
The only process that reads ClickHouse is the incremental export job that
writes Iceberg tables to MinIO. All analytical workloads go through Gold.

**Rule 3: PostgreSQL holds no time series data.**
The catalog layer is relational integrity only. No observed_at + value
columns in PostgreSQL. No exceptions.

### Technology Decisions

| Component | Technology | Reason |
|-----------|------------|--------|
| Orchestration | Dagster | Software-Defined Assets maps to metric_catalog exactly |
| Bronze storage | Apache Iceberg on MinIO | ACID, time travel, S3-portable |
| Silver (observation store) | ClickHouse | Columnar, proven at scale, strong HA |
| Gold (analytical) | Iceberg on MinIO + DuckDB | Engine agnostic, S3-portable |
| Marts | dbt + Python | SQL transforms versioned; Python for complex features |
| Catalog | PostgreSQL | Relational integrity, foreign keys, audit trail |
| Validation | Great Expectations | Rules from metric_catalog, not bespoke adapter code |
| Object storage | MinIO (self-hosted) | S3-compatible — config swap to cloud when ready |

### Managed Service Migration Path

MinIO → S3: config change, zero code changes.
ClickHouse self-hosted → ClickHouse Cloud: schema-compatible, data migration only.
Dagster self-hosted → Dagster Cloud: asset definitions portable.
Trigger: customer growth exceeding self-hosted SLA capacity.

---

## PHASE READING MAP

| Active Phase | Required Thread Files |
|---|---|
| Phase 0 | `thread_infrastructure.md` + `thread_4_data_universe.md` (catalog sections only) |
| Phase 1 | `thread_infrastructure.md` + `thread_5_collection.md` + `thread_6_build_plan.md` §Phase 1 |
| Phase 2 | `thread_3_features.md` + `thread_6_build_plan.md` §Phase 2 |
| Phase 3 | `thread_2_signal.md` §EDSx + `thread_6_build_plan.md` §Phase 3 |
| Phase 4 | `thread_2_signal.md` §ML + `thread_6_build_plan.md` §Phase 4 |
| Phase 5 | `thread_2_signal.md` §Synthesis + `thread_6_build_plan.md` §Phase 5 |
| Phase 6 | `thread_6_build_plan.md` §Phase 6 |

**Note:** Phase reading map will be verified and updated after thread rewrites
are complete. thread_infrastructure.md does not yet exist — it is written first
in the design rewrite sequence.

---

## LOCKED DECISIONS SUMMARY

### Thread 1 — Revenue & Product
*Status: Authoritative. Not stale.*

- Primary revenue: Intelligence-as-a-Service
- Revenue architecture: Multi-stream from day one (subscriptions, API
  licensing, protocol reporting, index v2, embedded v2)
- Product surface: Layer 6 (signals via Marts); Layer 4/5 as B2B secondary
- Content originality: Quantitative, systematic, auditable — not qualitative
- Asset coverage: Domain-driven, not ticker-driven
- MVP: Signal product, institutional early access, manual invoicing.
  No dashboard, no billing infra.
- Index licensing: v2 — deferred. Trigger: methodology documented +
  ToS audited.

### Thread 2 — Signal Architecture
*Status: Authoritative. Not stale. Carries forward unchanged.*

- Two independent tracks: EDSx (deterministic) + ML (LightGBM),
  shared data and features
- EDSx confidence = data completeness (signals_computed /
  signals_available), not prediction confidence
- Five pillars: Trend/Structure, Liquidity/Flow, Valuation,
  Structural Risk, Tactical Macro
- Five ML models: Derivatives Pressure, Capital Flow Direction,
  Macro Regime, DeFi Stress, Volatility Regime
- Prediction horizon: 14 days, volume-adjusted labels, tercile
  discretization per training window
- Synthesis: confidence-weighted, 0.5/0.5 EDSx/ML default,
  recalibrated quarterly
- Regime engine: legacy M2-only in production; H2 target =
  Volatility-Liquidity Anchor (4 quadrants: Full Offense /
  Selective Offense / Defensive Drift / Capital Preservation);
  ML POC experimental track (blocked UNI-01)
- Regime is not a pillar — it drives composite weight selection,
  does not score instruments
- Graduation: five hard criteria, no self-certification,
  minimum 30-day shadow period
- Magnitude: ML track only
- Live pillars: EDSx-02 (Trend/Structure), EDSx-03 R3 (Liquidity/Flow)
- Planned pillars: Valuation (REM-21), Tactical Macro (REM-22/23),
  Structural Risk (REM-24)

### Thread 3 — Feature Engineering
*Status: Authoritative. Not stale. Carries forward unchanged.*

- Features are transformations, not storage — recomputable at any time
- PIT constraint: absolute, no exceptions
- Null states: three distinct types — INSUFFICIENT_HISTORY,
  SOURCE_STALE, METRIC_UNAVAILABLE
- Computation order: A → C → B → F → G → D → E
- Computation trigger: event-driven on metric ingestion, not wall-clock
- Feature catalog entry required before any feature is computed;
  immutable once locked
- Breadth score: deterministic formula, fixed weights, not learned
- Idempotency: hard requirement
- forge_compute lives in Layer 6 (Marts)
- DuckDB reads Layer 5 (Gold) — never Layer 4 (Silver) directly

### Thread 4 — Data Universe
*Status: STALE on infrastructure. Rewrite pending.*
*Catalog table DDL and pre-populated data are deployed and correct.*
*Do not use for observation store schema — that is now ClickHouse.*

Decisions that carry forward:
- Schema model: EAV + metric catalog + materialized current-value view
- instrument_id nullable: correct for market-level metrics
- PIT model: bitemporal — observed_at + ingested_at
- Revision handling: new row inserted, old row preserved via
  data_version increment (ReplacingMergeTree in ClickHouse)
- Backfill PIT: ingested_at = load time; backtests exclude data
  ingested after T
- Canonical metric naming: domain.subdomain.metric_name —
  hierarchical, no abbreviations; immutable once assigned
- Instrument tiers: collection → scoring → signal_eligible,
  rule-driven promotion
- Schema immutability: new metric = catalog row; new source =
  catalog row; zero DDL

Decisions that changed:
- Primary DB: PostgreSQL (catalog only) + ClickHouse (observations)
  — NOT PostgreSQL + TimescaleDB
- Analytical layer: DuckDB against Iceberg tables on MinIO
  — NOT DuckDB against raw Parquet exports

### Thread 5 — Normalization & Collection
*Status: STALE. Rewrite pending.*
*Adapter contract (10-responsibility) carries forward unchanged.*
*Everything else — storage targets, migration plan, collector
inventory — is stale.*

Decisions that carry forward:
- Adapter interface: standardized 10-responsibility contract
- Validation: per-observation, independent; batch does not fail
  on single bad value
- Dead letter: every rejection logged with raw payload, reason,
  rejection code — nothing silently dropped
- Redistribution: flagged at source catalog level; enforced at
  serving layer
- Excluded permanently: sentiment data, development activity,
  Santiment (deprecated)

Decisions that changed:
- Landing zone: Iceberg tables on MinIO — NOT schema-per-source
  PostgreSQL tables
- Orchestration: Dagster Software-Defined Assets — not in prior design

### Thread 6 — Build Plan
*Status: STALE. All phase gates written against wrong stack.*
*Build sequence and phase gate model carry forward.*
*All gate criteria must be rewritten.*

Decisions that carry forward:
- Build sequence: Phase 0 → Phase 1 → Phase 2 → Phase 3 →
  Phase 4 → Phase 5 → Phase 6
- Phase gate model: hard pass/fail; no phase begins until
  previous gate passes; architect confirms
- ML shadow period: minimum 30 days; extension if shadow
  evaluation fails
- First customer: Phase 6 completion; direct engagement;
  real pricing; no free trials
- ToS audit: Phase 6, before any external data product ships

### Thread Infrastructure — NEW
*Status: Does not yet exist. Written first in design rewrite sequence.*

Covers: Technology stack decisions with full alternatives analysis.
Layer boundary rules. Infrastructure split triggers with defined
measurable thresholds. Managed service migration path. ADR for all
technology choices.

---

## PHASE 0 — PRECISE STATUS

### Deployed and correct — DO NOT TOUCH

| Table | Contents |
|-------|----------|
| forge.assets | Deployed, pre-populated |
| forge.asset_aliases | Deployed, pre-populated |
| forge.venues | Deployed, pre-populated |
| forge.instruments | Deployed, pre-populated |
| forge.source_catalog | Deployed, pre-populated — all 14 sources |
| forge.metric_catalog | Deployed, pre-populated — all domain metrics |
| forge.metric_lineage | Deployed, correct |
| forge.event_calendar | Deployed (data populated Phase 1) |
| forge.supply_events | Deployed, correct |
| forge.adjustment_factors | Deployed, correct |

### Redeployed — exactly these three objects

| Object | Action | Target |
|--------|--------|--------|
| forge.observations | Drop from PostgreSQL, rebuild | ClickHouse |
| forge.dead_letter | Drop from PostgreSQL, rebuild | ClickHouse |
| forge.current_values | Drop from PostgreSQL, rebuild as materialized view | ClickHouse |

### Removed entirely
- TimescaleDB hypertable configuration
- forge_writer / forge_reader permissions on forge.observations

---

## KNOWN GAPS WITH DOCUMENTED PLANS

| Gap | v1 Handling | Resolution Trigger |
|---|---|---|
| `defi.lending.utilization_rate` | Proxy: borrow/supply TVL ratio | v1.1 milestone |
| Options data (Deribit) | Null-propagate | v1.1 milestone |
| Exchange flows beyond 18 instruments | Accept coverage limit | v1.1 milestone |
| CoinMetrics redistribution | Internal use only | Phase 6 ToS audit |
| SoSoValue redistribution | Internal use only | v2 data product launch |
| Index/benchmark licensing | Deferred | v2 revenue milestone |
| Polygon.io | DROPPED — satisfied by Tiingo + FRED + existing EDS OHLCV | Closed |
| BLC-01 (Binance liquidations) ToS | Unaudited — internal only | Phase 6 ToS audit |
| BOJ/PBOC balance sheets | Evaluate FRED availability | During FRG-10 build |

---

## SOURCES CATALOG SUMMARY

| Source | Provides | ToS Risk | Redistribution |
|---|---|---|---|
| Coinalyze | Perpetual futures — funding, OI, liquidations, L/S ratio (121 instruments) | Unaudited | Pending audit |
| DeFiLlama | Protocol TVL, DEX volume, lending rates, stablecoins | Low | Yes |
| FRED | Macro time series (25–30 series) | None (public domain) | Yes |
| Tiingo | OHLCV (crypto + equities) | Paid tier for commercial | Yes (paid) |
| SoSoValue | ETF flows | Restricted — non-commercial only | **No** |
| Etherscan / Explorer | Exchange flows (18 instruments, ETH + ARB) | Unaudited | Pending audit |
| CoinPaprika | Market cap, price data | Low | Yes |
| CoinMetrics | On-chain transfer volume | Unaudited | **No (pending audit)** |
| BGeometrics | MVRV, SOPR, NUPL, Puell (BTC/ETH) — EDS legacy | Unaudited | Pending audit |
| Binance (BLC-01) | Tick-level liquidation events (~65–72k/day, 100+ symbols) | Unaudited | Pending audit |

---

*Full specifications in `docs/design/thread_*.md` and
`SESSION_HANDOFF_2026-03-05_v2.md`. Infrastructure decisions in thread_4,
thread_5, and thread_6 are stale — cross-reference the handoff document
until thread rewrites are complete. Changes to locked decisions require
architect approval.*
