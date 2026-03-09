# Thread 3 — Feature Engineering
**Version:** 2.0 (2026-03-04) — Expanded from four-pillar scope to full five-pillar + ML Layer 0
**Status:** Design complete. Governs Phase 2 build.
**Authority:** EDSx Pillar Architecture v1, ML Data Requirements & Layer 1 Design v0.1, Locked Decisions

---

## 1. Design Principles

**Features are transformations, not storage.** Every feature value is a deterministic function of raw Forge data. Features are recomputable at any time from the raw observation record. If a feature value is lost or corrupted, recomputation from Forge produces the identical result.

**PIT constraint is absolute, no exceptions.** Every feature value must be computable using only data available at the computation timestamp. No lookahead. A z-score computed at T uses only observations with `valid_from ≤ T`. Cross-sectional ranks at T include only instruments with data at T. Future event dates (FOMC, expiry) are known in advance and PIT-safe — scheduled future events are structural knowledge, not lookahead.

**Idempotency is a hard requirement.** Computing a feature twice from the same raw data must produce bit-identical results. Non-deterministic transforms (random seeds, floating-point ordering dependencies) are not permitted.

**Feature catalog entry required before any feature is computed.** The catalog is the contract between data and consumers. Once a feature is locked in the catalog (assigned a `feature_id` and formula), the definition is immutable. Methodology changes create a new `feature_id` — they do not overwrite the existing entry.

**Breadth scores use deterministic formula with fixed weights, not learned.** Breadth aggregations across the instrument universe produce values that cannot depend on any trained model. The weights are configuration, not parameters.

---

## 2. Feature Categories

Seven categories based on computational pattern. Each has different raw data requirements and PIT constraints.

### Category A: Rolling Statistical Transforms

Single time series → rolling window → statistic.

**Standard transforms:**
- Z-score: `(value − rolling_mean(W)) / rolling_std(W)`
- Momentum: `(value − value_lag(N)) / value_lag(N)`
- Rate of change: `diff(value, N) / N`
- Exponential moving average (EMA)
- Rolling percentile rank (within instrument history)
- Rolling min/max normalization
- Rolling skewness / kurtosis

**Window sizes required:** 1h, 4h, 8h, 24h, 3d, 7d, 14d, 30d, 90d. Multiple windows per raw input are standard — a 24h funding rate z-score captures different information than a 7d funding rate z-score. The model learns which windows matter; Layer 0 computes all of them.

**PIT constraint:** Rolling windows use only data up to computation timestamp. Standard.

**Minimum history requirement:** 450 days (360d training window + 90d feature lookback) for any raw time series that feeds ML. EDSx-only features follow EDSx minimum data requirements per pillar.

### Category B: Cross-Sectional Ranks

Compare an instrument's value against the full universe at the same timestamp.

**Pattern:** `raw_value(instrument, t) → rank across all instruments at t → percentile [0,1]`

**Standard transforms:**
- Cross-sectional percentile rank
- Deviation from cross-sectional median
- Z-score relative to cross-sectional distribution

**PIT constraint:** Computation uses only instruments with data at timestamp T. Instruments added later do not retroactively change historical ranks. Universe composition changes are handled by computing over whatever instruments have data at T.

**Coverage dependency:** Cross-sectional features for exchange flows are only meaningful across the 18 Explorer-covered instruments. Cross-sectional OI features are only meaningful across the 121 Coinalyze-covered instruments.

### Category C: Ratio / Interaction Features

Two or more raw series → composite feature.

**Examples (non-exhaustive):**
- Volume-price momentum: `OI_change × price_change` — captures whether positioning builds with or against price
- Funding-liquidation divergence: funding rate direction vs. liquidation imbalance direction
- TVL-to-revenue ratio: protocol capital efficiency
- Flow-price divergence: exchange net flow direction vs. price direction (smart money detection)
- Stablecoin dominance: `stablecoin_market_cap / total_crypto_market_cap`
- Realized vol / funding rate vol: leverage-adjusted vol
- VIX-crypto vol ratio: cross-asset volatility comparison
- Real rate: `10Y_yield − CPI_trailing`
- Global liquidity composite: weighted sum of Fed + ECB + BOJ balance sheet changes

**PIT constraint:** Both component series use only data available at computation time. If one series updates less frequently (e.g., daily FRED data vs. 8h derivatives), the stale value carries forward until the next observation. Staleness is flagged, not hidden.

**Cross-database requirement:** Volume-price momentum and flow-price divergence require joining Forge data (Coinalyze/Explorer) with OHLCV data. The feature computation layer reads ClickHouse (forge.observations) and the Gold layer (Iceberg on MinIO via DuckDB). This pattern is established in forge_compute (metrics #19, #20 in FRG-08).

### Category D: Regime / State Features

Multiple raw inputs → classification → state label or probability vector.

**Examples:**
- Volatility regime: low / medium / high / extreme based on realized vol percentile
- Trend state: trending-up / ranging / trending-down based on price action
- Funding regime: positive carry / neutral / negative carry based on funding rate persistence
- Macro environment: risk-on / transitional / risk-off based on cross-asset behavior
- Peg stress state: normal / stressed (binary, from peg deviation data)

**Distinction from Layer 1 outputs:** Category D features are simple rule-based classifications using backward-looking data. They are inputs to models, not outputs from models. Layer 1's regime model learns complex non-linear regime dynamics — these are different layers of the same concept.

**PIT constraint:** All classification uses only backward-looking data. The classification itself is a feature, not a prediction.

### Category E: Calendar / Structural Features

Timestamp → derived feature.

**Examples:**
- Hour of day / day of week
- Time since last major liquidation event
- Days since last regime transition
- Futures expiry proximity (quarterly expiry effects)
- Time since last FOMC / major macro event
- Weekend flag (reduced liquidity)
- CPI/NFP release proximity

**Data requirement:** Timestamps from existing data + `forge.event_calendar` table (FOMC dates, macro release dates, quarterly futures expiry). See thread_4 for schema.

**PIT constraint:** Calendar features are inherently PIT-safe. Future scheduled event dates are known in advance.

### Category F: Breadth / Aggregation Features

Per-instrument values → aggregate statistic across universe.

**Examples:**
- Market breadth: % of instruments with positive 24h momentum
- Aggregate funding rate: mean funding rate across top N instruments
- Liquidation concentration: are liquidations hitting many instruments or concentrated in few?
- Correlation dispersion: rolling std of pairwise correlations (high = systemic risk)
- Sector rotation: relative momentum of DeFi vs. L1 vs. other tokens (requires sector classification)
- TVL concentration: top-5 protocols as % of total DeFi TVL

**Data requirement:** Per-instrument data for a sufficient universe + `forge.assets` sector/category classification (required for sector rotation features).

**Breadth score formula:** Fixed weights, deterministic, not learned. Weights exposed in config.

**PIT constraint:** Aggregation uses only instruments with data at T. Universe composition changes handled by computing over available instruments.

### Category G: Cross-Asset Features

Crypto value + traditional asset value → relationship feature.

**Examples:**
- BTC-SPX rolling correlation (30d, 90d)
- BTC-Gold rolling correlation
- BTC-DXY inverse correlation strength
- Crypto vs. equity realized volatility ratio
- BTC beta to SPX (rolling regression coefficient)

**Data requirement:** Traditional asset price data at daily frequency minimum. Required series: SPX, gold, DXY, US 10Y yield, US 2Y yield, VIX, crude oil. All from FRED (FRG-10 expanded scope).

**PIT constraint:** Cross-asset features align on trading day timestamps. Convention: use daily FRED close for traditional assets, midnight UTC snapshot for crypto.

---

## 3. Null State Taxonomy

Three distinct null states. Silent null is not permitted — every missing feature value must carry a typed null reason.

| State                  | Meaning | Example |
|------------------------|---------|---------|
| `INSUFFICIENT_HISTORY` | Metric exists, source is current, but not enough historical observations to compute the feature reliably | New instrument with < 30 days of data; rolling z-score requires minimum window |
| `SOURCE_STALE`         | The underlying raw metric hasn't updated within its expected cadence | Coinalyze funding rate not updated in > 16h when cadence is 8h |
| `METRIC_UNAVAILABLE`   | The underlying raw metric does not exist for this instrument | Exchange flow data for an instrument not tracked by Explorer; derivatives data for an instrument not on Coinalyze |

Null states propagate through the computation pipeline. A feature that depends on a null input emits a null with the most severe upstream null reason (`METRIC_UNAVAILABLE` > `SOURCE_STALE` > `INSUFFICIENT_HISTORY`).

---

## 4. Computation Order

```
A → C → B → F → G → D → E
```

**Rationale:**
- A must complete before C (ratios need their component rolling stats)
- A and C must complete before B (cross-sectional ranks need per-instrument values)
- B and A must complete before F (breadth needs both per-instrument values and ranks)
- A through F must complete before G (cross-asset correlations need all per-instrument series)
- A through G must complete before D (regime classification needs all input features)
- E is independent (calendar features depend only on timestamps) but computed last by convention

---

## 5. Computation Trigger

**Event-driven on metric ingestion, not wall-clock.**

When a new observation is ingested into `forge.observations` for a given `(metric_id, instrument_id)`, a trigger fires to recompute all features that depend on that metric. This ensures features are always current relative to the data that exists, without polling.

**Idempotency guarantee:** The event trigger may fire multiple times for the same observation (delivery-at-least-once semantics). The feature computation must produce identical results on repeated execution for the same input state.

**Batch recompute:** Full recompute of all features for a date range is supported for backfill and correction scenarios. Batch recompute uses `ingested_at` filtering to enforce PIT integrity — features computed during backfill only use observations with `ingested_at ≤ T` for each feature timestamp T.

---

## 6. Feature Catalog

Every feature must have a catalog entry before first computation. The catalog is append-only — entries are never modified after locking.

**Required fields per catalog entry:**

```
feature_id:         str (unique, human-readable, immutable)
                    format: {domain}.{category}.{description}
                    example: derivatives.rolling.funding_zscore_7d
display_name:       str
category:           enum(A, B, C, D, E, F, G)
formula:            str (exact mathematical definition, not description)
inputs:             list[{metric_id, window_spec}]
window_size:        str (if applicable)
output_type:        enum(float, bool, categorical)
output_range:       str (e.g., "[-1, 1]", "[0, 1]", "boolean")
consumers:          list[str] (which pillars/models use this feature)
pit_safe:           bool (always true; if false, do not add to catalog)
null_behavior:      str (which null state to emit and under what conditions)
minimum_history:    str (e.g., "30 observations", "90d")
locked_at:          timestamptz
locked_version:     str
deprecated_at:      timestamptz (null if active)
```

---

## 7. Per-Pillar Feature Requirements (EDSx)

### Pillar 1: Trend / Structure (EDSx-02)

All Category A features from OHLCV. No cross-database joins required.

| Feature | Category | Source | Window |
|---------|----------|--------|--------|
| (SMA₅₀d − SMA₂₀₀d) / ATR | C | OHLCV | 200d, 800d in 6h bars |
| Bollinger Band position: (close − mid) / width | C | OHLCV | 20d |
| ATR percentile rank | B | OHLCV | 63d |
| ATR slope direction | A | OHLCV | 14d |
| RSI (14-period) | A | OHLCV | 14 periods |
| MACD (12/26/9) | A | OHLCV | standard |
| MA alignment score (20/50/200) | C | OHLCV | multiple |
| ADX (14-period) | A | OHLCV | 14 periods |
| BB width (squeeze detection) | A | OHLCV | 20d |
| Higher-high / lower-low structure | D | OHLCV | 7D, 30D |
| Volume-weighted trend confirmation | C | OHLCV + volume | 7D, 30D |

Degraded mode features (< 200 days history):
| Feature | Category | Notes |
|---------|----------|-------|
| MA₅₀ slope / ATR | A | Replaces full crossover signal |

### Pillar 2: Liquidity & Flow (EDSx-03)

Cross-database features require forge_compute materialization.

**Derivatives sub-score inputs:**

| Feature | Category | Source |
|---------|----------|--------|
| funding_zscore (multiple windows) | A | Coinalyze funding rates |
| oi_momentum_24h, oi_momentum_7d | A | Coinalyze OI |
| ls_ratio_zscore (30d) | A | Coinalyze L/S ratio |
| ls_ratio_extreme (binary) | D | Coinalyze L/S ratio |
| liquidation_asymmetry | C | Coinalyze liquidations |
| liquidation_intensity | C | Coinalyze liquidations + OI |
| funding_carry | A | Coinalyze funding rates |
| volume_price_momentum | C | Coinalyze OI + OHLCV (cross-DB) |

**Capital Flows sub-score inputs:**

| Feature | Category | Source |
|---------|----------|--------|
| exchange_flow_net_position | A | Explorer |
| flow_momentum_7d, flow_momentum_24h | A | Explorer |
| whale_flow_ratio | C | Explorer |
| exchange_reserve_proxy (Δ only) | A | Explorer |
| volume_price_momentum | C | forge_compute (cross-DB) |
| volume_universe_rank | B | forge_compute (cross-DB) |
| stablecoin_supply_momentum | A | DeFiLlama |
| stablecoin_supply_delta_24h | A | DeFiLlama |
| etf_net_flows | A | SoSoValue (FRG-05, pending) |

**DeFi Health sub-score inputs:**

| Feature | Category | Source |
|---------|----------|--------|
| defi_tvl_momentum (7d) | A | DeFiLlama |
| defi_revenue_ratio | C | DeFiLlama |
| defi_revenue_momentum | A | DeFiLlama |
| stablecoin_supply_delta_7d | A | DeFiLlama |
| peg_stress_active (binary) | D | DeFiLlama peg data |

**Macro Context sub-score inputs (null until FRG-10):**

| Feature | Category | Source |
|---------|----------|--------|
| macro_liquidity_composite | C | forge_compute (FRED) |
| yield_curve_regime | D | forge_compute (FRED) |
| dxy_momentum | A | FRED |
| real_rate_momentum | C | FRED |

### Pillar 3: Valuation (Planned)

| Feature | Category | Source | Confidence |
|---------|----------|--------|------------|
| NVT ratio (rolling percentile) | B | CoinPaprika market cap + Etherscan tx vol | Tier 2 |
| MVRV ratio | B | Glassnode free tier | Tier 2 |
| Realized price multiple | A | Glassnode | Tier 2 |
| SOPR z-score | A | Glassnode | Tier 2 |
| Puell Multiple (BTC only) | A | Glassnode | Tier 2 |
| Stock-to-flow deviation (BTC only) | C | Computed | Tier 2 |
| Relative value vs BTC/ETH | B | OHLCV | Tier 1 |
| Market cap / TVL ratio (DeFi tokens) | C | DeFiLlama + CoinPaprika | Tier 1 |
| Fee revenue multiple | C | DeFiLlama | Tier 1 |
| Thermocap multiple | C | Glassnode | Tier 2 |

### Pillar 4: Structural Risk (Planned)

| Feature | Category | Source |
|---------|----------|--------|
| Realized vol (7D/30D/90D) | A | OHLCV |
| Volatility of volatility | A | OHLCV derived |
| BTC correlation (per altcoin) | G | OHLCV |
| BTC-SPX correlation | G | OHLCV + FRED |
| BTC-Gold correlation | G | OHLCV + FRED |
| Max drawdown velocity | A | OHLCV |
| Liquidation cascade proximity | D | Coinalyze + forge_compute |
| DeFi protocol risk composite | F | DeFiLlama |
| Token unlock proximity | E | forge.event_calendar |
| Stablecoin depeg distance | C | DeFiLlama peg data |
| Exchange concentration (top-3) | F | DeFiLlama |

### Pillar 5: Tactical Macro (Planned)

All inputs require FRG-10.

| Feature | Category | Source |
|---------|----------|--------|
| DXY z-score (14d, 30d, 90d) | A | FRED DTWEXBGS |
| DXY momentum | A | FRED |
| US 10Y yield level + change | A | FRED DGS10 |
| 2Y-10Y spread level + momentum | A, C | FRED T10Y2Y |
| VIX level + momentum | A | FRED VIXCLS |
| VIX-crypto vol ratio | G | FRED + OHLCV |
| Credit spread (HY OAS) | A | FRED |
| Gold momentum | A | FRED GOLDAMGBD228NLBM |
| S&P 500 momentum | A | FRED SP500 |
| Fed funds implied rate | A | FRED |
| MOVE Index | A | FRED |
| Global M2 growth rate | A | FRED M2SL |
| FOMC meeting proximity | E | forge.event_calendar |
| BTC-SPX correlation | G | FRED SP500 + OHLCV |

---

## 8. ML Layer 0 Feature Requirements

ML Layer 0 consumes the same features as EDSx plus additional features not needed by any EDSx pillar. This section covers the ML-specific additions.

### Additional Derivatives Features (ML-only)

| Feature | Category | Source |
|---------|----------|--------|
| Funding rate persistence (consecutive positive/negative periods) | A | Coinalyze |
| OI absolute level percentile (cross-sectional) | B | Coinalyze |
| Liquidation clustering frequency | A | Coinalyze |
| L/S momentum | A | Coinalyze |
| Perp basis z-score (8h, 24h, 7d) | A | Coinalyze (dormant in EDSx, active for ML) |
| Funding-liquidation divergence | C | Coinalyze |
| Futures expiry proximity | E | forge.event_calendar |

### Additional Flow Features (ML-only)

| Feature | Category | Source |
|---------|----------|--------|
| Inflow/outflow ratio (rolling 24h, 7d) | C | Explorer |
| Whale net direction | C | Explorer |
| Exchange reserve change z-score | A | Explorer |
| Stablecoin dominance trend | C | DeFiLlama + CoinPaprika |
| Stablecoin peg stress (binary) | D | DeFiLlama |
| ETF cumulative flow trend | A | SoSoValue |
| ETF flow momentum | A | SoSoValue |

### Additional DeFi Features (ML-only)

Require DeFiLlama expansion (new endpoints for lending rates and DEX volume):

| Feature | Category | Source |
|---------|----------|--------|
| Lending borrow APY z-scores per protocol | A | DeFiLlama /yields |
| Lending supply APY per protocol | A | DeFiLlama /yields |
| Borrow-supply rate spread | C | DeFiLlama /yields |
| Lending utilization rate (if available) | C | DeFiLlama /yields |
| DEX volume z-score (7d, 14d) | A | DeFiLlama /overview/dexs |
| DEX volume momentum | A | DeFiLlama /overview/dexs |
| DEX volume / TVL ratio | C | DeFiLlama |
| Protocol diversity (count above min TVL) | F | DeFiLlama |

### Additional Macro Features (ML-only)

Require FRG-10 expanded scope:

| Feature | Category | Source |
|---------|----------|--------|
| Yield curve shape (30Y level) | A | FRED DGS30 |
| 10Y-3M spread | C | FRED T10Y3M |
| Real rate estimate (10Y − CPI trailing) | C | FRED |
| Fed balance sheet change (weekly, monthly) | A | FRED WALCL |
| Global liquidity composite (Fed + ECB + BOJ) | C | FRED multi-series |
| ECB balance sheet | A | FRED ECBASSETSW |
| Employment data trend (NFP, jobless claims) | A | FRED PAYEMS, ICSA |
| Inflation trend (CPI, Core PCE momentum) | A | FRED CPIAUCSL, PCEPILFE |
| CPI/NFP release proximity | E | forge.event_calendar |
| Days since last regime transition | E | Self-referencing |

### Volatility Model Features (ML-only)

| Feature | Category | Source |
|---------|----------|--------|
| Return distribution kurtosis (7d, 30d) | A | OHLCV |
| Return distribution skewness (7d, 30d) | A | OHLCV |
| Intraday range (high-low / close) | A | OHLCV |
| Volume-volatility correlation (rolling) | G | OHLCV |
| Funding rate volatility (rolling std) | A | Coinalyze |
| Liquidation intensity z-score | C | Coinalyze + OI |
| Cross-instrument correlation (30d, 90d) | G | OHLCV multi-instrument |
| Correlation dispersion (std of pairwise) | F | OHLCV multi-instrument |

### Category G: Cross-Asset Features (ML-specific requirement)

These require FRED data (FRG-10 expanded scope). Not currently in any EDSx pillar.

| Feature | Category | Required FRED Series |
|---------|----------|---------------------|
| BTC-SPX rolling correlation (30d, 90d) | G | SP500 |
| BTC-Gold rolling correlation | G | GOLDAMGBD228NLBM |
| BTC-DXY rolling correlation | G | DTWEXBGS |
| Crypto vs equity realized vol ratio | C | SP500 + OHLCV |
| BTC beta to SPX (rolling regression) | G | SP500 |

### Feature Count Estimate

| Category | Estimated Features Per Instrument |
|----------|-----------------------------------|
| A: Rolling transforms | ~200–300 |
| B: Cross-sectional ranks | ~30–50 |
| C: Ratio / interaction | ~40–60 |
| D: Regime / state | ~10–15 |
| E: Calendar / structural | ~10–15 |
| F: Breadth / aggregation | ~15–25 |
| G: Cross-asset | ~20–30 |

**Total raw feature count: ~325–495 per instrument** before feature selection. After selection: ~80–150 active features per Layer 1 model. The full set is computed and persisted; model training selects subsets.

---

## 9. Infrastructure Notes

**Feature store:** Layer 0 outputs are persisted in the Gold layer (Iceberg tables on MinIO, read via DuckDB). Not stored as rows in the observation store — features are derived, not raw observations.

**Cross-database compute:** forge_compute reads ClickHouse (forge.observations, port 8123) for time-series metrics and the PostgreSQL catalog (port 5433) for metric and instrument metadata. Cross-DB features are materialized as computed metrics before being consumed by pillar scorers. The EDSx-03 Retriever reads `forge.computed_metrics` — it does not join across systems at query time.

**Storage sizing:** ~500 features × 157 instruments × multiple timestamps per day × 450+ days of history. Storage is the binding constraint for ML feature store, not compute. Size accordingly.

**Minimum history for Forge-native data:** Coinalyze, Explorer, and DeFiLlama collection started ~February 2026. ML requires 450 days minimum. These are the binding constraints on ML training timeline — not data availability, but history depth. Macro (FRED) and price (OHLCV) data have full historical backfill. The Phase 1 backfill strategy determines which features can be historically reconstructed.

---

*Feature catalog entries must exist before computation begins. New features in a design session require catalog entries drafted in that session and approved by architect before the Phase 2 build prompt is written.*
