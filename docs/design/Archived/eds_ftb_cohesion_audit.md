# EDS ↔ FTB Cohesion Audit

**Date:** 2026-03-08
**Documents audited:**
- `FromTheBridge_design_v3.1.md` (FTB, synthesized 2026-03-06)
- `EDS_design_v1.1.md` (EDS, synthesized 2026-03-08)
- `thread_infrastructure.md` (FTB, archived)
- Both project CLAUDE.md files
- `db/migrations/postgres/0001_catalog_schema.sql` (FTB Phase 0 seed)

**Auditor:** Claude (parallel agent analysis across 6 dimensions)

---

## Executive Summary

EDS was designed with full awareness of FTB — its rules, schemas, and conventions are explicitly referenced throughout. FTB was designed before EDS existed and contains zero references to EDS, the `empire.*` ClickHouse schema, the `empire_utxo` PostgreSQL schema, or the `empire_to_forge_sync` data bridge. This asymmetry is the root cause of most findings.

The most critical finding is that the `empire_to_forge_sync` interface — the only data bridge between the two systems — has a **metric_id naming mismatch** that would cause every synced metric to be dead-lettered. Secondary issues include an `instrument_id` nullability mismatch, missing source catalog entries, undocumented resource budgets, and several documentation gaps where FTB needs to acknowledge EDS as a cohabitant.

**Findings by severity:**

| Severity | Count |
|----------|-------|
| Contradictions | 4 |
| Gaps | 9 |
| Contention risks | 5 |
| Agreements | 8 |

---

## 1. Schema Boundary — empire.* vs forge.*

### Agreements

**A1 — Schema ownership is architecturally clean.** EDS acknowledges `forge.*` as FTB's domain in 4 separate locations (EDS Rules 1-2, No-Drift Principles, CLAUDE.md). EDS creates `empire.*` as a separate ClickHouse database. Grant models are fully disjoint — `eds_writer`/`eds_reader` have zero grants on `forge.*`; `ch_writer`/`ch_export_reader` have zero grants on `empire.*`. Negative access tests in the EDS design confirm mutual isolation.

**A2 — Schema immutability scoping.** FTB's "no DDL after Phase 0" rule is scoped to `forge.*` by design — Phase 0 gate criteria only verify `forge` schema objects. EDS adding 12 `empire.*` tables does not violate this rule. Both docs support this interpretation.

### Contradictions

**C1 — ClickHouse user naming mismatch.**
EDS references FTB's ClickHouse writer as `forge_writer` (EDS design lines 155, 1255, 1263, 1292). FTB's actual user is `ch_writer` (FTB design line 4137). EDS's negative access tests reference `forge_writer SELECT FROM empire.observations → ACCESS_DENIED` — testing a user name that may not exist. The EDS sync asset specification (line 1292) hedges with `forge_writer (ch_writer, INSERT on forge.observations)` acknowledging the alias, but the canonical name must be resolved.

**C2 — instrument_id nullability mismatch.**
EDS `empire.observations`: `instrument_id String NOT NULL DEFAULT '__market__'` — uses sentinel string for market-level metrics. FTB `forge.observations`: `instrument_id Nullable(String)` — uses `NULL` for market-level metrics. The sync column mapping says "pass-through," which would store the literal string `'__market__'` where FTB expects `NULL`. Impact: FTB queries filtering on `instrument_id IS NULL` for market-level metrics would miss all EDS-sourced data. The `forge.current_values` materialized view would create duplicate entries (one for `NULL`, one for `'__market__'`).

### Gaps

**G1 — FTB has no awareness of empire.*.**
FTB's design v3.1, CLAUDE.md, and thread_infrastructure.md contain zero references to the `empire` ClickHouse database, the `empire_to_forge_sync` asset, or EDS as a data provider. FTB's architecture was designed as a self-contained system with 11 native data sources. The sync asset is an architectural anomaly from FTB's perspective.

**G2 — FTB defines no ClickHouse resource profiles.**
EDS defines explicit settings profiles: `eds_writer_profile` (4GB RAM, 4 threads, 300s timeout), `eds_reader_profile` (2GB RAM, 2 threads, 120s timeout). FTB defines no equivalent profiles for `ch_writer` or `ch_export_reader`. On a shared ClickHouse instance, FTB operations are unbounded while EDS is self-limiting — asymmetric resource contention.

**G3 — empire_utxo PostgreSQL schema undocumented in FTB.**
EDS adds `empire_utxo` schema to `empire_postgres` (port 5433) — the same PostgreSQL instance FTB uses for `forge.*` catalog. FTB's Database Rules and Database Targeting Reference list only `forge` schema. FTB's backup procedures, disaster recovery, and performance assumptions do not account for `empire_utxo`. The UTXO backfill (7-14 day intensive write processing every Bitcoin block from genesis) could impact FTB catalog read performance.

**G4 — empire_utxo PostgreSQL user/grants unspecified.**
EDS design defines the `empire_utxo` schema DDL but provides no CREATE USER or GRANT statements for the PostgreSQL user that reads/writes UTXO tables.

---

## 2. empire_to_forge_sync Interface

### Agreements

**A3 — Column mapping is broadly correct (5 of 7 columns clean).** `metric_id`, `observed_at`, `value`, `ingested_at` (set to sync time — correct for PIT semantics), and `data_version` are all properly mapped. The 3 EDS-specific columns (`chain_id`, `block_height`, `derivation_version`) are correctly dropped.

**A4 — Cadence is compatible.** EDS sync runs every 6h. FTB's Silver → Gold export is event-triggered with 1h fallback. Once sync writes hit `forge.observations`, FTB's `multi_asset_sensor` (30s polling) detects them and promotes to Gold within minutes. No data loss. Up to 6h additional latency for EDS-sourced metrics vs FTB native adapters — acceptable for daily/8h cadence metrics.

### Contradictions

**C3 — Metric_id "pass-through" claim is false.**
The sync column mapping (EDS line 1303) states metric_id is "pass-through (shared dot-notation convention)." In reality, EDS and FTB use fundamentally different metric_id naming for the same data:

| Concept | FTB metric_id | EDS metric_id |
|---------|---------------|---------------|
| Funding rate | `derivatives.perpetual.funding_rate` | `exchange.derivatives.funding_rate_8h` |
| Open interest | `derivatives.perpetual.open_interest_usd` | `exchange.derivatives.open_interest_usd` |
| Liquidations (long) | `derivatives.perpetual.liquidations_long_usd` | `exchange.derivatives.liquidations_long_usd` |
| Transfer volume | `flows.onchain.transfer_volume_usd` | `chain.activity.transfer_volume_usd` |
| MVRV | (valuation domain) | `chain.valuation.mvrv_ratio` |
| SOPR | (valuation domain) | `chain.valuation.sopr` |
| NUPL | (valuation domain) | `chain.valuation.nupl` |
| Puell Multiple | (valuation domain) | `chain.valuation.puell_multiple` |
| NVT proxy | `macro.nvt_txcount_proxy` | `chain.activity.nvt_proxy` |
| FRED funds rate | `macro.rates.fed_funds_effective` | `macro.rates.fed_funds_rate` |
| DeFi TVL | `defi.aggregate.tvl_usd` | `defi.tvl.total_usd` |
| ETF flows | `etf.flows.net_flow_usd` | `flows.etf.btc_net_flow_usd` |

The sync's own dead-letter logic would reject all of these with `METRIC_NOT_REGISTERED` since the EDS metric_ids do not exist in FTB's `forge.metric_catalog`. **This is the single most critical finding in this audit.**

**C4 — FTB domain CHECK constraint blocks EDS metric domains.**
FTB's `forge.metric_catalog.domain` CHECK constraint (FTB design line 1584) allows only: `('derivatives', 'spot', 'flows', 'defi', 'macro', 'etf', 'stablecoin')`. EDS's highest-value metrics use `chain` and `exchange` domains, which are not in this list. Even with a metric_id mapping table, promoted EDS metrics cannot be inserted into `forge.metric_catalog` without either extending the CHECK constraint (schema change, violates immutability) or mapping all metrics to FTB-compatible domains.

### Gaps

**G5 — No `eds_derived` entry in forge.source_catalog.**
The sync writes `source_id = 'eds_derived'`. FTB's source_catalog seed contains 10 entries (bgeometrics, binance_blc01, coinalyze, coinmetrics, coinpaprika, defillama, etherscan, fred, sosovalue, tiingo). No EDS-related source_id exists. ClickHouse has no foreign keys so writes succeed, but FTB's collection_events, coverage tracking, freshness monitoring, and GE checkpoints all join on source_id and would produce incomplete results for EDS-sourced data.

**G6 — Promotion path is circular/incomplete.**
EDS design (line 840): "Promotion to FTB's forge.metric_catalog happens via empire_to_forge_sync." EDS design (line 1295): "Only metrics registered in forge.metric_catalog are synced." These two statements create a circular dependency. The intended flow has a missing step: who writes the `forge.metric_catalog` PostgreSQL row? The sync asset uses ClickHouse credentials only — it has no PostgreSQL write access to `forge.metric_catalog`. Neither project documents the PostgreSQL write step in the promotion workflow.

### Contention Risks

**R1 — data_version collision for overlapping metrics.**
FTB's `forge.observations` ORDER BY is `(metric_id, instrument_id, observed_at)`. `source_id` is NOT part of the ordering key. If both FTB (via BGeometrics adapter) and EDS (via node derivation) write the same metric with the same `(metric_id, instrument_id, observed_at)` and both use `data_version = 1`, ReplacingMergeTree keeps one row arbitrarily during merge. This is non-deterministic data loss for the 5 critical Valuation pillar metrics (MVRV, SOPR, NUPL, Puell, transfer_volume) that EDS specifically replaces.

Resolution options: (a) add `source_id` to ORDER BY (DDL change — violates immutability), (b) ensure EDS and FTB never write the same metric (organizational constraint), or (c) establish version namespace convention (e.g., EDS always uses `data_version ≥ 1000`).

---

## 3. Shared Infrastructure Contention

### Agreements

**A5 — No Docker port or container name conflicts.** EDS does not introduce new Docker services on proxmox — it injects into existing FTB-defined services (same ClickHouse, same PostgreSQL, same Dagster). No port collisions.

**A6 — Storage is adequate.** FTB projects ~2.5GB ClickHouse over 5 years, ~75GB MinIO over 5 years. Both /mnt/empire-db (2TB) and /mnt/empire-data (4TB) have ample headroom. EDS does not currently plan MinIO usage.

### Gaps

**G7 — No proxmox resource budget documented by either project.**
The only proxmox memory figure is EDS line 1384: "56GB free RAM" — a point-in-time observation, not an architectural constraint. Neither project documents total proxmox RAM/CPU, per-service memory limits (Docker `mem_limit`), or combined steady-state projections. FTB at Phase 1 adds ~65 Dagster assets + active ClickHouse writes + MinIO. EDS adds 12 ClickHouse tables + 30+ Dagster assets + UTXO PostgreSQL writes. The "56GB free" figure does not account for FTB's Phase 1+ load.

**G8 — Dagster code server integration mechanism undefined.**
FTB's `empire_dagster_code` mounts `/opt/empire/pipeline`. EDS needs its asset definitions in the same code server. Neither doc specifies whether this is a single code server with both projects' assets (monorepo), two separate gRPC code servers (multi-location), or some other mechanism. The container name (`empire_dagster_code`) appears in both projects pointing to the same service.

**G9 — No Dagster concurrency limits defined.**
Neither project defines `max_concurrent_runs` or Dagster run queue limits. Combined steady-state: 100+ Dagster assets with overlapping schedules. The 8h cadence window would see simultaneous FTB (Coinalyze, Etherscan) and EDS (6 exchange derivatives) runs. The hourly window sees FTB export + EDS DeFiLlama + EDS Track 1 block metrics all firing together. Dagster's default is unlimited concurrent runs.

### Contention Risks

**R2 — ClickHouse merge contention.**
Combined: 15 tables across two databases on a single ClickHouse instance. EDS `empire.observations` produces ~84 partitions/year (7 chains × 12 months). FTB `forge.observations` produces ~12 partitions/year. Background merge threads are shared. EDS acknowledges this risk (pipeline item for "merge lag resolution") but neither project specifies merge thread allocation.

**R3 — UTXO backfill impact on PostgreSQL.**
The `empire_utxo` backfill processes every Bitcoin block from genesis — a 7-14 day intensive write operation. The UTXO set is ~180M records (~18GB+). This runs on the same PostgreSQL instance FTB uses for catalog reads. No scheduling coordination is documented.

**R4 — EDS ClickHouse storage projection missing.**
EDS does not project its ClickHouse footprint on `/mnt/empire-db`. While storage headroom is likely adequate, `raw_exchange_events` (~2.1M rows/month, 30-day TTL) and `raw_blocks` (7 chains, 90-day TTL) will consume non-trivial space. No projection exists for combined steady-state.

---

## 4. Metric Namespace

### Contradictions

(Covered in C3 above — metric_id naming mismatch is the primary finding.)

### Contention Risks

**R5 — Track 3 metric_id near-collisions.**
EDS Track 3 mirrors FTB's FRED, DeFiLlama, SEC EDGAR, and CFTC COT sources. The metric_ids are similar but not identical (e.g., `macro.rates.fed_funds_rate` vs `macro.rates.fed_funds_effective`, `defi.tvl.total_usd` vs `defi.aggregate.tvl_usd`). This creates ambiguity about whether Track 3 data should sync to FTB or whether FTB continues sourcing these directly. If both FTB adapters and EDS Track 3 feed the same data, the data_version collision issue (R1) applies.

### Gaps

**G5b — Metrics EDS claims to replace require FTB migration.**
EDS replaces BGeometrics (MVRV, SOPR, NUPL, Puell), Coinalyze (8 derivatives metrics), CoinMetrics (transfer volume), and partially Etherscan (exchange flows). In every case, the metric_ids differ. FTB would need either: new metric_catalog rows matching EDS naming, a mapping table in the sync layer, or convention alignment between both projects.

---

## 5. Operational Overlap

### Agreements

**A7 — Shared Dagster deployment agreed.** Both docs explicitly state assets coexist in the same Dagster deployment with asset group namespace isolation. EDS uses `eds_` prefix throughout; FTB uses no prefix.

**A8 — Health endpoints don't conflict.** FTB: `GET /healthz/ready` on port 8000. EDS: `GET /eds/v1/health` on TBD port. Different paths, different ports.

### Gaps

**G10 — FTB event_calendar blocks EDS event types.**
FTB's `event_calendar.event_type` CHECK constraint allows only: `fomc`, `cpi_release`, `nfp_release`, `gdp_release`, `futures_expiry`, `options_expiry`, `token_unlock`. EDS plans to write: capacity projections, drive replacements, node releases, maintenance windows. None fit the constraint. EDS pipeline item EDS-31 acknowledges this gap. Resolution requires either extending the CHECK constraint (schema change) or a separate mechanism.

**G11 — EDS Dagster watchdog scope is ambiguous.**
EDS defines a systemd timer (`eds-dagster-watchdog.sh`) running every 5 minutes on proxmox monitoring `empire_dagster_daemon`. Since both projects share the daemon, a watchdog restart affects FTB assets too. This is beneficial (protects both) but named as EDS-specific. If FTB later adds its own watchdog, they would conflict (duplicate restart attempts). Neither CLAUDE.md documents the shared scope.

### Contention Risks

**R6 — Prometheus disagreement.**
FTB references Prometheus for signal cache metrics (FTB design line 3944: `signal_cache_computed_at_epoch`). EDS explicitly rejects adding Prometheus, Grafana, or AlertManager (EDS design line 1914). Both run on proxmox. Either FTB brings its own Prometheus at Phase 5, or its signal cache monitoring approach needs to change. This is Phase 5 — not urgent but needs resolution before then.

---

## 6. Rule Conflicts

### Agreements

**A9 — UTXO state is not time series.**
EDS's `empire_utxo` stores UTXO lifecycle state (creation_timestamp, spending_timestamp, value_satoshis), not metric observations (observed_at + value). EDS Rule 4 explicitly inherits FTB Rule 3 and provides an enforcement check confirming no `observed_at`/`value`/`value_numeric` columns exist in `empire_utxo`. Derived time-series values (MVRV, SOPR, etc.) are written to `empire.observations` in ClickHouse, not PostgreSQL. Architecturally sound.

**A10 — Schema immutability scoped correctly.**
FTB schema immutability applies to `forge.*` only. EDS adding `empire.*` tables to ClickHouse operates in a separate database. No violation.

### Gaps

**G12 — FTB Rule 2 stated universally but scoped to forge.* by credentials.**
FTB Rule 2: "ClickHouse is write-only (only export asset reads)." FTB CLAUDE.md Forbidden Actions: "Read ClickHouse except export Dagster asset." EDS explicitly exempts `empire.*` from Rule 2 and reads it via EDS API. Architecturally, FTB's `ch_export_reader` has SELECT on `forge.observations` only — the rule is enforced on `forge.*` by credential design. But the text states the rule universally. A future builder reading only FTB docs would conclude that ANY ClickHouse read from any service violates Rule 2.

**G13 — FTB Rule 3 doesn't acknowledge empire_utxo.**
FTB Rule 3 enforcement check is scoped to `table_schema = 'forge'` and would not detect `empire_utxo`. EDS supplements this with its own enforcement check scoped to `empire_utxo`. But FTB's docs do not acknowledge that `empire_utxo` exists on its PostgreSQL instance.

### Contention Risks

**R7 — Server2 write prohibition scope.**
FTB CLAUDE.md: "Target Server2 (192.168.68.12) for any writes — Server2 is Binance Collector only." EDS CLAUDE.md: Allows BLC-01 data pull and pruned ETH failover on Server2. BLC-01 writes liquidation data to local storage on Server2. The literal text of FTB's rule ("any writes") conflicts with EDS operations. Practical intent is clear (FTB means "don't deploy FTB services to Server2") but the literal wording creates agent confusion.

---

## Recommendations

These are organized by urgency. No changes are proposed — flagged for architect decision only.

### Before empire_to_forge_sync build (Critical)

1. **Resolve metric_id naming convention** (C3, C4, G5b). The sync cannot function with pass-through metric_ids. Options: (a) explicit mapping table in the sync layer, (b) align EDS metric_ids to FTB conventions before EDS-1 launch, (c) extend FTB's domain CHECK constraint to include `chain` and `exchange`. Each has tradeoffs.

2. **Resolve instrument_id translation** (C2). The sync must map `'__market__'` → `NULL` before writing to `forge.observations`. Document the transform in both projects.

3. **Resolve ClickHouse user naming** (C1). Decide whether FTB's ClickHouse writer is `ch_writer` or `forge_writer`. Update both docs to use the canonical name. Ensure CREATE USER and GRANT statements match.

4. **Resolve data_version collision risk** (R1). Decide: can both FTB adapters and EDS sync write the same metric to `forge.observations`? If yes, define deduplication strategy. If no, document the mutual exclusion rule and decommission sequence.

### Before EDS-1 gate

5. **Add `eds_derived` to FTB source_catalog** (G5). Required for FTB's freshness monitoring, collection tracking, and GE checkpoints to function on EDS-sourced data.

6. **Document the promotion workflow** (G6). Specify who writes the `forge.metric_catalog` PostgreSQL row, what credential is used, and how `forge.source_catalog` and `forge.metric_lineage` are populated for promoted metrics.

7. **Define Dagster code server integration** (G8). Decide: single code server with both projects' assets, multi-code-location with separate gRPC servers, or other mechanism.

8. **Define Dagster concurrency limits** (G9). Set `max_concurrent_runs` or equivalent to prevent resource contention when 100+ combined assets fire simultaneously.

### Before Phase 1 deployment (either project)

9. **Create shared infrastructure capacity plan** (G7). Document proxmox total RAM/CPU, per-service memory limits, combined steady-state projection. The "56GB free" figure is insufficient for planning.

10. **Define FTB ClickHouse resource profiles** (G2). FTB should set `max_memory_usage` and `max_threads` for `ch_writer` and `ch_export_reader` to match EDS's self-limiting approach.

11. **Update FTB design docs to acknowledge EDS** (G1, G3, G12, G13). Specifically: (a) `empire.*` ClickHouse database exists on same instance, (b) `empire_utxo` PostgreSQL schema exists on same instance, (c) `empire_to_forge_sync` writes to `forge.observations`, (d) Rule 2 scope is `forge` database, (e) Rule 3 scope is `forge` schema; `empire_utxo` governed by EDS Rule 4.

12. **Clarify Server2 write prohibition scope** (R7). Both CLAUDE.md files should specify: "FromTheBridge services must not write to Server2. EDS services (BLC-01, pruned ETH) are the only authorized writers."

### Phase 5+ (lower urgency)

13. **Resolve Prometheus disagreement** (R6). FTB references Prometheus for signal cache; EDS rejects Prometheus. Decide before Phase 5.

---

## Resolution Status (Updated 2026-03-08)

Findings resolved by cross-project audit fixes applied to both design documents
(EDS v1.1.2 + FTB v3.1 amendments):

| Finding | Status | Resolution |
|---------|--------|------------|
| **C1** — ClickHouse user naming | **RESOLVED** | FTB consistently uses `ch_writer`. EDS references updated. |
| **C2** — instrument_id nullability | **RESOLVED** | Convention documented in both docs: EDS uses `'__market__'` (non-null), FTB ClickHouse uses `NULL`, sync maps `'__market__'` → `NULL`. `__market__` PG row is catalog-only. |
| **C3** — metric_id naming mismatch | **RESOLVED** | EDS v1.1.2 adopts FTB canonical names for all synced metrics. 14 FRED metrics renamed, DeFiLlama metrics aligned, ETF namespace changed from `flows.etf.*` to `etf.*`. EDS-exclusive metrics separated. |
| **C4** — Domain CHECK constraint | **RESOLVED** | `chain` and `valuation` domains added to FTB CHECK constraint. |
| **G1** — FTB unaware of empire.* | **RESOLVED** | FTB design updated with empire.* references, sync volume projections, shared_ops documentation. |
| **G2** — No FTB ClickHouse resource profiles | **RESOLVED** | Profiles defined in FTB design. |
| **G3** — empire_utxo undocumented in FTB | **RESOLVED** | Documented in FTB design. |
| **G4** — empire_utxo user/grants unspecified | **RESOLVED** | `eds_utxo_admin` fully specified in EDS design (line 580). |
| **G5** — No `eds_derived` in source_catalog | **RESOLVED** | Migration 0002 adds it. |
| **G6** — Promotion path circular | **RESOLVED** | Manual SQL migration workflow documented in FTB design (metric promotion section). |
| **G7** — No proxmox resource budget | **PARTIALLY RESOLVED** | ClickHouse profiles done. Full proxmox budget tracked by EDS-49. |
| **G8** — Dagster code server integration | **UNRESOLVED** | Tracked by EDS-47. Must resolve before Phase 1. |
| **G9** — Dagster concurrency limits | **UNRESOLVED** | Tracked by EDS-48. Must resolve before Phase 1. |
| **G10** — event_calendar blocks EDS types | **UNRESOLVED** | DDL not yet committed. Tracked by EDS-31. |
| **G11** — Dagster watchdog scope | **RESOLVED** | Shared scope noted in both EDS design (v1.1.2) and pipeline item EDS-41. |
| **G12** — Rule 2 scoped to forge.* | **RESOLVED** | Explicit scope statement in FTB design. |
| **G13** — Rule 3 doesn't acknowledge empire_utxo | **RESOLVED** | Explicit scope statement in FTB design. |
| **R1** — data_version collision | **RESOLVED** | Mutual exclusion protocol documented. |
| **R2** — ClickHouse merge contention | **DEFERRED** | Low risk at Phase 1 volumes. Monitor during deployment. |
| **R3** — UTXO backfill PostgreSQL impact | **UNRESOLVED** | Tracked by EDS-50. Schedule during low-FTB-activity period. |
| **R4** — EDS ClickHouse storage projection | **DEFERRED** | FTB projections updated to note EDS sync volume (+17,350 rows/day). |
| **R5** — Track 3 metric_id near-collisions | **RESOLVED** | All Track 3 metrics now use FTB canonical names (EDS v1.1.2). |
| **R6** — Prometheus disagreement | **DEFERRED** | Phase 5 decision. Tracked by EDS-51. |
| **R7** — Server2 write prohibition scope | **RESOLVED** | Clearly scoped in both CLAUDE.md files. |

**Summary:** 17 resolved, 2 partially resolved/deferred, 4 unresolved (tracked by pipeline items).

14. **Resolve event_calendar constraint** (G10). EDS-31 pipeline item. Extend event_type enum or create separate mechanism for infrastructure events.

15. **Coordinate UTXO backfill scheduling** (R3). Schedule the 7-14 day intensive PostgreSQL write during low-FTB-activity period.

16. **Rename Dagster watchdog to reflect shared scope** (G11). Both CLAUDE.md files should document that the watchdog covers the shared Dagster deployment.

---

## Appendix: Complete Finding Index

| ID | Type | Severity | Dimension | Summary |
|----|------|----------|-----------|---------|
| A1 | Agreement | — | D1 | Schema ownership architecturally clean |
| A2 | Agreement | — | D1 | Schema immutability scoped to forge.* |
| A3 | Agreement | — | D2 | Column mapping correct for 5/7 columns |
| A4 | Agreement | — | D2 | Sync cadence compatible |
| A5 | Agreement | — | D3 | No Docker port/container conflicts |
| A6 | Agreement | — | D3 | Storage adequate |
| A7 | Agreement | — | D5 | Shared Dagster agreed |
| A8 | Agreement | — | D5 | Health endpoints don't conflict |
| A9 | Agreement | — | D6 | UTXO state ≠ time series |
| A10 | Agreement | — | D6 | Schema immutability scoped correctly |
| C1 | Contradiction | High | D1 | ClickHouse user naming (ch_writer vs forge_writer) |
| C2 | Contradiction | High | D2 | instrument_id nullability (__market__ vs NULL) |
| C3 | Contradiction | Critical | D2/D4 | metric_id pass-through claim false; names don't match |
| C4 | Contradiction | High | D4 | Domain CHECK constraint blocks EDS domains |
| G1 | Gap | High | D1 | FTB unaware of empire.* |
| G2 | Gap | Medium | D1 | FTB has no ClickHouse resource profiles |
| G3 | Gap | High | D1 | FTB unaware of empire_utxo in PostgreSQL |
| G4 | Gap | Medium | D1 | empire_utxo PostgreSQL user/grants unspecified |
| G5 | Gap | Medium | D2 | No eds_derived in source_catalog |
| G6 | Gap | High | D2 | Promotion path circular/incomplete |
| G7 | Gap | High | D3 | No proxmox resource budget documented |
| G8 | Gap | Medium | D3 | Dagster code server integration undefined |
| G9 | Gap | Medium | D3 | No Dagster concurrency limits |
| G10 | Gap | Low | D5 | event_calendar constraint blocks EDS events |
| G11 | Gap | Low | D5 | Dagster watchdog scope ambiguous |
| G12 | Gap | Medium | D6 | Rule 2 stated universally, scoped to forge.* |
| G13 | Gap | Low | D6 | Rule 3 doesn't acknowledge empire_utxo |
| R1 | Contention | High | D2 | data_version collision for overlapping metrics |
| R2 | Contention | Medium | D3 | ClickHouse merge contention (96+ partitions) |
| R3 | Contention | Medium | D3 | UTXO backfill PostgreSQL impact |
| R4 | Contention | Low | D3 | EDS ClickHouse storage projection missing |
| R5 | Contention | Medium | D4 | Track 3 metric_id near-collisions |
| R6 | Contention | Low | D5 | Prometheus disagreement (Phase 5) |
| R7 | Contention | Low | D6 | Server2 write prohibition scope |
