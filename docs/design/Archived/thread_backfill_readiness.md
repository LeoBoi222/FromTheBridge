# Backfill & Model Readiness Assessment
**Version:** 1.0 (2026-03-07)
**Status:** Architect review required
**Inputs:** T1 (data audit), T2 (EDSx audit), T3 (API depth), T3b (data quality), T4 (BLC-01 audit)

---

## 1. Audit Findings Summary

### T1 — Data Audit (Forge DB + TimescaleDB Inventory)
- **16 datasets** inventoried across `empire_forge_db` (port 5435) and `empire_timescaledb` (port 5434)
- Strongest datasets: Coinalyze derivatives (283k rows, 5yr, 123 instruments in legacy Forge — actual signal-eligible universe determined by admission criteria), FRED macro (139k rows, 76yr, 23 series), DeFiLlama DEX volume (88k rows, 10yr, 11 protocols)
- **Wei bug confirmed** in exchange flows: gate.io `_OTHER` instrument has values up to 3.6e22 (wei denomination). PEPE/SHIB in token-native units. Per-instrument normalization required.
- **Catalog gaps:** 3 FRED series in catalog missing from data (HY OAS, Gold, MOVE Index). 2 Forge series not in catalog (BREAKEVEN_INFLATION_10Y, REAL_YIELD_10Y).
- `instrument_metric_coverage` table has 0 rows — seeded but never populated.
- `forge_reader` role has NOLOGIN — MCP and adapters need this fixed.
- TimescaleDB tables mostly < 30 days of history — Forge DB is the source of truth for migration.
- 449 unresolved dead letters from `forge_compute` (schema mismatch bug).

### T2 — EDSx Audit (Implementation State)
- **EDSx-02 (Trend/Structure):** Fully implemented in Nexus-Council. 3 horizons, confidence engine, guardrails, correlation monitor, 9 test files. Reads TimescaleDB directly (architectural violation expected — migrated in Phase 3).
- **EDSx-03 R3 (Liquidity/Flow):** Fully implemented. 4 sub-scorers (Derivatives Positioning, Capital Flows, DeFi Health, Macro Context stub). Reads Forge DB directly.
- **3 planned pillars:** Valuation (REM-21), Structural Risk (REM-24), Tactical Macro (REM-22/23) — architecture defined, no implementation.
- **ML track:** 1 of 5 models implemented (Macro Regime POC — inconclusive at 37.83% accuracy). Synthesis code does not exist.
- **Critical caveat:** All EDSx and ML accuracy results measured against potentially dirty, gapped legacy data. Results are provisional baselines, not final measurements. Fair evaluation requires retesting against clean FromTheBridge data (post Phase 2).

### T3 — API Depth & Rate Limits
- **Fastest backfills:** FRED (12 seconds), CFTC COT (seconds), CoinMetrics (seconds), DeFiLlama (minutes), Tiingo (minutes), Coinalyze (~30 min), BGeometrics (minutes)
- **Slowest backfill:** Etherscan (24-48h free tier, 2-4h Pro at $199/mo)
- **CoinPaprika historical blocked** on free tier — paid plan required for backfill
- **Binance bulk data already loaded:** 96,771 rows, 66 instruments, Dec 2021–Mar 2026 (OI + L/S ratio). Eliminates the 450-day ML wait for derivatives features.
- **SOL ETF launched** Oct 28, 2025 — only ~5 months of ETF flow data exists
- **CFTC COT:** BTC from Dec 2017 (CBOE), ETH from Apr 2021 (CME) — confirmed via live API probe

### T3b — Full Data Quality Audit (NEW)
- **CoinMetrics community CSV severely limited:** Only `active_addresses` and `transaction_count` populated. `transaction_volume_usd` (the key NVT metric) is 0% populated. Paid API or proxy required.
- **DeFi lending TVL/utilization empty:** `defi_lending_fees` has 9,651 rows but only `daily_fees` is populated. `daily_revenue` 0%, TVL/utilization ≈0%. DeFiLlama `/yields` endpoint needed (separate from `/fees`).
- **Multiple tables near-zero depth:** Stablecoin metrics (11 days), defi_protocols (11 days), exchange flows (40 days), individual ETF funds (5 days). All require API re-sourcing.
- **3 FRED series missing = Phase 0 gate question:** HY OAS (`BAMLH0A0HYM2`) was an explicit Phase 0 gate criterion but was never loaded. Gold and MOVE Index also missing.
- **Provenance gap:** All bulk-loaded tables show `collected_at` as load date, not actual observation date. ClickHouse bitemporal design handles this if adapters record true collection timestamps.
- **Migration classification:**
  - **Clean copy:** macro_indicators (140k rows), dex_volume (88k rows), event_calendar (137 rows)
  - **Copy with caveats:** chain_activity (sparse columns), defi_lending_fees (fees only), supply_snapshots (hash_rate only), etf_flows (aggregates), derivatives (T3 quality caveats)
  - **Do not migrate:** computed_metrics, current_metrics, dead_letters, metric_definitions, sources (all superseded)
  - **Re-source from API:** stablecoin_metrics, defi_protocols, exchange_flows, lending TVL/utilization, transaction_volume_usd
- **3 useful Forge series not in catalog:** BREAKEVEN_INFLATION_10Y, REAL_YIELD_10Y, MFG_EMPLOYMENT — consider adding
- **450-day ML wait is eliminated:** Every domain either has years of historical data in Forge or can be backfilled from API. The assumption that collection started Feb 2026 with no backfill was incorrect.

### T4 — BLC-01 Liquidation Collector
- **5 days of data** (Mar 3–7, 2026). 177,216 events. Daily JSONL files with `.complete` rotation.
- **SINGLE COPY — no backup.** Data exists only inside LXC 203 on Server2. No rsync, no NAS backup, no replication.
- **Irrecoverable:** Binance forceOrder WebSocket is real-time only. Lost data cannot be refetched.
- **Collector healthy:** systemd service, NordVPN WireGuard, 1 brief WS disconnect (auto-recovered). 1,566 days disk runway.
- **Schema verified:** Consistent Binance native format + `_received_at` timestamp. 64 unique symbols. BUY = short liq, SELL = long liq.
- **P0 action:** Set up hourly rsync of `.complete` files to proxmox immediately.

---

## 2. ML Training Window Analysis (Per Model)

### Walk-Forward Parameters (from thread_2_signal.md)
- Prediction horizon: 14 days
- Label: volume-adjusted, tercile discretization per training window
- Minimum training window: 2 years for cycle coverage, 3-4 years preferred
- OOS holdout: 20-30%
- Shadow: 30 calendar days minimum post-training
- Graduation: 5 hard criteria (AUC-ROC ≥ 0.56, calibration ECE < 0.05, no single feature > 40%, prediction stability, shadow consistency)

### Model 1: Derivatives Pressure

| Parameter | Value |
|-----------|-------|
| Earliest date all required features available | **2022-02-01** (Coinalyze quality floor; pre-Feb-2022 OI at 3.4%) |
| Alternative floor with Binance bulk only | 2021-12-01 (OI + L/S ratio, 66 instruments) |
| Total training window | **~4 years** (Feb 2022 – Mar 2026) |
| Walk-forward folds (720d train, 180d val, 90d step) | **~6 folds** |
| OOS validation window | ~9 months (last 20% ≈ 292 days) |
| Sparse features (< 18 months) | BLC-01 tick liquidations (5 days only) — **exclude from initial training**; CFTC COT ETH (Apr 2021, weekly) — **include as auxiliary** |

**Policy for sparse features:**
- BLC-01 tick liquidations: **Exclude.** Use Coinalyze aggregated liquidations instead (available from 2021-03). BLC-01 data can be introduced as supplemental feature once ≥450 days accumulate (~Jul 2027).
- CFTC COT: **Include as separate variant.** Weekly frequency, BTC from Dec 2017, ETH from Apr 2021. Train model variant with/without COT; compare OOS performance.
- Binance bulk metrics: **Include.** 96,771 rows already loaded. Provides OI + L/S ratio from Dec 2021 for 66 instruments not covered by Coinalyze.

**Verdict: READY TO TRAIN.** 4+ years of derivatives data across Coinalyze (123 instruments in legacy Forge, signal-eligible subset determined by admission criteria) + Binance bulk (66 instruments). No blocking gaps.

### Model 2: Capital Flow Direction

| Parameter | Value |
|-----------|-------|
| Earliest date all required features available | **~2020-05** (DeFiLlama stablecoin supply from Nov 2017; Etherscan exchange flows meaningful from ~2020; CoinMetrics transfer volume from 2009) |
| Total training window | **~5.8 years** (May 2020 – Mar 2026) |
| Walk-forward folds | **~10 folds** |
| OOS validation window | ~14 months |
| Sparse features | ETF flows (Jan 2024 BTC, Oct 2025 SOL) — **separate variant**; Exchange flows per-instrument (40 days in Forge; re-source from Etherscan API) |

**Policy for sparse features:**
- SoSoValue ETF flows: **Separate model variant.** BTC ETF from Jan 2024 (~800 trading days by Mar 2026). Insufficient as standalone training feature but valuable as late-entry input. Train variant with ETF flows as feature-available-from marker.
- Exchange flows: **Re-source required.** Only 40 days in Forge. Etherscan API can provide 3+ years of exchange address history. Budget for Etherscan Pro ($199/mo one-time) for initial backfill speed.
- `transaction_volume_usd`: **BLOCKED.** CoinMetrics community CSV does not include USD volume. Options: (a) CoinMetrics paid API, (b) derive NVT proxy from Tiingo spot volume × close, (c) exclude NVT from model. Recommendation: option (b) — Tiingo spot volume as proxy.

**Verdict: CONDITIONALLY READY.** Requires Etherscan historical backfill and `transaction_volume_usd` decision. Core flow signals (stablecoin supply, DeFi TVL) available for 5+ years.

### Model 3: Macro Regime

| Parameter | Value |
|-----------|-------|
| Earliest date all required features available | **2014-01** (BTC price from Tiingo; FRED macro data from decades earlier) |
| Total training window | **~12 years** (Jan 2014 – Mar 2026) |
| Walk-forward folds | **~25+ folds** |
| OOS validation window | ~2.4 years |
| Sparse features | None — all FRED series predate BTC |

**Note:** FRED data gap — DTWEXBGS (USD index) starts 2006 only. Not a constraint since BTC starts 2014. Missing 3 FRED series (HY OAS, Gold, MOVE) must be added to adapter — all available from FRED API with full history. HY OAS from Dec 1996, Gold daily from 1968, MOVE from Jun 2018.

**POC result context:** The existing Macro Regime POC (37.83% accuracy) was measured against legacy data of unknown quality. Retraining on clean FromTheBridge data with the 3 missing FRED series may materially improve results.

**Verdict: READY TO TRAIN.** Best data depth of any model. Add 3 missing FRED series + consider retaining BREAKEVEN_INFLATION_10Y and REAL_YIELD_10Y from Forge.

### Model 4: DeFi Stress

| Parameter | Value |
|-----------|-------|
| Earliest date all required features available | **2020-05** (DeFiLlama TVL from May 2020; DEX volume from Apr 2016; stablecoins from Nov 2017; lending from mid-2021) |
| Total training window | **~5.8 years** (May 2020 – Mar 2026) |
| Walk-forward folds | **~10 folds** |
| OOS validation window | ~14 months |
| Sparse features | Lending utilization (0% in Forge — re-source from DeFiLlama `/yields`); BGeometrics MVRV/SOPR (no Forge data — new source) |

**Policy for sparse features:**
- Lending utilization: **Re-source.** DeFiLlama `/yields` endpoint provides historical lending rates and utilization. Different from `/fees` endpoint currently used.
- BGeometrics on-chain valuation: **New source.** BTC MVRV/SOPR available from ~2011-2013. ETH from 2015. Integration test required but API backfill estimated at minutes.
- Stablecoin metrics: **Re-source.** Only 11 days in Forge. DeFiLlama stablecoins API has full history from coin launch dates (USDT from Nov 2017).

**Key training events:** Terra/Luna collapse (May 2022) and FTX cascade (Nov 2022) are the archetypal stress events. Both are within the training window.

**Verdict: CONDITIONALLY READY.** Requires DeFiLlama `/yields` integration for lending data and stablecoin metrics re-sourcing. DEX volume and protocol TVL data are strong (2016+ and 2020+ respectively).

### Model 5: Volatility Regime

| Parameter | Value |
|-----------|-------|
| Earliest date all required features available | **2014-01** (Tiingo BTC OHLCV; FRED VIX from 1990) |
| Total training window | **~12 years** (Jan 2014 – Mar 2026) |
| Walk-forward folds | **~25+ folds** |
| OOS validation window | ~2.4 years |
| Sparse features | Derivatives-based vol signals (OI, funding) from 2021 only — **include as feature-available-from** |

**Note:** Volatility models primarily need price data (realized vol computation) + VIX. Both available from 2014. Derivatives-derived vol signals (OI regime, funding volatility) only available from 2021 — model must handle feature availability across time. Use masking approach: train on available features per timestamp, with feature presence indicators.

**Verdict: READY TO TRAIN.** Strong data depth from OHLCV + FRED. Derivatives features add value from 2021 but are not blocking.

### Training Window Summary

| Model | Floor Date | Window (years) | Folds | Status |
|-------|-----------|----------------|-------|--------|
| Derivatives Pressure | 2022-02 | 4.1 | ~6 | Ready |
| Capital Flow Direction | 2020-05 | 5.8 | ~10 | Conditional (Etherscan backfill + NVT decision) |
| Macro Regime | 2014-01 | 12.2 | ~25 | Ready (add 3 FRED series) |
| DeFi Stress | 2020-05 | 5.8 | ~10 | Conditional (lending re-source + stablecoin re-source) |
| Volatility Regime | 2014-01 | 12.2 | ~25 | Ready |

**Critical finding: The 450-day forward collection assumption from thread_3 is eliminated.** All 5 models have sufficient historical data for training. The constraint is not data depth but data quality — clean ingestion via FromTheBridge Phase 1 adapters is the real prerequisite.

---

## 3. Backfill Execution Plan

### Priority 1 — Blocks ML Training

| Source | Target Depth | Fetch Strategy | Est. Volume | Time at Rate Limits | Phase |
|--------|-------------|----------------|-------------|--------------------|----|
| **Coinalyze full history** | 5yr (2021-03+) | Daily resolution, date-chunked. Signal-eligible instruments × 4 metrics. 40 req/min. | ~283k rows (already in Forge, 123 instruments) | ~30 min fresh pull | During Phase 1 |
| **DeFiLlama TVL** | 6yr (2020-05+) | Single request per protocol (full history JSON). ~20 protocols. | ~40k rows | ~5 min | During Phase 1 |
| **DeFiLlama DEX volume** | 10yr (2016-04+) | Single request per protocol. 11 protocols + aggregate. | ~88k rows (already in Forge) | ~2 min | During Phase 1 |
| **DeFiLlama stablecoins** | 9yr (2017-11+) | Single request per stablecoin. 12 stablecoins. | ~36k rows | ~2 min | During Phase 1 |
| **DeFiLlama lending (fees + yields)** | 5yr (2021+) | Two endpoints: `/fees` + `/yields`. 8 protocols. | ~20k rows | ~3 min | During Phase 1 |
| **BGeometrics** | 13yr BTC / 10yr ETH | 4 metrics × 2 assets. API with key auth. | ~20k rows | ~5 min | During Phase 1 |
| **Tiingo OHLCV** | 12yr BTC / 10yr ETH / varies altcoins | Comma-separated tickers, date-range params. ~30 instruments. | ~50k rows | ~30 min | Before Phase 1 (dependency for unit normalization) |
| **Binance bulk migration** | Already loaded | Migrate 96,771 rows from Forge DB → ClickHouse Silver | 96,771 rows | Migration only | During Phase 1 |

### Priority 2 — Improves Model Quality

| Source | Target Depth | Fetch Strategy | Est. Volume | Time at Rate Limits | Phase |
|--------|-------------|----------------|-------------|--------------------|----|
| **FRED full depth + 3 missing series** | Decades | 23+3 series × 1 request each. 120 req/min. | ~140k rows | ~12 seconds | During Phase 1 |
| **CoinMetrics (community CSV)** | 17yr BTC / 10yr ETH | 2 CSV downloads. BTC ~6,273 rows, ETH ~3,500. | ~10k rows | Seconds | During Phase 1 |
| **Etherscan exchange flows** | 4yr+ (2020+) | Block-range chunking per exchange address. 9 addresses. | ~500k+ rows | **24-48h free / 2-4h Pro** | During Phase 1 |
| **CFTC COT** | 8yr BTC / 5yr ETH | 2 Socrata API calls (full history per call). | ~1,400 rows | Seconds | During Phase 1 |
| **SoSoValue ETF backfill** | From ETF launch dates | BTC from Jan 2024, ETH from Jul 2024, SOL from Oct 2025. Per-fund + aggregate. | ~2k rows | ~1 min | During Phase 1 |

### Priority 3 — Limited by Hard Floor

| Source | Constraint | Policy |
|--------|-----------|--------|
| **SoSoValue SOL ETF** | Hard floor Oct 28, 2025 (~5 months) | Accept as sparse feature. Document in metric_catalog. Use as feature-available-from marker, not training requirement. |
| **BLC-01 tick liquidations** | Hard floor Mar 3, 2026 (5 days). No historical backfill possible. | Accept as forward-collection-only. Use Coinalyze aggregated liquidations for historical training. BLC-01 becomes ML-relevant ~Jul 2027 (450 days accumulated). |
| **CoinPaprika historical** | Blocked on free tier (confirmed via live probe). | Evaluate paid plan pricing. CoinPaprika is used for market cap/sector metadata, not OHLCV. If metadata is current-only, no backfill needed. |

### Backfill Budget

| Tier | Estimated Time | Cost |
|------|---------------|------|
| Free tier total | ~33 hours (Etherscan dominates) | $0 |
| With Etherscan Pro (1 month) | ~5 hours total | $199 one-time |
| With CoinPaprika Pro (if needed) | TBD | TBD |

**Recommendation:** Budget $199 for one month of Etherscan Pro. All other backfills are free and fast (minutes to 30 min). Cancel after initial backfill completes.

---

## 4. Server2 / BLC-01 Decision

### Recommendation: Option B — Keep on Server2, Implement Rsync Pipeline

**Option A (Migrate to proxmox)** — Not recommended.
- Pros: Single-machine simplification, direct Bronze write
- Cons: Requires VPN on proxmox (currently NordVPN runs inside LXC 203 on Server2 with kill switch), adds load to proxmox, migration risks data gap during cutover, NordVPN licensing may not support two simultaneous connections
- Risk: VPN configuration on proxmox is untested. A VPN failure = permanent data gap.

**Option B (Keep on Server2 + rsync)** — Recommended.
- rsync schedule: **Every 1 hour** (matches recommended cadence from T4 audit)
- Command: `rsync -av root@192.168.68.87:/data/liquidations/*.jsonl.complete /opt/empire/blc01/landing/`
- Only sync `.complete` files (avoids partial reads of active day file)
- Proxmox landing dir: `/opt/empire/blc01/landing/`
- Bronze write: Phase 1 BLC-01 adapter reads `.complete` files → Iceberg `bronze-hot`
- Silver write: Aggregation windowing (tick → 1h buckets) → ClickHouse `forge.observations`
- NAS backup: Daily rsync from proxmox landing to NAS
- Pros: Zero disruption to running collector, proven VPN setup stays in place, clean separation of concerns, landing directory integrates naturally with Dagster file sensor
- Cons: 1-hour data lag (acceptable — BLC-01 is not used for real-time signals in v1)

**Option C (Separate stream, integrate at Bronze during Phase 1)** — Functionally equivalent to Option B.

### Implementation Plan (Option B)

1. **Immediately (before Phase 1):** Create proxmox landing dir. Set up hourly cron rsync of `.complete` files. Verify first sync. This is a **P0** action — every day without backup risks irrecoverable data loss.
2. **Phase 1:** Build BLC-01 adapter as Dagster file sensor asset. Adapter reads JSONL → computes USD notional (qty × avg_price) → writes Bronze (Iceberg) + Silver (ClickHouse). Aggregates ticks to 1h buckets for Silver observation cadence.
3. **Phase 1:** Add NAS backup job for proxmox landing directory.
4. **Phase 1:** Add log rotation inside LXC 203 for `collector.log`.

---

## 5. Feature-to-Model Mapping

### Model 1: Derivatives Pressure (~60-80 features)

| Feature Domain | Source | Metrics | Depth | Status |
|---------------|--------|---------|-------|--------|
| Funding rates (z-scores, carry, persistence) | Coinalyze | funding_rate | 2021-03+ | In Forge (283k rows) |
| OI momentum (24h, 7d, level percentile) | Coinalyze + Binance bulk | open_interest | 2021-03+ / 2021-12+ | In Forge |
| L/S ratio (z-score, extreme, momentum) | Coinalyze + Binance bulk | long_short_ratio | 2021-03+ / 2021-12+ | In Forge |
| Liquidation metrics (asymmetry, intensity, clustering) | Coinalyze (aggregated) | liquidations_long/short | 2021-03+ | In Forge |
| Liquidation tick data | BLC-01 | tick liquidations | 2026-03-03+ | 5 days only — **exclude initially** |
| Perp basis (z-score, 8h/24h/7d) | Coinalyze | basis (dormant in EDSx) | 2021-03+ | Needs adapter verification |
| CFTC COT positioning | CFTC Socrata API | open_interest by trader type | BTC 2017-12+ / ETH 2021-04+ | **Backfill required** |
| Calendar (expiry proximity) | event_calendar | futures_expiry | Structural | In catalog |

### Model 2: Capital Flow Direction (~40-60 features)

| Feature Domain | Source | Depth | Status |
|---------------|--------|-------|--------|
| Exchange net flow, momentum, whale ratio | Etherscan | ~2020+ (after backfill) | **40 days in Forge — re-source required** |
| Exchange reserve proxy (Δ) | Etherscan | ~2020+ (after backfill) | **0% populated in Forge — re-source** |
| Stablecoin supply momentum/delta | DeFiLlama | 2017-11+ | **11 days in Forge — re-source from API** |
| Transfer volume USD (NVT proxy) | CoinMetrics / Tiingo proxy | 2009+ / 2014+ | **0% populated — decision needed** |
| ETF net flows + cumulative | SoSoValue | BTC 2024-01+, ETH 2024-07+, SOL 2025-10+ | Partial in Forge (305 agg rows). Backfill needed. |
| Volume-price momentum | Tiingo + Coinalyze | 2021+ | Cross-DB compute required |

### Model 3: Macro Regime (~80-100 features)

| Feature Domain | Source | Depth | Status |
|---------------|--------|-------|--------|
| Yield curve (10Y, 2Y, 30Y, spreads) | FRED | 1962+ | Clean in Forge |
| Fed/ECB/BOJ balance sheets | FRED | 1999-2002+ | Clean in Forge |
| VIX level + momentum | FRED | 1990+ | Clean in Forge |
| DXY momentum | FRED | 2006+ | Clean in Forge |
| S&P 500 momentum | FRED | 2016+ | Clean in Forge (shorter than expected) |
| CPI, Core PCE, employment | FRED | 1947-1959+ | Clean in Forge |
| **HY OAS (credit spread)** | FRED | 1996+ | **MISSING — add BAMLH0A0HYM2** |
| **Gold momentum** | FRED | 1968+ | **MISSING — add GOLDAMGBD228NLBM** |
| **MOVE Index** | FRED | 2018+ | **MISSING — add MOVE** |
| BTC/ETH price data | Tiingo | 2014+ / 2015+ | Backfill required |
| Calendar (FOMC, CPI/NFP proximity) | event_calendar | Structural | In catalog |

### Model 4: DeFi Stress (~50-70 features)

| Feature Domain | Source | Depth | Status |
|---------------|--------|-------|--------|
| Protocol TVL (momentum, concentration) | DeFiLlama | 2020-05+ | **11 days in Forge — re-source from API** |
| DEX volume (z-score, momentum, ratio) | DeFiLlama | 2016-04+ | Clean in Forge (88k rows) |
| Lending fees | DeFiLlama | 2019-11+ | Clean in Forge (fees column only) |
| Lending utilization/TVL/rates | DeFiLlama `/yields` | 2021+ | **0% populated — new endpoint required** |
| Stablecoin peg stress | DeFiLlama | 2017+ | **11 days in Forge — re-source** |
| On-chain valuation (MVRV, SOPR, NUPL, Puell) | BGeometrics | BTC 2011+, ETH 2015+ | **New source — backfill in Phase 1** |
| Transfer volume | CoinMetrics / proxy | 2009+ | Active addresses only (volume MISSING) |

### Model 5: Volatility Regime (~60-80 features)

| Feature Domain | Source | Depth | Status |
|---------------|--------|-------|--------|
| Realized vol (7d/30d/90d), vol-of-vol | Tiingo OHLCV | 2014+ | Backfill required |
| Return distribution (kurtosis, skew) | Tiingo OHLCV | 2014+ | Backfill required |
| VIX level + crypto-equity vol ratio | FRED | 1990+ | Clean in Forge |
| Funding rate volatility | Coinalyze | 2021-03+ | In Forge |
| Liquidation intensity z-score | Coinalyze | 2021-03+ | In Forge |
| Cross-instrument correlation | Tiingo OHLCV | 2017+ (when alt coverage grows) | Backfill required |
| OI regime features | Coinalyze + Binance bulk | 2021+ | In Forge |

---

## 6. Feature-to-Pillar Mapping (3 Planned EDSx Pillars)

### Pillar 3: Valuation (REM-21)

| Required Feature | Metric Catalog ID | Source | Data Available? | Depth | Null-State Behavior |
|-----------------|-------------------|--------|-----------------|-------|-------------------|
| NVT ratio (rolling percentile) | chain.activity.nvt_proxy | CoinMetrics + CoinPaprika market cap | **NO — transaction_volume_usd is 0% populated.** Market cap requires CoinPaprika paid plan for historical. | Blocked | `METRIC_UNAVAILABLE` until NVT proxy sourced |
| MVRV ratio | chain.valuation.mvrv_ratio | BGeometrics | **NO — new source.** API backfill from ~2011 (BTC). | Available after backfill | `INSUFFICIENT_HISTORY` until BGeometrics adapter deployed |
| SOPR z-score | chain.valuation.sopr | BGeometrics | **NO — new source.** | Available after backfill | Same as MVRV |
| Puell Multiple (BTC only) | chain.valuation.puell_multiple | BGeometrics | **NO — new source.** BTC hash_rate in Forge (6,262 rows from 2009). | Available after backfill | `METRIC_UNAVAILABLE` for non-BTC instruments |
| Relative value vs BTC/ETH | Computed from OHLCV | Tiingo | Yes after backfill | 2014+ | Standard |
| Market cap / TVL ratio (DeFi) | Computed from DeFiLlama + CoinPaprika | DeFiLlama + CoinPaprika | TVL yes (after re-source), market cap needs CoinPaprika | 2020+ | `METRIC_UNAVAILABLE` for non-DeFi instruments |
| Fee revenue multiple | Computed from DeFiLlama | DeFiLlama | Yes (daily_fees populated) | 2019+ | `METRIC_UNAVAILABLE` for non-DeFi instruments |

**Minimum data before pillar can score non-null:** BGeometrics adapter deployed + MVRV/SOPR data ingested + Tiingo OHLCV backfill complete. NVT requires the `transaction_volume_usd` decision. Without NVT, pillar operates with reduced coverage (~0.7 input_coverage for BTC, lower for non-BTC).

**Recommendation:** Build Valuation pillar without NVT initially. Add NVT when `transaction_volume_usd` is resolved. BGeometrics data is the critical prerequisite.

### Pillar 4: Structural Risk (REM-24)

| Required Feature | Source | Data Available? | Depth | Null-State Behavior |
|-----------------|--------|-----------------|-------|-------------------|
| Realized vol (7d/30d/90d) | Tiingo OHLCV | Yes after backfill | 2014+ BTC | Standard |
| Volatility of volatility | Tiingo OHLCV (derived) | Yes after backfill | 2014+ | Standard |
| BTC-altcoin correlation | Tiingo OHLCV (multi-instrument) | Yes after backfill | ~2017+ (when alt coverage grows) | `INSUFFICIENT_HISTORY` for new altcoins |
| BTC-SPX, BTC-Gold correlation | OHLCV + FRED | SPX from 2016, Gold **MISSING** | Partial | `METRIC_UNAVAILABLE` for Gold until FRED series added |
| Max drawdown velocity | Tiingo OHLCV | Yes after backfill | 2014+ | Standard |
| Liquidation cascade proximity | Coinalyze + forge_compute | Yes | 2021-03+ | `METRIC_UNAVAILABLE` pre-2021 |
| DeFi protocol risk composite | DeFiLlama | TVL after re-source; 11 days currently | 2020+ (after re-source) | `SOURCE_STALE` if not re-sourced |
| Token unlock proximity | event_calendar | **Partially — event_calendar has 137 FOMC rows.** Unlock data requires new collector. | No unlock data | `METRIC_UNAVAILABLE` until unlock collector built |
| Stablecoin depeg distance | DeFiLlama | 11 days — needs re-source | 2017+ (after re-source) | `SOURCE_STALE` until re-sourced |

**Minimum data before pillar can score non-null:** Tiingo OHLCV backfill + Coinalyze in Silver (for liquidation cascade) + FRED Gold series added. Can operate without token unlock data initially (null redistribution handles it).

### Pillar 5: Tactical Macro (REM-22/23)

| Required Feature | FRED Series ID | Data Available? | Depth | Null-State Behavior |
|-----------------|---------------|-----------------|-------|-------------------|
| DXY level + momentum | DTWEXBGS | Yes | 2006+ | Standard |
| US 10Y yield + change | DGS10 / YIELD_10Y | Yes | 1962+ | Standard |
| 2Y-10Y spread + momentum | T10Y2Y | Yes | 1976+ | Standard |
| VIX level + momentum | VIXCLS / VIX | Yes | 1990+ | Standard |
| **Credit spread (HY OAS)** | BAMLH0A0HYM2 | **NO — missing from Forge** | 1996+ (after add) | `METRIC_UNAVAILABLE` until FRED adapter updated |
| **Gold momentum** | GOLDAMGBD228NLBM | **NO — missing from Forge** | 1968+ (after add) | `METRIC_UNAVAILABLE` until FRED adapter updated |
| S&P 500 momentum | SP500 | Yes | 2016+ | Standard |
| **MOVE Index** | MOVE | **NO — missing from Forge** | 2018+ (after add) | `METRIC_UNAVAILABLE` until FRED adapter updated |
| Global M2 growth rate | M2SL | Yes | 1959+ | Standard |
| FOMC meeting proximity | event_calendar | Yes (137 rows) | Structural | Standard |

**Minimum data before pillar can score non-null:** All 3 missing FRED series (HY OAS, Gold, MOVE) added to adapter and ingested. This is a **Phase 1 prerequisite** — the FRED adapter must include these 3 series from day one.

**Hard dependency:** FRG-10 (FRED macro migration to Silver) is a prerequisite for both the Macro Context sub-scorer in Pillar 2 and the entire Tactical Macro pillar. FRG-10 = Phase 1 FRED adapter completion.

---

## 7. Parallel Phase 3/4 Build Structure

### Current Sequential Plan (from thread_6)
```
Phase 2 (Features) → Phase 3 (EDSx) → Phase 4 (ML Shadow) → Phase 5 (Serving)
                      1-2 weeks          3-4 weeks              1-2 weeks
```
Total Phase 3+4+5: 5-8 weeks sequential.

### Proposed Parallel Structure

Given that EDSx completion (Phase 3) and ML training (Phase 4) have **no dependencies on each other** after Phase 2 features are computed, they can run in parallel:

```
Phase 2 (Features)
  ├── Track A: EDSx Completion (Phase 3)
  │   ├── A1: Valuation pillar (REM-21)           [needs BGeometrics + OHLCV features]
  │   ├── A2: Structural Risk pillar (REM-24)     [needs OHLCV + liquidation features]
  │   ├── A3: Tactical Macro pillar (REM-22/23)   [needs FRED features — FRG-10]
  │   ├── A4: Regime engine H2 upgrade            [needs macro features — FRG-10]
  │   └── A5: EDSx output contract migration      [marts.signals_history]
  │
  └── Track B: ML Training (Phase 4)
      ├── B1: Walk-forward training pipeline       [infra]
      ├── B2: Label generation (14d vol-adjusted)  [needs OHLCV features]
      ├── B3: Model 3 — Macro Regime (retrain)     [FRED features — best data]
      ├── B4: Model 1 — Derivatives Pressure       [derivatives features]
      ├── B5: Model 5 — Volatility Regime          [OHLCV + VIX features]
      ├── B6: Model 2 — Capital Flow Direction     [flow features + Etherscan backfill]
      ├── B7: Model 4 — DeFi Stress                [DeFi features]
      ├── B8: Graduation evaluation (all 5 models) [OOS data]
      └── B9: Shadow deployment (30-day minimum)   [production infra]

Convergence: Phase 5 (Serving)
  - Requires: Track A complete (5 pillars scoring) + Track B complete (5 models graduated + 30-day shadow)
  - Synthesis layer built here (0.5 EDSx + 0.5 ML)
```

### Track A: EDSx Completion

**Prerequisites from Phase 2:**
- All feature catalog entries exist for Valuation, Structural Risk, and Tactical Macro inputs
- BGeometrics features computed (MVRV, SOPR, NUPL, Puell)
- Cross-asset features computed (BTC-SPX, BTC-Gold correlations)
- FRED macro features computed (all 23+3 series)

**Internal sequencing:**
1. A1 (Valuation) and A2 (Structural Risk) can start immediately — independent pillars
2. A3 (Tactical Macro) requires FRG-10 completion (FRED in Silver)
3. A4 (Regime engine) requires A3 inputs (macro features for liquidity axis)
4. A5 (output contract migration) requires all pillars scoring

**Estimated duration:** 1-2 weeks (unchanged from sequential plan)

### Track B: ML Training

**Prerequisites from Phase 2:**
- Gold layer readable by DuckDB
- Feature catalog entries for all ML Layer 0 features
- All Priority 1 + Priority 2 backfills complete and in Silver/Gold

**Internal sequencing:**
1. B1 (pipeline) and B2 (labels) first — infrastructure
2. B3 (Macro) first training target — best data, POC exists for reference
3. B4 (Derivatives) and B5 (Volatility) can train in parallel — independent
4. B6 (Capital Flow) after Etherscan backfill complete
5. B7 (DeFi Stress) after DeFiLlama re-sourcing complete
6. B8 (graduation) after all 5 models trained
7. B9 (shadow) starts when graduation passes — 30-day hard floor

**Estimated duration:** 3-4 weeks (unchanged — dominated by 30-day shadow period)

### Convergence Point

Both tracks must complete before Phase 5:
- Track A outputs: 5 pillar scores × 3 horizons × instrument universe, written to `marts.signals_history`
- Track B outputs: 5 model outputs logged in shadow, graduation criteria evaluated
- Phase 5 builds: synthesis layer, serving endpoints, provenance trace

### Schedule Impact

| Plan | Phase 3+4 Duration | Phase 5 Start |
|------|-------------------|---------------|
| Sequential (current) | 4-6 weeks | Week 6-8 after Phase 2 |
| Parallel (proposed) | 3-4 weeks (bound by ML shadow) | Week 4-5 after Phase 2 |

**Net savings: ~1-2 weeks.** The 30-day ML shadow period is the binding constraint regardless. Parallelization ensures EDSx is not idle during shadow.

---

## 8. Phase 1 Gate Additions

In addition to existing Phase 1 gate criteria from thread_6 (Dagster services healthy, Tiingo Silver rows, all sources Silver rows, Bronze Iceberg, GE checkpoint, dead letter, BLC-01 rsync, NAS backup, CH credential isolation, full round-trip, `macro.credit.hy_oas`), the following verifiable additions are proposed:

### Addition 1: Historical Depth Documentation

**Criterion:** Every source in `forge.source_catalog` has a corresponding `backfill_depth_days` value recorded in `forge.metric_catalog` for each metric it provides.

```sql
-- Check: No NULL backfill_depth_days for active metrics
SELECT mc.metric_id, mc.source_id, mc.backfill_depth_days
FROM forge.metric_catalog mc
WHERE mc.status = 'active'
  AND mc.backfill_depth_days IS NULL;
```
**Expected result:** Zero rows.

### Addition 2: Priority-1 Backfill Verification

**Criterion:** All Priority-1 backfill jobs complete with Silver rows verified for each source.

```sql
-- Check: Minimum row counts per source in ClickHouse
SELECT source_id, count() as rows
FROM forge.observations
GROUP BY source_id
ORDER BY source_id;
```
**Expected result:** Each Priority-1 source (Coinalyze, DeFiLlama, BGeometrics, Tiingo, Binance bulk) has rows spanning ≥ 2 years of historical data.

### Addition 3: BLC-01 Integration Path Confirmed

**Criterion:** Option B implemented — hourly rsync operational, proxmox landing directory has ≥ 7 days of `.complete` files.

```bash
ls -la /opt/empire/blc01/landing/*.jsonl.complete | wc -l
```
**Expected result:** ≥ 7 files.

### Addition 4: Training Window Viability

**Criterion:** Architect-signed confirmation that each ML model has sufficient training data. Documented as JSON report in `.claude/reports/T5_training_windows.json` with per-model floor dates and row counts.

### Addition 5: Wei Bug Resolution

**Criterion:** Exchange flows adapter applies per-instrument unit normalization. gate.io `_OTHER` values are converted from wei (÷ 1e18). PEPE/SHIB values are converted from token-native to USD using Tiingo spot prices. Verification query:

```sql
SELECT instrument_id, max(abs(value)) as max_val
FROM forge.observations
WHERE source_id = 'etherscan'
  AND metric_id = 'flows.exchange.net_position_usd'
GROUP BY instrument_id
HAVING max_val > 1e15;
```
**Expected result:** Zero rows (no unreasonable values).

### Addition 6: Dead Letter Triage

**Criterion:** Any backfill errors captured in `forge.dead_letter` during Phase 1 are triaged and documented. No unresolved dead letters older than 7 days.

```sql
SELECT source_id, count() as unresolved
FROM forge.dead_letter
WHERE received_at < now() - INTERVAL 7 DAY
GROUP BY source_id;
```
**Expected result:** Zero rows or all documented in triage report.

### Addition 7: Missing FRED Series Added

**Criterion:** HY OAS (BAMLH0A0HYM2), Gold (GOLDAMGBD228NLBM), and MOVE Index have Silver rows.

```sql
SELECT metric_id, count() as rows, min(observed_at) as earliest
FROM forge.observations
WHERE metric_id IN ('macro.credit.hy_oas', 'macro.commodity.gold', 'macro.rates.move_index')
GROUP BY metric_id;
```
**Expected result:** 3 rows, each with significant historical depth (HY OAS from 1996, Gold from 1968, MOVE from 2018).

---

## 9. Architect Decisions (All Resolved 2026-03-07)

| Q | Question | Decision |
|---|----------|----------|
| Q1 | Phase 0 gate — HY OAS never loaded | Accept Phase 0 as-is. HY OAS added to Phase 1 FRED adapter. |
| Q2 | NVT Proxy source | Option C: `macro.nvt_txcount_proxy` = market_cap / transaction_count. CoinMetrics community CSV for tx_count. Evidence-gated: if ML F1 drop > 3% without true NVT, get CoinMetrics paid quote. |
| Q3 | Market cap source for NVT proxy | CoinMetrics `CapMrktCurUSD` for BTC/ETH historical. Forward: Tiingo price × CoinPaprika circulating supply. CoinGecko rejected (free tier non-commercial ToS). NVT proxy is BTC/ETH-deep, altcoin-shallow. |
| Q4 | Extra FRED series | Add BREAKEVEN_INFLATION_10Y + REAL_YIELD_10Y to catalog (76 base metrics). Skip MFG_EMPLOYMENT. |
| Q5 | Etherscan Pro budget | Deferred. Free tier backfill first. Exchange flows Priority 2, not on critical path. Pro only if operationally painful. |
| Q6 | Parallel Phase 3/4 | Approved. EDSx and ML tracks independent after Phase 2 gate. Saves ~1-2 weeks. |
| Q7 | Token unlock data | Deferred from Phase 3. Accept null redistribution for this feature in v1. |
| Q8 | Polygon.io integration | Separate blocker. Design session required before Phase 1. Not resolved by T5. |

---

## Pipeline Items

```sql
-- Mark T5 synthesis as complete
UPDATE bridge.pipeline_items SET status = 'complete', completed_at = NOW(),
  decision_notes = 'T5 synthesis complete — architect review document produced'
  WHERE id = 'FRG-T5';

-- New pipeline items from synthesis
INSERT INTO bridge.pipeline_items (id, title, status, trigger_condition, system_ids)
VALUES
  ('FRG-30', 'Add HY OAS + Gold + MOVE to FRED adapter', 'not_started',
   'Phase 1 adapter build', '{fromthebridge}'),
  ('FRG-31', 'Etherscan exchange flow historical backfill', 'not_started',
   'Phase 1 adapter build — budget approval for Pro tier', '{fromthebridge}'),
  ('FRG-32', 'DeFiLlama stablecoin + protocol TVL re-source', 'not_started',
   'Phase 1 adapter build', '{fromthebridge}'),
  ('FRG-33', 'DeFiLlama /yields endpoint integration for lending utilization', 'not_started',
   'Phase 1 adapter build', '{fromthebridge}'),
  ('FRG-34', 'NVT proxy: macro.nvt_txcount_proxy = market_cap / tx_count', 'complete',
   'Q2+Q3 closed: CoinMetrics CapMrktCurUSD for BTC/ETH historical, Tiingo×CoinPaprika forward. Evidence-gated revisit if ML F1 > 3% drop.', '{fromthebridge}'),
  ('FRG-35', 'BLC-01 rsync setup (P0 — immediate)', 'not_started',
   'Before Phase 1', '{fromthebridge}'),
  ('FRG-36', 'Binance bulk 96,771 rows migration to ClickHouse Silver', 'not_started',
   'Phase 1 adapter build', '{fromthebridge}'),
  ('FRG-37', 'Consider adding BREAKEVEN_INFLATION_10Y + REAL_YIELD_10Y to catalog', 'not_started',
   'Architect review of Q4', '{fromthebridge}'),
  ('FRG-38', 'SoSoValue ETF historical backfill (aggregates + per-fund)', 'not_started',
   'Phase 1 adapter build', '{fromthebridge}'),
  ('FRG-39', 'BGeometrics adapter build + historical backfill', 'not_started',
   'Phase 1 adapter build', '{fromthebridge}');

-- Data Source Legal Compliance Framework (thread_7 addition)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, decision_notes)
VALUES
  ('FRG-40', 'Coinalyze commercial use ToS audit',
   'Phase 5 pre-gate. Verify free tier permits commercial use in derived products. Upgrade or exclude if not.',
   'waiting', 'not_started', '{fromthebridge}',
   'Verify free tier permits commercial use in derived products. Upgrade or exclude if not.'),
  ('FRG-41', 'BGeometrics ToS audit',
   'Phase 5 pre-gate. Verify free tier permits commercial use in derived products. Upgrade or exclude if not.',
   'waiting', 'not_started', '{fromthebridge}',
   'Verify free tier permits commercial use in derived products. Upgrade or exclude if not.'),
  ('FRG-42', 'Etherscan commercial use audit',
   'Phase 5 pre-gate. Verify freemium tier permits commercial use in derived products. Upgrade or exclude if not.',
   'waiting', 'not_started', '{fromthebridge}',
   'Verify freemium tier permits commercial use in derived products. Upgrade or exclude if not.'),
  ('FRG-43', 'CoinPaprika commercial use audit',
   'Phase 5 pre-gate. Verify free tier permits commercial use in derived products. Upgrade or exclude if not.',
   'waiting', 'not_started', '{fromthebridge}',
   'Verify free tier permits commercial use in derived products. Upgrade or exclude if not.'),
  ('FRG-44', 'Binance WebSocket ToS audit',
   'Phase 5 pre-gate. Verify public WebSocket stream permits commercial use in derived products.',
   'waiting', 'not_started', '{fromthebridge}',
   'Verify public WebSocket stream permits commercial use in derived products.'),
  ('FRG-45', 'Tiingo commercial redistribution clause verification',
   'Phase 1 — before live collection. Paid tier — confirm commercial use and derived product redistribution rights before building adapters.',
   'ready', 'not_started', '{fromthebridge}',
   'Paid tier — confirm commercial use and derived product redistribution rights before building adapters.'),
  ('FRG-46', 'Formal ToS audit sign-off — all 11 sources',
   'Phase 5 pre-gate. Architect sign-off that all 11 sources are either clear, upgraded, or excluded from customer outputs.',
   'waiting', 'not_started', '{fromthebridge}',
   'Architect sign-off that all 11 sources are either clear, upgraded, or excluded from customer outputs.');
```

---

*This document synthesizes T1, T2, T3, T3b, and T4 audit reports into an architect-ready assessment. All open questions in §9 require architect decision before Phase 1 begins. The BLC-01 rsync (FRG-35) is P0 — implement immediately regardless of other decisions.*
