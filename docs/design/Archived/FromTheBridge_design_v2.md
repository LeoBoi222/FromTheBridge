# FromTheBridge — Complete System Design
## Empire Architecture v2.0

**Date:** 2026-03-05
**Synthesized:** 2026-03-06
**Type:** Canonical synthesis — standalone, all layers, all threads
**Owner:** Stephen (architect, sole operator)
**Status:** Authoritative. All locked decisions require architect approval to reopen.

> This is the single reference document for the FromTheBridge data platform. It
> synthesizes thread_1 through thread_7 and thread_infrastructure into one document.
> Every decision in every build session must trace to a section here.
> If a build requirement cannot be traced here, stop and surface it to the architect.

> **Conflict resolutions applied:**
> `defi.lending.utilization_rate` is the canonical metric name throughout. The v1
> computation is a proxy (borrow/supply TVL ratio); this is documented in the
> methodology field of the metric catalog entry, not in the name. Thread_5's source
> gap analysis reference to `utilization_proxy` is superseded.
> Dagster asset count at Phase 1 launch = 53 (Option B, per-instrument partitioning).
> ADR-005's ~200 estimate applies at full buildout. Both figures are correct in context.
> Phase 1 hard gate uses `observations_written` (matching collection_events DDL).

---

## DESIGN PHILOSOPHY

### Top-Down, Consumer-First

The system is designed from revenue downward to sources. Each layer exists only
because the layer above it requires specific inputs. The conceptual framing:

```
Layer 7: Revenue Streams       — What customers pay for
Layer 6: Output Products        — Concrete deliverables (API, signals, delivery)
Layer 5: Signal Generation      — EDSx + ML signal tracks, synthesis
Layer 4: Feature Engineering    — Computed features, transforms, aggregations
Layer 3: Data Universe          — Canonical, auditable, normalized data store
Layer 2: Normalization          — Source adapters, cleaning, validation
Layer 1: Raw Collection         — Agents writing to landing zone (Bronze/Iceberg)
Layer 0: Sources                — External APIs, bulk files, WebSockets
```

The infrastructure implementation uses a 9-layer stack (Layer 0 through Layer 8)
with specific technology assignments per layer. The conceptual layers above map to
multiple infrastructure layers. Full infrastructure layer specification is in the
Infrastructure section of this document.

### No-Drift Principles

1. **Schema is immutable once Phase 0 gate passes.** Adding a new source or metric
   adds catalog entries, not columns or tables.
2. **Every layer has a contract.** Input format, output format, error handling, null
   handling — specified before build.
3. **Adapters absorb source variance.** Nothing above Layer 2 ever sees
   source-specific structure.
4. **Validation is structural.** Every value is checked against its metric definition
   at ingestion. Out-of-range, wrong type, wrong cadence — rejected with audit trail.
5. **No build without complete spec.** Each phase is reviewed and approved before
   implementation begins.

### Forge Is Dead

The prior bottom-up architecture (Forge) is superseded entirely. Forge tables are not
authoritative. Forge data is a migration candidate evaluated against the adapter
contract. If Forge data can be cleanly normalized to canonical schema, it migrates.
If not, it is re-backfilled from the original source. No component of the new system
consults Forge tables for anything other than migration input.

---

## ARCHITECTURE OVERVIEW — INFRASTRUCTURE LAYER STACK

Nine layers. Data flows downward only. No layer reads a layer above itself.

```
Layer 8: Serving
  Decoupled API process. DuckDB reads Gold + Marts.
  Arrow Flight (bulk timeseries), REST JSON (signals).
  /v1/signals, /v1/timeseries, webhooks, Telegram.
  Never reads ClickHouse or PostgreSQL directly.
  Phase 6 scope — not built until all prior layers verified.

Layer 7: Catalog
  PostgreSQL. Relational integrity only.
  metric_catalog, source_catalog, instruments, assets,
  asset_aliases, venues, metric_lineage, event_calendar,
  supply_events, adjustment_factors.
  No time series data here — ever.

Layer 6: Marts
  Feature Store. dbt (SQL transforms) + Python
  (rolling window, cross-sectional features).
  forge_compute lives here. PIT enforced.
  Feature catalog entry required before compute.

Layer 5: Gold
  Analytical Layer. Iceberg tables on MinIO.
  DuckDB reads here. Feature compute reads here ONLY.
  Never reads ClickHouse directly — hard rule.
  Populated by incremental export from Silver every 6h.

Layer 4: Silver
  Observation Store. ClickHouse.
  EAV: (metric_id, instrument_id, observed_at, value).
  ReplacingMergeTree. Bitemporal: observed_at + ingested_at.
  dead_letter table here. current_values materialized view.
  Write-only except for the 6h export job.

Layer 3: Bronze
  Raw Landing. Apache Iceberg tables on MinIO.
  Partitioned by (source_id, date, metric_id).
  ACID, schema evolution, time travel native.
  Append-only, 90-day retention. Raw payload preserved.
  Great Expectations validation at Bronze → Silver boundary.

Layer 2: Adapters
  Per-source. 10-responsibility contract.
  Auth, rate limiting, pagination, schema normalization,
  timestamp normalization, unit normalization, validation,
  extreme value handling, idempotency, observability.

Layer 1: Orchestration
  Dagster (dedicated Docker service).
  Software-Defined Assets. One asset per (metric_id, source_id).
  Asset graph mirrors metric_catalog + metric_lineage.
  Freshness from cadence_hours. Retry, backoff, alerting
  as framework primitives. BLC-01: file-sensor trigger.

Layer 0: Sources
  Coinalyze, DeFiLlama, FRED, Tiingo, SoSoValue,
  Etherscan/Explorer, CoinPaprika, BGeometrics, CoinMetrics,
  Binance (BLC-01). 10 sources at v1.
```

---

## THREE HARD RULES

### Rule 1: Layer boundary is a one-way gate

Data flows down only. Nothing reads a layer above its own. Feature compute reads
Gold (Layer 5), never Silver (Layer 4). Serving reads Marts (Layer 6) via DuckDB.
No exceptions.

**Enforcement:** Dagster asset dependency graph. A cycle-detecting violation fails
at pipeline definition time. Credential isolation prevents workarounds at runtime.

### Rule 2: ClickHouse is write-only except for the export job

The only process that reads ClickHouse is the Dagster Software-Defined Asset that
runs the incremental Silver → Gold export every 6h. All analytical workloads go
through Gold (Iceberg on MinIO, read by DuckDB).

**Enforcement:** ClickHouse credentials issued exclusively to the export asset's
environment. No other Docker service has a ClickHouse connection string. A direct
read from an unauthorized service fails at authentication. ClickHouse query log
records all connections — unexpected client IPs are immediately visible.

**Why this matters:** DuckDB reads Gold, which is the merged, consistent Iceberg
snapshot. ClickHouse `ReplacingMergeTree` deduplication is eventual — unmerged
rows exist before OPTIMIZE runs. Analytical workloads against Silver would produce
silently incorrect results on unmerged data.

### Rule 3: PostgreSQL holds no time series data

The catalog layer holds relational integrity only. No `observed_at + value` columns
exist in any PostgreSQL table. No metric observations, no derived computations, no
feature values.

**Enforcement check:**
```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'forge'
  AND column_name IN ('observed_at','value','value_numeric','ingested_at')
  AND table_name NOT IN ('observations','dead_letter');
-- Must return zero rows
```

---

## TECHNOLOGY DECISIONS SUMMARY

Seven Architecture Decision Records. All locked. Full ADRs in the Infrastructure
section of this document.

| ADR | Component | Decision | Migration path |\
|-----|-----------|----------|----------------|\
| ADR-001 | Silver (observation store) | ClickHouse, ReplacingMergeTree | ClickHouse Cloud — connection string only |\
| ADR-002 | Bronze + Gold storage format | Apache Iceberg on MinIO | S3 — endpoint config swap only |\
| ADR-003 | Object storage | MinIO self-hosted | AWS S3 — endpoint config swap only |\
| ADR-004 | Analytical engine | DuckDB embedded | Trino/Spark at terabyte scale |\
| ADR-005 | Orchestration | Dagster Docker service, SDA | Dagster Cloud — asset code portable |\
| ADR-006 | Marts / Feature Store | dbt (SQL) + forge_compute (Python) | No direct cloud equivalent |\
| ADR-007 | Catalog | PostgreSQL | AWS RDS — connection string only |\

All four managed migrations (MinIO→S3, ClickHouse→Cloud, Dagster→Cloud,
PostgreSQL→RDS) require zero application code changes. Trigger conditions defined
in the Infrastructure section.

---

## THREAD 1: REVENUE & PRODUCT DEFINITION

### Revenue Architecture

Three economically distinct positions exist in market data:

**Position 1 — Infrastructure provider:** Clean, normalized, auditable data.
Customers are builders. Layer 3 is the product surface. Competitors: Glassnode,
Coin Metrics, Kaiko.

**Position 2 — Intelligence provider:** Interpretation and signals. Customers are
decision-makers. Layer 5 is the product surface. Competitors: research desks,
quant shops.

**Position 3 — Workflow product:** Complete tool — dashboard, alerts, briefs.
Customers are operators. The UI is the product surface.

**FromTheBridge occupies Position 2 with Position 1 as the structural foundation.**
The existing EDSx + ML architecture, pillar structure, and prediction targets all
point to an intelligence product. The data layer is internal infrastructure that
becomes an additional revenue surface, not the primary product.

### Revenue Streams (Multi-Stream from Day One)

**Stream 1 — Direct subscriptions (tiered)**
Prosumer to institutional tiers. Dashboard + API access gated by feature depth and
history. Lowest friction to start.

**Stream 2 — API data licensing (B2B)**
Counterparties pay for normalized, auditable, attributed data to power their own
products. They do not use the interface — they build on the data layer. Customers:
exchanges, funds, fintech builders, index providers. Higher ACV, fewer customers,
relationship-driven. Layer 3 is the product surface for this stream.

**Stream 3 — Protocol / ecosystem reporting (sponsored)**
A protocol (Solana Foundation, Uniswap Labs, etc.) pays for ongoing data-driven
reports on their ecosystem. Recurring B2B revenue with no subscriber dependency.
Maps naturally to DeFi data coverage. Underexploited by competition.

**Stream 4 — Index / benchmark licensing**
Rules-based index constructed from the canonical data, licensed to financial product
issuers for settlement benchmarks. Deferred to v2 — requires methodology
documentation and legal structure, but the data infrastructure is already the hard
part.

**Stream 5 — Embedded analytics (white-label)**
Signal engine or data feeds embedded in a fund or exchange's own interface. Sticky,
large contracts, longer sales cycles.

### Content Originality

The differentiator is not qualitative research dressed up with charts. It is:
**systematic, backtested, quantitative signals with documented methodology and
PIT-correct historical data.** This is what Glassnode, Kaiko, and Messari do not
produce. EDSx's deterministic scoring with full audit trail, combined with calibrated
ML probabilities, is a genuine differentiation in a market full of opinion-based
research.

### Coverage Framing

Coverage is expressed as domain breadth, not ticker count. The product is
"derivatives + flows + DeFi + macro intelligence across the instruments those domains
cover" — not "BTC, ETH, SOL signals." The instrument universe emerges from data
completeness thresholds in Phase 1. Coinalyze alone covers 121 instruments on
derivatives. Coverage is substantially broader than 3 assets from day one.

### MVP Definition

**MVP = one signal report, delivered on schedule, to one paying customer, that they
trust enough to act on.**

No billing infrastructure. No self-serve onboarding. No dashboard. The signal, on
time, defensibly produced, to a small number of institutional early-access customers
paying real money.

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
| Index licensing | v2 — deferred. Trigger: methodology documented + ToS audited |

---

## THREAD 2: SIGNAL ARCHITECTURE (Layer 5)

### Architecture Overview

Two independent signal generation tracks share a common data foundation and feature
layer. They never cross-contaminate from Layer 1 onward.

```
                     Shared Foundation
        Layer 0 Sources → Layer 3 Data Universe → Layer 4 Features
                              ↓               ↓
              EDSx Track                   ML Track
        5 Pillar Scores × 3 Horizons   5 Domain Model Outputs
                              ↓               ↓
                         Layer 2 Synthesis (future design session)
                              ↓
                    Customer-facing signal output
```

**Regime engine** sits alongside both tracks — it is not a pillar and does not score
instruments. It classifies market-wide state and drives composite weight selection.

**EDSx confidence** = data completeness (signals_computed / signals_available). Not
prediction confidence.

**Synthesis default** = 0.5 / 0.5 EDSx / ML weight, recalibrated quarterly against
outcomes.

### Regime Engine

**Current production (legacy):** M2-only regime classifier. Known to be limited.
Retained until H2 target is validated.

**H2 target:** 2-axis, 4-quadrant model (Volatility-Liquidity Anchor). Architecturally
separate from the five alpha pillars. Does not score instruments — classifies
market-wide state and drives composite weight selection.

**Four quadrants:**
- **Full Offense** — low volatility, high liquidity. Maximum weight on Trend/Structure
  and Liquidity/Flow.
- **Selective Offense** — moderate conditions. Balanced weight profile.
- **Defensive Drift** — elevated volatility or deteriorating liquidity. Increased
  weight on Structural Risk.
- **Capital Preservation** — high volatility, low liquidity. Structural Risk receives
  up to ~45% weight. Alpha pillars suppressed.

**ML POC track:** Experimental. HMM for regime classification + LightGBM for
transition probability estimation. Six regime states (empirically determined via
BIC/AIC). Does not influence production until it demonstrates > 5pp accuracy
improvement over rule-based baseline (UNI-01 blocked).

**Consumers:** EDSx composite weights · W6 portfolio profiles · Signal Gate 1 ·
Content engine regime alerts · CAA regime transitions endpoint.

### Track A: EDSx (Deterministic Scoring)

Rule-based, fully transparent. Given feature values at a point in time, produces
directional score and confidence for each pillar. No training. Fully reproducible
from methodology documentation alone.

**Why EDSx exists alongside ML:** Institutional customers need to interrogate signals.
"Why is this bullish?" must have an answer that does not require understanding a
gradient boosted tree. EDSx provides that. It also provides a baseline — if ML
consistently underperforms EDSx, the ML pipeline has a problem.

#### Five Pillars

**Pillar 1: Trend / Structure** (`pillar_id = "trend_structure"`)
Price action, momentum, and market structure analysis. Describes the current state
of price structure and the momentum regime. Does not predict — prediction comes from
composite synthesis. Most horizon-sensitive pillar: 1D bullish, 30D bearish is a
real and common configuration.
*Status: LIVE (EDSx-02)*

**Pillar 2: Liquidity & Flow** (`pillar_id = "liquidity_flow"`)
Derivatives positioning (funding, OI, liquidations), exchange flow direction,
stablecoin supply dynamics, ETF flow direction. The "money in motion" pillar.
*Status: LIVE (EDSx-03 R3, shadow mode on rebuild track)*

**Pillar 3: Valuation** (`pillar_id = "valuation"`)
On-chain valuation ratios (NVT, MVRV, realized price multiples), relative value
across instruments, mean-reversion signals. Provides the gravitational anchor.
Identifies what to buy/sell, not when — timing comes from other pillars.
*Status: PLANNED (REM-21)*

**Pillar 4: Structural Risk** (`pillar_id = "structural_risk"`)
Volatility regime classification, correlation clustering, tail risk, liquidation
cascade proximity, drawdown velocity. Measures risk landscape — not direction, but
damage potential.
**Architectural privilege:** When Structural Risk score crosses the 80th percentile,
its effective weight scales up to 2× base weight, stealing proportionally from other
pillars. This prevents aggressive bullish signals during volatility spikes even
without a regime transition.
*Status: PLANNED (REM-24)*

**Pillar 5: Tactical Macro** (`pillar_id = "tactical_macro"`)
DXY movements, real rate changes, credit spread dynamics, risk appetite proxies,
cross-asset flows. Captures external forces from traditional finance. This is the
**tactical** layer — alpha signals from macro data. Structural macro classification
(regime engine) is separate by design. Hard prerequisite: FRG-10 (FRED migration).
*Status: PLANNED (REM-22/23)*

**Important boundary:** Structural macro classification (regime type) belongs to the
regime engine. Tactical Macro captures alpha signals within whatever regime is active.
The old EDS macro pillar tried to do both — that mistake is not repeated.

#### Pillar Weights by Regime State

| Pillar | Full Offense | Selective Offense | Defensive Drift | Capital Preservation |
|---|---|---|---|---|
| Trend/Structure | 0.35 | 0.30 | 0.20 | 0.10 |
| Liquidity/Flow | 0.30 | 0.30 | 0.25 | 0.15 |
| Valuation | 0.15 | 0.20 | 0.20 | 0.15 |
| Structural Risk | 0.10 | 0.10 | 0.25 | 0.45 |
| Tactical Macro | 0.10 | 0.10 | 0.10 | 0.15 |

*Note: Starting parameters, exposed in config, tunable per evaluation profile. Structural
Risk modulation (2× privilege) applies on top of these weights.*

#### EDSx Framework (v2.2)

All five pillars conform to the three-layer standard. The framework is locked —
changes require architect review.

**Three layers per pillar:**
- **Layer 1 — Signal computation:** Raw metric values → scored signals
  ([0,1] per signal)
- **Layer 2 — Aggregation:** Weighted combination → PillarScore
  (per instrument, per horizon)
- **Layer 3 — Guardrails (G1/G2/G3):** Confidence gates applied using
  `confidence_base`

**Three horizons:** 1D · 7D · 30D. All 15 cells (5 pillars × 3 horizons) are active.
Empirical pruning after 90-day shadow measurement, not before.

**Output schema (PillarScore):**
```
PillarScore {
    instrument_id, pillar_id, horizon, as_of, track
    score             # [0, 1]
    direction         # "bullish" | "neutral" | "bearish"
    confidence_base   # signals_computed / signals_available
    null_state        # None | "INSUFFICIENT_HISTORY" | "SOURCE_STALE" |
                      #        "METRIC_UNAVAILABLE"
    freshness_seconds # age of oldest contributing observation
}
```

**Composite formation:**
```
composite_score = Σ(pillar_score × regime_adjusted_weight)
direction = "bullish" if composite_score > threshold_high
direction = "bearish" if composite_score < threshold_low
direction = "neutral" otherwise
```
Thresholds calibrated by F1 maximization against backtested history at each horizon.

**Null handling:** Pillar below completeness threshold → zero weight contribution,
null in pillar block. Signal still serves. Confidence recalculated on available
pillars. The system never crashes on missing data — it degrades honestly.

### Track B: ML Models (Layer 1 Domain Models)

Five LightGBM classifiers. All consume Layer 4 features from the shared feature
store. No ML output feeds back into EDSx. No EDSx output feeds into ML training.

#### Model 1: Derivatives Pressure

**Objective:** Estimate directional pressure from leveraged positioning.
**Granularity:** Per-instrument (121 Coinalyze instruments) + market-level aggregate.
**Input dimension:** ~60–80 features per instrument.
**Output:** `p_bullish`, `p_neutral`, `p_bearish` (sum = 1.0) + `pressure_magnitude`
[0,1] + `feature_coverage` + `prediction_entropy` + `top_features` (SHAP top 5)

#### Model 2: Capital Flow Direction

**Objective:** Estimate net capital movement direction and intensity.
**Granularity:** Per-instrument where exchange flow data exists (18 instruments) +
market-level proxy for all others. Two-mode operation: `has_exchange_flow` flag
signals which mode is active.
**Input dimension:** ~40–60 features.
**Output:** `p_inflow`, `p_neutral`, `p_outflow` (sum = 1.0) + `flow_magnitude` [0,1]
+ `has_exchange_flow`, `has_etf_flow` (coverage flags for Layer 2 weighting)

#### Model 3: Macro Regime

**Objective:** Classify macro-financial environment and estimate regime transition
probabilities.
**Granularity:** Market-level only. One output applies to all instruments.
**Input dimension:** ~80–100 features. Mixed-frequency inputs (daily/weekly/monthly/
quarterly) handled via carry-forward with staleness indicator.
**Regime states (6):** `risk_on_expansion` · `risk_on_tightening` · `risk_off_orderly`
· `risk_off_crisis` · `transitional` · `recovery` (count TBD via BIC/AIC comparison)
**Model type candidate:** HMM for regime classification + LightGBM for transition
probability estimation.
**Cold-start advantage:** FRED provides decades of history — this model may be the
first trained.

#### Model 4: DeFi Stress

**Objective:** Detect systemic DeFi stress conditions with cascade risk early warning.
**Granularity:** Market-level stress indicator + per-protocol health scores.
**Input dimension:** ~50–70 features.
**Archetypal training examples:** Terra/Luna and FTX cascade events.
**Output:** `p_normal`, `p_elevated`, `p_critical` (sum = 1.0) + `peg_stress`,
`liquidity_stress`, `activity_stress` decomposition [0,1] + `distressed_protocols`
**Model type candidate:** Isolation Forest / One-Class SVM as anomaly baseline +
LightGBM for labeled stress classification. Semi-supervised given scarcity of true
stress events.

#### Model 5: Volatility Regime

**Objective:** Classify volatility environment and estimate expected magnitude.
**Granularity:** Per-instrument + market-level.
**Input dimension:** ~60–80 features. Most cross-domain model — consumes derivatives
(liquidation patterns), macro (VIX), and price data.
**Regime states (4):** `compressed` · `normal` · `elevated` · `extreme`
**Output:** `regime`, `regime_probabilities` + `expected_daily_vol` (annualized) +
`vol_direction` [-1,1] + `vol_persistence` + `tail_risk` [0,1] + `asymmetry` [-1,1]
**Model type candidate:** LightGBM for regime + quantile regression for magnitude
(10th/50th/90th percentile).

**Note on Layer 2 synthesis:** The Volatility Regime model output conditions how
Layer 2 interprets the other four models — a bullish Derivatives Pressure signal
during compressed volatility has different implications than during extreme volatility.
Layer 2 synthesis design is a future session.

#### Training Protocol

- Walk-forward only. No random splits. No holdout from the end only.
- **Labels:** Volume-adjusted 14-day forward returns, discretized to tercile
  boundaries. Boundaries computed on each training window independently (not global
  constants).
- **Calibration:** Isotonic regression applied post-training on held-out validation
  fold.
- **Minimum OOS period:** 12 months before graduation consideration.
- **GPU:** RTX 3090 (24GB VRAM) on proxmox.

#### Graduation Criteria (all five must pass per model)

1. Walk-forward Sharpe of directional calls > 0.5 over minimum 12 months OOS
2. Calibration error (ECE) < 0.05
3. Directional accuracy > 55% on OOS data
4. Feature importance stable across ≥ 3 training cycles (no single feature > 40%
   importance)
5. Manual architect review before each production deployment

**Shadow mode:** All models run in production infrastructure writing to shadow tables,
not live output. Minimum 30-day shadow period. Graduation from shadow requires
passing all five criteria on shadow period data plus historical OOS.

### Signal Synthesis (Layer 2 — Future Design Session)

Layer 2 is explicitly deferred to a separate design session. This section defines
what Layer 2 receives and what it must produce. Layer 2 implementation begins
only after Phase 4 (ML shadow period passes).

**Layer 2 receives:**
- 5 EDSx PillarScores × 3 horizons per instrument
- 5 ML domain model outputs per instrument
- Regime engine state

**Layer 2 must produce (per instrument, per horizon):**
```json
{
  "instrument": "BTC",
  "timestamp": "2026-03-05T12:00:00Z",
  "signal": {
    "direction": "bullish",
    "confidence": 0.73,
    "confidence_tier": "high",
    "magnitude": 0.45,
    "horizon": "14d",
    "regime": "risk_on"
  },
  "edsx": {
    "direction": "bullish",
    "confidence": 0.71,
    "pillars_computed": 4,
    "pillars_available": 5
  },
  "ml": {
    "direction": "bullish",
    "probability": 0.76,
    "calibrated": true
  },
  "staleness_flag": false
}
```

**`confidence_tier`** is a human-readable label: `low` (< 0.40), `moderate`
(0.40–0.59), `high` (0.60–0.79), `very_high` (≥ 0.80). Always present alongside
the float.

**Synthesis principles (locked, implementation deferred):**
- EDSx/ML weight default: 0.5 / 0.5, recalibrated quarterly
- Agreement between tracks → confidence boost
- Disagreement → confidence penalty + flag in output
- Magnitude from ML track only (EDSx does not produce magnitude)
- Regime state drives EDSx pillar weight selection
- Null propagation: missing pillar → zero weight contribution, honest confidence
  degradation, signal still serves

### Decisions Locked

| Decision | Outcome |
|---|---|
| Track architecture | Two independent tracks (EDSx + ML), shared data and features |
| EDSx confidence | Data completeness (signals_computed / signals_available) |
| EDSx pillar count | Five: trend_structure, liquidity_flow, valuation, structural_risk, tactical_macro |
| ML model count | Five domain models: Derivatives Pressure, Capital Flow Direction, Macro Regime, DeFi Stress, Volatility Regime |
| ML algorithm | LightGBM + isotonic calibration |
| Prediction horizon | 14 days, volume-adjusted labels |
| Label discretization | Tercile boundaries on training set, recalculated each cycle |
| Synthesis | Confidence-weighted, not simple average |
| Regime engine | Separate classifier, market-level. H2 = Volatility-Liquidity Anchor (4 quadrants) |
| Graduation | Five hard criteria per model, no self-certification |
| Instrument coverage | Data completeness driven, not manually selected |
| Magnitude | ML track only |
| Layer 2 synthesis design | Future session — begins after Phase 4 shadow period passes |

---

## THREAD 3: FEATURE ENGINEERING (Layer 4)

### Design Principles

- Features are transformations, not storage. Recomputable from canonical store at
  any time.
- PIT is absolute. Feature at T uses only observations with `ingested_at ≤ T`.
- Null handling is typed. Three states: `INSUFFICIENT_HISTORY`, `SOURCE_STALE`,
  `METRIC_UNAVAILABLE`. Not interchangeable.
- Features are versioned. Formula changes create new version entries. Models declare
  which version they were trained on.
- Computation is event-triggered on metric ingestion, not wall-clock scheduled.
- Computation is idempotent. Same inputs always produce same outputs.

### Null State Definitions

| State | Meaning |
|---|---|
| `INSUFFICIENT_HISTORY` | Window requires N observations, fewer than N exist |
| `SOURCE_STALE` | Most recent observation older than 2× expected cadence |
| `METRIC_UNAVAILABLE` | Metric is not tracked for this instrument |

### Computation Order (within each cadence trigger)

**A → C → B → F → G → D → E**

| Category | Name | Dependencies |
|---|---|---|
| A | Rolling Statistical Transforms | None — no cross-instrument dependencies |
| C | Ratio / Interaction Features | Depends on A |
| B | Cross-Sectional Ranks | Requires all instruments' A values at timestamp |
| F | Breadth and Market Aggregations | Requires all instruments' A + B values |
| G | Cross-Asset Features | Requires specific instruments' A values |
| D | Regime / State Labels | Requires A–C current values |
| E | Calendar Features | Independent, always computable |

### Category A: Rolling Statistical Transforms

**Statistic types:** `value` · `change_pct` · `zscore` · `percentile_rank` · `ma` ·
`ema` · `cumsum` · `min` · `max` · `range`

**Window sizes:** 7d · 14d · 30d · 90d · 365d (calendar days, not observation counts)

**Staleness threshold:** observation older than 2× expected cadence → `SOURCE_STALE`

**Derivatives features (per instrument, 8h cadence):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Funding rate raw | `derivatives.perpetual.funding_rate` | value | 1 period |
| Funding rate zscore | `derivatives.perpetual.funding_rate` | zscore | 30d |
| Funding rate zscore | `derivatives.perpetual.funding_rate` | zscore | 90d |
| Funding rate MA | `derivatives.perpetual.funding_rate` | ma | 7d |
| Funding rate MA | `derivatives.perpetual.funding_rate` | ma | 30d |
| OI USD raw | `derivatives.perpetual.open_interest_usd` | value | 1 period |
| OI change | `derivatives.perpetual.open_interest_usd` | change_pct | 1d |
| OI change | `derivatives.perpetual.open_interest_usd` | change_pct | 7d |
| OI zscore | `derivatives.perpetual.open_interest_usd` | zscore | 30d |
| OI zscore | `derivatives.perpetual.open_interest_usd` | zscore | 90d |
| Long liquidations 24h | `derivatives.perpetual.liquidations_long_usd` | cumsum | 24h |
| Short liquidations 24h | `derivatives.perpetual.liquidations_short_usd` | cumsum | 24h |
| Net liquidation direction | derived (long - short) | value | 1 period |
| Liquidation imbalance | derived (net / total, signed) | value | 1 period |
| Liquidation zscore | derived net_liquidation | zscore | 30d |
| Perpetual basis | derived (perp_price - spot) / spot | value | 1 period |
| Perpetual basis MA | perp_basis | ma | 7d |
| Perpetual basis zscore | perp_basis | zscore | 30d |
| Options delta skew | `derivatives.options.delta_skew_25` | value | 1 period |
| Options skew zscore | `derivatives.options.delta_skew_25` | zscore | 30d |
| IV term structure slope | derived (iv_1m - iv_1w) / iv_1w | value | 1 period |

**Capital flows features (per instrument, 1d cadence):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Exchange net flow | `flows.exchange.net_flow_usd` | value | 1 period |
| Net flow MA | `flows.exchange.net_flow_usd` | ma | 7d |
| Net flow zscore | `flows.exchange.net_flow_usd` | zscore | 30d |
| Net flow cumulative | `flows.exchange.net_flow_usd` | cumsum | 7d |
| Net flow cumulative | `flows.exchange.net_flow_usd` | cumsum | 30d |
| Inflow zscore | `flows.exchange.inflow_usd` | zscore | 30d |
| Outflow zscore | `flows.exchange.outflow_usd` | zscore | 30d |
| Stablecoin supply change | `stablecoin.supply.total_usd` | change_pct | 1d |
| Stablecoin supply change | `stablecoin.supply.total_usd` | change_pct | 7d |
| Stablecoin supply zscore | `stablecoin.supply.total_usd` | zscore | 30d |
| ETF net flow | `etf.flows.net_flow_usd` | value | 1 period |
| ETF flow cumulative | `etf.flows.net_flow_usd` | cumsum | 7d |
| ETF flow cumulative | `etf.flows.net_flow_usd` | cumsum | 30d |
| On-chain transfer vol | `flows.onchain.transfer_volume_usd` | value | 1 period |
| On-chain transfer MA | `flows.onchain.transfer_volume_usd` | ma | 7d |
| On-chain transfer zscore | `flows.onchain.transfer_volume_usd` | zscore | 30d |

**DeFi health features (market-level, 1d cadence):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Total DeFi TVL | `defi.aggregate.tvl_usd` | value | 1 period |
| TVL change | `defi.aggregate.tvl_usd` | change_pct | 1d |
| TVL change | `defi.aggregate.tvl_usd` | change_pct | 7d |
| TVL zscore | `defi.aggregate.tvl_usd` | zscore | 30d |
| DEX volume | `defi.dex.volume_usd_24h` | value | 1 period |
| DEX volume MA | `defi.dex.volume_usd_24h` | ma | 7d |
| DEX volume MA | `defi.dex.volume_usd_24h` | ma | 30d |
| DEX volume zscore | `defi.dex.volume_usd_24h` | zscore | 30d |
| Lending utilization | `defi.lending.utilization_rate` | value | 1 period |
| Lending utilization zscore | `defi.lending.utilization_rate` | zscore | 30d |
| Stablecoin peg deviation | `stablecoin.peg.price_usd` | derived max_deviation | 1 period |
| Peg deviation MA | derived max_deviation | ma | 7d |
| Protocol TVL change top 20 | `defi.protocol.tvl_usd` | change_pct per protocol | 7d |

**Macro features (market-level, 1d cadence):**

| Feature Concept | Input Metric | Statistic | Window |
|---|---|---|---|
| Yield curve spread | `macro.rates.yield_10y` - `macro.rates.yield_2y` | value | 1 period |
| Yield curve trend | yield_curve_spread | ma | 30d |
| Yield curve zscore | yield_curve_spread | zscore | 365d |
| DXY raw | `macro.fx.dxy` | value | 1 period |
| DXY change | `macro.fx.dxy` | change_pct | 1d |
| DXY change | `macro.fx.dxy` | change_pct | 30d |
| DXY zscore | `macro.fx.dxy` | zscore | 90d |
| HY credit spread | `macro.credit.hy_oas` | value | 1 period |
| Credit spread change | `macro.credit.hy_oas` | change_pct | 7d |
| Credit spread zscore | `macro.credit.hy_oas` | zscore | 90d |
| Fed funds rate | `macro.rates.fed_funds_effective` | value | 1 period |
| Rate trend | `macro.rates.fed_funds_effective` | change_pct | 90d |

### Category B: Cross-Sectional Ranks

Value for one instrument ranked as percentile against all signal-eligible instruments
at the same timestamp. Historically stable — adding a new instrument does not
retroactively change historical ranks.

| Feature | Input | Universe |
|---|---|---|
| Funding rate rank | `derivatives.perpetual.funding_rate` | All signal-eligible instruments |
| OI change rank | oi_change_pct_1d | All signal-eligible instruments |
| Net flow rank | `flows.exchange.net_flow_usd` | All signal-eligible instruments |
| Liquidation imbalance rank | liquidation_imbalance | All signal-eligible instruments |

### Category C: Ratio and Interaction Features

| Feature | Formula | Cadence |
|---|---|---|
| OI-to-volume ratio | oi_usd / spot_volume_usd | 8h |
| OI-to-volume zscore | zscore(oi_to_volume, 30d) | 8h |
| Liquidation-to-OI ratio | (long_liq + short_liq) / oi_usd | 8h |
| Flow-to-OI ratio | exchange_net_flow_usd / oi_usd | 1d |
| Funding-to-basis spread | funding_rate - perp_basis | 8h |
| Stablecoin-to-DeFi ratio | stablecoin_supply_usd / defi_tvl_usd | 1d |
| ETF flow-to-OI ratio | etf_net_flow_usd / oi_usd | 1d |

### Category D: Regime and State Labels

| Feature | Computation | Output |
|---|---|---|
| Funding regime | Discretize funding_rate_zscore_30d: >1.5 = ELEVATED, <-1.5 = SUPPRESSED | Categorical (3) |
| OI regime | Discretize oi_zscore_30d: >1.5 = HIGH, <-1.5 = LOW, else NORMAL | Categorical (3) |
| Macro regime | Classify from yield curve + credit spread + DXY composite | Categorical (3) |
| Liquidation regime | Classify from liquidation_imbalance + liquidation_zscore | Categorical (3) |
| Market regime | RISK_ON / RISK_OFF / TRANSITION — from separate ML classifier | Categorical (3) |

Market regime is market-level. Same label applies to all instruments at a given
timestamp.

### Category E: Calendar Features

Day of week · Day of month · Days to options expiry · Days since/to Fed meeting ·
Funding period index (0/1/2) · Quarter

### Category F: Breadth and Market Aggregation

| Feature | Computation | Cadence |
|---|---|---|
| Market funding rate median | Median across all signal-eligible instruments | 8h |
| Market funding rate 90th pct | 90th percentile across universe | 8h |
| % instruments funding elevated | % where funding_rate_zscore_30d > 1.5 | 8h |
| % instruments funding suppressed | % where funding_rate_zscore_30d < -1.5 | 8h |
| % instruments OI increasing | % where oi_change_pct_1d > 0 | 8h |
| % instruments net outflow | % where exchange_net_flow_usd < 0 | 1d |
| Market liquidation imbalance | Sum(long_liq - short_liq) across universe, normalized | 8h |
| Breadth score | pct_funding_elevated×0.30 + pct_oi_increasing×0.30 + (1-pct_net_outflow)×0.25 + liq_imbalance_normalized×0.15 | 8h |

Breadth score is a deterministic formula with fixed weights. Not learned.

### Category G: Cross-Asset Features

| Feature | Computation | Cadence |
|---|---|---|
| BTC dominance | BTC_market_cap / total_crypto_market_cap | 1d |
| BTC-ETH correlation | Rolling 30d return correlation | 1d |
| Altcoin funding vs BTC | instrument_funding / BTC_funding | 8h |
| Altcoin OI vs BTC OI | instrument_oi / BTC_oi | 8h |
| BTC beta | Rolling 30d beta of instrument returns to BTC | 1d |

### Feature Catalog Entry Structure

Every feature has a catalog entry before it is computed. Immutable once locked.
Formula changes require a new version entry.

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
  pit_constraint: "uses only observations with ingested_at <= computation_timestamp"
  consuming_models: ["M-Derivatives", "EDSx-Pillar2"]
  status: "active"
```

### Data Requirements → Layer 3

**Derivatives (per instrument, 8h):** funding rate · OI in USD · long liquidations USD ·
short liquidations USD · perpetual price · options 25-delta skew (nullable) · IV 1w and
1m (nullable)

**Spot (per instrument, 1d):** close price USD · volume USD 24h · market cap USD

**Flows (per instrument, 1d):** exchange inflow USD · exchange outflow USD · on-chain
transfer volume USD

**Stablecoins (market-level + per asset, 1d):** total supply USD · per-asset supply USD ·
peg price USD

**ETF (per product, 1d):** net flow USD · AUM USD

**DeFi (market-level + per protocol, 1d):** aggregate TVL USD · protocol TVL USD · DEX
volume USD 24h · lending utilization rate

**Macro (market-level, 1d):** 10Y yield · 2Y yield · DXY · HY OAS · fed funds
effective rate

**Derived (market-level, 1d):** total crypto market cap USD · BTC dominance %

**History depth requirements:**
- Derivatives: 3yr minimum
- Macro: 10yr minimum
- Flows / on-chain: 3yr
- DeFi: 2yr
- ETF: from product inception

### Decisions Locked

| Decision | Outcome |
|---|---|
| Feature versioning | Versioned catalog entry. Formula changes = new version. |
| Null typing | Three distinct null states. Not interchangeable. |
| PIT constraint | Absolute. `ingested_at ≤ computation_timestamp`. No exceptions. |
| Computation trigger | Event-driven on metric ingestion, not wall-clock |
| Computation order | A → C → B → F → G → D → E |
| Idempotency | Hard requirement |
| Breadth score | Deterministic formula, fixed weights, not learned |
| Feature catalog | Required before any feature is computed. Immutable once locked. |
| forge_compute location | Layer 6 (Marts) |
| DuckDB reads | Layer 5 (Gold) — never Layer 4 (Silver) directly |

---

## THREAD 4: DATA UNIVERSE (Layer 3)

### Schema Model Decision

**EAV observations table with metric catalog, partitioned by time, with materialized
current-value views.**

Wide tables eliminated: adding a metric adds a column — violates schema immutability.
Hybrid typed tables eliminated: separate tables per domain leaks source structure
above the adapter layer. EAV selected: schema immutability, asset-class extensibility,
consumer ignorance, and full audit completeness — all satisfied simultaneously.

Performance addressed through ClickHouse columnar storage, ReplacingMergeTree engine,
composite ordering keys, and a materialized current-value view.

### Catalog DDL (PostgreSQL)

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
        CHECK (asset_class IN ('crypto','equity','commodity','forex','index','etf',
                               'defi_protocol')),
    CONSTRAINT instruments_collection_tier_valid
        CHECK (collection_tier IN ('collection','scoring','signal_eligible'))
);
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
        CHECK (domain IN ('derivatives','spot','flows','defi','macro','etf',
                          'stablecoin')),
    CONSTRAINT metrics_value_type_valid
        CHECK (value_type IN ('numeric','categorical','boolean')),
    CONSTRAINT metrics_granularity_valid
        CHECK (granularity IN ('per_instrument','per_protocol','per_product',
                               'market_level')),
    CONSTRAINT metrics_status_valid
        CHECK (status IN ('active','deprecated','planned'))
);
```

**Canonical name convention:** `domain.subdomain.metric_name`

Examples: `derivatives.perpetual.funding_rate` · `flows.exchange.net_flow_usd` ·
`macro.rates.yield_10y` · `defi.aggregate.tvl_usd`

Canonical names are immutable once assigned. To change a metric: deprecate the
existing entry, create a new entry with the new name.

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
        CHECK (tos_risk IN ('none','low','unaudited','restricted','prohibited')),
    CONSTRAINT sources_cost_tier_valid
        CHECK (cost_tier IN ('free','freemium','paid','enterprise'))
);
```

#### collection_events

```sql
CREATE TABLE collection_events (
    event_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id               UUID        NOT NULL REFERENCES sources (source_id),
    started_at              TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ,
    status                  TEXT        NOT NULL,
    observations_written    INTEGER,
    observations_rejected   INTEGER,
    metrics_covered         TEXT[],
    instruments_covered     TEXT[],
    error_detail            TEXT,
    metadata                JSONB       NOT NULL DEFAULT '{}',

    CONSTRAINT event_status_valid
        CHECK (status IN ('running','completed','failed','partial'))
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

### Observation Store DDL (ClickHouse)

#### forge.observations (Silver)

```sql
-- Engine: ReplacingMergeTree(data_version)
-- Ordered by: (metric_id, instrument_id, observed_at)
-- Partitioned by: toYYYYMM(observed_at)

CREATE TABLE forge.observations
(
    metric_id           String          NOT NULL,
    instrument_id       Nullable(String),           -- NULL for market-level metrics
    source_id           String          NOT NULL,
    observed_at         DateTime64(3)   NOT NULL,   -- When true in the world
    ingested_at         DateTime64(3)   NOT NULL,   -- When value entered this store
    value               Float64,
    data_version        UInt64          NOT NULL    -- Revision counter
)
ENGINE = ReplacingMergeTree(data_version)
ORDER BY (metric_id, instrument_id, observed_at)
PARTITION BY toYYYYMM(observed_at);
```

**`instrument_id` is nullable.** Market-level metrics (macro, DeFi aggregate,
stablecoin aggregate) have `instrument_id = NULL`. This is correct, not a defect.

#### forge.dead_letter (ClickHouse)

```sql
CREATE TABLE forge.dead_letter
(
    source_id           String          NOT NULL,
    metric_id           Nullable(String),
    instrument_id       Nullable(String),
    raw_payload         String          NOT NULL,
    rejection_reason    String          NOT NULL,
    rejection_code      LowCardinality(String) NOT NULL,
    -- Valid codes:
    -- RANGE_VIOLATION · TYPE_MISMATCH · NULL_VIOLATION · UNKNOWN_METRIC
    -- UNKNOWN_INSTRUMENT · DUPLICATE_OBSERVATION · STALE_OBSERVATION
    -- SCHEMA_ERROR · UNIT_UNKNOWN · EXTREME_VALUE_PENDING_REVIEW
    collected_at        DateTime64(3)   NOT NULL,
    rejected_at         DateTime64(3)   NOT NULL
)
ENGINE = MergeTree()
ORDER BY (rejection_code, source_id, rejected_at);
```

### PIT Strategy (Bitemporal)

**`observed_at`** — when the value was true in the real world.
**`ingested_at`** — when the value entered the canonical store.

**Revision handling:** A revised value increments `data_version`. `ReplacingMergeTree`
retains the highest `data_version` row during merges.

**Backfill PIT semantics:** Backfilled observations have `ingested_at` = load time.
Backtests at time T exclude observations with `ingested_at > T`. This is what makes
backtests correct — a test at 2024-01-01 does not see data backfilled in 2026.

**PIT query pattern (ClickHouse):**
```sql
SELECT value
FROM forge.observations FINAL
WHERE metric_id = :metric_id
  AND instrument_id = :instrument_id
  AND observed_at = :observation_timestamp
  AND ingested_at <= :as_of_timestamp
ORDER BY ingested_at DESC
LIMIT 1;
```

### Metric Catalog Seed Data

**Derivatives domain (per instrument, 8h):**
`derivatives.perpetual.funding_rate` · `derivatives.perpetual.open_interest_usd` ·
`derivatives.perpetual.liquidations_long_usd` · `derivatives.perpetual.liquidations_short_usd` ·
`derivatives.perpetual.price_usd` · `derivatives.options.delta_skew_25` ·
`derivatives.options.iv_1w` · `derivatives.options.iv_1m`

**Spot domain (per instrument, 1d):**
`spot.price.close_usd` · `spot.volume.usd_24h` · `spot.market_cap.usd`

**Flows domain (per instrument, 1d):**
`flows.exchange.inflow_usd` · `flows.exchange.outflow_usd` ·
`flows.onchain.transfer_volume_usd`

**Stablecoin domain (1d):**
`stablecoin.supply.total_usd` · `stablecoin.supply.per_asset_usd` ·
`stablecoin.peg.price_usd`

**ETF domain (per product, 1d):**
`etf.flows.net_flow_usd` · `etf.aum.total_usd`

**DeFi domain (1d):**
`defi.aggregate.tvl_usd` · `defi.protocol.tvl_usd` · `defi.dex.volume_usd_24h` ·
`defi.lending.utilization_rate`

> **Note on `defi.lending.utilization_rate`:** The canonical name is `utilization_rate`
> (concept-driven, not implementation-driven). The v1 computation uses a proxy:
> borrow TVL / supply TVL ratio from DeFiLlama. This is documented in the metric
> catalog `methodology` field. The canonical name does not change when the
> full-subgraph metric becomes available in v1.1 — the metric entry is updated in
> place with the improved `computation` field.

**Macro domain (market-level, 1d) — Phase 0 seed (5 metrics):**
`macro.rates.yield_10y` · `macro.rates.yield_2y` · `macro.fx.dxy` ·
`macro.credit.hy_oas` · `macro.rates.fed_funds_effective`

**Macro domain — Phase 1 FRED expansion (18 additional metrics):**
`macro.rates.yield_30y` · `macro.rates.yield_10y_2y_spread` ·
`macro.rates.yield_10y_3m_spread` · `macro.rates.real_yield_10y` ·
`macro.rates.breakeven_inflation_10y` · `macro.equities.sp500` ·
`macro.volatility.vix` · `macro.fx.wti_crude` · `macro.money.m2_supply` ·
`macro.money.monetary_base` · `macro.cb.fed_total_assets` ·
`macro.cb.ecb_total_assets` · `macro.cb.boj_total_assets` ·
`macro.employment.nonfarm_payrolls` · `macro.employment.initial_claims` ·
`macro.inflation.cpi_all_urban` · `macro.inflation.core_pce` ·
`macro.gdp.real_growth`

> **Note:** `macro.credit.hy_oas` (FRED series `BAMLH0A0HYM2`) is in the Phase 0
> seed and must be added to the FRED adapter during Phase 1 build — it is not yet
> in the legacy FRED adapter.

**DeFi protocol metrics — Phase 1 additions:**
`defi.protocol.fees_usd_24h` · `defi.protocol.revenue_usd_24h`

**Derived (market-level, 1d):**
`spot.market_cap.total_crypto_usd` · `spot.dominance.btc_pct`

### Database Engine Summary

**Catalog (PostgreSQL):** instruments, metrics, sources, and operational tables.
Relational integrity, foreign keys, audit trail. No time series data here.

**Observation store (ClickHouse):** `forge.observations` and `forge.dead_letter`.
Columnar storage, ReplacingMergeTree, write-only except for export job.

**Analytical layer (DuckDB against Gold):** Reads Iceberg tables on MinIO.
Not a service — runs embedded in forge_compute and backtesting jobs. Zero operational
overhead. Sub-second performance on multi-year scans.

### Extensibility Proof

**Adding equities:** Register instruments with `asset_class = 'equity'`. Register
equity-specific metrics in catalog. Write adapters. Zero DDL changes.

**Adding a new metric:** Add row to `metrics`. Add feature catalog entry. Add adapter
mapping. Zero DDL changes.

**Adding a new source for an existing metric:** Add row to `sources`. Write adapter.
Zero DDL changes.

### Decisions Locked

| Decision | Outcome |
|---|---|
| Schema model | EAV + metric catalog + materialized current-value view |
| Primary key for observations | (metric_id, instrument_id, observed_at) — ClickHouse ordering key |
| Null instrument_id | Permitted and correct for market-level metrics |
| PIT model | Bitemporal: observed_at + ingested_at. data_version for revisions. |
| Revision handling | New row with incremented data_version. ReplacingMergeTree deduplicates on merge. |
| Backfill PIT semantics | ingested_at = load time. Backtests exclude ingested_at > T. |
| Canonical naming | domain.subdomain.metric_name — hierarchical, no abbreviations |
| Instrument tiers | collection → scoring → signal_eligible, rule-driven promotion |
| Observation store | ClickHouse — forge.observations, ReplacingMergeTree |
| Dead letter | ClickHouse — forge.dead_letter |
| Catalog tables | PostgreSQL — no time series data |
| Analytical layer | DuckDB against Iceberg tables on MinIO (Gold layer) |
| Schema immutability | New metric = catalog row. New source = catalog row. Zero DDL. |

---

## THREAD 5: NORMALIZATION & COLLECTION (Layers 1 + 2)

### Layer 1: Landing Zone — Bronze (Iceberg on MinIO)

Raw payloads land in Bronze partitioned by `(source_id, date, metric_id)`.
Every Bronze record preserves the original payload exactly as received.
No transformations occur before Bronze write. Append-only. 90-day retention enforced
via Iceberg snapshot expiration. S3-compatible — MinIO endpoint swap migrates to S3
with zero code changes.

### Layer 2: Adapter Contract (10 Responsibilities)

Every adapter implements exactly these responsibilities, no more, no less:

1. Fetch data from source API (auth, rate limiting, pagination)
2. Write raw payload to Bronze Iceberg table (append-only, partitioned by
   source/date/metric)
3. Map source-specific field names to canonical metric names
4. Convert units to canonical units
5. Resolve source instrument identifiers to canonical `instrument_id`
6. Resolve source metric identifiers to canonical `metric_id`
7. Validate values against metric catalog definitions (range, type, nullability)
8. Write validated observations to ClickHouse `forge.observations`
9. Write rejected observations to ClickHouse `forge.dead_letter` with rejection
   code and raw payload
10. Write a run record to `collection_events` on completion

**Adapters must NOT:** call external APIs · create catalog entries · silently drop
invalid data · transform values in undocumented ways · know anything about the
signal layer

**Validation applied per observation (independent, not batch):**
A single bad value is dead-lettered. The rest of the batch continues.

### Per-Source Specifications

#### Coinalyze

**Provides:** Perpetual futures — funding rate, OI, liquidations, L/S ratio
(121 instruments)
**ToS:** Unaudited — commercial use and redistribution pending Phase 6 audit
**Cadence:** Every 8h, offset 5 minutes past settlement (00:05, 08:05, 16:05 UTC)

**Field mappings:**

| Source field | Canonical metric | Notes |
|---|---|---|
| `funding_rate` | `derivatives.perpetual.funding_rate` | Range: [-0.05, 0.05] |
| `open_interest_usd` | `derivatives.perpetual.open_interest_usd` | Use USD field, not contracts field |
| `long_liquidations` | `derivatives.perpetual.liquidations_long_usd` | Verify unit in integration test |
| `short_liquidations` | `derivatives.perpetual.liquidations_short_usd` | Same |
| `open_time` | `observed_at` | Unix ms → DateTime64: open_time / 1000 |

**Known issues:**
- Open interest units vary by endpoint — verify in integration test, do not assume.
- Three instruments (ANKR, FRAX, OGN) have historically extreme funding rate values
  — dead-letter with `EXTREME_VALUE_PENDING_REVIEW`, queue for manual review, do not
  silently reject or pass.

#### DeFiLlama

**Provides:** Protocol TVL, DEX volume, stablecoin metrics
**ToS:** Free, low risk, attribution recommended
**Cadence:** Daily at 06:00 UTC
**Three separate collection jobs:** protocols · dex · stablecoins

**Field mappings:**

| Source field | Canonical metric | Notes |
|---|---|---|
| `tvl_usd` (protocol) | `defi.protocol.tvl_usd` | instrument_id = protocol slug in instruments catalog |
| Sum of tvl_usd | `defi.aggregate.tvl_usd` | Computed by adapter from protocol data |
| `volume_usd_24h` | `defi.dex.volume_usd_24h` | Market-level, instrument_id = NULL |
| `circulating_usd` | `stablecoin.supply.per_asset_usd` | |
| Sum of circulating_usd | `stablecoin.supply.total_usd` | Computed by adapter |
| `price_usd` | `stablecoin.peg.price_usd` | Range [0.90, 1.10]. Values outside = dead-letter |
| borrow_tvl / supply_tvl | `defi.lending.utilization_rate` | v1 proxy computation. Documented in metric catalog methodology field. |

**Known issues:** Shallow existing history — backfill from DeFiLlama historical API
must run before live collection starts. Protocol slugs change on rebrands — maintain
normalization map.

#### FRED

**Provides:** Macro time series — yields, DXY, credit spreads, fed funds, and 18
additional series (Phase 1 expansion)
**ToS:** Public domain. No restrictions.
**Cadence:** Daily at 18:00 UTC (incremental — only new observations since last fetch)

**Series mappings (Phase 0 seed):**

| FRED series_id | Canonical metric |
|---|---|
| `DGS10` | `macro.rates.yield_10y` |
| `DGS2` | `macro.rates.yield_2y` |
| `DTWEXBGS` | `macro.fx.dxy` |
| `BAMLH0A0HYM2` | `macro.credit.hy_oas` |
| `EFFR` | `macro.rates.fed_funds_effective` |

> **Note:** `BAMLH0A0HYM2` must be added to the FRED adapter during Phase 1 — it
> is in the metric catalog but not currently collected. The full Phase 1 FRED
> expansion adds 18 additional series (see metric catalog Phase 1 additions above).

**Known issues:** Returns `'.'` for missing values — adapter maps to NULL with
`SOURCE_MISSING_VALUE` flag. Weekend/holiday gaps are structural, not quality issues.

#### SoSoValue

**Provides:** ETF flows (BTC/ETH spot ETFs)
**ToS:** Non-commercial only. **Hard constraint.** `redistribution = false` in source
catalog. Cannot appear in any external data product until ToS audit resolves or paid
tier acquired.
**Cadence:** Daily at 20:00 UTC (after US market close)

#### Tiingo

**Provides:** OHLCV (crypto + equities)
**ToS:** Free tier available, commercial use on paid tier
**Known issues:** Equity volume is in shares — multiply by close price for USD.
Adapter must branch on `asset_class`.

#### Exchange Flows (Explorer / Etherscan V2)

**Provides:** Exchange inflows/outflows for 18 instruments (ETH + ARB chains,
9 exchanges)
**ToS:** Unaudited for commercial use
**Known issues:**
- **Gate.io values are in wei, not ETH.** Confirmed bug. Adapter applies conversion:
  `eth_value = wei_value / 1e18`, then `usd_value = eth_value × spot_price`. Raw
  Bronze landing preserves original wei values.
- Spot price for conversion fetched from `spot.price.close_usd` in canonical store.

#### CoinPaprika

**Provides:** Market cap, price data
**ToS:** Low risk
**Cadence:** Daily

#### CoinMetrics

**Provides:** On-chain transfer volume (BTC + ETH, from GitHub CSV releases)
**ToS:** Unaudited. `redistribution = false` — internal use only until Phase 6 audit.
**Cadence:** Daily

#### BGeometrics

**Provides:** MVRV, SOPR, NUPL, Puell multiple (BTC/ETH)
**ToS:** Unaudited. Pending Phase 6 audit.
**Cadence:** Daily

#### Binance (BLC-01)

**Provides:** Tick-level liquidation events (~70k/day, 100+ symbols)
**ToS:** Unaudited. Internal only pending Phase 6 audit.
**Collection path:**
```
Binance WS (live)
  → LXC 203 on Server2 (192.168.68.12)
    → JSONL files (rolling, local storage)
      → rsync to proxmox landing directory
        → Dagster file sensor detects new files
          → Bronze adapter aggregates 8h, writes Iceberg to MinIO
            → Great Expectations validation
              → Silver adapter writes to ClickHouse
```
**Note:** The rsync pull routine from Server2 to proxmox is unbuilt as of
2026-03-05. Phase 1 item. BLC-01 data unavailable in new system until built.

### Source Gap Analysis

| Metric | Gap | Decision |
|---|---|---|
| `derivatives.perpetual.price_usd` | Coinalyze perpetual price not confirmed | Verify in integration test. If absent: add Binance perp price collection. |
| `defi.lending.utilization_rate` | DeFiLlama does not provide directly | v1: proxy (borrow/supply TVL ratio). Full metric v1.1 (Aave/Compound subgraph). |
| `flows.exchange.*` beyond 18 instruments | Explorer limited coverage | Accept for v1. Expand in v1.1. |
| `spot.market_cap.usd` | Tiingo does not provide | Use CoinPaprika (existing, low ToS risk). |
| Options metrics | No current source | Null-propagate in v1. Deribit adapter in v1.1. |
| `flows.onchain.transfer_volume_usd` | CoinMetrics community covers BTC+ETH | Use CoinMetrics. Flag `redistribution = false` pending ToS audit. |
| BTC directional exchange flows | No v1 source covers per-exchange BTC in/out | Null-propagate. CryptoQuant (parked, paid) is the resolution path. v1.1 milestone. |

### Migration Plan

Assessment criteria: Are timestamps reliable? Are units documented? Are symbols
mappable? Is metric identity clear?

| Dataset | Rows | Status | Decision |
|---|---|---|---|
| Tiingo OHLCV | ~800k | GREEN | Migrate first — spot price needed by flows adapter |
| Coinalyze derivatives | 185,066 | GREEN | Migrate |
| FRED macro | 140,261 | GREEN | Migrate |
| DeFiLlama DEX | 88,239 | GREEN | Migrate |
| DeFiLlama lending | 9,651 | GREEN | Migrate |
| CoinMetrics on-chain | 10,137 | GREEN | Migrate, flag internal-only |
| Exchange flows | 2,177 | RED (wei bug) | Migrate with wei→ETH conversion applied in migration adapter |
| ETF flows | 774 | GREEN | Migrate, flag internal-only |
| DeFi protocols | 195 | SHALLOW | Skip — backfill from DeFiLlama API |
| Stablecoins | 180 | SHALLOW | Skip — backfill from DeFiLlama API |

Migration adapters implement the same 10-responsibility interface as production
adapters. They run before live collection agents start. No timestamp conflicts.

### Failure Handling

**Per collection run:** Retry 3× with exponential backoff. On third failure: log
`collection_events.status = 'failed'`, alert. Do not retry indefinitely.

**Circuit breaker:** 3 consecutive failures → `DEGRADED` state. Collection continues
attempting. No automatic source substitution.

**Staleness propagation chain:**
Source fails → `collection_events.status = 'failed'` → `instrument_metric_coverage.
latest_observation` stops updating → feature engineering emits `SOURCE_STALE` →
EDSx pillar confidence decreases → composite confidence decreases → customer-facing
signal carries honest reduced confidence.

### Decisions Locked

| Decision | Outcome |
|---|---|
| Landing zone | Iceberg tables on MinIO (Bronze), append-only, 90-day retention, S3-compatible |
| Adapter interface | Standardized 10-responsibility contract |
| Validation scope | Per-observation, independent. Batch does not fail on single bad value. |
| Dead letter | Every rejection logged with raw payload, reason, code. Nothing silently dropped. |
| Redistribution | Flagged at source catalog level. Enforced at serving layer. |
| Gate.io wei bug | Fixed in adapter via unit conversion. Raw Bronze landing preserved. |
| Coinalyze extreme values | `EXTREME_VALUE_PENDING_REVIEW`. Manual review queue. |
| Migration order | Tiingo first (spot price dependency), then remaining in volume order |
| Excluded permanently | Santiment, Glassnode (deprecated), BSCScan (deprecated), Solscan (deprecated) |
| Parked (not in budget) | CoinGlass, CryptoQuant, CoinMarketCap |
| T3 fallback (not catalogued) | CoinGecko, KuCoin |

---

## THREAD 7: OUTPUT DELIVERY

### Product Surface Definition

**What v1 sells:** A systematic, domain-driven signal API covering the crypto market
across derivatives, capital flows, DeFi, and macro — producing directional signals
with calibrated confidence scores, regime context, and full component provenance,
updated on schedule, available via REST.

**The v1 customer:** Savvy retail with a technical lean. Understands the underlying
mechanics — knows what funding rate, OI, and MVRV mean without a glossary. Runs
their own analysis but is looking for a systematic, data-rich layer they trust more
than social noise. Evaluates the product by whether the methodology is legible, not
whether it has a compliance audit trail. Will read a methodology doc if it is clear.
Does not submit RFPs.

**Primary use case:** Informing entry and exit decisions. **Secondary use case:**
Portfolio allocation context. Broad coverage is part of the value proposition — the
customer discovers instruments they were not watching.

**"Institutional grade" defined:** An **internal quality bar**, not a customer
descriptor. PIT-correct historical data, reproducible outputs with full audit trails,
calibrated ML probabilities, no self-certification, dead letter logging for every
rejected value. The customer never sees most of this — but it makes the signal
defensible when they ask how it was produced.

**What is not in v1:** Dashboard or UI · self-serve account creation or billing
infrastructure · tiered subscription management (manual key issuance only) · index
or benchmark licensing · white-label / embedded analytics

### Delivery Model

**v1: API-first.** The API is the product surface. Every field that a future
dashboard would display exists in the API response from day one.

**v2: Dashboard.** Read-only signal dashboard. Gated on quality, not a calendar date.
Both trigger conditions must be true: (A) API has paying customers and conversion
friction is measurably losing signups due to absence of UI; AND (B) social funnel is
generating inbound volume that API alone cannot convert. Neither condition alone is
sufficient.

### API Authentication and Tiers

**Authentication:** Manual key issuance in v1. No self-serve. Key in
`X-API-Key` header. Tier resolved from key at request time.

**Free tier** — key required for API calls. Endpoints: prices, macro, instruments.
Schema is a public contract on ship. The hook: normalized, aggregated data available
nowhere else in this form.

**Paid tier** — signals, signal detail, regime, health.

**Redistribution-gated:** Data from Coinalyze, CoinMetrics, and SoSoValue is
excluded from all tiers until Phase 6 ToS audit clears. A query that would return
gated data returns an empty result set with an explicit `redistribution_pending` flag
on affected fields. Enforced structurally at the response layer, not by policy alone.

### Rate Limits

| Tier | Requests/minute | Requests/day |
|---|---|---|
| Free | 30 | 1,000 |
| Paid | 120 | 20,000 |

Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
HTTP 429 on breach with `Retry-After` header.

### API Endpoints

#### Free Tier

**`GET /v1/market/prices`**
Current price, market cap, and 24h change for the crypto instrument universe.
Source: CoinPaprika. Parameters: `instruments` (comma-separated, optional),
`page`, `per_page` (max 500).

**`GET /v1/macro`**
Current values for all FRED macro series in the catalog. Public domain — no
redistribution restriction. No parameters.

**`GET /v1/instruments`**
The full instrument catalog. Parameters: `domain`, `signal_eligible` (boolean).

#### Paid Tier

**`GET /v1/signals`**
Full universe signal snapshot. The primary entry point. Parameters: `direction`,
`confidence_min`, `regime`, `domain`, `instruments`, `page`, `per_page` (max 200).

Response includes `generated_at`, market `regime` block, `stale_sources` list,
and per-instrument signal objects with `direction`, `confidence`, `confidence_tier`,
`magnitude`, `horizon`, `as_of`, and `staleness_flag`.

---

**`GET /v1/signals/{instrument_id}`**
Single instrument detail with full component breakdown and provenance.

```json
{
  "instrument_id": "BTC",
  "generated_at": "2026-03-05T12:00:00Z",
  "signal": {
    "direction": "bullish",
    "confidence": 0.73,
    "confidence_tier": "high",
    "magnitude": 0.45,
    "horizon": "14d",
    "regime": "risk_on"
  },
  "synthesis": {
    "edsx_weight": 0.50,
    "ml_weight": 0.50,
    "agreement": true,
    "confidence_adjustment": null
  },
  "edsx": {
    "direction": "bullish",
    "confidence": 0.71,
    "pillars_computed": 4,
    "pillars_available": 5,
    "pillars": {
      "trend_structure": { "direction": "bullish", "score": 0.68, "null_state": null },
      "liquidity_flow": { "direction": "bullish", "score": 0.74, "null_state": null },
      "valuation": { "direction": "neutral", "score": 0.51, "null_state": null },
      "structural_risk": { "direction": "bullish", "score": 0.66, "null_state": null },
      "tactical_macro": { "direction": null, "score": null,
                          "null_state": "METRIC_UNAVAILABLE" }
    }
  },
  "ml": {
    "direction": "bullish",
    "probability": 0.76,
    "calibrated": true
  },
  "provenance": {
    "features_as_of": "2026-03-05T11:45:00Z",
    "oldest_observation_used": "2026-03-04T00:00:00Z",
    "sources_contributing": ["coinalyze", "etherscan", "defillama", "fred"],
    "stale_sources": [],
    "staleness_flag": false
  }
}
```

Null states are always explicit. A null score with no null_state is a bug, not a
valid response state.

---

**`GET /v1/regime`**
Current regime state with supporting context. Includes `state`, human-readable
`label` (Full Offense / Selective Offense / Defensive Drift / Capital Preservation),
`confidence`, volatility and liquidity anchors, and current EDSx pillar weights.

---

**`GET /v1/health`**
System freshness per source. `status` is `healthy`, `degraded` (one or more sources
stale, signals still computing on available data), or `impaired` (signal computation
affected). Includes `signals_last_computed` and `stale_instrument_count`.

### Latency SLAs

| Endpoint | p50 | p95 | p99 |
|---|---|---|---|
| `GET /v1/signals` | 200ms | 800ms | 1500ms |
| `GET /v1/signals/{instrument}` | 100ms | 400ms | 800ms |
| `GET /v1/regime` | 50ms | 200ms | 400ms |
| `GET /v1/health` | 50ms | 150ms | 300ms |
| `GET /v1/market/prices` | 100ms | 400ms | 800ms |
| `GET /v1/macro` | 50ms | 200ms | 400ms |
| `GET /v1/instruments` | 50ms | 150ms | 300ms |

SLA breach = p95 exceeds committed value for 3 consecutive 5-minute windows.

### API Versioning

Path-level versioning (`/v1/`). Fields may be added without a version bump. Fields
will not be removed or renamed within a version. Breaking changes require a new
version path (`/v2/`) with minimum 60-day deprecation notice.

### Error Responses

```json
{
  "error": {
    "code": "INSTRUMENT_NOT_FOUND",
    "message": "Instrument ETH2 is not in the covered universe.",
    "request_id": "req_01jnx4k2m8f3p"
  }
}
```

Standard codes: `INSTRUMENT_NOT_FOUND` · `INVALID_PARAMETER` ·
`REDISTRIBUTION_RESTRICTED` · `RATE_LIMIT_EXCEEDED` · `UNAUTHORIZED` ·
`SOURCE_STALE` · `SIGNAL_UNAVAILABLE`

### Signal Cadence and Freshness

Signals recomputed on Dagster asset graph trigger — event-driven on metric ingestion.

| Source cadence | Expected signal refresh |
|---|---|
| Coinalyze (8h) | ~3×/day |
| Explorer / Etherscan (8h) | ~3×/day |
| DeFiLlama (12h) | ~2×/day |
| FRED (24h) | ~1×/day |

`as_of` on every signal response = feature computation timestamp, not API request
timestamp. `staleness_flag` on a signal = one or more contributing sources missed
their expected ingestion window.

### Webhook Delivery

Available in v1 as customer-initiated integration (not a default).

**Event types:** `signal.updated` · `signal.stale` · `health.degraded` ·
`health.impaired`

**Delivery guarantee:** At-least-once. Retry on non-2xx: 3 attempts with exponential
backoff (30s, 5min, 30min). After 3 failures: dead-letter + customer notification.

**HMAC signature:** `X-Signature-256: hmac-sha256=<hex digest>` over raw request body
using customer's webhook secret.

### Social Distribution

Channels: Telegram (`@FromTheBridgeChannel`) + X (`@BridgeDispatch`). Both receive
identical content from one automation. These are a distribution funnel — not customer
delivery. Social automation activates after Phase 4 shadow period passes graduation
criteria. Manual activation by Stephen after reviewing shadow evaluation.

**Post format:** Direction first, confidence tier in plain English, horizon, regime
context, timestamp, domain reference. No jargon requiring a glossary.

### SLA Definitions

**SLA 1 — Signal Freshness:** Signals recomputed within 90 minutes of a source
ingestion event completing. Breach: lag exceeds 90 minutes for any signal-eligible
instrument for more than one consecutive compute cycle. Customer notification: email
within 30 minutes of breach detection.

**SLA 2 — API Uptime:** 99.5% monthly uptime for all paid-tier endpoints. Blackbox
exporter probing `/v1/health` every 60 seconds. Allows ~3.6 hours downtime/month.
Customer notification: status page update within 15 minutes of outage start.

**SLA 3 — Staleness Notification:** Customers notified within 60 minutes of a source
failure affecting their signals. Forge monitor checks freshness every 30 minutes.
Notification includes: which source is stale, which instruments are affected,
estimated resolution timeline if known.

**SLA 4 — Methodology Change Notice:** Minimum 14-day advance notice before any
change to signal methodology that affects interpretation of outputs. Scope: model
retraining that changes output distributions, pillar weight adjustments, regime
classification changes. Does NOT cover: bug fixes that correct erroneous outputs,
source replacements that do not change the metric being measured. Breach: methodology
change deployed without 14-day advance customer notice.

### Methodology Documentation

**Location:** `fromthebridge.net/methodology` (public, no auth). Versioned in git.
Prior versions accessible at stable URLs indefinitely.

**Eight sections:** (1) What this product produces · (2) Coverage · (3) Signal
architecture · (4) EDSx methodology · (5) ML methodology · (6) Data sources ·
(7) Known limitations · (8) Changelog.

Methodology doc version increments when: a model is retrained and output distribution
changes materially · a pillar definition changes · a source is added or removed from
signal computation · regime classification logic changes.

### First Customer Onboarding (Complete Sequence)

1. Initial contact via network or social funnel (no cold outreach in v1)
2. Customer receives methodology doc URL and `GET /v1/instruments` before any
   commercial conversation
3. Direct pricing conversation. Monthly subscription, paid upfront. No free trials.
   No discounts for testimonials or referrals.
4. Written agreement (structured email sufficient in v1). Covers: deliverables, SLAs,
   redistribution restrictions.
5. API key issued manually (paid tier). Webhook secret provisioned separately if
   requested.
6. Onboarding note: base URL, auth, recommended first queries, how to read confidence
   tiers and null states, how to check staleness, support contact, methodology URL.
7. First signal pull — the API is the product.
8. Day 7 check-in: three questions — data format working, missing instruments,
   methodology questions.
9. Ongoing: methodology change notices per SLA 4, staleness notifications per SLA 3,
   monthly manual invoicing (net 7 payment terms).

### Decisions Locked

| Decision | Outcome |
|---|---|
| v1 delivery model | API-first. No dashboard. |
| v1 customer | Savvy retail, technical lean, broad coverage interest |
| "Institutional grade" | Internal quality bar only |
| Free tier endpoints | `GET /v1/market/prices`, `GET /v1/macro`, `GET /v1/instruments` |
| Paid tier endpoints | `GET /v1/signals`, `GET /v1/signals/{id}`, `GET /v1/regime`, `GET /v1/health` |
| Redistribution gating | Coinalyze, CoinMetrics, SoSoValue excluded until Phase 6 ToS audit |
| Authentication | Manual key issuance. No self-serve. |
| Social distribution | Telegram + X. Funnel only — not customer delivery. |
| Social gate | Activates after Phase 4 shadow period passes. Manual by Stephen. |
| Webhook | Available in v1 as customer-initiated integration. At-least-once, HMAC-signed. |
| SLA count | Four: signal freshness, API uptime, staleness notification, methodology change |
| Methodology doc | Public URL. Versioned. 8 sections. Updated on every material change. |
| First customer | Direct engagement. Written agreement. No free trials. Manual invoicing. |
| v2 triggers | Both conditions required: API conversion friction AND social inbound UI-blocked |


---

## THREAD 6: BUILD PLAN

### Governing Constraints

1. Schema is the foundation — nothing built until Phase 0 gate passes
2. Data flows bottom-up; builds are validated top-down
3. One operator — phases are sequential, not concurrent

### Phase Sequence

---

#### Phase 0: Foundation

**Scope:** Infrastructure provisioning, DDL deployment, catalog seeding, baseline
verification. No collection. No computation.

**Steps:**
1. Provision PostgreSQL (catalog), ClickHouse (observation store), MinIO (object
   storage), Dagster (orchestration) as Docker services on proxmox
2. Apply PostgreSQL DDL in dependency order:
   `sources → instruments → metrics → collection_events → instrument_metric_coverage`
3. Deploy ClickHouse `forge.observations`, `forge.dead_letter`,
   `forge.current_values` materialized view
4. Configure MinIO bucket and Iceberg catalog
5. Seed metric catalog (every metric in Thread 3 requirements list, including Phase 1
   FRED expansion entries with `status = 'planned'`)
6. Seed sources catalog (all 10 sources, with redistribution flags)
7. Seed initial instrument universe (BTC, ETH, SOL + full Coinalyze list)
8. Verify all bitemporal query patterns against test observations

**Hard gate — all must pass before Phase 1 begins:**

| Criterion | Pass condition |
|---|---|
| PostgreSQL catalog tables | All tables exist, all constraints enforced, row counts match seed data |
| ClickHouse observations | `forge.observations` deployed, ReplacingMergeTree confirmed, test write/read succeeds |
| ClickHouse dead_letter | `forge.dead_letter` deployed, test rejection row written and readable |
| ClickHouse current_values | Materialized view deployed, refreshes after test observation write |
| MinIO accessible | Bucket created, Iceberg catalog configured, test Parquet file written and DuckDB-readable |
| Metric catalog | Every metric from Thread 3 requirements has a catalog entry |
| PIT query | PIT query returns correct value from manually inserted test observation with data_version revision |
| Redistribution flags | SoSoValue and CoinMetrics have `redistribution = false` in source catalog |

**Timeline estimate:** 3–5 days. Primary risk: schema defects during DDL application.

---

#### Phase 1: Data Ingestion

**Scope:** All collection agents, all adapters, migration execution. ~53 Dagster
Software-Defined Assets at launch (one per (metric_id, source_id), Option B
per-instrument partitioning).

**Steps:**
1. Migration adapters in order: Tiingo → Coinalyze → FRED → DeFiLlama DEX →
   DeFiLlama Lending → CoinMetrics → Exchange Flows (with wei fix) → ETF Flows
2. Backfill jobs for shallow datasets: DeFiLlama protocols, stablecoins,
   CoinPaprika market cap
3. Add `BAMLH0A0HYM2` (HY OAS) and 18 additional FRED series to FRED adapter
4. Build BLC-01 rsync pull routine (Server2 → proxmox landing directory)
5. Deploy production collection agents one at a time, verify each before next
6. NAS backup job for MinIO configured and verified
7. Coverage verification query — all active metrics at ≥ 90% completeness for
   signal-eligible instruments
8. First instrument tier promotion run

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Migration complete | All datasets loaded, spot-checked, no systematic errors |
| Live collection | All production agents collecting at specified cadence |
| Rejection rate | Global < 5%. Known exceptions documented. |
| Coverage | All active metrics ≥ 90% completeness for signal-eligible instruments |
| observations_written | `collection_events.observations_written` populated correctly for all agents |
| Tiingo history | BTC from 2014, ETH from 2015 confirmed |
| Wei fix | Exchange flows Gate.io values in USD confirmed (not wei) |
| Tier promotion | ≥ 20 instruments at signal_eligible tier |
| Redistribution | SoSoValue/CoinMetrics rows confirmed with correct source flags |
| NAS backup | MinIO backup job running, last successful backup verified |

**Timeline estimate:** 2–3 weeks. Primary risk: migration adapter bugs, DeFiLlama
backfill rate limits.

---

#### Phase 2: Feature Engineering

**Scope:** Feature computation layer, feature catalog, historical feature matrix.

**Steps:**
1. Populate feature catalog (all features from Thread 3) before any code
2. Build event trigger infrastructure
3. Implement categories in order: A → C → B → F → G → D → E
4. Per-category verification: hand-calculate 5 known values, compare to computed
5. PIT constraint audit: every feature manually verified for no look-ahead bias
   (uses `ingested_at ≤ computation_timestamp`, not `observed_at`)
6. Historical feature matrix generation via DuckDB against Gold

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Feature catalog | All Thread 3 features have catalog entries before code is written |
| Hand calculation | 5 spot-checked values per feature type match exactly |
| PIT audit | Zero features fail the constraint audit |
| Historical matrix | Generated from earliest available history to present |
| Null state coverage | All three null states verified to fire correctly |
| Idempotency | Two runs on same inputs produce identical output |

**Timeline estimate:** 2–3 weeks. Primary risk: PIT violations in audit, rolling
window edge cases.

---

#### Phase 3: Signal Generation — EDSx

**Scope:** Deterministic scoring track (available pillars), calibration, backtesting.

**Steps:**
1. Write pillar rule set methodology documents before any code
2. Implement pillar scoring for available pillars (trend_structure and liquidity_flow
   are live; valuation, structural_risk, tactical_macro are planned — implement in
   priority order per REM roadmap)
3. Implement composite formation with regime-adjusted weights and threshold
   calibration
4. Implement regime classifier (H2 Volatility-Liquidity Anchor rule-based baseline)
5. Backtest against historical feature matrix
6. Calibrate thresholds against F1 maximization at each horizon
7. Output schema verification against API contract

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Rule documentation | All live pillar rule sets written as methodology docs before code |
| Backtested accuracy | > 52% directional accuracy at 14d horizon on OOS period |
| Confidence calibration | Monotonic relationship between confidence and accuracy |
| Regime classification | ≥ 8 of 10 known historical regime periods correctly classified |
| Output schema | Validates against API contract for all signal-eligible instruments |
| Null propagation | Missing pillar degrades confidence, signal still serves |

**Timeline estimate:** 1–2 weeks.

---

#### Phase 4: Signal Generation — ML

**Scope:** Five ML domain models, walk-forward training, calibration, shadow
deployment.

**Steps:**
1. Build training infrastructure: walk-forward generator, LightGBM wrapper,
   evaluation framework, model registry
2. Generate 14-day volume-adjusted labels (PIT-correct — labels in training pipeline
   only, never served)
3. Train models: M-Macro → M-Derivatives → M-Flows → M-DeFi → M-Volatility
4. Apply isotonic calibration to each model
5. Evaluate all five graduation criteria per model
6. Deploy to shadow mode (write to shadow tables, not live output)
7. Shadow period: minimum 30 days, extendable if shadow evaluation fails
8. Shadow evaluation: compare to EDSx, verify stability, check all five graduation
   criteria on shadow data

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| All 5 models trained | Walk-forward training complete, OOS ≥ 12 months |
| Graduation criteria | All 5 models pass all 5 criteria on OOS data |
| Calibration | ECE < 0.05 for all models |
| Shadow period | ≥ 30 days without infrastructure failures |
| Shadow accuracy | Consistent with OOS evaluation (no cliff) |
| Feature importance | No single feature > 40% in any model |
| Model 5 output | Valid outputs for all signal-eligible instruments each shadow run |

**Timeline estimate:** 3–4 weeks. Primary risk: graduation criteria not met on first
pass.

---

#### Phase 5: Signal Synthesis and Serving

**Scope:** Layer 2 synthesis, API, delivery mechanisms.

**Note:** Layer 2 synthesis design is finalized in a dedicated design session before
this phase begins. The synthesis principles are locked (§ Thread 2 — Signal Synthesis)
but the implementation design is a future session.

**Steps:**
1. Finalize Layer 2 synthesis design (design session)
2. Implement synthesis logic (EDSx/ML weighting, agreement check, confidence
   adjustment, magnitude, regime context, null handling)
3. Verify synthesis under all scenarios: agreement, disagreement, pillar missing
4. Build FastAPI serving layer (all seven endpoints, auth, redistribution filter,
   rate limiting)
5. Build webhook delivery (at-least-once, HMAC signing)
6. Full end-to-end provenance trace: signal → feature values → metric observations →
   collection event → source

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Synthesis output | Matches API contract for all signal-eligible instruments |
| Agreement/disagreement | Both scenarios produce correct confidence adjustments |
| Magnitude | Non-null for all signal-eligible instruments |
| API endpoints | All seven endpoints return correct responses |
| Redistribution filter | Query that would return restricted data returns empty result with flag |
| Provenance | Full trace verified for BTC and ≥ 2 other instruments |
| Delivery | Webhook verified against test endpoints |
| Staleness flag | Simulated source failure triggers flag within 2 collection cycles |
| Latency SLAs | All endpoints meet p95 targets under representative load |

**Timeline estimate:** 1–2 weeks.

---

#### Phase 6: Productization

**Scope:** Health monitoring, methodology docs, ToS audit, first customer.

**Steps:**
1. Health monitoring dashboards (collection, coverage, signal, infrastructure)
2. Methodology documentation (8 sections — see Output Delivery section)
3. ToS audit completion for all 10 sources
4. First customer delivery

**Hard gate (before first customer delivery):**

| Criterion | Required |
|---|---|
| Health monitoring | Any collection failure diagnosable within 15 minutes |
| Methodology docs | All 8 sections complete at `fromthebridge.net/methodology` |
| ToS audit | All sources audited, restrictions enforced in API |
| API key auth | Tier enforcement working |
| Redistribution filter | Verified in production |
| Public status page | Exists, manually updatable |

**Timeline estimate:** 1–2 weeks.

### Timeline Summary (Single Operator)

| Phase | Estimate | Primary risk |
|---|---|---|
| Phase 0 | 3–5 days | Schema defects during DDL application |
| Phase 1 | 2–3 weeks | Migration adapter bugs, DeFiLlama backfill rate limits |
| Phase 2 | 2–3 weeks | PIT violations in audit, rolling window edge cases |
| Phase 3 | 1–2 weeks | Rule calibration, regime edge cases |
| Phase 4 | 3–4 weeks | Graduation criteria not met on first pass |
| Phase 5 | 1–2 weeks | API integration, webhook reliability |
| Phase 6 | 1–2 weeks | ToS audit findings |
| **Total** | **13–18 weeks** | Migration data quality is the primary variance driver |

### Parallel Operation

There is no parallel operation. Forge is dead. Forge database retained read-only for
90 days as a data safety net only. New system does not consult it.

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Migration discovers data quality beyond audit | Medium | High | Budget 1 extra week Phase 1. Re-backfill from source rather than debug corrupt data. |
| Coinalyze/BGeometrics/Etherscan ToS fails for redistribution | Medium | Medium | Internal signal unaffected. Data API excludes affected data until replacement found. |
| ML models fail graduation first pass | Medium | Medium | Shadow period extended. EDSx serves as production signal. Customers receive EDSx-only confidence values. |
| Layer 2 synthesis design takes longer than estimated | Medium | Low | EDSx serves independently. Synthesis ships when design is complete. |
| Source API breaking change | Low | Medium | Staleness propagates honestly. Adapter fix scoped to Layer 2. |
| ClickHouse self-hosted capacity | Low | Medium | Split trigger thresholds defined. ClickHouse Cloud migration is schema-compatible. |
| Schema defect after Phase 0 gate | Low | Very High | Phase 0 gate is the primary mitigation — PIT query test, ClickHouse engine verification, catalog constraint tests. |

### Decisions Locked

| Decision | Outcome |
|---|---|
| Build sequence | Schema → Data → Features → EDSx → ML → Serving |
| Phase gate model | Hard pass/fail. No phase begins until previous gate passes. |
| Migration order | Tiingo first (spot price dependency), then remaining |
| Parallel operation | None. Forge read-only 90 days. |
| ML shadow period | Minimum 30 days. Extension if shadow evaluation fails. |
| Layer 2 synthesis | Design session before Phase 5. Not a Phase 4 deliverable. |
| First customer | Phase 6 completion. Direct engagement. Real pricing. No free trials. |
| ToS audit timing | Phase 6, before any external data product ships. |
| Schema defects | Fixed before Phase 1 begins. No schema changes after Phase 0 gate. |


---

## INFRASTRUCTURE

### Physical Deployment Topology

| Machine | IP | Role |
|---------|-----|------|
| proxmox | 192.168.68.11 | Production. All new-architecture services run here. GPU: RTX 3090 (24GB). |
| Server2 (srv-rack-02) | 192.168.68.12 | Binance Collector only. LXC 203 + VPN. Single-purpose. |
| bluefin | 192.168.68.64 | Development. Build and test here. Never edit on proxmox. |
| NAS | 192.168.68.91 | Backup destination only. No service or agent writes here. |

**Forbidden:** Edit code on proxmox · Write to NAS from services or agents · Write to
Server2 from anything other than the Binance collector.

### Storage Mounts (proxmox)

| Mount | Capacity | Type | Contents |
|-------|----------|------|---------| 
| `/` | 4TB | NVMe | OS, Docker engine, container layers |
| `/mnt/empire-db` | 2TB | SSD | PostgreSQL data, ClickHouse data, Dagster metadata |
| `/mnt/empire-data` | 4TB | SSD | MinIO data (Bronze Iceberg + Gold Iceberg) |

**Rationale:** MinIO on the 4TB SSD — Bronze raw payloads are the largest storage
consumer, projected ~75 GB over 5 years. ClickHouse on the 2TB SSD — compressed
Silver projected ~2.5 GB over 5 years.

### Docker Services

All services run as Docker containers on proxmox. New-architecture services:

| Service | Container | Port | Volume mount |
|---------|-----------|------|-------------|
| ClickHouse | empire_clickhouse | 8123 (HTTP), 9000 (native) | `/mnt/empire-db/clickhouse` |
| MinIO | empire_minio | 9001 (API), 9002 (console) | `/mnt/empire-data/minio` |
| Dagster webserver | empire_dagster_webserver | 3010 | `/mnt/empire-db/dagster` |
| Dagster daemon | empire_dagster_daemon | — | `/mnt/empire-db/dagster` |
| Dagster code server | empire_dagster_code | — | `/opt/empire/pipeline` |

**Network:** ClickHouse and MinIO are on the internal Docker network only. Dagster
webserver is LAN-accessible (port 3010) — not exposed via Cloudflare tunnel. The
2am operational interface is the Dagster UI, accessed directly on the LAN.

**Correction (locked here):** design_index previously stated "Dagster dedicated LXC."
Corrected to "Dagster dedicated Docker service." LXC containers are Proxmox-specific
and do not translate to cloud deployment. All services run as Docker containers.

### Architecture Decision Records

Full rationale and alternatives analysis for each technology decision.

#### ADR-001: ClickHouse as Silver (Observation Store)

**Decision:** ClickHouse self-hosted, single node, Docker. `ReplacingMergeTree(data_version)`.
Ordered by `(metric_id, instrument_id, observed_at)`. Partitioned by
`toYYYYMM(observed_at)`.

**Disqualified alternatives:**
- PostgreSQL + TimescaleDB: Poor export scan performance at scale. 2-4× compression
  vs ClickHouse's 10-20×. TimescaleDB-only migration path (not ClickHouse Cloud).
- InfluxDB: EAV incompatibility — assumes fixed tag/field schema per measurement.
  Adding a new metric requires schema coordination; in ClickHouse it is a new row.
- DuckDB for Silver: No concurrent write support (embedded library, no server mode).
  Multiple Dagster assets write simultaneously — structurally unsuitable.

**Key constraint:** `ReplacingMergeTree` deduplication is eventual. The export job
must use `SELECT ... FINAL` to ensure clean data reaches Gold.

**Migration path:** ClickHouse Cloud. Connection string swap. Zero code changes.

#### ADR-002: Apache Iceberg as Bronze and Gold Storage Format

**Decision:** Apache Iceberg tables on MinIO for both Bronze and Gold. PyIceberg for
writes. DuckDB with `iceberg` extension for reads.

**Disqualified alternatives:**
- Raw Parquet on MinIO: No time travel, no ACID, no schema evolution.
- Delta Lake: Functionally equivalent but DuckDB's Iceberg support is first-class;
  Delta Lake support is less mature.
- PostgreSQL partitioned tables for Bronze: Rule 3 violation + retention management
  complexity.

**Migration path:** AWS S3. MinIO endpoint swap in config. Zero code changes. Files
are S3-compatible — no data migration needed, only file sync.

#### ADR-003: MinIO as Object Storage

**Decision:** MinIO self-hosted, Docker, writing to `/mnt/empire-data`. S3-compatible
API — all application code uses S3 SDK with endpoint override.

**Disqualified:** AWS S3 (unnecessary cost pre-revenue) · Ceph (distributed, over-
engineered for single-node) · Local filesystem (breaks cloud migration portability).

**Migration path:** AWS S3. Environment variable swap. Zero code changes.

#### ADR-004: DuckDB as Analytical Engine

**Decision:** DuckDB embedded, no server mode. Runs inside forge_compute Python
processes and dbt Python models. Reads Iceberg tables on MinIO via `iceberg` extension.

**Disqualified:** Spark / Trino / Presto (cluster overhead, JVM, no benefit at sub-
terabyte scale) · PostgreSQL FDW (Rule 3 coupling risk, poor Iceberg scan performance).

**Migration path:** Trino or Spark when Gold layer reaches terabyte scale. Iceberg
table format preserved — only query engine changes.

#### ADR-005: Dagster as Orchestration

**Decision:** Dagster self-hosted, dedicated Docker service. Software-Defined Assets
(one per (metric_id, source_id)). Asset graph built from metric_catalog + metric_lineage
at startup. ~53 assets at Phase 1 launch; ~200 at full buildout.

**Disqualified:** Apache Airflow (task-centric, no native asset model, metric_lineage
duplication hazard) · Prefect (no asset model, managed dependency not yet warranted) ·
APScheduler/cron (no observability, no retry framework, no asset graph).

**Migration path:** Dagster Cloud. Asset definitions fully portable. Connection
string change only.

#### ADR-006: dbt + Python (forge_compute) for Marts

**Decision:** dbt with dbt-duckdb adapter for SQL transforms. forge_compute Python
service for rolling window, cross-sectional, and ML-assembly features. Both use DuckDB
to read Gold. Both write to Marts (Iceberg on MinIO). Feature catalog entry required
before any feature is computed — enforced by forge_compute startup check.

**Boundary:** SQL-expressible transforms → dbt. Python-required (rolling windows,
cross-sectional ranks across all instruments simultaneously) → forge_compute.

#### ADR-007: PostgreSQL as Catalog

**Decision:** PostgreSQL self-hosted, Docker. Existing `empire_postgres` container
extended with `forge` schema for catalog tables. No time series data — ever.

**Disqualified:** SQLite (no concurrent writes) · ClickHouse (no foreign key
enforcement) · MongoDB (application-level referential integrity only).

**Migration path:** AWS RDS. Connection string swap. Zero code changes.

### Managed Service Migration Triggers

| Component | Trigger threshold | Code changes |
|-----------|------------------|-------------|
| MinIO → AWS S3 | 50 paying customers OR `/mnt/empire-data` > 80% capacity | Zero |
| ClickHouse self-hosted → Cloud | 50 paying customers OR merge queue > 100 parts for 7+ days | Zero |
| Dagster self-hosted → Cloud | > 4 hours/month operator time on Dagster infrastructure | Zero |
| PostgreSQL → AWS RDS | 50 paying customers | Zero |

All four migrations are zero-code-change, environment variable swaps only.

### Resource Boundaries and Scaling Thresholds

**ClickHouse:**
- Disk usage on `/mnt/empire-db` > 80% → evaluate cloud migration
- Row count > 1 billion → evaluate cloud migration
- Merge queue depth > 100 parts → investigate write throughput
- Current projection: ~72,000 rows/day; 1B threshold = ~38 years at current volume

**MinIO:**
- Disk usage on `/mnt/empire-data` > 80% → migrate to S3
- Current projection: ~17 GB/year; 80% threshold = ~188 years at current volume

**Dagster:**
- Run history DB > 10 GB → archive old runs or consider Dagster Cloud
- Concurrent asset materializations > 20 → tune executor concurrency limits

**PostgreSQL:**
- Catalog table row count > 100,000 in any single table → investigate (catalog tables
  should be small by design)

### Cold-Start Sequence

Execute in order. Do not skip steps.

```
1. STORAGE
   df -h /mnt/empire-db /mnt/empire-data
   Both must show correct capacities before proceeding.

2. POSTGRESQL (all other services depend on this)
   docker compose up -d empire_postgres
   docker compose ps empire_postgres  # wait for healthy
   cat db/migrations/0001_phase0_schema.sql | \
     docker exec -i empire_postgres psql -U crypto_user -d crypto_structured
   Verify: SELECT COUNT(*) FROM forge.metric_catalog;  # > 0 rows

3. CLICKHOUSE
   docker compose up -d empire_clickhouse
   curl http://localhost:8123/ping  # wait for healthy
   cat db/migrations/clickhouse/0001_silver_schema.sql | \
     docker exec -i empire_clickhouse clickhouse-client --multiquery
   Verify: SHOW TABLES FROM forge;  # observations, dead_letter, current_values

4. MINIO
   docker compose up -d empire_minio
   curl http://localhost:9001/minio/health/live  # wait for healthy
   mc alias set local http://localhost:9001 $MINIO_ACCESS_KEY $MINIO_SECRET_KEY
   mc mb local/bronze local/gold  # if not restored from backup
   Verify: mc ls local/

5. DAGSTER (last — after all targets verified reachable)
   docker compose up -d empire_dagster_daemon empire_dagster_webserver \
     empire_dagster_code
   curl http://localhost:3010  # wait for healthy
   Verify: Dagster UI → Assets tab, all assets from metric_catalog visible

6. CONNECTIVITY VERIFICATION
   From Dagster code server: verify PostgreSQL catalog read, ClickHouse
   SELECT 1, MinIO bronze bucket list — all three reachable.

7. FIRST COLLECTION RUN
   Trigger one adapter manually in Dagster UI. Verify full path:
   Bronze write → GE validation → Silver write → dead_letter on rejection.
   SELECT count() FROM forge.observations;  # row appears

8. EXPORT VERIFICATION
   Manually trigger Silver → Gold export asset in Dagster UI.
   duckdb -c "SELECT count(*) FROM iceberg_scan('s3://gold/...')"  # readable
```

### Component Failure Modes and Recovery

**PostgreSQL failure:**
1. `docker restart empire_postgres` — resolves most container crashes (< 2 min)
2. Data corruption: `pg_restore` from NAS backup (< 30 min)
3. Connection exhaustion: terminate idle connections (< 5 min)

Data loss risk: Low. Catalog data changes only when new sources/metrics added. Daily
backup adequate.

**ClickHouse failure:**
1. `docker restart empire_clickhouse` — resolves most crashes (< 5 min)
2. Disk full: `TRUNCATE TABLE forge.dead_letter` if unexpectedly large (< 10 min)
3. Merge storm: `SYSTEM STOP MERGES`, investigate with `system.merges`,
   `SYSTEM START MERGES` (< 15 min)
4. Data corruption: ClickHouse checksums catch corrupt parts. Remove corrupt parts,
   re-run export to rebuild Gold (< 60 min)

Data loss risk: Low. Durable writes. Adapter retries are safe (ReplacingMergeTree).

**MinIO failure:**
1. `docker restart empire_minio` — resolves most crashes (< 2 min)
2. Disk full: run Iceberg snapshot expiration for Bronze 90-day retention (< 15 min)
3. Gold corruption: reconstructable — re-run Silver → Gold export Dagster asset
   (< 30 min)
4. Bronze corruption: requires source re-fetch. BLC-01 tick data has no historical
   refetch path — the only copy is in JSONL on Server2.

Data loss risk: Bronze corruption is highest-severity. NAS backup is the mitigation.
Verify NAS backup cadence before Phase 1 goes live.

**Dagster failure:**
1. `docker restart empire_dagster_daemon empire_dagster_webserver empire_dagster_code`
   (< 3 min)
2. Metadata DB corrupted: delete SQLite file and restart — Dagster rebuilds from asset
   definitions. No source data lost. (< 10 min)
3. Missed runs: Dagster `catchup` is configured per asset. Default: run once on
   recovery, not replay all missed intervals.

Data loss risk: None for source data.

**BLC-01 (Server2 / rsync) failure:**
1. SSH to Server2: verify LXC 203 status, VPN, WS collector process, rsync service
2. BLC-01 data loss during outage is not recoverable — tick data is real-time only.
   Accept the gap. `SOURCE_STALE` propagates honestly through the signal stack.

Data loss risk: Any BLC-01 downtime results in permanent tick data loss. Accepted risk.

### Known Infrastructure Gaps

| Gap | Resolution trigger |
|-----|-------------------|
| BLC-01 rsync pull routine — unbuilt | Phase 1 |
| ClickHouse DDL migration file — does not yet exist | Phase 0 corrective action |
| MinIO bucket initialization — not yet created | Phase 0 corrective action |
| Dagster service definition — not yet in docker-compose.yml | Phase 1 |
| Great Expectations setup — not yet installed | Phase 1 |
| Silver → Gold export asset — not yet written | Phase 2 |
| NAS backup job for MinIO — not yet configured | Phase 1, before live collection |
| Dagster metadata DB backup — not yet configured | Phase 1 |

---

## SUCCESS CRITERIA

The design is correctly implemented when:

1. **Revenue to source traceability.** Any customer-facing output can be traced back
   through signals → features → metrics → raw data → source. Every link is a defined
   contract.

2. **Schema immutability.** Adding a new source, metric, instrument, or asset class
   requires zero schema changes — only catalog entries and adapters.

3. **Audit completeness.** Every value has: source, collection timestamp, ingestion
   timestamp, validation status, and revision history.

4. **Consumer ignorance.** Nothing above Layer 2 knows where data came from.
   Changing from one source to another changes one adapter. Nothing else moves.

5. **Validation is structural.** Out-of-range values are rejected at ingestion, not
   discovered months later by audit scripts.

6. **Build is phased and gated.** Each phase has explicit deliverables and hard
   pass/fail criteria.

7. **One operator viability.** The system is operationally simple enough for one
   person to run, debug, and extend.

8. **No unnamed gaps.** Every known data gap has a documented plan.

---

## KNOWN GAPS WITH DOCUMENTED PLANS

| Gap | v1 Handling | Resolution trigger |
|---|---|---|
| `defi.lending.utilization_rate` full computation | v1 proxy: borrow/supply TVL ratio from DeFiLlama. Documented in metric catalog methodology field. | v1.1 milestone (Aave/Compound subgraph) |
| Options data (Deribit) | Null-propagate. `METRIC_UNAVAILABLE` propagates through derivatives features. | v1.1 milestone |
| Exchange flows beyond 18 instruments | Accept coverage limit for v1. | v1.1 milestone |
| BTC directional exchange flows | Null-propagate. CryptoQuant (parked, paid) is the resolution path. | v1.1 milestone |
| EDSx Pillar 3 (Valuation) | Planned, not yet built (REM-21) | REM-21 |
| EDSx Pillar 4 (Structural Risk) | Planned, not yet built (REM-24) | REM-24 |
| EDSx Pillar 5 (Tactical Macro) | Planned, not yet built (REM-22/23). Hard prereq: FRG-10. | REM-22/23 after FRG-10 |
| Layer 2 synthesis design | Deferred to design session before Phase 5 | Before Phase 5 begins |
| CoinMetrics redistribution | Internal use only | Phase 6 ToS audit |
| SoSoValue redistribution | Non-commercial confirmed. | Phase 6 ToS audit or paid tier |
| Coinalyze / BGeometrics / Etherscan redistribution | Unaudited | Phase 6 ToS audit |
| BLC-01 ToS audit | Unaudited — internal only | Phase 6 ToS audit |
| Index/benchmark licensing | Deferred to v2 | Methodology documented + ToS audited |
| ML H2 regime engine (Volatility-Liquidity Anchor) | Rule-based baseline in production | H2 target, after UNI-01 unblocked |
| PBOC balance sheet via FRED | BOJ confirmed in FRED (boj_total_assets). PBOC: evaluate during FRG-10 build. | During FRG-10 / Phase 1 FRED expansion |
| Dashboard / UI | Not in v1 | v2 trigger conditions met (both) |

---

## SOURCES CATALOG (v1)

10 sources at v1.

| Source | Provides | ToS status | Redistribution |
|--------|----------|------------|----------------|
| Coinalyze | Perpetual futures — funding, OI, liquidations, L/S ratio (121 instruments) | Unaudited | Pending Phase 6 audit |
| DeFiLlama | Protocol TVL, DEX volume, lending rates, stablecoins, fees, revenue | Low risk | Yes |
| FRED | 23 macro series (yields, VIX, SPX, DXY, employment, CB balance sheets) | None (public domain) | Yes |
| Tiingo | OHLCV (crypto + equities) | Paid commercial tier | Yes (paid) |
| SoSoValue | ETF flows (BTC/ETH/SOL) | Non-commercial only | **No** |
| Etherscan/Explorer | Exchange flows — ETH + ARB, 9 exchanges, 18 instruments | Unaudited | Pending Phase 6 audit |
| CoinPaprika | Market cap, price data | Low risk | Yes |
| CoinMetrics | On-chain transfer volume (GitHub CSVs) | Unaudited | **No** (pending audit) |
| BGeometrics | MVRV, SOPR, NUPL, Puell (BTC/ETH) | Unaudited | Pending Phase 6 audit |
| Binance (BLC-01) | Tick-level liquidation events (~70k/day, 100+ symbols) | Unaudited | Pending Phase 6 audit |

**Excluded permanently:** Santiment · Glassnode · BSCScan · Solscan
**Parked (paid, not in budget):** CoinGlass · CryptoQuant · CoinMarketCap
**T3 fallback (not catalogued):** CoinGecko · KuCoin

---

## CONSOLIDATED LOCKED DECISIONS

All decisions from all threads in one place.

### Revenue & Product

| Decision | Outcome |
|---|---|
| Primary revenue | Intelligence-as-a-Service (Position 2) |
| Revenue architecture | Multi-stream from day one |
| Content originality | Quantitative, systematic, auditable — not qualitative |
| Asset coverage | Domain-driven, not ticker-driven |
| MVP | Signal product, institutional early access, manual invoicing |
| Index licensing | v2 — deferred |
| Dashboard | v2 — both trigger conditions required |

### Signal Architecture

| Decision | Outcome |
|---|---|
| Track architecture | Two independent tracks (EDSx + ML), shared data and features |
| EDSx confidence | Data completeness (signals_computed / signals_available) |
| EDSx pillar count | Five: trend_structure, liquidity_flow, valuation, structural_risk, tactical_macro |
| EDSx framework | v2.2, three-layer standard, locked |
| ML model count | Five domain models: Derivatives Pressure, Capital Flow Direction, Macro Regime, DeFi Stress, Volatility Regime |
| ML algorithm | LightGBM + isotonic calibration |
| Prediction horizon | 14 days, volume-adjusted labels |
| Label discretization | Tercile boundaries on training window, recalculated each cycle |
| Synthesis weights | 0.5 / 0.5 EDSx / ML default, recalibrated quarterly |
| Regime engine | Separate classifier, market-level. H2 = Volatility-Liquidity Anchor (4 quadrants) |
| Regime is not a pillar | Drives composite weight selection; does not score instruments |
| Graduation | Five hard criteria per model, no self-certification, minimum 30d shadow |
| Magnitude | ML track only |
| Layer 2 synthesis | Future design session before Phase 5 |

### Feature Engineering

| Decision | Outcome |
|---|---|
| Feature versioning | Versioned catalog entry. Formula changes = new version. |
| Null typing | Three distinct null states: INSUFFICIENT_HISTORY, SOURCE_STALE, METRIC_UNAVAILABLE |
| PIT constraint | Absolute. ingested_at ≤ computation_timestamp. No exceptions. |
| Computation trigger | Event-driven on metric ingestion, not wall-clock |
| Computation order | A → C → B → F → G → D → E |
| Idempotency | Hard requirement |
| Breadth score | Deterministic formula, fixed weights, not learned |
| Feature catalog | Required before any feature is computed. Immutable once locked. |

### Data Universe

| Decision | Outcome |
|---|---|
| Schema model | EAV + metric catalog + materialized current-value view |
| Observation store | ClickHouse — forge.observations, ReplacingMergeTree |
| Dead letter | ClickHouse — forge.dead_letter |
| Catalog tables | PostgreSQL — no time series data |
| Analytical layer | DuckDB against Iceberg tables on MinIO (Gold layer) |
| Null instrument_id | Permitted and correct for market-level metrics |
| PIT model | Bitemporal: observed_at + ingested_at. data_version for revisions. |
| Canonical naming | domain.subdomain.metric_name — hierarchical, no abbreviations, immutable |
| Instrument tiers | collection → scoring → signal_eligible, rule-driven |
| Schema immutability | New metric = catalog row. New source = catalog row. Zero DDL. |

### Collection

| Decision | Outcome |
|---|---|
| Landing zone | Iceberg on MinIO, append-only, 90-day retention, S3-compatible |
| Adapter interface | 10-responsibility contract |
| Validation | Per-observation, independent |
| Dead letter | Every rejection logged. Nothing silently dropped. |
| Redistribution | Flagged at source catalog, enforced at serving layer |

### Output Delivery

| Decision | Outcome |
|---|---|
| v1 delivery | API-first. No dashboard. |
| Authentication | Manual key issuance. No self-serve. |
| Free tier | /v1/market/prices, /v1/macro, /v1/instruments |
| Paid tier | /v1/signals, /v1/signals/{id}, /v1/regime, /v1/health |
| Redistribution gating | Coinalyze, CoinMetrics, SoSoValue excluded until Phase 6 ToS audit |
| API versioning | Path-level (/v1/). Breaking changes require /v2/. |
| Webhook | At-least-once delivery, HMAC-signed |
| SLA count | Four: signal freshness (90min), API uptime (99.5%), staleness notification (60min), methodology change (14d) |
| Methodology doc | Public URL, versioned, 8 sections |
| First customer | Phase 6. Direct engagement. Written agreement. No free trials. Manual invoicing. |
| v2 triggers | Both: API conversion friction measurable AND social inbound UI-blocked |

### Infrastructure

| Decision | Outcome |
|---|---|
| Orchestration | Dagster dedicated Docker service (not LXC) |
| Bronze + Gold storage | Apache Iceberg on MinIO |
| Observation store | ClickHouse, ReplacingMergeTree |
| Object storage | MinIO self-hosted, S3-compatible |
| Analytical engine | DuckDB embedded |
| Feature compute | dbt (SQL) + forge_compute Python |
| Catalog | PostgreSQL |
| All managed migrations | Zero code changes — environment variable swaps only |

### Build Plan

| Decision | Outcome |
|---|---|
| Build sequence | Schema → Data → Features → EDSx → ML → Serving |
| Phase gate model | Hard pass/fail. No phase begins until previous gate passes. |
| Migration order | Tiingo first (spot price dependency), then remaining |
| Parallel operation | None. Forge read-only 90 days. |
| ML shadow period | Minimum 30 days |
| ToS audit | Phase 6, before any external data product ships |

---

*Document synthesized: 2026-03-06*
*Authority: thread_1 through thread_7 + thread_infrastructure + FromTheBridge_design_v1_1.md*
*All locked decisions require architect approval to reopen.*
*Next action: Phase 0 — provision infrastructure, apply DDL, seed catalogs, verify gates.*
