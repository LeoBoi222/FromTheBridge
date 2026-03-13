# Phase Status

**As of:** 2026-03-13

| Phase | Status | Detail |
|-------|--------|--------|
| 0 — Schema | Complete | 76 metrics, 11 sources, 4 instruments, CH Silver, MinIO buckets |
| 1 — Collection | In progress | All FTB-buildable work complete. Blocked on EDS adapter delivery. |
| 2–6 | Not started | Gated on Phase 1 |

## Deployed on proxmox

- PostgreSQL `forge` schema: 12 catalog tables, seed data above
- ClickHouse `forge`: 3 tables (observations, dead_letter, current_values MV), ~16k rows
- MinIO: bronze-hot (90d lifecycle), bronze-archive, gold buckets
- Dagster: 4 containers (webserver :3010, daemon, code_ftb, code_eds)
- empire_to_forge_sync: 6h schedule, FRED + Tiingo flowing
- Ops health: 3 assets on 30m schedule. Bronze archive: daily 02:00 UTC. Gold export: hourly :15.

## Blocked

- **EDS metric_id rename (C3 violation):** EDS adapters write 17 metric_ids with EDS-namespace names instead of FTB canonical names. Sync gets zero rows because names don't match. EDS must rename first, then FTB catalog migration enables sync. See §Sync Unblock Sequence below.
- **Infra:** Server2 OS upgrade (blocks EDS-0, not FTB).
- **Cleared:** ai-srv-01 operational (2026-03-13). Unblocks Phase 4 ML.

## Gate Criteria (Phase 1)

13 of 40 criteria passing. Full audit 2026-03-13.

| Status | Criteria |
|--------|----------|
| ✅ | Dagster services healthy, Dagster in docker-compose, observations_written, redistribution flags, C2 bronze-archive bucket, C2 bronze_archive_log DDL, historical depth (backfill_depth_days), training window viability (T5 report), dead letter triage (0 old), ops assets, runbooks (FTB-01–09), ops credentials, calendar schema |
| ⚠️ | NAS backup (script exists, timer inactive), C2 archive credentials (user exists, isolation unverified) |
| ❌ EDS-blocked | Migration complete, live collection, rejection rate, coverage, Tiingo history (2019 not 2014), wei fix, tier promotion (0 signal_eligible), PF-6 utilization unit, FRED series (HY OAS/Gold/MOVE), DeFiLlama yields, CFTC COT, ingested_at correctness, priority-1 backfill, BLC-01 rsync |
| ❌ Needs data | Export round-trip, FINAL query benchmarks (50k/500k), export benchmark baseline, GE checkpoint, dead letter nullability, C2 archive/expiry/partition jobs, C2 reprocessing test |

Detailed pass conditions: v4.0 §Phase Gates (lines 4245–4286).

## Sync Unblock Sequence

The primary Phase 1 bottleneck. Sequenced — each step depends on the previous.

1. **EDS: Rename 17 metric_ids** in adapters to FTB canonical names (C3 contract). Add `institutional_long_pct` derivation to CFTC adapter. Migrate existing `empire.observations` rows.
2. **FTB: Catalog migration** — replace `sources` with `{eds_derived}` for all 23 metrics EDS produces. Add 5 new metric_catalog rows: `macro.cot.institutional_net_position`, `macro.cot.institutional_long_pct`, `macro.cot.open_interest_contracts`, `macro.cot.dealer_net_position`, `defi.protocol.fees_usd_24h`. All `sources = {eds_derived}`.
3. **Sync auto-flows** — `empire_to_forge_sync` picks up all promoted metrics on next 6h run. No code changes needed.

**Decisions locked (2026-03-13):**
- `eds_derived` replaces old source entries in forge catalog — does not append.
- `breakeven_inflation_10y`, `real_yield_10y` stay EDS-only. No forge catalog row.
- `exchange.liquidation.*` deferred to Phase 2 per v4.0.
- CFTC: EDS produces 7 metrics. 4 map to forge. 3 stay EDS-only.

## Next Actions

Priority queue — FTB-side work not blocked on EDS:

1. **Activate NAS backup timer** — script exists at `/opt/empire/FromTheBridge/scripts/ftb_backup.sh`, timer is inactive. Enable + verify.
2. **Verify C2 archive credential isolation** — confirm `bronze-archive-rw` MinIO user cannot write to `bronze-hot`.
3. **Tiingo backfill** — current earliest is 2019-01-01. Gate requires BTC from 2014, ETH from 2015. Coordinate with EDS.

After EDS rename (step 1 of Sync Unblock):
4. **FTB catalog migration** — step 2 of Sync Unblock Sequence above.
5. Export round-trip verification (Bronze → Silver → Gold → DuckDB)
6. FINAL query benchmarks
7. GE checkpoint (bronze_core suite)
8. Tier promotion run
