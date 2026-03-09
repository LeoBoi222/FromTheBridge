# T3b HANDOFF — Session State for Next Executor

## What Was Completed This Session

### T3: API Depth Research (COMPLETE)
- Report: `.claude/reports/T3_api_depth.json`
- All 11 v1 sources researched: depth, rate limits, pagination, backfill estimates
- Live API probes: DeFiLlama (TVL, DEX, stablecoins), CFTC COT (BTC/ETH row counts + dates), CoinMetrics CSV (BTC rows), CoinPaprika (confirmed free tier blocks historical)
- Key finding: SOL spot ETF launched Oct 28, 2025 (Bitwise BSOL)
- Key finding: Coinalyze rate limit = 40 req/min, intraday data purged daily (only daily resolution retains full history)

### T3 Data Quality (COMPLETE — derivatives only)
- Report: `.claude/reports/T3_data_quality.json`
- Audited: `forge.derivatives` (Binance 96,771 rows + Coinalyze 185,429 rows)
- 3 CRITICAL findings: degraded pre-Feb-2022 Coinalyze, OI cross-source drift, no provenance tracking
- 4 MATERIAL findings: extreme funding rates, degraded row concentration, CoinPaprika paid-only, Binance bulk lacks funding/liquidation

### T3b Prompt (WRITTEN, NOT EXECUTED)
- Prompt: `docs/plans/T3b_data_quality_audit.md`
- Covers 11 table groups including design assumption validation
- All SQL queries written and ready to execute
- Context exhausted before execution could begin

### Binance Bulk Data Discovery
- `Nexus-Council/empire/forge/backfill/binance_bulk_download.py` — downloads from data.binance.vision
- `Nexus-Council/empire/forge/backfill/binance_bulk_load.py` — loads to forge.derivatives
- Staging on bluefin: 66 symbols, 96,709 ZIPs, 1.2 GB at `Nexus-Council/empire/forge/backfill/staging/binance_metrics/`
- Loaded in Forge DB: 96,771 rows, 2021-12-01 to 2026-03-03, 66 instruments
- Raw 5-min CSVs preserved for potential ML sub-daily use

---

## What the Next Session Must Do

Execute the T3b prompt at `docs/plans/T3b_data_quality_audit.md` against the legacy Forge DB.

### Connection Details
- Container: `empire_forge_db` on proxmox (192.168.68.11)
- Database: `empire_forge`
- User: `forge_user`
- Command pattern: `ssh root@192.168.68.11 "docker exec -i empire_forge_db psql -U forge_user -d empire_forge -c \"SQL\""`

### Tables to Audit (in priority order)

| Priority | Table Group | Size | Why |
|----------|------------|------|-----|
| P0 | macro_indicators | 50 MB | Largest table, feeds macro regime model |
| P0 | chain_activity (partitioned) | ~200 partitions | 17 years of CoinMetrics data, feeds capital flow + DeFi models |
| P0 | dead_letters | 280 KB | Reveals systematic collection failures |
| P1 | dex_volume | 21 MB | DeFi stress model input |
| P1 | exchange_flows (partitioned) | ~900 KB | Capital flow model, depends on correct wallet addresses |
| P1 | computed_metrics (partitioned) | ~48 MB | Derived features — quality inherited from upstream |
| P2 | defi_lending_fees | 2.5 MB | DeFi stress model |
| P2 | etf_flows | 496 KB | SoSoValue, redistribution blocked |
| P2 | stablecoin_metrics | 336 KB | Stablecoin supply signals |
| P2 | supply_snapshots | 1.5 MB | Token supply data |

### Schema Already Discovered (save the next session a query)
- `macro_indicators`: indicator_id, observation_date, value, unit, source_agent, source_id, collected_at, quality, product_tier
- `derivatives`: instrument_id, exchange, snapshot_time, funding_rate, open_interest, open_interest_change_pct, long_short_ratio, liquidations_long, liquidations_short, source_agent, source_id, collected_at, quality, exchange_key, product_tier
- `dead_letters`: dlq_id, agent_id, run_id, table_name, instrument_id, payload, error, first_failed_at, last_failed_at, attempts, max_attempts, next_retry_at, resolved, resolved_at, created_at

### Key Context for Design Assumption Validation (Table Group 11)

The next session must also answer:
1. How many data series in Forge are NOT in the 74-metric catalog?
2. Which of the 74 metrics can begin ML training NOW vs need forward collection?
3. Which tables should be re-sourced from API rather than migrated from Forge?
4. What design decisions in v3.1 need revision based on actual data quality?

Reference docs for this analysis:
- `docs/design/FromTheBridge_design_v3.1.md` — canonical design
- `docs/design/thread_3_features.md` or archived version — feature engineering, 450-day assumption
- `docs/design/thread_5_collection.md` or archived version — adapter contracts, migration plan
- `db/migrations/0001_phase0_schema.sql` — metric catalog seed (74 metrics)

### Output
Save to: `.claude/reports/T3b_data_quality_full.json` (format specified in the T3b prompt)

---

## Existing Reports (do not overwrite)
- `.claude/reports/T3_api_depth.json` — API depth + rate limits + training windows
- `.claude/reports/T3_data_quality.json` — derivatives quality audit + sourcing risk matrix
- `.claude/reports/T1_data_audit.json` — prior audit (pre-existing)
- `.claude/reports/T4_blc01_audit.json` — BLC-01 audit (pre-existing)
