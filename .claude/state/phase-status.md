# Phase Status

**As of:** 2026-03-13

| Phase | Status | Detail |
|-------|--------|--------|
| 0 — Schema | Complete | 80 metrics (76 seed + 4 added), 11 sources, 4 instruments, CH Silver, MinIO buckets |
| 1 — Collection | In progress | Catalog migration deployed (0012). 41 eds_derived metrics. Sync unblock step 2 complete. |
| 2–6 | Not started | Gated on Phase 1 |

## Deployed on proxmox

- PostgreSQL `forge` schema: 12 catalog tables, seed data above
- ClickHouse `forge`: 3 tables (observations, dead_letter, current_values MV), ~16k rows
- MinIO: bronze-hot (90d lifecycle), bronze-archive, gold buckets
- Dagster: 4 containers (webserver :3010, daemon, code_ftb, code_eds)
- empire_to_forge_sync: 6h schedule, 41 eds_derived metrics promoted (0012 migration deployed)
- Ops health: 3 assets on 30m schedule. Bronze archive: daily 02:00 UTC. Gold export: hourly :15.

## Blocked

- **Infra:** Server2 OS upgrade (blocks EDS-0, not FTB).
- **Cleared:** EDS metric_id rename / C3 violation (2026-03-13). `empire.observations` now uses FTB canonical metric_ids. FTB catalog migration is unblocked.
- **Cleared:** ai-srv-01 operational (2026-03-13). Unblocks Phase 4 ML.

## Gate Criteria (Phase 1)

13 of 40 criteria passing. Full audit 2026-03-13.

| Status | Criteria |
|--------|----------|
| ✅ | Dagster services healthy, Dagster in docker-compose, observations_written, redistribution flags, C2 bronze-archive bucket, C2 bronze_archive_log DDL, historical depth (backfill_depth_days), training window viability (T5 report), dead letter triage (0 old), ops assets, runbooks (FTB-01–09), ops credentials, calendar schema |
| ⚠️ | NAS backup (script exists, timer inactive), C2 archive credentials (user exists, isolation unverified) |
| ✅ (0012) | Migration complete (35 UPDATE + 4 INSERT, 41 eds_derived total), CFTC COT (3 of 4 metrics — institutional_long_pct pending EDS) |
| ❌ EDS-blocked | live collection, rejection rate, coverage, Tiingo history (2019 not 2014), wei fix, tier promotion (0 signal_eligible), PF-6 utilization unit, FRED series (Gold/MOVE/BOJ — not yet in empire.observations), DeFiLlama yields, ingested_at correctness, priority-1 backfill, BLC-01 rsync |
| ❌ Needs data | Export round-trip, FINAL query benchmarks (50k/500k), export benchmark baseline, GE checkpoint, dead letter nullability, C2 archive/expiry/partition jobs, C2 reprocessing test |

Detailed pass conditions: v4.0 §Phase Gates (lines 4245–4286).

## Sync Unblock Sequence

The primary Phase 1 bottleneck. Sequenced — each step depends on the previous.

1. ~~**EDS: Rename 17 metric_ids**~~ — **COMPLETE (2026-03-13).** `empire.observations` uses FTB canonical metric_ids. C3 violation cleared.
2. ~~**FTB: Catalog migration**~~ — **COMPLETE (2026-03-13).** `0012_eds_sync_promotion.sql` deployed. 35 UPDATE + 4 INSERT = 41 eds_derived metrics. Pre-migration baseline was 7 (not 2 — the earlier "2 already correct" counted only BLC-01 sole-source rows, missing the 5 mixed-source rows from 0007/0010 that also carried eds_derived). Assertion verified against live state.
3. **Sync auto-flows** — `empire_to_forge_sync` picks up all 41 promoted metrics on next 6h run. No code changes needed.

**Pending EDS delivery** (add catalog row ONLY after metric appears in `empire.observations`):
- `macro.cot.institutional_long_pct` — CFTC derivation not yet deployed
- `defi.protocol.revenue_usd_24h` — not yet in empire.observations
- `macro.commodity.gold` — FRED series not yet in empire.observations
- `macro.liquidity.boj_balance_sheet` — FRED series not yet in empire.observations
- `macro.rates.move_index` — FRED series not yet in empire.observations

**Decisions locked (2026-03-13):**
- `eds_derived` replaces old source entries in forge catalog — does not append.
- `breakeven_inflation_10y`, `real_yield_10y` stay EDS-only. No forge catalog row.
- `exchange.liquidation.*` deferred to Phase 2 per v4.0.
- CFTC: EDS produces 7 metrics. 4 map to forge. 3 stay EDS-only.

## Next Actions

Priority queue — catalog migration done, sync flows next:

1. **Verify sync auto-flow** — confirm empire_to_forge_sync picks up 41 metrics on next 6h run. Check Dagster logs + forge.observations row count.
2. **Export round-trip verification** (Bronze → Silver → Gold → DuckDB)
3. **FINAL query benchmarks**
4. **GE checkpoint** (bronze_core suite)
5. **Tier promotion run**
6. **Activate NAS backup timer** — script exists at `/opt/empire/FromTheBridge/scripts/ftb_backup.sh`, timer is inactive.
7. **Verify C2 archive credential isolation** — confirm `bronze-archive-rw` MinIO user cannot write to `bronze-hot`.
8. **Tiingo backfill** — current earliest is 2019-01-01. Gate requires BTC from 2014, ETH from 2015. Coordinate with EDS.
