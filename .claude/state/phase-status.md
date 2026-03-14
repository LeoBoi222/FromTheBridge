# Phase Status

**As of:** 2026-03-14

| Phase | Status | Detail |
|-------|--------|--------|
| 0 — Schema | Complete | 80 metrics (76 seed + 4 added), 11 sources, 7 instruments, CH Silver, MinIO buckets |
| 1 — Collection | In progress | Sync verified: 38/38 eds_derived metrics flowing, 100% coverage. Instrument fixes deployed. |
| 2–6 | Not started | Gated on Phase 1 |

## Deployed on proxmox

- PostgreSQL `forge` schema: 12 catalog tables, seed data above
- ClickHouse `forge`: 3 tables (observations, dead_letter, current_values MV), ~21k rows
- MinIO: bronze-hot (90d lifecycle), bronze-archive, gold buckets
- Dagster: 4 containers (webserver :3010, daemon, code_ftb, code_eds)
- empire_to_forge_sync: 6h schedule, 38 eds_derived metrics (0012 promotion + 0014 DeFi correction)
- Ops health: 3 assets on 30m schedule. Bronze archive: daily 02:00 UTC. Gold export: hourly :15.
- Instruments: BTC-USD, ETH-USD, SOL-USD, USDT-USD, USDC-USD, DAI-USD, __market__ (0013 migration)

## Blocked

- **Infra:** Server2 OS upgrade (blocks EDS-0, not FTB).
- **Cleared:** EDS metric_id rename / C3 violation (2026-03-13).
- **Cleared:** ai-srv-01 operational (2026-03-13). Unblocks Phase 4 ML.
- **Cleared:** Sync auto-flow verified (2026-03-14). 38/38 metrics, 100% coverage.

## Gate Criteria (Phase 1)

15 of 40 criteria passing. Updated 2026-03-14.

| Status | Criteria |
|--------|----------|
| ✅ | Dagster services healthy, Dagster in docker-compose, observations_written, redistribution flags, C2 bronze-archive bucket, C2 bronze_archive_log DDL, historical depth (backfill_depth_days), training window viability (T5 report), dead letter triage (0 old), ops assets, runbooks (FTB-01–09), ops credentials, calendar schema |
| ✅ (new) | Sync auto-flow verified (38/38 metrics, 100% coverage, 7,960 obs written) |
| ⚠️ | NAS backup (script exists, timer inactive), C2 archive credentials (user exists, isolation unverified) |
| ✅ (0012) | Migration complete (35 UPDATE + 4 INSERT, 41 eds_derived total → 38 after 0014 DeFi correction), CFTC COT (3 of 4 metrics — institutional_long_pct pending EDS) |
| ❌ EDS-blocked | live collection, rejection rate, coverage, Tiingo history (2019 not 2014), wei fix, tier promotion (0 signal_eligible), PF-6 utilization unit, FRED series (Gold/MOVE/BOJ — not yet in empire.observations), DeFiLlama yields, ingested_at correctness, priority-1 backfill, BLC-01 rsync |
| ✅ (new) | Export round-trip verified: Silver → Gold export → DuckDB read. 5,746 rows across 4 domains (derivatives/6, defi/2, flows/3, macro/25). PyIceberg→Arrow→DuckDB hybrid read (ADR-002 updated). |
| ❌ Needs data | FINAL query benchmarks (50k/500k), export benchmark baseline, GE checkpoint, dead letter nullability, C2 archive/expiry/partition jobs, C2 reprocessing test |

Detailed pass conditions: v4.0 §Phase Gates (lines 4245–4286).

## Sync Unblock Sequence

**COMPLETE.** All 3 steps done.

1. ~~**EDS: Rename 17 metric_ids**~~ — COMPLETE (2026-03-13).
2. ~~**FTB: Catalog migration**~~ — COMPLETE (2026-03-13). 0012 deployed.
3. ~~**Sync auto-flows**~~ — **COMPLETE (2026-03-14).** Manual sync verified 38/38 metrics flowing, 100% coverage. Instrument format fixes deployed to EDS (COT/ETF: BTC→BTC-USD, stablecoins: top-3 filter with -USD pairs). 3 per-protocol/per-chain DeFi metrics reclassified as EDS-only (0014).

**Pending EDS delivery** (add catalog row ONLY after metric appears in `empire.observations`):
- `macro.cot.institutional_long_pct` — CFTC derivation not yet deployed
- `defi.protocol.revenue_usd_24h` — not yet in empire.observations
- `macro.commodity.gold` — FRED series not yet in empire.observations
- `macro.liquidity.boj_balance_sheet` — FRED series not yet in empire.observations
- `macro.rates.move_index` — FRED series not yet in empire.observations

**Decisions locked:**
- `eds_derived` replaces old source entries in forge catalog — does not append.
- `breakeven_inflation_10y`, `real_yield_10y` stay EDS-only. No forge catalog row.
- `exchange.liquidation.*` deferred to Phase 2 per v4.0.
- CFTC: EDS produces 7 metrics. 4 map to forge. 3 stay EDS-only.
- Per-protocol/per-chain DeFi metrics (tvl_usd, fees_usd_24h, volume_by_chain_usd) stay EDS-only — instrument_ids are protocol slugs/chain names, not forge instruments. FTB uses aggregates.
- Stablecoins: top-3 only (USDT-USD, USDC-USD, DAI-USD). 200+ others dropped at EDS emission.
- Gold reads: PyIceberg→Arrow→DuckDB (not DuckDB `iceberg_scan()`). ADR-002 updated. `forge_compute` and API require PyIceberg dependency.

## Next Actions

1. ~~**Export round-trip verification**~~ — **COMPLETE (2026-03-14).** 5,746 rows, 36 metrics, 4 domains. ADR-002 updated: PyIceberg→Arrow→DuckDB locked as permanent read path.
2. **FINAL query benchmarks** (50k/500k row targets)
3. **GE checkpoint** (bronze_core suite)
4. **Tier promotion run**
5. **Activate NAS backup timer** — script exists at `/opt/empire/FromTheBridge/scripts/ftb_backup.sh`, timer is inactive.
6. **Verify C2 archive credential isolation** — confirm `bronze-archive-rw` MinIO user cannot write to `bronze-hot`.
7. **Tiingo backfill** — current earliest is 2019-01-01. Gate requires BTC from 2014, ETH from 2015. Coordinate with EDS.
