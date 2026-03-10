# Handoff: empire_to_forge_sync Complete — What's Next

**Date:** 2026-03-10
**Session:** Built and deployed the sync bridge (empire_to_forge_sync)
**Commits:** e1bdd64..ea372e6 (8 commits on master)

---

## What Was Built

`empire_to_forge_sync` — a Dagster asset that reads `empire.observations` (ClickHouse, EDS data) and writes validated rows to `forge.observations` with `source_id='eds_derived'`. This is the primary Phase 1 deliverable per v4.0 §Sync Layer.

**Files created:**
- `src/ftb/sync/bridge.py` — pure business logic (map empire→forge, query builder)
- `src/ftb/sync/sync_asset.py` — Dagster asset with watermark-based incremental sync
- `tests/sync/test_bridge.py` — 7 unit tests
- `tests/sync/test_sync_asset.py` — 5 unit tests
- `tests/sync/test_integration.py` — 2 integration tests
- `db/migrations/postgres/0006_eds_derived_source.sql` — eds_derived in source_catalog
- `db/migrations/clickhouse/0003_empire_reader_user.sql` — ch_empire_reader user

**Files modified:**
- `src/ftb/resources.py` — added `ch_empire_reader_resource`
- `src/ftb/definitions.py` — registered asset + 6h schedule
- `docker-compose.yml` — added `ch_empire_reader` secret mount

**Deployed and verified on proxmox:**
- 249 `macro.rates.fed_funds_effective` rows synced as smoke test
- 0 dead letters, 134ms execution time
- 6h schedule active at :30 past each 6th hour

---

## Key Design Decisions

1. **Watermark is query-based, not cursor-based.** `AssetExecutionContext` doesn't support `.cursor` (that's sensors only). Watermark = `MAX(ingested_at) FROM forge.observations WHERE source_id='eds_derived'`. Self-healing, no external state.

2. **Metric promotion is manual.** Only metrics with `'eds_derived' = ANY(sources)` in `forge.metric_catalog` get synced. Add via SQL migration + architect approval.

3. **C2 mapping:** empire uses `'__market__'` for instrument_id (non-null String); forge uses `NULL`. The bridge maps this automatically.

4. **No Bronze write.** The sync bridge writes Silver only — Bronze is for raw API responses. EDS owns the raw data.

---

## Known Issue: EDS Metric Name Mismatches (C3)

EDS and forge metric names don't all align. Only exact matches can be promoted until EDS adopts canonical names. Examples:

| EDS Name | Forge Canonical Name | Status |
|----------|---------------------|--------|
| `macro.rates.fed_funds_effective` | `macro.rates.fed_funds_effective` | Match — synced |
| `macro.credit.hy_oas` | `macro.credit.hy_oas` | Match — promotable |
| `macro.volatility.vix` | `macro.volatility.vix` | Match — promotable |
| `macro.equities.sp500` | `macro.equity.sp500` | MISMATCH |
| `macro.rates.yield_10y_2y_spread` | `macro.rates.yield_spread_10y2y` | MISMATCH |
| `macro.fx.wti_crude` | `macro.commodity.wti_crude` | MISMATCH |

**Action needed:** EDS needs to rename metrics to match forge canonical names (EDS_design_v1.1.md C3 resolution). Until then, only promote exact matches.

---

## Current Phase 1 Gate Progress

Updated after this session:

| Criterion | Status |
|-----------|--------|
| Dagster services healthy | ✅ |
| Dagster in docker-compose | ✅ |
| `empire_to_forge_sync` flowing data | ✅ (249 rows, smoke test) |
| Tiingo collecting | ✅ (29k obs, needs EDS migration) |
| EDS adapters collecting (FRED, DeFiLlama) | ✅ (6,589 obs in empire.*) |
| Bronze archive job | ❌ Not built |
| Export round-trip (Silver → Gold → DuckDB) | ❌ Not built |
| Ops assets (adapter_health, export_health, sync_health) | ❌ Not built |
| Runbooks FTB-01 through FTB-08 | ❌ Not written |
| Ops credentials (calendar_writer, risk_writer, ch_ops_reader) | ❌ Not created |
| Great Expectations checkpoint | ❌ Not configured |
| Most collection sources | ❌ Waiting on EDS adapters |

---

## Next Actions (from CLAUDE.md)

1. ~~Build `empire_to_forge_sync`~~ ✅ **DONE**
2. **Build Bronze archive job (`bronze_cold_archive`)** — v4.0 §Bronze Archive
3. **Build export round-trip (Silver → Gold via DuckDB Iceberg write)** — v4.0 §Gold Layer
4. **Build ops assets (adapter_health, export_health, sync_health)** — v4.0 §Solo Operator Operations
5. **Create ops credentials + calendar schema** — v4.0 §Solo Operator Operations

---

## Test Suite Status

39/39 passing:
- `tests/adapters/test_tiingo.py` — 8 tests
- `tests/sync/test_bridge.py` — 7 tests
- `tests/sync/test_sync_asset.py` — 5 tests
- `tests/sync/test_integration.py` — 2 tests
- `tests/validation/test_core.py` — 6 tests
- `tests/writers/test_silver.py` — 5 tests
- `tests/writers/test_bronze.py` — 4 tests
- `tests/writers/test_collection.py` — 2 tests

Run: `uv run python -m pytest tests/ -v`
