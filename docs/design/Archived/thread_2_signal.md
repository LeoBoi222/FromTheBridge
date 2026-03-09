# Thread 2 — Signal Architecture
**Version:** 2.0 (2026-03-04) — Full rewrite against actual architecture
**Status:** Design complete. Supersedes v1.0 (four-pillar, wrong regime engine).
**Authority:** EDSx Pillar Architecture v1, EDSx-02 production code, EDSx-03 R3, EDSx v2.2 framework, ML Data Requirements & Layer 1 Design v0.1

---

## 1. Architecture Overview

Two independent signal generation tracks share a common data foundation and feature layer. They never cross-contaminate from Layer 1 onward.

```
Forge DB (raw data — thread_4)
  ↓
Layer 0: Feature Engineering (deterministic transforms — thread_3)
  ↓                              ↓
EDSx Track                   ML Track
(deterministic)              (LightGBM)
  ↓                              ↓
5 Pillar Scores × 3 Horizons   5 Domain Model Outputs
  ↓                              ↓
  └─────────── Synthesis ────────┘
                  ↓
         /v1/signals endpoint
```

**Regime engine** sits alongside both tracks — it is not a pillar and does not score instruments. It classifies market-wide state and drives composite weight selection. Separate design section below.

**EDSx confidence** = data completeness (signals_computed / signals_available). Not prediction confidence.

**Synthesis default** = 0.5 / 0.5 EDSx / ML weight, recalibrated quarterly against outcomes.

---

## 2. Regime Engine

### 2.1 Legacy (Current Production)

The production regime engine is M2 YoY growth alone, stored in `contract.regime_states` with a 3-value enum:

| M2 YoY Growth | Internal Label     | Contract Value |
|---------------|--------------------|----------------|
| > 8%          | strong_expansion   | risk_on        |
| > 5%          | expansion          | risk_on        |
| > 0%          | neutral            | transitional   |
| > -3%         | contraction        | risk_off       |
| ≤ -3%         | severe_contraction | risk_off       |

Fallback: Fear & Greed Index if EDS macro fails (F&G ≥ 60 → risk_on, ≤ 40 → risk_off).

`transition_probability` and `stability_days` columns exist in `contract.regime_states` but are NULL in production. Both the H2 engine and ML POC are designed to populate them.

Consumers: EDSx composite weights, W6 portfolio profiles, Signal Gate 1, content engine regime alerts, CAA regime transitions endpoint.

### 2.2 H2 Target: Volatility-Liquidity Anchor

The redesigned regime engine is a 2-axis, 4-quadrant model. It is architecturally separate from the five alpha pillars.

```
                    │ Low Volatility    │ High Volatility
────────────────────┼───────────────────┼──────────────────────
Expanding Liquidity │ Full Offense      │ Selective Offense
Contracting        │ Defensive Drift   │ Capital Preservation
Liquidity          │                   │
```

**Liquidity axis inputs:** M2 trend, QT/QE policy direction, stablecoin supply growth, DeFiLlama TVL trend, real rates, yield curve shape.

**Volatility axis inputs:** VIX level and trend, realized vol, funding rate volatility, DXY.

**Weight matrices:** Each quadrant drives a distinct weight profile across the five alpha pillars. Capital Preservation allocates ≈ 45% weight to Structural Risk. Full Offense allocates maximum weight to Trend/Structure and Liquidity/Flow.

**Boundary handling:** Sigmoid blending at quadrant boundaries to prevent whipsaw on minor threshold crossings.

**Data dependency:** All liquidity-axis inputs require FRG-10 (FRED macro migration). Engine cannot run until FRG-10 is complete. Legacy M2-only engine remains in production until H2 engine is shadow-validated and promoted.

### 2.3 ML POC (Parallel Experimental Track)

Location: `ml/poc_macro_regime/`

Two-model stack:
1. GaussianHMM on 10 FRED indicators (yield curve, VIX, SPX, Fed assets, DXY, claims, breakeven inflation, real yield, WTI, near-term spread) → discovers 2–5 regime states via BIC
2. LightGBM takes HMM regime features + macro + BTC price → predicts 3-day forward BTC direction (bullish / neutral / bearish)

**Status:** Awaiting execution. Blocked by UNI-01.

**Promotion gate:** > 5 percentage-point accuracy improvement over baselines before this track influences production.

---

## 3. Five-Pillar Framework

### 3.1 Pillar Definitions

**Pillar 1: Trend / Structure** (`pillar_id = "trend_structure"`)
*What is price doing?*
Price action, momentum, and market structure analysis. Describes the current state of price structure and the momentum regime. Does not predict — prediction comes from composite synthesis. Most horizon-sensitive pillar: 1D bullish, 30D bearish is a real and common configuration.

**Pillar 2: Liquidity & Flow** (`pillar_id = "liquidity_flow"`)
*Where is money moving?*
Volume, order flow dynamics, funding rates, exchange flows, stablecoin dynamics. Captures the behavioral signature of market participants — accumulation vs. distribution, leverage building. Sees what Trend/Structure cannot: pressure beneath the surface.

**Pillar 3: Valuation** (`pillar_id = "valuation"`)
*Is this cheap or expensive relative to fundamentals?*
On-chain valuation ratios (NVT, MVRV, realized price multiples), relative value across instruments, mean-reversion signals. Provides the gravitational anchor. Identifies what to buy/sell, not when — timing comes from other pillars.

**Pillar 4: Structural Risk** (`pillar_id = "structural_risk"`)
*How dangerous is the current environment?*
Volatility regime classification, correlation clustering, tail risk, liquidation cascade proximity, drawdown velocity. Measures risk landscape — not direction, but damage potential. **Architectural privilege:** Structural Risk modulates the weight of other pillars in the composite when its score crosses defined thresholds (max 2× base weight, proportionally redistributed from other pillars).

**Pillar 5: Tactical Macro** (`pillar_id = "tactical_macro"`)
*What is the macro backdrop doing to crypto?*
DXY movements, real rate changes, credit spread dynamics, risk appetite proxies, cross-asset flows. Captures external forces from traditional finance. This is the **tactical** layer — alpha signals from macro data. Structural macro classification (regime engine) is separate by design; the old macro pillar tried to do both and did neither well.

### 3.2 Horizon Matrix

All 15 cells (5 pillars × 3 horizons) are active. Empirical pruning after 90-day shadow measurement, not before.

| Horizon | Label | Contract Value | Semantics |
|---------|-------|----------------|-----------|
| Short   | 1D    | "1D"           | 24h forward directional expectation. Tactical entry/exit timing. |
| Medium  | 7D    | "1W"           | 7-day forward. Primary decision horizon. Fullest information set. |
| Long    | 30D   | "1M"           | 30-day forward structural positioning. |

| Pillar           | 1D                        | 7D                          | 30D                            |
|------------------|---------------------------|-----------------------------|--------------------------------|
| Trend/Structure  | High — state differs by TF | High — primary momentum read | High — structural direction    |
| Liquidity & Flow | High — acute dislocations  | High — sustained flow shifts | Low (hypothesis) — may average to noise |
| Valuation        | Low (hypothesis)           | Medium — mean reversion      | High — deep value signals      |
| Structural Risk  | High — cascade proximity   | High — correlation regime    | Low (hypothesis) — regime engine's domain |
| Tactical Macro   | Low (hypothesis)           | High — macro inflections     | High — structural positioning  |

### 3.3 Computation Cadence

Cadence (how often a cell recomputes) is independent of horizon (the forward window evaluated).

| Cell                      | Cadence | Rationale |
|---------------------------|---------|-----------|
| Trend/Structure 1D, 7D    | 1h      | Trend state can shift intraday |
| Trend/Structure 30D       | 4h      | Monthly structure changes slowly |
| Liquidity & Flow 1D, 7D   | 4h      | Meaningful at 4h aggregation |
| Liquidity & Flow 30D      | 24h     | Monthly flow is slow-moving |
| Valuation (all horizons)  | 24h     | On-chain metrics update daily at best |
| Structural Risk 1D        | 1h      | Volatility spikes need intraday detection |
| Structural Risk 7D        | 4h      | Weekly risk regime needs multi-hour confirmation |
| Structural Risk 30D       | 24h     | Monthly risk landscape changes slowly |
| Tactical Macro 1D, 7D     | 4h      | Intraday DXY/rates moves relevant at 4h |
| Tactical Macro 30D        | 24h     | Monthly macro from daily data releases |

**Composite cadence:** Hourly. Reads latest-valid score from each cell. `freshness_seconds` in each PillarScore reports staleness.

### 3.4 Regime-Adaptive Composite Weights

Weights are a function of (1) regime state from the regime engine, (2) data quality from each PillarScore, and (3) Structural Risk score modulation. These are starting parameters — exposed in config, tunable per evaluation profile.

**Risk-On:**

| Pillar           | Weight | Rationale |
|------------------|--------|-----------|
| Trend/Structure  | 0.30   | Momentum matters most when trend is your friend |
| Liquidity & Flow | 0.25   | Flow confirmation high-value in trending markets |
| Valuation        | 0.15   | Valuation stretches last longer in risk-on |
| Structural Risk  | 0.10   | Base weight; can modulate upward if risk spikes |
| Tactical Macro   | 0.20   | Need to know if macro tailwinds shift |

**Transitional:**

| Pillar           | Weight | Rationale |
|------------------|--------|-----------|
| Trend/Structure  | 0.20   | Trend is ambiguous; reduce reliance |
| Liquidity & Flow | 0.20   | Flow divergences are the early signal |
| Valuation        | 0.20   | Equal weighting — no strong conviction |
| Structural Risk  | 0.20   | Elevated — transitions can break either way |
| Tactical Macro   | 0.20   | Macro often leads the transition |

**Risk-Off:**

| Pillar           | Weight | Rationale |
|------------------|--------|-----------|
| Trend/Structure  | 0.15   | Bearish trend is known; less incremental info |
| Liquidity & Flow | 0.15   | Confirms damage but doesn't lead recovery |
| Valuation        | 0.25   | Valuation extremes are the recovery signal |
| Structural Risk  | 0.30   | Risk dominates; plus modulation privilege |
| Tactical Macro   | 0.15   | Macro set the stage |

---

## 4. EDSx Framework (v2.2)

All five pillars conform to the three-layer standard. The framework is locked — changes require architect review.

### 4.1 Three-Layer Standard

**Layer 1 — Core:** Deterministic, feature-based, no fitting. Logs key inputs and derived features.

**Layer 2 — Distribution (rolling percentile normalization):**
- Window: W_pct = 252 observations per (instrument, pillar, horizon) on 4h canonical grid (≈ 42 days)
- Min samples: MIN_pct = 96 observations (≈ 16 days)
- If samples < MIN_pct: emit score = 0.5, set `warmup_flag=true`, `pct_insufficient=true`
- If MIN_pct ≤ samples < W_pct: compute on available samples, set `pct_partial=true`

**Layer 3 — Guardrails (G1/G2/G3):** Applied per pillar per horizon using `confidence_base`.

Output scale: `PillarScore.score ∈ [0,1]`. Centered form: `c = 2*score - 1 ∈ [-1,1]`.

### 4.2 Confidence (Deterministic)

```
confidence_base = 0.30×coverage + 0.25×freshness + 0.20×stability + 0.25×agreement
```

- `stability(t)` uses C_final history up to (t−1) — no circularity
- Cold start: stability defaults to 0.5 until 10 bars; flagged `warmup_stab=true`
- `confidence_final = confidence_base` (single-penalty rule: correlation affects deployment only)

### 4.3 Guardrails

Evaluated on `confidence_base`. Semantic: data quality, not redundancy.

- **G1:** confidence_base < 0.50 → `w ← w × 0.5`
- **G2:** confidence_base < 0.30 → `score ← clamp(score, 0.30, 0.70)` and `w ← w × 0.5`
- **G3:** confidence_base < 0.10 → `w ← 0`, renormalize remaining weights

### 4.4 Null Handling and Weight Redistribution

When a sub-score is entirely null (e.g., Macro Context pre-FRG-10), weights redistribute proportionally among available non-null sub-scores:

```
effective_weight(i) = nominal_weight(i) / sum(nominal_weights of non-null sub-scores)
```

Same proportional redistribution applies within a sub-score when individual inputs are null.

Every scoring run emits `input_coverage` (fraction of nominal inputs with data) and `null_inputs` (list of null metric names) in the EvidenceBundle.

### 4.5 Correlation Monitor (Spearman, MAX across horizons)

- Grid: canonical 4h bars
- Window: W_corr = 24 bars
- Method: Spearman rank correlation, upper-triangle mean (exclude diagonal)
- Aggregation: `ρ_MAX = max(ρ_1D, ρ_7D, ρ_30D)`
- Staleness discount applied to forward-filled pairs: `ρ_ij' = ρ_ij × max(0, 1 − stale/FF_max)` where FF_max = 6 bars

Leverage multiplier bands (locked at v2-final baseline):

| Band | ρ_MAX Range       | leverage_mult |
|------|-------------------|---------------|
| 0    | < 0.75            | 1.00          |
| 1    | 0.75 – 0.85       | 0.95          |
| 2    | 0.85 – 0.92       | 0.70          |
| 3    | ≥ 0.92            | 0.40          |

Hysteresis: enter higher band immediately; exit lower band only after 2 consecutive bars below lower-band boundary.

**Single-penalty rule (locked):** Correlation affects deployment only. Does not modify `confidence_final`, does not cap/zero composites. `leverage_mult` is consumed by deployment layer.

### 4.6 Transition Control

```
D = |C_1D − C_30D| + vol_spike_flag + dispersion_flag
```

States: `BASE → TRANSITION_ACTIVE → COOLDOWN → BASE`

- Cooldown lasts N = 4 bars after transition exit
- D_hard = 2.5 (deterministic override threshold)
- Anti-oscillation guard: D_hard override resets cooldown counter; maximum one override per 2×N bars (8 bars)

### 4.7 Object ID Format (v2 from Day One)

```
PillarScore:    {instrument_id}|{pillar_id}|{horizon}|{as_of}|{track}
CompositeScore: {instrument_id}|{horizon}|{as_of}|{track}
Recommendation: {instrument_id}|{horizon}|{as_of}|{track}
```

Example: `BTC|trend_structure|1W|2026-02-22T08:00:00Z|rebuild`

---

## 5. Pillar 1: Trend & Technical Structure (LIVE — EDSx-02)

**Status:** Live in production. OHLCV-only. No external APIs.

**Data source:** TimescaleDB — OHLCV candles
**Grid:** 6-hour bars
**Minimum data:** 200 bars (50 days)

### Sub-Components

| Sub-Component     | Method                                               | Range           | In Score? |
|-------------------|------------------------------------------------------|-----------------|-----------|
| Trend Signal      | (SMA₅₀d − SMA₂₀₀d) / ATR, clamped and scaled         | [−1, 1]         | Yes       |
| Momentum Signal   | Bollinger Band position: (close − BB_mid) / BB_width | [−1, 1]         | Yes       |
| Volatility Regime | ATR percentile rank + ATR slope direction             | [0,1] + direction | No — informational only (fix #13) |

### Score Calculation

```
raw = (trend_signal + momentum_signal) / 2    → [−1, 1]
raw_unit = (raw + 1) / 2                       → [0, 1]
```

Equal weight between trend and momentum. Volatility computed and stored in sub-components array but deliberately excluded from the directional composite.

### Key Constants (in 6h bars)

| Constant         | Bars | Days |
|------------------|------|------|
| MA short (SMA₅₀d) | 200  | 50   |
| MA long (SMA₂₀₀d) | 800  | 200  |
| Bollinger Band    | 80   | 20   |
| ATR               | 56   | 14   |
| Vol rank window   | 252  | 63   |

### Degraded Mode

200–799 bars (enough for SMA₅₀ but not SMA₂₀₀): trend signal falls back to MA₅₀ slope / ATR. Reduced fidelity but still produces a score.

### Signal Taxonomy (from Pillar Architecture v1)

Included: RSI (14), MACD (12/26/9), moving average alignment (20/50/200), ADX (14), Bollinger Band width + %B, higher-high/lower-low structure, ATR (14), volume-weighted trend confirmation, price vs. realized price (30D), Ichimoku cloud.

Excluded (with rationale): Elliott Wave (subjective, not deterministic), Fibonacci retracements (post-hoc fitting), Point & Figure (non-standard transform, low incremental value), proprietary black-box indicators (must be expressible as formula from OHLCV).

---

## 6. Pillar 2: Liquidity & Flow (LIVE — EDSx-03 R3)

**Status:** Live in production (rebuild track, shadow mode). Full R3 spec governs.

**Architecture:**

```
Forge DB (raw data)
  ↓
forge_compute (derived metrics)
  ↓
EDSx-03 Retriever
  ↓
Normalizer (adaptive cross-sectional quantile ranks)
  ↓
4 Sub-score Scorers
  ├── Derivatives Positioning (0.40) — momentum core + stress override
  ├── Capital Flows (0.35) — directional flow confirmation
  ├── DeFi Health (0.15) — protocol health + binary stress detector
  └── Macro Context (0.10) — regime context [null until FRG-10]
  ↓
Weighted Composition (with null redistribution)
  ↓
Confidence Modifiers (cross_asset_correlation, volatility_regime)
  ↓
PillarScore emission → DisconnectDetector
```

### Normalization

All inputs use adaptive cross-sectional quantile rank — no z-scores mixed into the same weighted sum. For each metric on each scoring day: rank within cross-section → percentile [0,1]; for directional metrics map to [−1, +1] with cross-sectional median = 0.

Tier-specific adjustments (applied before ranking):
- Large cap (universe_rank 1–20): raw values, standard windows
- Mid cap (universe_rank 21–60): 1.5× smoothing window, 1st/99th percentile winsorization
- Small cap (universe_rank 61+): 2× smoothing window, 5th/95th percentile winsorization, L/S ratio weight capped at 50% of nominal

### §2: Derivatives Positioning (Weight: 0.40)

**Framing:** Momentum-primary with stress override.

**Layer A — Directional Leverage Build (Momentum Core):**

Trend gate: `T = sign(return_1d + return_5d)`

| Input              | Metric               | Weight | Directional Logic |
|--------------------|----------------------|--------|-------------------|
| Funding pressure   | funding_zscore       | 0.30   | Positive = longs paying (bullish) |
| OI impulse (aligned) | avg(oi_momentum_24h, oi_momentum_7d) | 0.30 | Gated by T |
| L/S skew           | ls_ratio_zscore      | 0.15   | Gated by T |
| Liquidation skew   | liquidation_asymmetry | 0.15  | Short liq dominating = bullish |
| Funding carry      | funding_carry        | 0.10   | Structural positioning lean |

**Layer B — Instability Penalty (Stress Override):**

```
Penalty = 0.3×|funding_z| + 0.4×(liq_total/ADV_30d) + 0.3×divergence_term
```

```
S_derivatives = tanh(S_dir) × exp(−Penalty)
```

Output: [−1, +1]. Near zero = neutral positioning or high stress (momentum dampened).

Dropped from R2: oi_momentum composite (24h+7d+blended), perp_basis (dormant, D11), fixed 0.7/0.3 thresholds.
Added in R3: ls_ratio_zscore, Layer B instability penalty, trend gate T.

### §3: Capital Flows (Weight: 0.35)

**Framing:** Directional flow confirmation. Same trend gate T as Derivatives.

| Input                   | Metric                     | Weight | Notes |
|-------------------------|----------------------------|--------|-------|
| Net exchange flow       | exchange_flow_net_position | 0.25   | ETH + ARB, 18 instruments |
| Flow momentum 7d        | flow_momentum_7d           | 0.15   |       |
| Flow momentum 24h       | flow_momentum_24h          | 0.10   |       |
| Volume-price momentum   | volume_price_momentum      | 0.15   | volume_ratio_7d × sign(price_change_7d) |
| Volume universe rank    | volume_universe_rank       | 0.05   | Liquidity context, not directional |
| Whale flow ratio        | whale_flow_ratio           | 0.10   | Tier 2 confidence |
| Exchange reserve proxy  | exchange_reserve_proxy     | 0.05   | Use Δ only, level drifts |
| Stablecoin supply momentum | stablecoin_supply_momentum | 0.10 | Market-level |
| Stablecoin supply delta 24h | stablecoin_supply_delta_24h | 0.05 | Moved from DeFi Health (R3) |
| ETF net flows           | etf_net_flows              | 0.00   | Null until FRG-05 deploys |

ETF weight activation (config change, not code change): when FRG-05 deploys, reduce volume_universe_rank to 0.00, assign ETF 0.05, reassess flow_momentum split.

Coverage: Exchange flow data covers 18 instruments. Remaining ~139 operate on market-level features only with proportional weight redistribution. `has_exchange_flow` flag tells downstream how much to trust per-instrument output.

### §4: DeFi Health (Weight: 0.15)

**Framing:** Protocol health + binary stress detection. Not directional.

| Input                     | Metric                    | Weight |
|---------------------------|---------------------------|--------|
| TVL momentum              | defi_tvl_momentum         | 0.30   |
| Revenue ratio             | defi_revenue_ratio        | 0.25   |
| Revenue momentum          | defi_revenue_momentum     | 0.25   |
| Stablecoin supply delta 7d | stablecoin_supply_delta_7d | 0.10  |
| Peg stress flag           | peg_stress_active         | 0.10   |

Binary peg stress detector: `peg_stress_active = 1` if peg_deviation_severity > 0.02 OR peg_stress_index > 0.8. When active: DeFi Health score multiplied by 0.5 (circuit breaker).

### §5: Macro Context (Weight: 0.10)

All inputs null until FRG-10 deploys. Weight redistributes proportionally to other sub-scores. No placeholder logic — honest null.

When active: macro_liquidity_composite (0.35), yield_curve_regime (0.25), dxy_momentum (0.25), real_rate_momentum (0.15).

### Confidence Modifiers

```
pillar_confidence = base_confidence × correlation_modifier × volatility_modifier
base_confidence = input_coverage ^ 0.5
```

- Correlation modifier: 1.0 at normal correlation, 0.7 at extreme (> 0.8)
- Volatility modifier: 1.0 at normal vol, 0.7 at extreme vol
- If modifiers null: default 1.0, log `modifier_defaulted: true` in EvidenceBundle

### forge_compute Dependencies (Pre-Build Requirements)

| Metric               | Description                                     | Status |
|----------------------|-------------------------------------------------|--------|
| volume_price_momentum | volume_ratio_7d × sign(price_change_7d)         | New    |
| volume_universe_rank | Percentile rank of 7d avg volume                | Exists (FRG-08 Phase 3 — verify) |
| ls_ratio_zscore      | Z-score of L/S ratio, 30d rolling window        | New    |
| ls_ratio_extreme     | Binary: L/S ratio > 2σ from rolling mean        | New    |

Coinalyze agent update required: add L/S ratio endpoint to production collection (currently backfill only).

---

## 7. Pillar 3: Valuation (Planned)

**Status:** Architecture defined. Build pending. REM-21.

**Signal taxonomy (from Pillar Architecture v1):**

Included: NVT ratio, MVRV ratio, realized price multiple, stock-to-flow deviation (BTC only), relative value vs BTC/ETH, market cap / TVL ratio (DeFi tokens), fee revenue multiple, thermocap multiple, SOPR, Puell Multiple (BTC).

**Data dependencies:** Glassnode free tier (MVRV, SOPR, realized price, Puell, thermocap), DeFiLlama (protocol TVL/revenue), CoinPaprika (market cap). Most Tier 2 confidence — heuristic-dependent. MVRV/SOPR are structurally unavailable without full UTXO data; Glassnode Professional at $79/mo is the cheapest path if on-chain demand emerges.

**Horizon sensitivity:** Low signal at 1D (NVT/MVRV don't have daily signal). High value at 30D (deep value/overvaluation).

---

## 8. Pillar 4: Structural Risk (Planned)

**Status:** Architecture defined. Build pending. REM-24.

**Signal taxonomy (from Pillar Architecture v1):**

Included: Realized volatility (7D/30D/90D), volatility of volatility, BTC correlation (altcoin-specific), cross-asset correlation (BTC-SPX, BTC-Gold), maximum drawdown velocity, liquidation cascade proximity, DeFi protocol risk (TVL concentration, depeg events), token unlock schedule impact, exchange concentration, stablecoin depeg distance.

**Architectural privilege:** When Structural Risk score crosses 80th percentile, effective weight scales up to 2× base weight, stealing proportionally from other pillars. This prevents aggressive bullish signals during volatility spikes even without a regime transition.

**Data dependencies:** OHLCV (Tiingo/TimescaleDB), Coinalyze liquidations, DeFiLlama, FRED (cross-asset correlation), token unlock calendars (new collector required).

---

## 9. Pillar 5: Tactical Macro (Planned)

**Status:** Architecture defined. Build pending. REM-22/23.

**Signal taxonomy (from Pillar Architecture v1):**

Included: DXY level + momentum (1D/7D/30D), US 10Y yield level + change, US 2Y-10Y yield spread, VIX level + change, credit spreads (HY OAS), gold price momentum, S&P 500 momentum, Fed funds futures implied rate, MOVE Index, global M2 growth rate.

**Data dependency:** FRG-10 (FRED migration) is a hard prerequisite. Until FRG-10 completes, Macro Context sub-score in Pillar 2 is null and Tactical Macro pillar cannot build. FRED data for DXY, yields, VIX, SPX, gold are all part of the FRG-10 expanded scope.

**Architectural boundary (locked):** Structural macro classification (regime type) belongs to the regime engine. This pillar captures tactical alpha from macro movements within whatever regime is active. The old EDS macro pillar tried to do both — that mistake is not repeated.

---

## 10. ML Track — Layer 1 Domain Models

All five models are LightGBM-based (preferred for initial implementation). They consume Layer 0 features from the shared feature store — see thread_3 for feature definitions. No ML output feeds back into EDSx and no EDSx output feeds into ML training.

### Model 1: Derivatives Pressure

**Objective:** Estimate directional pressure from leveraged positioning.
**Granularity:** Per-instrument (121 Coinalyze instruments) + market-level aggregate.
**Input dimension:** ~60–80 features per instrument.

**Output:**
```
DerivativesPressureOutput {
    instrument_id, timestamp, model_version
    p_bullish, p_neutral, p_bearish  # sum = 1.0
    pressure_magnitude               # [0,1] — strength regardless of direction
    feature_coverage, prediction_entropy
    top_features                     # top 5 SHAP contributions
}
```

Special note: Perp basis is dormant in EDSx (D11) but available in Forge. ML uses it — a signal EDSx explicitly chose not to use.

### Model 2: Capital Flow Direction

**Objective:** Estimate net capital movement direction and intensity.
**Granularity:** Per-instrument where exchange flow data exists (18 instruments) + market-level proxy for all others. Two-mode operation — `has_exchange_flow` flag signals which mode is active.
**Input dimension:** ~40–60 features.

**Output:**
```
CapitalFlowOutput {
    instrument_id (or "MARKET"), timestamp, model_version
    p_inflow, p_neutral, p_outflow  # sum = 1.0
    flow_magnitude                  # [0,1]
    feature_coverage, prediction_entropy
    has_exchange_flow, has_etf_flow # coverage flags for Layer 2 weighting
    top_features
}
```

### Model 3: Macro Regime

**Objective:** Classify macro-financial environment and estimate regime transition probabilities.
**Granularity:** Market-level only. One output applies to all instruments.
**Input dimension:** ~80–100 features. Mixed-frequency inputs (daily/weekly/monthly/quarterly) handled via carry-forward with staleness indicator.

Six regime states (vs. three in EDSx legacy): `risk_on_expansion`, `risk_on_tightening`, `risk_off_orderly`, `risk_off_crisis`, `transitional`, `recovery`. Final state count to be determined empirically via BIC/AIC comparison.

**Output:**
```
MacroRegimeOutput {
    timestamp, model_version
    regime                          # primary label
    regime_probabilities            # dict[state → float]
    regime_stability                # P(staying in current regime) [0,1]
    transition_risk                 # P(transition to each other regime)
    regime_duration_days
    liquidity_direction             # [-1,1]
    dollar_pressure                 # [-1,1]
    risk_appetite                   # [-1,1]
    feature_coverage, prediction_entropy
    top_features
}
```

Model type candidate: HMM for regime classification + LightGBM for transition probability estimation. Cold-start advantage: FRED provides decades of history — this model may be the first trained.

### Model 4: DeFi Stress

**Objective:** Detect systemic DeFi stress conditions with cascade risk early warning.
**Granularity:** Market-level stress indicator + per-protocol health scores.
**Input dimension:** ~50–70 features.

Historical context: Terra/Luna and FTX cascade events are the archetypal training examples — stress events that propagated from DeFi through lending protocols into broader market liquidations.

**Output:**
```
DeFiStressOutput {
    timestamp, model_version
    p_normal, p_elevated, p_critical  # sum = 1.0
    peg_stress, liquidity_stress, activity_stress  # decomposition [0,1]
    distressed_protocols              # list[str]
    feature_coverage, prediction_entropy
    top_features
}
```

Model type candidate: Isolation Forest or One-Class SVM for anomaly baseline + LightGBM for labeled stress event classification. Semi-supervised approach given scarcity of true stress events.

### Model 5: Volatility Regime

**Objective:** Classify volatility environment and estimate expected magnitude.
**Granularity:** Per-instrument + market-level.
**Input dimension:** ~60–80 features.

Most cross-domain Layer 1 model — consumes features from derivatives (liquidation patterns), macro (VIX), and price data.

Four regime states: `compressed`, `normal`, `elevated`, `extreme`.

**Output:**
```
VolatilityRegimeOutput {
    instrument_id (or "MARKET"), timestamp, model_version
    regime, regime_probabilities
    expected_daily_vol              # predicted annualized daily vol
    vol_direction                   # [-1,1] — expanding/compressing
    vol_persistence                 # P(current vol level persists)
    tail_risk                       # P(>3σ move in next 24h) [0,1]
    asymmetry                       # [-1,1] — downside vs upside tail
    feature_coverage, prediction_entropy
    top_features
}
```

Model type candidate: LightGBM for regime classification + quantile regression for magnitude estimates (10th/50th/90th percentile of next-period vol).

The Volatility Regime model's output conditions how Layer 2 interprets the other four models — a bullish Derivatives Pressure signal during compressed volatility has different implications than during extreme volatility.

---

## 11. Synthesis

### 11.1 EDSx/ML Weight Default

```
final_score = 0.5 × edsx_composite + 0.5 × ml_composite
```

Recalibrated quarterly against 14-day forward outcomes. Adjustment trigger: when one track demonstrates sustained > 5pp directional accuracy advantage over a full quarter.

EDSx confidence (data completeness) and ML feature_coverage are emitted independently and consumed by downstream synthesis — they are not collapsed into a single confidence value before synthesis.

### 11.2 Prediction Horizon

14 days. Volume-adjusted labels. Tercile discretization per training window (bullish top tercile, bearish bottom tercile, neutral middle) — discretization boundaries are recomputed per training window, not global constants.

### 11.3 Synthesis Architecture (Layer 2 — Future Design Session)

Layer 2 consumes the structured outputs of all five ML domain models and the three EDSx composite scores per instrument. Layer 2 design is a separate session. This document defines what Layer 2 receives — the output specifications in §10 above.

---

## 12. ML Graduation Criteria

Five hard criteria. No self-certification. Minimum 30-day shadow period (extendable if shadow evaluation fails).

1. All profile-active metrics equal or better than EDSx on rebuild track
2. Non-HOLD directional accuracy > 60% (above coin-flip baseline)
3. Conviction calibration positive (higher conviction → better outcomes)
4. Performance measured through at least 2 distinct regime periods
5. No single metric dramatically worse even if overall is better (no hidden pathologies)

---

## 13. Decision Log

| ID  | Decision | Source |
|-----|----------|--------|
| D11 | perp_basis dropped from EDSx active inputs. Dormant in EDSx. Available for ML. | Prior thread |
| D14 | Derivatives Positioning: momentum-primary with stress override. Not contrarian. | Backtest gate + external research |
| D15 | Adaptive cross-sectional quantile ranks. Fixed thresholds eliminated. | Backtest gate + external research |
| D16 | L/S ratio included. Add to Coinalyze production collection. | Backfill discovery |
| D17 | oi_momentum composite dropped. Keep 24h and 7d separately. | External design review |
| D18 | stablecoin_supply_delta_24h moved from DeFi Health to Capital Flows. | External design review |
| D19 | Peg metrics → binary stress detector. | External design review |
| D20 | Sub-score weights: 0.40 / 0.35 / 0.15 / 0.10. | External design review |
| D21 | Pillar confidence = base_confidence × correlation_modifier × volatility_modifier. | R3 gap resolution |
| D22 | Null redistribution: proportional weight reallocation + coverage tracking. | R3 gap resolution |
| D23 | Normalization: unified adaptive quantile ranks across all sub-scores. | R3 gap resolution |
| D24 | Tier-specific parameters: smoothing, winsorization, L/S cap. | External research |
| D25 | No persistence dampening needed (avg 1.1–1.3 days). | Backtest finding |
| D26 | Capital Flows: directional flow confirmation, same trend-gate as Derivatives. | R3 consistency |

---

*Thread files are read-only design references. Changes to locked decisions require architect approval and a design document revision. Implementation details are CC prompt territory, constrained by this spec.*
