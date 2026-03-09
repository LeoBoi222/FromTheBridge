# FromTheBridge — Complete System Design
## Empire Architecture v1.0

**Date:** 2026-03-04
**Type:** Top-down specification — complete, all layers
**Owner:** Stephen (architect, sole operator)
**Status:** Design complete. Phase 0 build ready.

> This document is the canonical reference for all build sessions.
> Every decision in every build session must trace to a specification in this document.
> If a build requirement cannot be traced here, stop and surface it to the architect.
> Do not implement anything not specified here without a documented design revision.

---

## DESIGN PHILOSOPHY

### Top-Down, Consumer-First

The system is designed from revenue downward to sources. Each layer exists only because the layer above it requires specific inputs. The sequence:

```
Layer 7: Revenue Streams       — What customers pay for
Layer 6: Output Products        — Concrete deliverables (API, signals, delivery)
Layer 5: Signal Generation      — EDSx + ML signal tracks, synthesis
Layer 4: Feature Engineering    — Computed features, transforms, aggregations
Layer 3: Data Universe          — Canonical, auditable, normalized data store
Layer 2: Normalization          — Source adapters, cleaning, validation
Layer 1: Raw Collection         — Agents writing to landing zone
Layer 0: Sources                — External APIs, bulk files, WebSockets
```

### No-Drift Principles

1. **Schema is immutable once Phase 0 gate passes.** Adding a new source or metric adds catalog entries, not columns or tables.
2. **Every layer has a contract.** Input format, output format, error handling, null handling — specified before build.
3. **Adapters absorb source variance.** Nothing above Layer 2 ever sees source-specific structure.
4. **Validation is structural.** Every value is checked against its metric definition at ingestion. Out-of-range, wrong type, wrong cadence — rejected with audit trail.
5. **No build without complete spec.** Each phase is reviewed and approved before implementation begins.

### Forge Is Dead

The prior bottom-up architecture (Forge) is superseded entirely. Forge tables are not authoritative. Forge data is a migration candidate evaluated against the adapter contract. If Forge data can be cleanly normalized to canonical schema, it migrates. If not, it is re-backfilled from the original source. No component of the new system consults Forge tables for anything other than migration input.

---

## THREAD 1: REVENUE & PRODUCT DEFINITION

### Revenue Architecture

Three economically distinct positions exist in market data:

**Position 1 — Infrastructure provider:** Clean, normalized, auditable data. Customers are builders. Layer 3 is the product surface. Competitors: Glassnode, Coin Metrics, Kaiko.

**Position 2 — Intelligence provider:** Interpretation and signals. Customers are decision-makers. Layer 5 is the product surface. Competitors: research desks, quant shops.

**Position 3 — Workflow product:** Complete tool — dashboard, alerts, briefs. Customers are operators. The UI is the product surface.

**FromTheBridge occupies Position 2 with Position 1 as the structural foundation.** The existing EDSx + ML architecture, pillar structure, and prediction targets all point to an intelligence product. The data layer is internal infrastructure that becomes an additional revenue surface, not the primary product.

### Revenue Streams (Multi-Stream from Day One)

**Stream 1 — Direct subscriptions (tiered)**
Prosumer to institutional tiers. Dashboard + API access gated by feature depth and history. Lowest friction to start.

**Stream 2 — API data licensing (B2B)**
Counterparties pay for normalized, auditable, attributed data to power their own products. They don't use the interface — they build on the data layer. Customers: exchanges, funds, fintech builders, index providers. Higher ACV, fewer customers, relationship-driven. Layer 3 is the product surface for this stream.

**Stream 3 — Protocol / ecosystem reporting (sponsored)**
A protocol (Solana Foundation, Uniswap Labs, etc.) pays for ongoing data-driven reports on their ecosystem. Recurring B2B revenue with no subscriber dependency. Maps naturally to DeFi data coverage. Underexploited by competition.

**Stream 4 — Index / benchmark licensing**
Rules-based index constructed from the canonical data, licensed to financial product issuers for settlement benchmarks. Deferred to v2 — requires methodology documentation and legal structure, but the data infrastructure is already the hard part.

**Stream 5 — Embedded analytics (white-label)**
Signal engine or data feeds embedded in a fund or exchange's own interface. Sticky, large contracts, longer sales cycles.

### Content Originality

The differentiator is not qualitative research dressed up with charts. It is: **systematic, backtested, quantitative signals with documented methodology and PIT-correct historical data.** This is what Glassnode, Kaiko, and Messari do not produce. EDSx's deterministic scoring with full audit trail, combined with calibrated ML probabilities, is a genuine differentiation in a market full of opinion-based research.

### Coverage Framing

Coverage is expressed as domain breadth, not ticker count. The product is "derivatives + flows + DeFi + macro intelligence across the instruments those domains cover" — not "BTC, ETH, SOL signals." The instrument universe emerges from data completeness thresholds in Phase 1. Coinalyze alone covers 121 instruments on derivatives. Coverage is substantially broader than 3 assets from day one.

### MVP Definition

**MVP = one signal report, delivered on schedule, to one paying customer, that they trust enough to act on.**

No billing infrastructure. No self-serve onboarding. No dashboard. The signal, on time, defensibly produced, to a small number of institutional early-access customers paying real money.

### Decisions Locked

| Decision | Outcome |
|---|---|
| Primary revenue | Intelligence-as-a-Service |
| Revenue architecture | Multi-stream from day one |
| Product surface | Layer 5 (signals), Layer 3 as B2B secondary |
| Content originality | Quantitative, systematic, auditable — not qualitative |
| Asset coverage | Domain-driven, not ticker-driven |
| MVP | Signal product, institutional early access, manual invoicing |
| Not in v1 | Dashboard UI, billing infrastructure, content products |
| Index licensing | v2 — deferred with trigger condition: methodology documented + ToS audited |

---

## THREAD 2: SIGNAL ARCHITECTURE (Layer 5)

### Output Contract (Layer 6 → Layer 5)

Everything in Layer 5 exists to produce this, reliably, on schedule:

```json
{
  "instrument": "BTC",
  "timestamp": "2026-03-04T12:00:00Z",
  "signal": {
    "direction": "bullish",
    "confidence": 0.73,
    "magnitude": 0.45,
    "horizon": "14d",
    "regime": "risk_on"
  },
  "components": {
    "derivatives_pressure": { "direction": "bullish", "weight": 0.40, "confidence": 0.81 },
    "capital_flows":        { "direction": "neutral",  "weight": 0.35, "confidence": 0.65 },
    "defi_health":          { "direction": "neutral",  "weight": 0.15, "confidence": null },
    "macro_context":        { "direction": "bullish",  "weight": 0.10, "confidence": 0.70 }
  }
}
```

### Architecture Decisions (M1, M3, M5, M9, D6, D41 validated)

**M1 revised:** EDSx and ML are computationally independent but share the full Data Universe (Layers 1–3) and Feature Engineering (Layer 4). Independence means neither track's output influences the other's inputs. They run separately, produce separate output schemas, combine at synthesis.

**M3 resolved:** Five models = four domain models + one synthesis model. Not five arbitrary models.

**M5 kept:** 14-day horizon, volume-adjusted labels. Horizon exposed in output schema and documented in methodology.

**M9 kept:** LightGBM for all models with mandatory isotonic calibration and feature importance logging.

**D6 kept:** Four pillar sub-scores. Maps directly to component structure in output schema.

**D41 kept, renamed:** collection tier → scoring tier → signal_eligible tier. Rule-driven promotion. Instrument universe emerges from data, not from manual selection.

### Track A: EDSx (Deterministic Scoring)

Rule-based, fully transparent. Given feature values at a point in time, produces directional score and confidence for each pillar. No training. Fully reproducible from methodology documentation alone.

**Why EDSx exists alongside ML:** Institutional customers need to interrogate signals. "Why is this bullish?" must have an answer that doesn't require understanding a gradient boosted tree. EDSx provides that. It also provides a baseline — if ML consistently underperforms EDSx, the ML pipeline has a problem.

**Four pillars:**
- *Derivatives Pressure* — funding rates, OI, liquidations, perpetual basis, options skew. Is derivatives positioning leaning long or short? Is leverage building or unwinding?
- *Capital Flows* — exchange inflows/outflows, stablecoin supply, ETF flows, on-chain transfer volume. Is money entering or leaving risk assets?
- *DeFi Health* — protocol TVL, lending utilization, DEX volume, stablecoin peg stability. Is DeFi expanding or contracting?
- *Macro Context* — yield curve, credit spreads, DXY, Fed funds trajectory. Is the macro environment supportive or hostile?

**EDSx pillar output contract:**
```json
{
  "pillar": "derivatives_pressure",
  "instrument": "BTC",
  "timestamp": "...",
  "direction": "bullish",
  "score": 0.72,
  "confidence": 0.81,
  "signals_fired": ["funding_rate_elevated", "oi_increasing", "liquidations_low"],
  "signals_available": 8,
  "signals_computed": 7
}
```

**Confidence semantics:** Confidence = data completeness, not prediction confidence. `signals_computed / signals_available`. Null confidence = not enough data to score the pillar at all. This is honest, auditable, and has a specific meaning.

**Composite formation:**
```
composite_score = Σ(pillar_score × pillar_weight)
direction = "bullish" if composite_score > threshold_high
direction = "bearish" if composite_score < threshold_low
direction = "neutral" otherwise
```
Thresholds calibrated by F1 maximization against backtested history at 14-day horizon.

### Track B: ML Models

Five LightGBM classifiers producing probability distributions over {bullish, neutral, bearish} at 14-day horizon.

**Model architecture:**

| Model | Input features | Scope |
|---|---|---|
| M-Derivatives | Derivatives features | Per-instrument |
| M-Flows | Capital flow features | Per-instrument |
| M-DeFi | DeFi health features | Per-instrument (nullable for non-DeFi assets) |
| M-Macro | Macro feature vectors | Market-level, applied to all instruments |
| M-Synthesis | Outputs of M1–M4 + cross-asset breadth features | Per-instrument |

**Training protocol:**
- Walk-forward only. No random splits. No holdout from the end only.
- Labels: volume-adjusted 14-day forward returns, discretized to tercile boundaries computed on each training window independently
- Calibration: isotonic regression applied post-training
- Minimum OOS period: 12 months before graduation consideration

**Graduation criteria (all five must pass):**
1. Walk-forward Sharpe of directional calls > 0.5 over minimum 12 months OOS
2. Calibration error (ECE) < 0.05
3. Directional accuracy > 55% on OOS data
4. Feature importance stable across ≥3 training cycles (no single feature > 40% importance)
5. Manual architect review before each production deployment

**Shadow mode:** All models run in production infrastructure writing to shadow tables, not live output. Minimum 30-day shadow period. Graduation from shadow requires passing all five criteria on shadow period data plus historical OOS.

### Signal Synthesis

**Step 1: Agreement check.** EDSx and ML agree → confidence boost. Disagree → confidence penalty and flag set in output.

**Step 2: Confidence-weighted direction:**
```
final = argmax(
  EDSx_score × EDSx_confidence × 0.5 +
  ML_p_direction × ML_confidence × 0.5
)
```
EDSx/ML weights default 0.5/0.5, recalibrated quarterly.

**Step 3: Magnitude from ML only.** EDSx does not produce magnitude.

**Step 4: Regime classification.** Separate lightweight LightGBM classifier, market-level, consumes macro + breadth features. Produces RISK_ON / RISK_OFF / TRANSITION. Applies uniformly to all instruments at a given timestamp.

**Step 5: Null handling.** Pillar below completeness threshold → zero weight contribution, null in component block. Signal still serves. Confidence recalculated on available pillars. System never crashes on missing data — it degrades honestly.

### Decisions Locked

| Decision | Outcome |
|---|---|
| Track architecture | Two independent tracks (EDSx + ML), shared data and features |
| EDSx confidence | Data completeness, not prediction confidence |
| Pillar count | Four: derivatives, flows, DeFi, macro |
| ML model count | Five: four domain + one synthesis |
| ML algorithm | LightGBM + isotonic calibration |
| Prediction horizon | 14 days, volume-adjusted labels |
| Label discretization | Tercile boundaries on training set, recalculated each cycle |
| Synthesis | Confidence-weighted, not simple average |
| Regime | Separate classifier, market-level |
| Graduation | Five hard criteria, no self-certification |
| Instrument coverage | Data completeness driven, not manually selected |
| Magnitude | ML track only |

---

## THREAD 3: FEATURE ENGINEERING (Layer 4)

### Design Principles

- Features are transformations, not storage. Recomputable from canonical store at any time.
- PIT is absolute. Feature at T uses only observations with `valid_from ≤ T`.
- Null handling is typed. Three states: `INSUFFICIENT_HISTORY`, `SOURCE_STALE`, `METRIC_UNAVAILABLE`. Not interchangeable.
- Features are versioned. Formula changes create new version entries. Models declare which version they were trained on.
- Computation is event-triggered on metric ingestion, not wall-clock scheduled.
- Computation is idempotent. Same inputs always produce same outputs.

### Null State Definitions

| State | Meaning |
|---|---|
| `INSUFFICIENT_HISTORY` | Window requires N observations, fewer than N exist |
| `SOURCE_STALE` | Most recent observation older than 2× expected cadence |
| `METRIC_UNAVAILABLE` | Metric is not tracked for this instrument |

### Computation Order (within each cadence trigger)

A → C → B → F → G → D → E

Where:
- **A:** Rolling statistical transforms (no cross-instrument dependencies)
- **C:** Ratio/interaction features (depends on A)
- **B:** Cross-sectional ranks (requires all instruments' A values at timestamp)
- **F:** Breadth aggregations (requires all instruments' A + B values)
- **G:** Cross-asset features (requires specific instruments' values)
- **D:** Regime/state labels (requires A–C current)
- **E:** Calendar features (independent, always computable)

### Feature Categories

#### Category A: Rolling Statistical Transforms

Statistic types: `value`, `change_pct`, `zscore`, `percentile_rank`, `ma`, `ema`, `cumsum`, `min`, `max`, `range`

Window sizes: 7d, 14d, 30d, 90d, 365d (calendar days, not observation counts)

Staleness threshold: observation older than 2× expected cadence triggers `SOURCE_STALE`

**Derivatives features (per instrument, 8h):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Funding rate raw | derivatives.perpetual.funding_rate | value | 1 period |
| Funding rate zscore | derivatives.perpetual.funding_rate | zscore | 30d |
| Funding rate zscore | derivatives.perpetual.funding_rate | zscore | 90d |
| Funding rate MA | derivatives.perpetual.funding_rate | ma | 7d |
| Funding rate MA | derivatives.perpetual.funding_rate | ma | 30d |
| OI USD raw | derivatives.perpetual.open_interest_usd | value | 1 period |
| OI change | derivatives.perpetual.open_interest_usd | change_pct | 1d |
| OI change | derivatives.perpetual.open_interest_usd | change_pct | 7d |
| OI zscore | derivatives.perpetual.open_interest_usd | zscore | 30d |
| OI zscore | derivatives.perpetual.open_interest_usd | zscore | 90d |
| Long liquidations 24h | derivatives.perpetual.liquidations_long_usd | cumsum | 24h |
| Short liquidations 24h | derivatives.perpetual.liquidations_short_usd | cumsum | 24h |
| Net liquidation direction | derived (long - short) | value | 1 period |
| Liquidation imbalance | derived (net / total, signed) | value | 1 period |
| Liquidation zscore | derived net_liquidation | zscore | 30d |
| Perpetual basis | derived (perp_price - spot) / spot | value | 1 period |
| Perpetual basis MA | perp_basis | ma | 7d |
| Perpetual basis zscore | perp_basis | zscore | 30d |
| Options delta skew | derivatives.options.delta_skew_25 | value | 1 period |
| Options skew zscore | derivatives.options.delta_skew_25 | zscore | 30d |
| IV term structure slope | derived (iv_1m - iv_1w) / iv_1w | value | 1 period |

**Capital flows features (per instrument, 1d):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Exchange net flow | flows.exchange.net_flow_usd | value | 1 period |
| Net flow MA | flows.exchange.net_flow_usd | ma | 7d |
| Net flow zscore | flows.exchange.net_flow_usd | zscore | 30d |
| Net flow cumulative | flows.exchange.net_flow_usd | cumsum | 7d |
| Net flow cumulative | flows.exchange.net_flow_usd | cumsum | 30d |
| Inflow zscore | flows.exchange.inflow_usd | zscore | 30d |
| Outflow zscore | flows.exchange.outflow_usd | zscore | 30d |
| Stablecoin supply change | stablecoin.supply.total_usd | change_pct | 1d |
| Stablecoin supply change | stablecoin.supply.total_usd | change_pct | 7d |
| Stablecoin supply zscore | stablecoin.supply.total_usd | zscore | 30d |
| ETF net flow | etf.flows.net_flow_usd | value | 1 period |
| ETF flow cumulative | etf.flows.net_flow_usd | cumsum | 7d |
| ETF flow cumulative | etf.flows.net_flow_usd | cumsum | 30d |
| On-chain transfer vol | flows.onchain.transfer_volume_usd | value | 1 period |
| On-chain transfer MA | flows.onchain.transfer_volume_usd | ma | 7d |
| On-chain transfer zscore | flows.onchain.transfer_volume_usd | zscore | 30d |

**DeFi health features (market-level, 1d):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Total DeFi TVL | defi.aggregate.tvl_usd | value | 1 period |
| TVL change | defi.aggregate.tvl_usd | change_pct | 1d |
| TVL change | defi.aggregate.tvl_usd | change_pct | 7d |
| TVL zscore | defi.aggregate.tvl_usd | zscore | 30d |
| DEX volume | defi.dex.volume_usd_24h | value | 1 period |
| DEX volume MA | defi.dex.volume_usd_24h | ma | 7d |
| DEX volume MA | defi.dex.volume_usd_24h | ma | 30d |
| DEX volume zscore | defi.dex.volume_usd_24h | zscore | 30d |
| Lending utilization | defi.lending.utilization_rate | value | 1 period |
| Lending utilization zscore | defi.lending.utilization_rate | zscore | 30d |
| Stablecoin peg deviation | stablecoin.peg.price_usd | derived max_deviation | 1 period |
| Peg deviation MA | derived max_deviation | ma | 7d |
| Protocol TVL change top 20 | defi.protocol.tvl_usd | change_pct per protocol | 7d |

**Macro features (market-level, 1d):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Yield curve spread | macro.rates.yield_10y - macro.rates.yield_2y | value | 1 period |
| Yield curve trend | yield_curve_spread | ma | 30d |
| Yield curve zscore | yield_curve_spread | zscore | 365d |
| DXY raw | macro.fx.dxy | value | 1 period |
| DXY change | macro.fx.dxy | change_pct | 1d |
| DXY change | macro.fx.dxy | change_pct | 30d |
| DXY zscore | macro.fx.dxy | zscore | 90d |
| HY credit spread | macro.credit.hy_oas | value | 1 period |
| Credit spread change | macro.credit.hy_oas | change_pct | 7d |
| Credit spread zscore | macro.credit.hy_oas | zscore | 90d |
| Fed funds rate | macro.rates.fed_funds_effective | value | 1 period |
| Rate trend | macro.rates.fed_funds_effective | change_pct | 90d |

#### Category B: Cross-Sectional Ranks

Value for one instrument ranked as percentile against all signal-eligible instruments at same timestamp. Historically stable — adding a new instrument doesn't retroactively change historical ranks.

| Feature | Input | Universe |
|---|---|---|
| Funding rate rank | derivatives.perpetual.funding_rate | All signal-eligible instruments |
| OI change rank | oi_change_pct_1d | All signal-eligible instruments |
| Net flow rank | flows.exchange.net_flow_usd | All signal-eligible instruments |
| Liquidation imbalance rank | liquidation_imbalance | All signal-eligible instruments |

#### Category C: Ratio and Interaction Features

| Feature | Formula | Cadence |
|---|---|---|
| OI-to-volume ratio | oi_usd / spot_volume_usd | 8h |
| OI-to-volume zscore | zscore(oi_to_volume, 30d) | 8h |
| Liquidation-to-OI ratio | (long_liq + short_liq) / oi_usd | 8h |
| Flow-to-OI ratio | exchange_net_flow_usd / oi_usd | 1d |
| Funding-to-basis spread | funding_rate - perp_basis | 8h |
| Stablecoin-to-DeFi ratio | stablecoin_supply_usd / defi_tvl_usd | 1d |
| ETF flow-to-OI ratio | etf_net_flow_usd / oi_usd | 1d |

#### Category D: Regime and State Labels

| Feature | Computation | Output |
|---|---|---|
| Funding regime | Discretize funding_rate_zscore_30d: >1.5 = ELEVATED, <-1.5 = SUPPRESSED, else NEUTRAL | Categorical (3) |
| OI regime | Discretize oi_zscore_30d: >1.5 = HIGH, <-1.5 = LOW, else NORMAL | Categorical (3) |
| Macro regime | Classify from yield curve + credit spread + DXY composite | Categorical (3) |
| Liquidation regime | Classify from liquidation_imbalance + liquidation_zscore | Categorical (3) |
| Market regime | RISK_ON / RISK_OFF / TRANSITION — from separate ML classifier | Categorical (3) |

Market regime is market-level. Same label applies to all instruments at a given timestamp.

#### Category E: Calendar Features

Day of week · Day of month · Days to options expiry · Days since/to Fed meeting · Funding period index (0/1/2) · Quarter

#### Category F: Breadth and Market Aggregation

| Feature | Computation | Cadence |
|---|---|---|
| Market funding rate median | Median across all signal-eligible instruments | 8h |
| Market funding rate 90th pct | 90th percentile across universe | 8h |
| % instruments funding elevated | % where funding_rate_zscore_30d > 1.5 | 8h |
| % instruments funding suppressed | % where funding_rate_zscore_30d < -1.5 | 8h |
| % instruments OI increasing | % where oi_change_pct_1d > 0 | 8h |
| % instruments net outflow | % where exchange_net_flow_usd < 0 | 1d |
| Market liquidation imbalance | Sum(long_liq - short_liq) across universe, normalized | 8h |
| Breadth score | Composite: pct_funding_elevated×0.30 + pct_oi_increasing×0.30 + (1-pct_net_outflow)×0.25 + liq_imbalance_normalized×0.15 | 8h |

Breadth score is a deterministic formula with fixed weights. Not learned.

#### Category G: Cross-Asset Features

| Feature | Computation | Cadence |
|---|---|---|
| BTC dominance | BTC_market_cap / total_crypto_market_cap | 1d |
| BTC-ETH correlation | Rolling 30d return correlation | 1d |
| Altcoin funding vs BTC | instrument_funding / BTC_funding | 8h |
| Altcoin OI vs BTC OI | instrument_oi / BTC_oi | 8h |
| BTC beta | Rolling 30d beta of instrument returns to BTC | 1d |

### Feature Catalog Entry Structure

Every feature has a catalog entry before it is computed. Immutable once locked. Changes require new version entry.

```yaml
feature:
  name: "funding_rate_zscore_30d"
  version: "v1"
  category: "A"
  input_metric: "derivatives.perpetual.funding_rate"
  formula: "(value - rolling_mean(30d)) / rolling_std(30d)"
  window_days: 30
  min_observations: 20
  cadence: "8h"
  granularity: "per_instrument"
  output_type: "continuous"
  output_range: [-5.0, 5.0]
  null_states:
    INSUFFICIENT_HISTORY: "fewer than 20 observations in 30d window"
    SOURCE_STALE: "most recent observation older than 16h"
    METRIC_UNAVAILABLE: "metric not tracked for this instrument"
  pit_constraint: "uses only observations with valid_from <= computation_timestamp"
  consuming_models: ["M-Derivatives", "EDSx-Derivatives"]
  status: "active"
```

### Data Requirements → Layer 3

**Derivatives (per instrument, 8h):** funding rate, OI in USD, long liquidations USD, short liquidations USD, perpetual price, options 25-delta skew (nullable), IV 1w and 1m (nullable)

**Spot (per instrument, 1d):** close price USD, volume USD 24h, market cap USD

**Flows (per instrument, 1d):** exchange inflow USD, exchange outflow USD, on-chain transfer volume USD

**Stablecoins (market-level + per asset, 1d):** total supply USD, per-asset supply USD, peg price USD

**ETF (per product, 1d):** net flow USD, AUM USD

**DeFi (market-level + per protocol, 1d):** aggregate TVL USD, protocol TVL USD, DEX volume USD 24h, lending utilization rate

**Macro (market-level, 1d):** 10Y yield, 2Y yield, DXY, HY OAS, fed funds effective rate

**Derived (market-level, 1d):** total crypto market cap USD, BTC dominance %

**History depth requirements:** Derivatives: 3yr minimum · Macro: 10yr minimum · Flows/on-chain: 3yr · DeFi: 2yr · ETF: from product inception

### Decisions Locked

| Decision | Outcome |
|---|---|
| Feature versioning | Every feature has versioned catalog entry. Formula changes = new version. |
| Null typing | Three distinct null states. Not interchangeable. |
| PIT constraint | Absolute. No exceptions. |
| Computation trigger | Event-driven on metric ingestion, not wall-clock |
| Computation order | A → C → B → F → G → D → E |
| Idempotency | Hard requirement |
| Breadth score | Deterministic formula, fixed weights, not learned |
| Feature catalog | Required before any feature is computed. Immutable once locked. |

---

## THREAD 4: DATA UNIVERSE (Layer 3)

### Schema Model Decision

**EAV observations table with metric catalog, partitioned by time, with materialized current-value views.**

Wide tables eliminated: adding a metric adds a column — violates schema immutability.
Hybrid typed tables eliminated: separate tables per domain leaks source structure above the adapter layer.
EAV selected: schema immutability, asset-class extensibility, consumer ignorance, full audit completeness — all satisfied simultaneously.

Performance addressed through: TimescaleDB hypertable partitioning, composite indexes, and materialized current-value view.

### DDL

#### instruments

```sql
CREATE TABLE instruments (
    instrument_id       UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_symbol    TEXT            NOT NULL UNIQUE,
    asset_class         TEXT            NOT NULL,
    name                TEXT            NOT NULL,
    is_active           BOOLEAN         NOT NULL DEFAULT true,
    collection_tier     TEXT            NOT NULL DEFAULT 'collection',
    base_currency       TEXT,
    quote_currency      TEXT            DEFAULT 'USD',
    metadata            JSONB           NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    deprecated_at       TIMESTAMPTZ,

    CONSTRAINT instruments_asset_class_valid
        CHECK (asset_class IN ('crypto', 'equity', 'commodity', 'forex', 'index', 'etf', 'defi_protocol')),
    CONSTRAINT instruments_collection_tier_valid
        CHECK (collection_tier IN ('collection', 'scoring', 'signal_eligible'))
);

CREATE INDEX idx_instruments_symbol      ON instruments (canonical_symbol);
CREATE INDEX idx_instruments_asset_class ON instruments (asset_class);
CREATE INDEX idx_instruments_tier        ON instruments (collection_tier) WHERE is_active = true;
```

**Tier semantics:**
- `collection` — data collected, not yet sufficient for scoring
- `scoring` — sufficient data quality and history for EDSx and feature computation
- `signal_eligible` — meets all thresholds for signal output to customers

Tier promotion is rule-driven and automatic. Changes logged with timestamp and reason.

#### metrics

```sql
CREATE TABLE metrics (
    metric_id           UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name      TEXT            NOT NULL UNIQUE,
    domain              TEXT            NOT NULL,
    subdomain           TEXT,
    description         TEXT            NOT NULL,
    unit                TEXT            NOT NULL,
    value_type          TEXT            NOT NULL DEFAULT 'numeric',
    granularity         TEXT            NOT NULL,
    cadence             INTERVAL        NOT NULL,
    staleness_threshold INTERVAL        NOT NULL,
    expected_range_low  DOUBLE PRECISION,
    expected_range_high DOUBLE PRECISION,
    is_nullable         BOOLEAN         NOT NULL DEFAULT false,
    methodology         TEXT,
    computation         TEXT,
    sources             TEXT[]          NOT NULL DEFAULT '{}',
    status              TEXT            NOT NULL DEFAULT 'active',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    deprecated_at       TIMESTAMPTZ,

    CONSTRAINT metrics_domain_valid
        CHECK (domain IN ('derivatives', 'spot', 'flows', 'defi', 'macro', 'etf', 'stablecoin')),
    CONSTRAINT metrics_value_type_valid
        CHECK (value_type IN ('numeric', 'categorical', 'boolean')),
    CONSTRAINT metrics_granularity_valid
        CHECK (granularity IN ('per_instrument', 'per_protocol', 'per_product', 'market_level')),
    CONSTRAINT metrics_status_valid
        CHECK (status IN ('active', 'deprecated', 'planned'))
);
```

**Canonical name convention:** `domain.subdomain.metric_name`
Examples: `derivatives.perpetual.funding_rate` · `flows.exchange.net_flow_usd` · `macro.rates.yield_10y` · `defi.aggregate.tvl_usd`

Canonical names are immutable once assigned. To change a metric: deprecate the existing entry, create a new entry with the new name.

#### sources

```sql
CREATE TABLE sources (
    source_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name          TEXT        NOT NULL UNIQUE,
    display_name            TEXT        NOT NULL,
    tier                    INTEGER     NOT NULL,
    tos_risk                TEXT        NOT NULL DEFAULT 'unaudited',
    commercial_use          BOOLEAN,
    redistribution          BOOLEAN,
    attribution_required    BOOLEAN     NOT NULL DEFAULT true,
    cost_tier               TEXT        NOT NULL DEFAULT 'free',
    reliability_slo         NUMERIC(4,3),
    metadata                JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT sources_tos_risk_valid
        CHECK (tos_risk IN ('none', 'low', 'unaudited', 'restricted', 'prohibited')),
    CONSTRAINT sources_cost_tier_valid
        CHECK (cost_tier IN ('free', 'freemium', 'paid', 'enterprise'))
);
```

#### observations (core table)

```sql
CREATE TABLE observations (
    observation_id      UUID            NOT NULL DEFAULT gen_random_uuid(),
    instrument_id       UUID            REFERENCES instruments (instrument_id),
    metric_id           UUID            NOT NULL REFERENCES metrics (metric_id),
    source_id           UUID            NOT NULL REFERENCES sources (source_id),

    valid_from          TIMESTAMPTZ     NOT NULL,
    valid_to            TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    collected_at        TIMESTAMPTZ     NOT NULL,

    value_numeric       DOUBLE PRECISION,
    value_text          TEXT,

    is_validated        BOOLEAN         NOT NULL DEFAULT false,
    validation_flags    JSONB           NOT NULL DEFAULT '{}',

    CONSTRAINT observations_value_present
        CHECK (value_numeric IS NOT NULL OR value_text IS NOT NULL),
    CONSTRAINT observations_valid_range
        CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT observations_pk
        PRIMARY KEY (metric_id, valid_from, instrument_id, source_id)
);

SELECT create_hypertable(
    'observations',
    'valid_from',
    chunk_time_interval => INTERVAL '1 month',
    partitioning_column => 'metric_id',
    number_partitions => 4
);

CREATE INDEX idx_obs_metric_instrument_time
    ON observations (metric_id, instrument_id, valid_from DESC)
    WHERE valid_to IS NULL;

CREATE INDEX idx_obs_current
    ON observations (metric_id, instrument_id, valid_from DESC)
    WHERE valid_to IS NULL AND is_validated = true;

CREATE INDEX idx_obs_source_time
    ON observations (source_id, ingested_at DESC);

CREATE INDEX idx_obs_pit
    ON observations (metric_id, instrument_id, valid_from, valid_to);
```

**`instrument_id` is nullable.** Market-level metrics (macro, DeFi aggregate, stablecoin aggregate) have `instrument_id IS NULL`. This is correct, not a defect.

#### observations_rejected

```sql
CREATE TABLE observations_rejected (
    rejection_id        UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID            NOT NULL REFERENCES sources (source_id),
    metric_id           UUID            REFERENCES metrics (metric_id),
    instrument_id       UUID            REFERENCES instruments (instrument_id),
    raw_payload         JSONB           NOT NULL,
    rejection_reason    TEXT            NOT NULL,
    rejection_code      TEXT            NOT NULL,
    collected_at        TIMESTAMPTZ     NOT NULL,
    rejected_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT rejection_code_valid
        CHECK (rejection_code IN (
            'RANGE_VIOLATION', 'TYPE_MISMATCH', 'NULL_VIOLATION',
            'UNKNOWN_METRIC', 'UNKNOWN_INSTRUMENT', 'DUPLICATE_OBSERVATION',
            'STALE_OBSERVATION', 'SCHEMA_ERROR', 'UNIT_UNKNOWN',
            'EXTREME_VALUE_PENDING_REVIEW'
        ))
);
```

#### collection_events

```sql
CREATE TABLE collection_events (
    event_id            UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID            NOT NULL REFERENCES sources (source_id),
    started_at          TIMESTAMPTZ     NOT NULL,
    completed_at        TIMESTAMPTZ,
    status              TEXT            NOT NULL,
    observations_written    INTEGER,
    observations_rejected   INTEGER,
    metrics_covered     TEXT[],
    instruments_covered TEXT[],
    error_detail        TEXT,
    metadata            JSONB           NOT NULL DEFAULT '{}',

    CONSTRAINT event_status_valid
        CHECK (status IN ('running', 'completed', 'failed', 'partial'))
);
```

#### instrument_metric_coverage

```sql
CREATE TABLE instrument_metric_coverage (
    instrument_id       UUID            NOT NULL REFERENCES instruments (instrument_id),
    metric_id           UUID            NOT NULL REFERENCES metrics (metric_id),
    first_observation   TIMESTAMPTZ,
    latest_observation  TIMESTAMPTZ,
    expected_cadence    INTERVAL,
    completeness_30d    NUMERIC(5,4),
    is_active           BOOLEAN         NOT NULL DEFAULT true,
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, metric_id)
);
```

#### observations_current (materialized view)

```sql
CREATE MATERIALIZED VIEW observations_current AS
SELECT
    o.instrument_id, o.metric_id,
    i.canonical_symbol, m.canonical_name,
    o.valid_from, o.value_numeric, o.value_text,
    o.collected_at, o.ingested_at, o.source_id
FROM observations o
JOIN instruments i ON o.instrument_id = i.instrument_id
JOIN metrics m ON o.metric_id = m.metric_id
WHERE o.valid_to IS NULL AND o.is_validated = true;

CREATE UNIQUE INDEX idx_obs_current_mv
    ON observations_current (instrument_id, metric_id);
```

Refreshed after each collection event completes.

### PIT Strategy

**Four timestamp fields, four distinct concepts:**

| Field | Meaning |
|---|---|
| `valid_from` | When this value was true in the real world |
| `valid_to` | When this value was superseded (NULL = currently valid) |
| `ingested_at` | When this value entered the canonical store |
| `collected_at` | When the collection agent fetched it from the source |

**Revision handling:** Close existing row (set `valid_to = now()`), insert new row with same `valid_from`. Original row preserved. Full revision history queryable.

**Backfill PIT semantics:** Backfilled observations have `ingested_at` = load time. Backtests at time T exclude observations with `ingested_at > T`. This is what makes backtests correct — a test at 2024-01-01 does not see data backfilled in 2026.

**PIT query pattern:**
```sql
SELECT value_numeric
FROM observations o
WHERE o.metric_id = :metric_id
  AND o.instrument_id = :instrument_id
  AND o.valid_from = :observation_timestamp
  AND o.ingested_at <= :as_of_timestamp
  AND (o.valid_to IS NULL OR o.valid_to > :observation_timestamp)
ORDER BY o.ingested_at DESC
LIMIT 1;
```

### Metric Catalog Seed Data

**Derivatives domain (per instrument, 8h):**
`derivatives.perpetual.funding_rate` · `derivatives.perpetual.open_interest_usd` · `derivatives.perpetual.liquidations_long_usd` · `derivatives.perpetual.liquidations_short_usd` · `derivatives.perpetual.price_usd` · `derivatives.options.delta_skew_25` · `derivatives.options.iv_1w` · `derivatives.options.iv_1m`

**Spot domain (per instrument, 1d):**
`spot.price.close_usd` · `spot.volume.usd_24h` · `spot.market_cap.usd`

**Flows domain (per instrument, 1d):**
`flows.exchange.inflow_usd` · `flows.exchange.outflow_usd` · `flows.onchain.transfer_volume_usd`

**Stablecoin domain (1d):**
`stablecoin.supply.total_usd` · `stablecoin.supply.per_asset_usd` · `stablecoin.peg.price_usd`

**ETF domain (per product, 1d):**
`etf.flows.net_flow_usd` · `etf.aum.total_usd`

**DeFi domain (1d):**
`defi.aggregate.tvl_usd` · `defi.protocol.tvl_usd` · `defi.dex.volume_usd_24h` · `defi.lending.utilization_rate`

**Macro domain (market-level, 1d):**
`macro.rates.yield_10y` · `macro.rates.yield_2y` · `macro.fx.dxy` · `macro.credit.hy_oas` · `macro.rates.fed_funds_effective`

**Derived (market-level, 1d):**
`spot.market_cap.total_crypto_usd` · `spot.dominance.btc_pct`

### Database Engine

**Primary:** PostgreSQL 16 + TimescaleDB. ACID guarantees for PIT correctness, native time-series partitioning, compression on cold chunks (>90 days).

**Analytical:** DuckDB (embedded, not a service). Reads Parquet exports of observations. Sub-second performance on multi-year scans. Used by feature engineering batch jobs and backtesting. Zero operational burden.

### Extensibility Proof

**Adding equities:** Register instruments with `asset_class = 'equity'`. Register equity-specific metrics in catalog. Write adapters. Zero DDL changes.

**Adding a new metric:** Add row to `metrics`. Add feature catalog entry. Add adapter mapping. Zero DDL changes.

**Adding a new source for an existing metric:** Add row to `sources`. Write adapter. Zero DDL changes.

The only DDL change this design requires is adding a new value storage type beyond `value_numeric` and `value_text` — an edge case for future vector-valued metrics.

### Decisions Locked

| Decision | Outcome |
|---|---|
| Schema model | EAV + metric catalog + materialized current-value view |
| Primary key for observations | (metric_id, valid_from, instrument_id, source_id) |
| Null instrument_id | Permitted and correct for market-level metrics |
| PIT model | Bitemporal: valid_from/valid_to + ingested_at + collected_at |
| Revision handling | Close old row, insert new row. Both preserved. |
| Backfill PIT semantics | ingested_at = load time. Backtests exclude data ingested after T. |
| Canonical naming | domain.subdomain.metric_name — hierarchical, no abbreviations |
| Instrument tiers | collection → scoring → signal_eligible, rule-driven |
| Primary database | PostgreSQL 16 + TimescaleDB |
| Analytical layer | DuckDB against Parquet exports |
| Schema immutability | New metric = catalog row. New source = catalog row. Zero DDL. |

---

## THREAD 5: NORMALIZATION & COLLECTION (Layers 1 + 2)

### Layer 1: Landing Zone Design

Schema-per-source landing tables. Append-only. Never modified after data lands. 90-day retention then cold archive.

**Required columns on every landing table:**
```sql
collected_at        TIMESTAMPTZ     NOT NULL
collection_event_id UUID            NOT NULL
is_processed        BOOLEAN         NOT NULL DEFAULT false
processed_at        TIMESTAMPTZ
```

### Layer 2: Adapter Contract

Every adapter implements exactly these responsibilities, no more, no less:

1. Read unprocessed records from its landing table
2. Map source-specific field names to canonical metric names
3. Convert units to canonical units
4. Resolve source instrument identifiers to canonical `instrument_id`
5. Resolve source metric identifiers to canonical `metric_id`
6. Validate values against metric catalog definitions (range, type, nullability)
7. Write validated observations to `observations`
8. Write rejected observations to `observations_rejected` with rejection code and raw payload
9. Mark processed landing records as `is_processed = true`
10. Write a `collection_events` record on completion

**Adapters must NOT:** call external APIs · create catalog entries · silently drop invalid data · transform values in undocumented ways · know anything about the signal layer

**Validation applied per observation (independent, not batch):**
```python
def validate(value, metric_definition):
    if metric_definition.value_type == 'numeric':
        if not isinstance(value, (int, float)):
            raise ValidationError('TYPE_MISMATCH', value)
    if value is None and not metric_definition.is_nullable:
        raise ValidationError('NULL_VIOLATION', value)
    if metric_definition.expected_range_low is not None:
        if value < metric_definition.expected_range_low:
            raise ValidationError('RANGE_VIOLATION', value)
    if metric_definition.expected_range_high is not None:
        if value > metric_definition.expected_range_high:
            raise ValidationError('RANGE_VIOLATION', value)
    return True
```

A single bad value is dead-lettered. The rest of the batch continues.

### Per-Source Specifications

#### Coinalyze
**Provides:** Perpetual futures — funding rate, OI, liquidations (121 instruments)
**ToS:** Unaudited — commercial use audit required before redistribution product ships
**Cadence:** Every 8h, offset 5 minutes past settlement (00:05, 08:05, 16:05 UTC)
**Known issues:**
- Open interest units vary by endpoint — verify in integration test, do not assume
- Three instruments (ANKR, FRAX, OGN) have historically extreme funding rate values — dead-letter with `EXTREME_VALUE_PENDING_REVIEW`, queue for manual review, do not silently reject or pass

**Field mappings:**

| Source field | Canonical metric | Unit conversion |
|---|---|---|
| `funding_rate` | `derivatives.perpetual.funding_rate` | None — already rate per 8h. Range: [-0.05, 0.05] |
| `open_interest_usd` | `derivatives.perpetual.open_interest_usd` | None — use USD field, not contracts field |
| `long_liquidations` | `derivatives.perpetual.liquidations_long_usd` | Verify unit in integration test — may be USD or contracts |
| `short_liquidations` | `derivatives.perpetual.liquidations_short_usd` | Same |
| `open_time` | `valid_from` | Unix ms → TIMESTAMPTZ: to_timestamp(open_time / 1000) |

#### DeFiLlama
**Provides:** Protocol TVL, DEX volume, stablecoin metrics
**ToS:** Free, low risk, attribution recommended
**Cadence:** Daily at 06:00 UTC
**Three separate collection jobs** (protocols, dex, stablecoins) with separate landing tables
**Known issues:** Shallow existing history — backfill from DeFiLlama historical API must run before live collection starts. Protocol slugs change on rebrands — maintain normalization map.

**Field mappings:**

| Source field | Canonical metric | Notes |
|---|---|---|
| `tvl_usd` (protocol) | `defi.protocol.tvl_usd` | instrument_id = protocol slug in instruments catalog |
| Sum of tvl_usd | `defi.aggregate.tvl_usd` | Computed by adapter from protocol data |
| `volume_usd_24h` | `defi.dex.volume_usd_24h` | Market-level, instrument_id = NULL |
| `circulating_usd` | `stablecoin.supply.per_asset_usd` | |
| Sum of circulating_usd | `stablecoin.supply.total_usd` | Computed by adapter |
| `price_usd` | `stablecoin.peg.price_usd` | Range [0.90, 1.10]. Values outside = dead-letter |

#### FRED
**Provides:** Macro time series — yields, DXY, credit spreads, fed funds
**ToS:** Public domain. No restrictions.
**Cadence:** Daily at 18:00 UTC (incremental — only new observations since last fetch)
**Known issues:** Returns '.' for missing values — adapter maps to NULL with `SOURCE_MISSING_VALUE` flag. Weekend/holiday gaps are structural, not quality issues.

**Series mappings:**

| FRED series_id | Canonical metric |
|---|---|
| `DGS10` | `macro.rates.yield_10y` |
| `DGS2` | `macro.rates.yield_2y` |
| `DTWEXBGS` | `macro.fx.dxy` |
| `BAMLH0A0HYM2` | `macro.credit.hy_oas` |
| `EFFR` | `macro.rates.fed_funds_effective` |

#### SoSoValue
**Provides:** ETF flows (BTC/ETH spot ETFs)
**ToS:** Non-commercial only. **Hard constraint.** `redistribution = false` in sources catalog. Cannot appear in any external data product until ToS audit resolves or paid tier acquired.
**Cadence:** Daily at 20:00 UTC (after US market close)

#### Tiingo
**Provides:** OHLCV (crypto + equities)
**ToS:** Free tier available, commercial use on paid tier
**Known issues:** Equity volume is in shares — multiply by close price for USD. Adapter must branch on `asset_class`.

#### Exchange Flows (Explorer / Etherscan)
**Provides:** Exchange inflows/outflows for 18 instruments
**ToS:** Etherscan V2 — unaudited for commercial use
**Known issues:**
- **Gate.io values are in wei, not ETH.** This is a confirmed bug. Adapter applies conversion: `eth_value = wei_value / 1e18`, then `usd_value = eth_value × spot_price`. Raw landing table preserves original wei values.
- Spot price for conversion fetched from `spot.price.close_usd` in canonical store

### Source Gap Analysis

| Metric | Gap | Decision |
|---|---|---|
| `derivatives.perpetual.price_usd` | Coinalyze perpetual price not confirmed | Verify in Coinalyze integration test. If absent: add Binance perp price collection. |
| `defi.lending.utilization_rate` | DeFiLlama does not provide directly | v1: proxy metric `defi.lending.utilization_proxy` from borrow/supply TVL ratio. Full metric v1.1. |
| `flows.exchange.*` beyond 18 instruments | Explorer limited coverage | Accept for v1. Expand in v1.1. |
| `spot.market_cap.usd` | Tiingo does not provide | Add CoinPaprika as source (existing, low ToS risk). |
| Options metrics | No current source | Null-propagate in v1. Deribit adapter in v1.1. |
| `flows.onchain.transfer_volume_usd` | CoinMetrics community covers BTC+ETH | Use CoinMetrics. Flag `redistribution = false` pending ToS audit. |

### Migration Plan

**Assessment criteria:** Are timestamps reliable? Are units documented? Are symbols mappable? Is metric identity clear?

| Dataset | Rows | Status | Decision |
|---|---|---|---|
| Tiingo OHLCV | ~800k | GREEN | Migrate first — spot price needed by flows adapter |
| Coinalyze derivatives | 185,066 | GREEN | Migrate |
| FRED macro | 140,261 | GREEN | Migrate |
| DeFiLlama DEX | 88,239 | GREEN | Migrate |
| DeFiLlama lending | 9,651 | GREEN | Migrate |
| CoinMetrics on-chain | 10,137 | GREEN | Migrate, flag internal-only |
| Exchange flows | 2,177 | RED (wei bug) | Migrate with wei→ETH fix applied in migration adapter |
| ETF flows | 774 | GREEN | Migrate, flag internal-only |
| DeFi protocols | 195 | SHALLOW | Skip — backfill from DeFiLlama API |
| Stablecoins | 180 | SHALLOW | Skip — backfill from DeFiLlama API |

Migration adapters implement the same interface as production adapters. They run before live collection agents start. No timestamp conflicts.

### Failure Handling

**Per collection run:** Retry 3× with exponential backoff. On third failure: log `collection_events.status = 'failed'`, alert. Do not retry indefinitely.

**Circuit breaker:** 3 consecutive failures → `DEGRADED` state. Collection continues attempting. Health monitoring escalates. No automatic source substitution.

**Staleness propagation chain:**
Source fails → `collection_events` status = `failed` → `instrument_metric_coverage.latest_observation` stops updating → feature engineering emits `SOURCE_STALE` → EDSx pillar confidence decreases → composite confidence decreases → customer-facing signal carries honest reduced confidence

### Decisions Locked

| Decision | Outcome |
|---|---|
| Landing zone | Schema-per-source, append-only, 90-day retention |
| Adapter interface | Standardized 10-responsibility contract |
| Validation scope | Per-observation, independent. Batch does not fail on single bad value. |
| Dead letter | Every rejection logged with raw payload, reason, code. Nothing silently dropped. |
| Redistribution | Flagged at source catalog level. Enforced at serving layer. |
| Gate.io wei bug | Fixed in adapter via unit conversion. Raw landing preserved. |
| Coinalyze extreme values | `EXTREME_VALUE_PENDING_REVIEW`. Manual review queue. |
| Migration order | Tiingo first (spot price dependency), then remaining in volume order |

---

## THREAD 6: INTEGRATION & BUILD PLAN

### Governing Constraints

1. Schema is the foundation — nothing built until Phase 0 gate passes
2. Data flows bottom-up; builds are validated top-down
3. One operator — phases are sequential, not concurrent

### Phase Sequence

#### Phase 0: Foundation
**Scope:** Database only. No collection. No computation.

Steps:
1. Provision PostgreSQL 16 + TimescaleDB
2. Apply DDL in dependency order: `sources → instruments → metrics → observations → observations_rejected → collection_events → instrument_metric_coverage`
3. Create all indexes — verify each with `EXPLAIN ANALYZE` (must show index scans, not seq scans)
4. Seed metric catalog (every metric in Thread 3 requirements list)
5. Seed sources catalog (all sources, with redistribution flags)
6. Seed initial instrument universe (BTC, ETH, SOL + full Coinalyze list)
7. Provision DuckDB analytical layer

**Hard gate — all must pass:**

| Criterion | Pass condition |
|---|---|
| Schema applied | All tables exist, all constraints enforced |
| Hypertable | TimescaleDB confirms observations is a hypertable |
| Index verification | EXPLAIN ANALYZE shows index scans for all four query patterns from Thread 4 |
| Metric catalog | Every metric from Thread 3 requirements has a catalog entry |
| PIT query | PIT query returns correct value from manually inserted test observation with revision |
| Redistribution flags | SoSoValue and CoinMetrics have `redistribution = false` |

**Phase 0 does not end until every gate passes.**

---

#### Phase 1: Data Ingestion
**Scope:** All collection agents, all adapters, migration execution.

Steps:
1. Migration adapters in order: Tiingo → Coinalyze → FRED → DeFiLlama DEX → DeFiLlama Lending → CoinMetrics → Exchange Flows → ETF Flows
2. Backfill jobs for shallow datasets (DeFiLlama protocols, stablecoins, CoinPaprika market cap)
3. Deploy production collection agents one at a time, verify each before next
4. Coverage verification query — all active metrics at ≥90% completeness for signal-eligible instruments
5. First instrument tier promotion run

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Migration complete | All datasets loaded, spot-checked, no systematic errors |
| Live collection | All production agents collecting at specified cadence |
| Rejection rate | Global < 5%. Known exceptions documented. |
| Coverage | All active metrics ≥90% completeness for signal-eligible instruments |
| Tiingo history | BTC from 2014, ETH from 2015 confirmed |
| Wei fix | Exchange flows Gate.io values in USD confirmed |
| Tier promotion | ≥20 instruments at signal_eligible tier |
| Redistribution | SoSoValue/CoinMetrics rows confirmed with correct source flags |

---

#### Phase 2: Feature Engineering
**Scope:** Feature computation layer, feature catalog, historical feature matrix.

Steps:
1. Populate feature catalog (all features from Thread 3) before any code
2. Build event trigger infrastructure
3. Implement categories in order: A → C → B → F → G → D → E
4. Per-category verification: hand-calculate 5 known values, compare to computed output
5. PIT constraint audit: every feature manually verified for no look-ahead
6. Historical feature matrix generation via DuckDB

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Feature catalog | All Thread 3 features have catalog entries |
| Hand calculation | 5 spot-checked values per feature type match exactly |
| PIT audit | Zero features fail the constraint audit |
| Historical matrix | Generated from earliest available history to present |
| Null state coverage | All three null states verified to fire correctly |
| Idempotency | Two runs on same inputs produce identical output |

---

#### Phase 3: Signal Generation — EDSx
**Scope:** Deterministic scoring track, calibration, backtesting.

Steps:
1. Write pillar rule set methodology documents before any code
2. Implement pillar scoring for all four pillars
3. Implement composite formation with threshold calibration
4. Implement regime classifier (rule-based, not ML)
5. Backtest against historical feature matrix
6. Calibrate thresholds against F1 maximization
7. Output schema verification against Layer 6 contract

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Rule documentation | All four pillar rule sets written as methodology docs before code |
| Backtested accuracy | > 52% directional accuracy at 14d horizon on OOS period |
| Confidence calibration | Monotonic relationship between confidence and accuracy |
| Regime classification | ≥8 of 10 known historical regime periods correctly classified |
| Output schema | Validates against Layer 6 contract for all signal-eligible instruments |
| Null propagation | Missing pillar degrades confidence, signal still serves |

---

#### Phase 4: Signal Generation — ML
**Scope:** Five ML models, walk-forward training, calibration, shadow deployment.

Steps:
1. Build training infrastructure (walk-forward generator, LightGBM wrapper, evaluation framework, model registry)
2. Generate 14-day volume-adjusted labels (PIT-correct — labels only in training pipeline, never served)
3. Train domain models: M-Macro → M-Derivatives → M-Flows → M-DeFi → M-Synthesis
4. Apply isotonic calibration to each model
5. Evaluate all five graduation criteria per model
6. Deploy to shadow mode (minimum 30 days)
7. Evaluate shadow period — compare to EDSx, verify stability

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| All 5 models trained | Walk-forward training complete, OOS ≥12 months |
| Graduation criteria | All 5 models pass all 5 criteria on OOS data |
| Calibration | ECE < 0.05 for all models |
| Shadow period | ≥30 days without infrastructure failures |
| Shadow accuracy | Consistent with OOS evaluation (no cliff) |
| Feature importance | No single feature > 40% in any model |
| M-Synthesis | Valid probability distributions for all signal-eligible instruments each shadow run |

---

#### Phase 5: Signal Synthesis and Serving
**Scope:** Synthesis layer, API, delivery mechanisms.

Steps:
1. Implement synthesis logic (agreement check, confidence-weighted combination, magnitude, regime, null handling)
2. Verify synthesis under all three scenarios (agree, disagree, pillar missing)
3. Build FastAPI serving layer (signals endpoints, metrics endpoint, auth, redistribution filter, rate limiting)
4. Build push delivery (webhook, Telegram)
5. Full end-to-end provenance trace: signal → feature values → metric observations → collection event → source

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Synthesis output | Matches Layer 6 contract for all signal-eligible instruments |
| Agreement/disagreement | Both scenarios produce correct confidence adjustments |
| Magnitude | Non-null for all signal-eligible instruments |
| API endpoints | All endpoints return correct responses |
| Redistribution filter | Query that would return SoSoValue data returns empty or excluded data |
| Provenance | Full trace verified for BTC and ≥2 other instruments |
| Delivery | Webhook and Telegram verified against test endpoints |
| Staleness flag | Simulated source failure triggers flag within 2 collection cycles |

---

#### Phase 6: Productization
**Scope:** Health monitoring, methodology docs, ToS audit, first customer.

Steps:
1. Health monitoring dashboards (collection, coverage, signal, infrastructure health)
2. Methodology documentation (metric catalog, EDSx methodology, ML methodology, data quality policy)
3. ToS audit completion for all sources
4. First customer delivery (direct engagement, real pricing, no free trials)

**Gate (before first customer delivery):**

| Criterion | Required |
|---|---|
| Health monitoring | Any collection failure diagnosable within 15 minutes |
| Methodology docs | EDSx methodology and metric catalog complete |
| ToS audit | All sources audited, restrictions enforced in API |
| API key auth | Tier enforcement working |
| Redistribution filter | Verified in production |

### Timeline Estimates (Single Operator)

| Phase | Estimate | Primary risk |
|---|---|---|
| Phase 0 | 3–5 days | Schema defects during DDL application |
| Phase 1 | 2–3 weeks | Migration adapter bugs, DeFiLlama backfill rate limits |
| Phase 2 | 2–3 weeks | PIT violations in audit, rolling window edge cases |
| Phase 3 | 1–2 weeks | Rule calibration, regime edge cases |
| Phase 4 | 3–4 weeks | Graduation criteria not met on first pass |
| Phase 5 | 1–2 weeks | API integration, webhook reliability |
| Phase 6 | 1–2 weeks | ToS audit findings |
| **Total** | **10–17 weeks** | Migration data quality is the primary variance driver |

### Parallel Operation Plan

There is no parallel operation. Forge is dead. Forge database retained read-only for 90 days as a data safety net only. New system does not consult it.

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Migration discovers data quality issues beyond the audit | Medium | High | Budget 1 extra week Phase 1. Re-backfill from source rather than debug corrupt data. |
| Coinalyze ToS fails for redistribution | Medium | Medium | Internal signal unaffected. Data API excludes derivatives until replacement source found. |
| ML models fail graduation on first training | Medium | Medium | Shadow period extended. EDSx serves as production signal. Customers receive EDSx-only confidence values. |
| Source API breaking change | Low | Medium | Staleness propagates honestly. Adapter fix scoped to Layer 2. |
| TimescaleDB performance insufficient | Low | Medium | DuckDB analytical layer already built in. |
| Schema defect after Phase 0 gate | Low | Very High | Phase 0 gate is the primary mitigation — PIT query test, index verification, constraint tests. |
| First customer dissatisfied with quality | Low | Medium | Both EDSx and ML graduation criteria are conservative. Methodology docs set expectations. |

### Decisions Locked

| Decision | Outcome |
|---|---|
| Build sequence | Schema → Data → Features → EDSx → ML → Serving |
| Phase gate model | Hard pass/fail. No phase begins until previous gate passes. |
| Migration order | Tiingo first (spot price dependency), then remaining |
| Parallel operation | None. Forge read-only 90 days. |
| ML shadow period | Minimum 30 days. Extension if shadow evaluation fails. |
| First customer | Phase 6 completion. Direct engagement. Real pricing. No free trials. |
| ToS audit timing | Phase 6, before any external data product ships. |
| Schema defects | Fixed before Phase 1 begins. No schema changes after Phase 0 gate. |

---

## SUCCESS CRITERIA

The design is correctly implemented when:

1. **Revenue to source traceability:** Any customer-facing output can be traced back through signals → features → metrics → raw data → source. Every link is a defined contract.

2. **Schema immutability:** Adding a new source, metric, instrument, or asset class requires zero schema changes — only catalog entries and adapters.

3. **Audit completeness:** Every value has: source, collection timestamp, ingestion timestamp, validation status, and revision history.

4. **Consumer ignorance:** Nothing above Layer 2 knows where data came from. Changing from Coinalyze to CoinGlass changes one adapter. Nothing else moves.

5. **Validation is structural:** Out-of-range values are rejected at ingestion, not discovered months later by audit scripts.

6. **Build is phased and gated:** Each phase has explicit deliverables and hard pass/fail criteria.

7. **One operator viability:** The system is operationally simple enough for one person to run, debug, and extend.

8. **No unnamed gaps:** Every known data gap has a documented plan.

---

## KNOWN GAPS WITH DOCUMENTED PLANS

| Gap | Plan | Trigger for resolution |
|---|---|---|
| Lending utilization proxy vs. actual | Proxy in v1. Full metric (Aave/Compound subgraph) in v1.1. | v1.1 milestone |
| Options data (Deribit) | Null-propagate in v1. Add Deribit adapter in v1.1. | v1.1 milestone |
| Exchange flows coverage beyond 18 instruments | Accept for v1. Expand via additional on-chain sources in v1.1. | v1.1 milestone |
| CoinMetrics redistribution | Internal use only until ToS audit. Phase 6 audit resolves. | Phase 6 ToS audit |
| SoSoValue redistribution | Non-commercial confirmed. Paid tier evaluation or source replacement in v2. | v2 data product launch |
| Index/benchmark licensing | Deferred to v2. Trigger: methodology documented + ToS audited for all constituent sources. | v2 revenue milestone |

---

*Design session completed: 2026-03-04*
*Next action: Phase 0 — provision database, apply DDL, seed catalogs, verify gates.*
