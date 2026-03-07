# FromTheBridge — Complete System Design
## Empire Architecture v3.1

**Date:** 2026-03-06
**Synthesized:** 2026-03-06
**Upgraded from:** v2.0 (2026-03-05)
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
> Dagster asset count at Phase 1 launch = ~65 (Option B, per-instrument partitioning).
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
  Populated by event-triggered Silver → Gold export (multi_asset_sensor)
  with 1-hour fallback schedule. See §Silver→Gold Export.

Layer 4: Silver
  Observation Store. ClickHouse.
  EAV: (metric_id, instrument_id, observed_at, value).
  ReplacingMergeTree. Bitemporal: observed_at + ingested_at.
  dead_letter table here. current_values materialized view.
  Write-only except for the export Dagster asset (event-triggered + hourly fallback).

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
  Coinalyze, CFTC COT, DeFiLlama, FRED, Tiingo, SoSoValue,
  Etherscan/Explorer, CoinPaprika, BGeometrics, CoinMetrics,
  Binance (BLC-01). 11 sources at v1.
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
runs the incremental Silver → Gold export. The export fires on two triggers:
(1) `multi_asset_sensor` polling 11 collection asset keys at 30s intervals, and
(2) `@hourly` fallback schedule as a safety net. All analytical workloads go
through Gold (Iceberg on MinIO, read by DuckDB).

**Enforcement:** ClickHouse credentials issued exclusively to the export asset's
environment (`ch_export_reader` — SELECT on `forge.observations FINAL` only). No
other Docker service has a ClickHouse connection string. A direct read from an
unauthorized service fails at authentication. ClickHouse query log records all
connections — unexpected client IPs are immediately visible.

**Why this matters:** DuckDB reads Gold, which is the merged, consistent Iceberg
snapshot. ClickHouse `ReplacingMergeTree` deduplication is eventual — unmerged
rows exist before OPTIMIZE runs. The export query uses `SELECT ... FINAL` to force
deduplication at read time on the watermark delta. Analytical workloads against
Silver would produce silently incorrect results on unmerged data.

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

**Stream 1 — Direct subscriptions (hybrid tier + module model)**
Tiers set access level and contract structure; modules set entitlement scope within
tiers. Identity-neutral naming — tiers describe what the customer gets, not what
they are.

| Tier | Renamed | Audience | Contract | Price |
|---|---|---|---|---|
| Free | Preview | Public | None | $0 |
| Pro | Signal API | Individual traders, quant researchers, small funds | Monthly or annual, self-serve | $199/month · $1,990/year |
| Institutional | Intelligence Suite | Asset managers, crypto hedge funds, family offices | Annual, direct sales | $2,500/month |
| Exchange | Risk Feed | Exchanges, market makers, prop desks | Annual, direct sales | Custom |
| Protocol | Ecosystem Monitor | Foundations, DAOs, protocol treasuries | Annual, direct sales or addendum on Intelligence Suite | $2,500–$5,000/month |

**Preview tier:** 48h-delayed composite top-10 on website only. No account. No API.

**Module-Tier Access Matrix:**

| Module | Preview | Signal API | Intelligence Suite | Risk Feed | Ecosystem Monitor |
|---|---|---|---|---|---|
| Derivatives Intelligence Feed | — | ✅ | ✅ | ✅ | — |
| Market Regime Engine | — | ✅ (30d) | ✅ (full) | ✅ | ✅ |
| Liquidation Risk Monitor | — | — | ✅ | ✅ (primary) | — |
| Flow Intelligence | — | — | ✅ | — | — |
| Protocol Health Score | — | — | — | — | ✅ (primary) |
| On-Chain Valuation Monitor | — | ✅ | ✅ | — | ✅ |

**Coverage by Tier and Licensing State:**

When a source is `blocked` or `pending`, derived fields are null-flagged per the
redistribution enforcement model. This table shows what customers lose per tier:

| Module | Affected Sources | Fields Suppressed When Blocked | Tiers Affected |
|--------|-----------------|-------------------------------|----------------|
| Derivatives Intelligence Feed | Coinalyze (`pending`) | Funding rate, OI, liquidations, L/S ratio | Signal API, Intelligence Suite, Risk Feed |
| Flow Intelligence | SoSoValue (`blocked`), Etherscan (`pending`) | ETF flows (SoSoValue), exchange net flows (Etherscan) | Intelligence Suite |
| Liquidation Risk Monitor | Binance BLC-01 (`pending`) | Real-time tick liquidations | Intelligence Suite, Risk Feed |
| On-Chain Valuation Monitor | BGeometrics (`pending`), CoinMetrics (`blocked`) | MVRV, SOPR, NUPL, Puell (BGeometrics); transfer volume (CoinMetrics) | Signal API, Intelligence Suite, Ecosystem Monitor |
| Protocol Health Score | DeFiLlama (`allowed`) | None — DeFiLlama is redistribution-clear | Ecosystem Monitor |
| Market Regime Engine | FRED (`allowed`) | None — FRED is public domain | All paid tiers |

**DG-R1 resolution effect:** After Phase 4 ToS audits clear Coinalyze, BGeometrics,
Etherscan, and BLC-01, the `pending` rows above transition to `allowed`. Only
SoSoValue and CoinMetrics remain `blocked` at Phase 5 launch — affecting Flow
Intelligence ETF flows and On-Chain transfer volume respectively.

Annual plan at $1,990 = ~16% discount (2 months free). Manual invoicing at sub-20
customers. Stripe deferred: trigger = 20+ active subscribers.

**OPEX:** ~$85/month (Tiingo ~$50, electricity ~$30, domain ~$5).

**Break-even targets:**

| Milestone | Target MRR | Path |
|---|---|---|
| 6 months | $2,000/month | 10 Signal API subscribers OR 1 Ecosystem Monitor engagement ($4.5k) |
| 12 months | $8,000/month | ~35 Signal API OR 20 Signal API + 1 Ecosystem Monitor retainer ($18k/quarter) |
| 18 months | $20,000/month | ~60 Signal API + 2 Ecosystem Monitor retainers + 1 Intelligence Suite |

**Stream 2 — API data licensing (B2B)**
Counterparties pay for normalized, auditable, attributed data to power their own
products. They do not use the interface — they build on the data layer. Customers:
exchanges, funds, fintech builders, index providers. Higher ACV, fewer customers,
relationship-driven. Layer 3 is the product surface for this stream.

**Stream 3 — Protocol / ecosystem reporting (sponsored — separate SKU, not a tier)**
Priority targets: Aave (primary), Uniswap, Lido, ARB Foundation, Solana Foundation
(long-lead). v0 Aave report drafted during Phase 3 as a production validation test.
Ecosystem reports are bespoke consulting engagements fulfilled manually — not an API
product tier. Ecosystem Monitor tier API access and consulting engagements are
independent: a protocol client may have both, either, or neither. Consulting uses
custom contract addenda, not the entitlement middleware.

| Engagement Type | Price | Notes |
|---|---|---|
| Founding rate (first engagement only) | $4,500 one-time | Buys reference + case study |
| Standard one-time deep report | $7,500 | Floor after first engagement |
| 90-day retainer (3 monthly reports) | $18,000 | Preferred recurring structure |
| Quarterly retainer | $20,000–25,000 | Established relationships, expanded scope |

Payment terms: 50% on engagement, 50% on delivery. Never go below $4,500.

**Stream 4 — Index / benchmark licensing (future state)**
Rules-based index constructed from the canonical data, licensed to financial product
issuers for settlement benchmarks. Deferred until live conversion evidence exists —
requires methodology documentation, legal structure, and proven demand. The data
infrastructure is already the hard part, but commercial emphasis stays on Streams 1–3
until index licensing has an identified buyer.

**Stream 5 — Embedded analytics / white-label (future state)**
Signal engine or data feeds embedded in a fund or exchange's own interface. Sticky,
large contracts, longer sales cycles. Deferred until Streams 1–3 have traction —
no commercial emphasis until an inbound request validates demand.

### Content Originality

The differentiator is not qualitative research dressed up with charts. It is:
**systematic, backtested, quantitative signals with documented methodology and
PIT-correct historical data.** This is what Glassnode, Kaiko, and Messari do not
produce. EDSx's deterministic scoring with full audit trail, combined with calibrated
ML probabilities, is a genuine differentiation in a market full of opinion-based
research.

### Customer Profiles and Acquisition (F1)

**Profile A — Prosumer Quant (acquired first):**
Technically sophisticated independent crypto trader, fund analyst, or quant researcher.
Currently builds own signal infrastructure or pays $99–499/month for raw data. Understands
funding rates, OI, MVRV, macro overlays. Sub-$10M discretionary book or small fund
research. Acquired via content pull — no cold outreach.

**Profile B — Protocol Ecosystem (acquired second):**
Protocol foundation or ecosystem DAO. Direct outreach + warm intro path. Minimum 3
Profile A subscribers + 60-day live history before first approach. Revenue leverage:
the first $4,500–$18,000 engagement is worth more than 50 Signal API subscribers.

**Sequencing:** Profile A builds reputation that funds the business via Profile B.
Shadow period → Profile A content pull (shadow week 4) → Signal API tier opens (Phase 5
gate) → Profile B outreach at 60 days post-launch.

**48h-Delayed Public Preview:**
Top-10 composite score (top 5 / bottom 5) on `fromthebridge.net`, updated daily, 48h
delayed. No account required. No pillar detail, no ML breakdown, no confidence values.
Live at shadow week 2 (Phase 3 EDSx confirmed — EDSx-only composite; ML not
included until graduation). Purpose: demonstrate operational
cadence and attract organic Profile A discovery.

**48h Preview Implementation Spec:**

| Aspect | Specification |
|---|---|
| Data source | `signal_snapshot_writer` Dagster asset (same as API cache) |
| Delay mechanism | Snapshot written to `gold/snapshots/preview/{date}.json` with 48h lag — reads snapshot from `CURRENT_DATE - 2` |
| Instrument selection | Top 5 and bottom 5 by `final_score` from composite track, filtered to `collection_tier = 'signal_eligible'` instruments only |
| Fields displayed | `instrument_id`, `canonical_symbol`, `final_score`, `direction` (bullish/bearish/neutral), `computed_at` |
| Fields excluded | Pillar scores, ML probabilities, confidence, regime, null states — no detail beyond composite |
| Endpoint | `GET /v1/preview` — unauthenticated, public, rate-limited (60 req/min per IP) |
| Rendering | Server-side rendered HTML at `fromthebridge.net` (Next.js page). No JavaScript API call from browser. |
| Redistribution | Preview data is 48h delayed and aggregated (top/bottom only). Redistribution-blocked sources (SoSoValue, CoinMetrics) contribute to composite score but individual source attribution is not exposed. |
| Phase gate | Phase 4 gate row: "48h public preview operational on `fromthebridge.net` by shadow week 2" |

**Pre-Launch Content Strategy:**
- Single highest-leverage asset: 2,000-word PIT-correctness post published simultaneously
  as Twitter/X thread + Substack. Links to methodology doc and GitHub schema. Written
  once, never needs updating.
- Public GitHub repo: metric catalog schema (sanitized), PIT-correctness README, EDSx
  pillar weight specification (not implementation code). Methodology transparency signal,
  not open source.
- 90-day sprint: ~27 hours total (~1.7h/week). All GTM work batched during natural
  build pauses.

**90-Day Sprint Timeline:**

| Week | Action | Hours |
|---|---|---|
| 1 | Minimal landing page: system description, methodology teaser, waitlist | 2h |
| 2 | Initialize Twitter/X account. Follow relevant accounts. Do not post. | 1h |
| 3 | Draft PIT-correctness post (2,000 words). Do not publish. | 3h |
| 4 | Phase 3 live. Enable 48h-delayed public preview on website. | 2h |
| 5 | Publish PIT post as Twitter/X thread + Substack simultaneously. | 1h |
| 6 | Initialize public GitHub repo: schema + formula + PIT README. | 2h |
| 7 | Draft methodology document (~3,000 words, all pillars, null states, PIT). | 3h |
| 8 | Publish methodology at fromthebridge.net/methodology. Draft v0 Aave report. | 4h |
| 10 | Phase 4 shadow begins. Second Twitter/X thread (domain market structure). | 2h |
| 12 | Add pricing page. Launch Signal API tier. First Ecosystem Monitor outreach to Aave. | 3h |
| 14 | Third Twitter/X thread using shadow performance data. | 2h |
| 16 | Follow up Aave. Introduce pricing. Draft Uniswap outreach. | 2h |

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
| Not in v1 | Dashboard UI, self-serve billing, content products |
| Index licensing | v2 — deferred. Trigger: methodology documented + ToS audited |
| Signal API pricing | $199/month · $1,990/year (~16% discount). Manual invoicing. (F1) |
| Ecosystem Monitor pricing | $2,500–$5,000/month API tier. Consulting engagements (Stream 3) priced separately. (F1) |
| Intelligence Suite pricing | $2,500/month. Sales-only. Phase 6. (F1) |
| Risk Feed pricing | Custom. Annual contract, direct sales. (F1) |
| Stripe | Deferred. Trigger: 20+ active subscribers. (F1) |
| Profile A acquisition | Content pull. No cold outreach. Self-serve after Phase 5 gate. (F1) |
| Profile B acquisition | Direct outreach + warm intro. ≥3 Profile A + 60d history first. (F1) |
| Public preview | 48h-delayed top-10 composite. No account. Shadow week 2. (F1) |
| Primary content asset | PIT-correctness post (2,000 words). Twitter/X + Substack. (F1) |
| GTM sprint | ~27 hours / 90 days. Batched during build pauses. (F1) |
| Signal API tier opens | After Phase 5 gate (not Phase 4 shadow). (F1) |

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

> **Superseded by §L2.7 for VLA production weights.** This table applies only during the
> legacy M2-only regime period. When VLA promotes to production, §L2.7 pillar weights
> replace this table. See §L2.7 "VLA Regime — Pillar Weights per Quadrant."

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

### Signal Synthesis (Layer 2 — Algorithm Specification)

Layer 2 consumes the structured outputs of the five EDSx pillar scorers (three
horizons each) and the five ML domain model outputs (14-day horizon), and produces
the customer-facing composite signals. This specification defines every formula,
scale, and null-handling rule with sufficient precision for unambiguous Python
implementation.

**Input scales:**
- EDSx PillarScore: `score ∈ [0, 1]`. Centered form `c = 2×score − 1 ∈ [−1, 1]`.
- ML domain models: `p_bullish, p_neutral, p_bearish ∈ [0, 1]`, sum = 1.0.
- All synthesis intermediates and final composite: `[0, 1]`.
- Direction scalars: `[−1, 1]` (internal only; converted to [0, 1] before output).

#### §L2.1 — Horizon Alignment

EDSx scores three horizons (1D, 7D, 30D). The ML track produces a single 14-day
forecast. Layer 2 emits three customer-facing composite signals.

| Signal Horizon | EDSx Input | ML Input | ML Weight Adjustment |
|---|---|---|---|
| **1D** | EDSx 1D composite | None | ML excluded (14D horizon incompatible with 1D signal) |
| **7D** | EDSx 7D composite | ML composite (14D) | Full weight (`w_ml_default`) |
| **30D** | EDSx 30D composite | ML composite (14D) | Discounted (`w_ml_30d = w_ml_default × 0.70`) |

**Rationale for 1D exclusion:** ML training labels are 14-day volume-adjusted
returns. There is no calibrated ML output at a 1-day horizon. The 1D signal is
EDSx-only with track = `"edsx_only"`.

**Rationale for 30D discount:** The ML horizon (14D) is directionally consistent
with 30D structural positioning but is not calibrated to it. The 30% discount
reflects horizon mismatch without discarding the directional information entirely.

**Customer-facing labels:**

| Signal | Label | Semantics |
|---|---|---|
| `signals.1D` | Short-term | 24h tactical direction. EDSx only. |
| `signals.7D` | Medium-term | 7–14D forward. Primary synthesized signal. |
| `signals.30D` | Structural | 30D positioning. EDSx-dominant with ML cross-validation. |

**Python representation:**
```python
HORIZON_ML_WEIGHTS = {
    "1D":  0.00,   # ML excluded
    "7D":  1.00,   # full ML weight (w_ml_default applied)
    "30D": 0.70,   # 70% of w_ml_default
}
```

#### §L2.2 — EDSx Pillar Aggregation

**Step 1: Select Regime Pillar Weights.** Pillar base weights are a function of
the active regime. During the legacy M2-only regime period, use the three-regime
table from the Pillar Weights section. After VLA promotion (H2), use the VLA
quadrant tables from §L2.7 below.

**Step 2: Structural Risk Modulation.** Structural Risk has architectural
privilege. Before any other adjustments:

```python
SR_THRESHOLD = 0.80
SR_MAX_MULTIPLIER = 2.0

if s_sr is not None and s_sr >= SR_THRESHOLD:
    r = (s_sr - SR_THRESHOLD) / (1.0 - SR_THRESHOLD)   # r ∈ [0, 1]
    sr_multiplier = 1.0 + r                              # ∈ [1.0, 2.0]
    w_sr_new = w_sr_base * sr_multiplier
    # Redistribute excess proportionally from all other non-null pillars
    excess = w_sr_new - w_sr_base
    other_weight_sum = sum(w[p] for p in active_pillars if p != "structural_risk")
    for p in active_pillars:
        if p != "structural_risk":
            w[p] = w[p] * (1.0 - excess / other_weight_sum)
    w["structural_risk"] = w_sr_new
else:
    sr_multiplier = 1.0
```

**Step 3: Apply Guardrails Per Active Pillar.** Evaluated on `confidence_base`
per pillar per horizon. Applied in order G3 → G2 → G1.

```python
for pillar in active_pillars:
    cb = confidence_base[pillar][horizon]
    if cb < 0.10:                          # G3: zero weight
        w[pillar] = 0.0
    elif cb < 0.30:                        # G2: clamp score + half weight
        score[pillar][horizon] = clamp(score[pillar][horizon], 0.30, 0.70)
        w[pillar] *= 0.5
    elif cb < 0.50:                        # G1: half weight only
        w[pillar] *= 0.5
```

**Step 4: Handle Null Pillars.** A pillar is null if: (a) it is PLANNED and not
yet built, (b) all sub-scores are null, or (c) G3 fired. Null pillars receive
`w[pillar] = 0.0`.

**Step 5: Renormalize.**

```python
total_w = sum(w[p] for p in active_pillars if w[p] > 0)

if total_w == 0.0:
    edsx_composite[horizon] = None
    edsx_null_reason[horizon] = "all_pillars_null_or_zeroed"
else:
    w_norm = {p: w[p] / total_w for p in active_pillars}
    edsx_composite[horizon] = sum(w_norm[p] * score[p][horizon]
                                  for p in active_pillars if w[p] > 0)
    edsx_confidence[horizon] = sum(w_norm[p] * confidence_base[p][horizon]
                                   for p in active_pillars if w[p] > 0)
    active_pillar_count[horizon] = sum(1 for p in active_pillars if w[p] > 0)
```

**Result:** `edsx_composite[horizon] ∈ [0, 1]`, or `None`.
**Degradation flag:** `edsx_degraded[horizon] = (active_pillar_count[horizon] < 5)`

**v1 production note:** With Trend/Structure and Liquidity/Flow live and three
pillars null, this produces a valid two-pillar composite. `edsx_degraded = True`,
`active_pillar_count = 2`. This is expected and correct.

#### §L2.3 — ML Model Aggregation

**Step 1: Classify Model Roles.**

| Model | Role | Direction Contribution |
|---|---|---|
| Derivatives Pressure | Directional | `d = p_bullish − p_bearish` |
| Capital Flow Direction | Directional | `d = p_inflow − p_outflow` |
| Macro Regime | Directional | `d = risk_appetite` scalar ∈ [−1, 1] |
| DeFi Stress | Directional (inverted) | `d = p_normal − p_critical` |
| Volatility Regime | Conditioner | Modulates weights; does NOT contribute direction |

**Step 2: Compute Per-Model Weights.** Base weight = `feature_coverage`. Entropy
discount applied:

```python
MAX_ENTROPY_3CLASS = 1.5849625   # log2(3)

def entropy_discount(p_vec: list[float]) -> float:
    h = -sum(p * math.log2(p) for p in p_vec if p > 0)
    return 1.0 - 0.5 * (h / MAX_ENTROPY_3CLASS)
    # range: 0.50 (uniform) to 1.00 (perfect certainty)

def model_weight(coverage: float, p_vec: list[float]) -> float:
    return coverage * entropy_discount(p_vec)
```

**Step 3: Volatility Regime Conditioning.** The Volatility Regime model modulates
the weights of the four directional models:

```python
VOL_REGIME_MULTIPLIERS = {
    #              deriv   flow   macro  defi
    "compressed": (1.20,   1.10,  0.90,  0.90),
    "normal":     (1.00,   1.00,  1.00,  1.00),
    "elevated":   (0.85,   0.90,  1.10,  1.15),
    "extreme":    (0.70,   0.80,  1.20,  1.20),
}
```

**Step 4: Minimum Viable Model Count.** The ML composite requires at least 2
active directional models. If fewer than 2 have non-null output: ML composite =
null (`insufficient_active_models`).

**Step 5: Compute ML Direction Scalar and Composite.**

```python
if len(active_directional) >= 2:
    total_w = sum(w[m] for m in active_directional)
    d_weighted = sum(w[m] * model_direction(m, output[m])
                     for m in active_directional) / total_w
    ml_composite = (d_weighted + 1.0) / 2.0   # map [-1,1] to [0,1]
    ml_coverage = mean(output[m].feature_coverage for m in active_directional)
    ml_degraded = len(active_directional) < 4
```

#### §L2.4 — Track-Level Agreement and Confidence Adjustment

**Agreement Score:**

```python
def agreement_score(edsx: float, ml: float) -> float:
    edsx_centered = edsx - 0.5
    ml_centered   = ml   - 0.5
    return (edsx_centered * ml_centered) / 0.25  # ∈ [-1, 1]
```

**Confidence Boost / Penalty:**

```python
AGREEMENT_BOOST_FACTOR   = 0.15   # max +15% on strong agreement
AGREEMENT_PENALTY_FACTOR = 0.20   # max −20% on strong disagreement
DISAGREEMENT_FLAG_THRESHOLD = -0.40

agr = agreement_score(edsx_composite_h, ml_composite)
confidence_boost   = max(0.0, agr)  * AGREEMENT_BOOST_FACTOR
confidence_penalty = max(0.0, -agr) * AGREEMENT_PENALTY_FACTOR
disagreement_flag  = agr < DISAGREEMENT_FLAG_THRESHOLD
```

**Base Composite Confidence:**

```python
base_composite_confidence = (
    w_edsx_effective * edsx_confidence[horizon]
    + w_ml_effective  * ml_coverage
)
composite_confidence = clamp(
    base_composite_confidence + confidence_boost - confidence_penalty,
    0.0, 1.0
)
```

**Single-track fallback:** If EDSx is null, confidence = `ml_coverage`.
If ML is null, confidence = `edsx_confidence[horizon]`.

#### §L2.5 — Final Composite Score and Direction Classification

**Synthesis Weights:**

```python
W_EDSX_DEFAULT = 0.50
W_ML_DEFAULT   = 0.50

w_ml_horizon   = W_ML_DEFAULT * HORIZON_ML_WEIGHTS[horizon]
w_edsx_horizon = 1.0 - w_ml_horizon

# Single-track degeneracy
if edsx_composite[horizon] is None and ml_composite is None:
    final_score[horizon] = None; track[horizon] = "null"
elif edsx_composite[horizon] is None:
    final_score[horizon] = ml_composite; track[horizon] = "ml_only"
elif ml_composite is None or HORIZON_ML_WEIGHTS[horizon] == 0.0:
    final_score[horizon] = edsx_composite[horizon]; track[horizon] = "edsx_only"
else:
    final_score[horizon] = (w_edsx_horizon * edsx_composite[horizon]
                            + w_ml_horizon * ml_composite)
    track[horizon] = "synthesized"
```

**Direction Classification:**

```python
BULLISH_THRESHOLD = 0.55
BEARISH_THRESHOLD = 0.45

def classify_direction(score: float | None) -> str | None:
    if score is None: return None
    if score >= BULLISH_THRESHOLD: return "bullish"
    if score <= BEARISH_THRESHOLD: return "bearish"
    return "neutral"
```

#### §L2.6 — Null Propagation Rules (Complete)

Every null state carries an explicit reason. Silent nulls are not permitted.

**EDSx Null Chain:**

| Condition | EDSx Composite Result | Reason Code |
|---|---|---|
| All pillars null or G3-zeroed | `None` | `all_pillars_null_or_zeroed` |
| 1 pillar active with G2 | Computed, `edsx_degraded=True` | `single_pillar_g2` |
| Pillar score itself null | Excluded via `w[p]=0` | propagated upstream |

**ML Null Chain:**

| Condition | ML Composite Result | Reason Code |
|---|---|---|
| ML not graduated | `None` | `ml_not_graduated` |
| < 2 directional models active | `None` | `insufficient_active_models` |
| Model `feature_coverage < 0.20` | Model excluded | `low_coverage_excluded` |
| Vol Regime null | No conditioning (multipliers = 1.0) | — |

**Synthesis Null Chain:**

| EDSx | ML | Result | `track` |
|---|---|---|---|
| non-null | non-null | Synthesized formula | `"synthesized"` |
| non-null | null | EDSx only | `"edsx_only"` |
| null | non-null | ML only | `"ml_only"` |
| null | null | `None` | `"null"` |

**1D horizon:** ML is always excluded (`HORIZON_ML_WEIGHTS["1D"] = 0.0`), so 1D
is always `"edsx_only"` or `"null"`. This is designed, not degradation.

#### §L2.7 — Regime Weighting

**Legacy Regime:** During the M2-only regime period, synthesis weights are fixed
at 0.5/0.5. The regime affects only EDSx pillar weights.

**VLA Regime — Pillar Weights per Quadrant:**

When the VLA engine promotes to production, these weight matrices replace the
legacy three-regime tables:

| Pillar | Full Offense | Selective Offense | Defensive Drift | Capital Preservation |
|---|---|---|---|---|
| Trend/Structure | 0.32 | 0.28 | 0.18 | 0.12 |
| Liquidity/Flow | 0.28 | 0.22 | 0.18 | 0.12 |
| Valuation | 0.12 | 0.15 | 0.22 | 0.20 |
| Structural Risk | 0.08 | 0.15 | 0.25 | 0.45 |
| Tactical Macro | 0.20 | 0.20 | 0.17 | 0.11 |

**VLA Regime — EDSx/ML Synthesis Weights:**

| Quadrant | w_edsx | w_ml | Rationale |
|---|---|---|---|
| Full Offense | 0.40 | 0.60 | Trending: ML models most reliable |
| Selective Offense | 0.50 | 0.50 | Default balanced (high uncertainty) |
| Defensive Drift | 0.55 | 0.45 | Structural signals more reliable in slow unwinding |
| Capital Preservation | 0.65 | 0.35 | SR pillar dominates; ML less reliable in crisis |

These replace `W_EDSX_DEFAULT` and `W_ML_DEFAULT` in §L2.5. Horizon adjustment
multipliers from §L2.1 apply on top.

**VLA Quadrant Boundary Blending:** At quadrant boundaries, sigmoid-blend adjacent
weight sets to prevent whipsaw. Blending applied when either VLA axis score is
within `BLEND_ZONE = 0.15` of the quadrant dividing line (0.50).

#### §L2.8 — Output Schema: /v1/signals Response (Per Instrument)

All fields nullable unless marked (required). Null fields must be present with
explicit `null` value — omission is not permitted.

```python
SignalResponse = {
    # Identity
    "instrument_id":       str,             # required
    "as_of":               str,             # ISO 8601, required
    "synthesis_version":   str,             # "1.0", required

    # Regime context
    "regime":              str | None,      # current regime label
    "regime_source":       str | None,      # "legacy_m2" | "vla"
    "regime_quadrant":     str | None,      # VLA only

    # Per-horizon synthesized signals
    "signals": {
        "1D": {
            "score":               float | None,   # [0, 1]
            "direction":           str | None,     # "bullish"|"neutral"|"bearish"
            "confidence":          float | None,   # [0, 1]
            "track":               str,            # "synthesized"|"edsx_only"|"ml_only"|"null"
            "synthesis_weights":   {"edsx": float, "ml": float},
            "agreement":           float | None,   # [-1, 1]; null if single-track
            "disagreement_flag":   bool,
            "staleness_seconds":   int | None,
        },
        "7D": { "..." },
        "30D": { "..." }
    },

    # Magnitude (ML track only)
    "flow_magnitude":      float | None,    # [0, 1]; null until ML graduates
    "magnitude_source":    str | None,      # "capital_flow_direction"

    # EDSx component (per horizon)
    "edsx": {
        "1D": {
            "composite":       float | None,
            "confidence":      float | None,
            "active_pillars":  int,
            "pillar_coverage": float,
            "degraded":        bool,
            "pillars": {
                "trend_structure":  "PillarDetail | None",
                "liquidity_flow":   "PillarDetail | None",
                "valuation":        "PillarDetail | None",
                "structural_risk":  "PillarDetail | None",
                "tactical_macro":   "PillarDetail | None",
            }
        },
        "7D": { "..." },
        "30D": { "..." }
    },

    # ML composite (single 14D horizon)
    "ml": {
        "composite":           float | None,
        "direction_scalar":    float | None,   # [-1, 1]
        "active_models":       int,
        "model_coverage":      float,
        "degraded":            bool,
        "graduated":           bool,
        "vol_regime_conditioning_applied": bool,
        "models": {
            "derivatives_pressure":  "MLModelDetail | None",
            "capital_flow_direction": "MLModelDetail | None",
            "macro_regime":          "MLModelDetail | None",
            "defi_stress":           "MLModelDetail | None",
            "volatility_regime":     "MLModelDetail | None",
        }
    },

    # Provenance (immutable audit trail)
    "provenance": {
        "edsx_pillar_weights_used":       "dict[str, float]",
        "ml_model_weights_used":          "dict[str, float]",
        "structural_risk_modulation":     bool,
        "sr_multiplier":                  float,
        "guardrails_applied":             "list[str]",
        "vol_regime_multipliers_applied": "dict[str, float] | None",
        "agreement_boost":                float,
        "agreement_penalty":              float,
        "null_inputs":                    "list[str]",
        "null_reasons":                   "dict[str, str]",
    },

    # Redistribution notice (present when fields suppressed; null otherwise)
    "_redistribution_notice": {
        "fields_suppressed":  int,
        "detail":             "[{field: str, status: str, sources: list[str]}]",
    } | None,
}

PillarDetail = {
    "score":             float | None,
    "confidence":        float | None,
    "weight_used":       float,
    "guardrail":         str | None,     # "G1" | "G2" | "G3" | None
    "null_reason":       str | None,
    "freshness_seconds": int | None,
}

MLModelDetail = {
    "p_bullish":          float | None,
    "p_neutral":          float | None,
    "p_bearish":          float | None,
    "feature_coverage":   float | None,
    "prediction_entropy": float | None,
    "weight_used":        float,
    "null_reason":        str | None,
}
```

**`confidence_tier`** is a human-readable label: `low` (< 0.40), `moderate`
(0.40–0.59), `high` (0.60–0.79), `very_high` (≥ 0.80). Always present alongside
the float.

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
| Layer 2 synthesis | Designed and locked (§L2.1–L2.8). EDSx/ML composite algorithm, horizon alignment, null propagation, VLA regime weights, §L2.8 response schema. |

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
| Lending utilization zscore | `defi.lending.utilization_rate` | zscore | 52w |
| Lending utilization change | `defi.lending.utilization_rate` | change_pct | 28d |
| Supply APY | `defi.lending.supply_apy` | value | 1 period |
| Borrow APY | `defi.lending.borrow_apy` | value | 1 period |
| Borrow-supply spread | `defi.lending.borrow_apy` − `defi.lending.supply_apy` | derived ratio | 1 period |
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
| Institutional net position | `macro.cot.institutional_net_position` | value | 1 period |
| Institutional net change | `macro.cot.institutional_net_position` | change | 4w |
| Institutional long pct | `macro.cot.institutional_long_pct` | value | 1 period |
| COT open interest | `macro.cot.open_interest_contracts` | value | 1 period |
| Dealer net position | `macro.cot.dealer_net_position` | value | 1 period |

> **CFTC COT PIT anchor:** All `macro.cot.*` features must use `ingested_at` (Friday
> release) as the PIT anchor, not `observed_at` (Tuesday as-of date). The 3-day
> publication lag means data describing Tuesday positioning is not available until
> Friday. Using `observed_at` as the PIT anchor would introduce look-ahead bias.
> See adapter spec for `observed_at`/`ingested_at` semantics.

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
| Market regime | RISK_ON / RISK_OFF / TRANSITION — derived from Macro Regime model `risk_appetite` scalar: ≥ 0.6 → RISK_ON, ≤ 0.4 → RISK_OFF, else TRANSITION | Categorical (3) |

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

**Performance Marts → Layer 6 (B3):**

Two additional dbt marts are required for the performance history endpoint. These live
in Layer 6 (Marts) and depend on `marts.signals_history` (Phase 3 output) and
`gold.prices_ohlcv` (Layer 5). They are not feature engineering outputs but share the
same PIT discipline:

- **`signal_outcomes`** — One row per `(signal_id, horizon_days)`. PIT JOIN on Tiingo
  closes strictly after `computed_at`. Backfill guard: `ingested_at_signal <= outcome_observed_at`.
  Daily incremental (resolved outcomes only).
- **`performance_metrics`** — Rolling aggregations per `(instrument_id, track, horizon_days,
  window_days)`. ~7,380 rows total. Recomputed daily after `signal_outcomes` update.

Full specification in §Performance History Endpoint (Thread 7).

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
        CHECK (collection_tier IN ('collection','scoring','signal_eligible','system'))
);
```

**Tier semantics:**
- `collection` — data collected, not yet sufficient for scoring
- `scoring` — sufficient data quality and history for EDSx and feature computation
- `signal_eligible` — meets all thresholds for signal output to customers

Tier promotion is rule-driven and automatic. Changes logged with timestamp and reason.

**System-reserved instrument: `__market__`**

A single row in `forge.instruments` with `canonical_symbol = '__market__'`,
`asset_class = 'index'`, `collection_tier = 'system'`, `base_currency = NULL`,
`quote_currency = NULL`. Represents aggregate/market-level metrics that don't
belong to a single instrument: DeFiLlama aggregate TVL, breadth scores, macro
series, stablecoin totals, market cap totals. Every adapter that produces
market-level metrics writes Silver observations against this instrument_id.
Never promoted to `signal_eligible` — system instruments don't produce signals.
Must be seeded in Phase 0 instruments migration.

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
    redistribution          BOOLEAN,               -- deprecated; replaced by redistribution_status
    redistribution_status   TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (redistribution_status IN ('allowed', 'pending', 'blocked')),
    propagate_restriction   BOOLEAN     NOT NULL DEFAULT true,
    redistribution_notes    TEXT,
    redistribution_audited_at TIMESTAMPTZ,
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
ORDER BY (rejection_code, source_id, rejected_at)
TTL rejected_at + INTERVAL 90 DAY DELETE;
```

#### forge.current_values (ClickHouse)

Materialized view providing latest observation per `(metric_id, instrument_id)`.
Used by the export asset to compute watermark deltas efficiently. Not queried by
any other service (Rule 2 still applies — only the export asset reads ClickHouse).

```sql
CREATE MATERIALIZED VIEW forge.current_values
ENGINE = AggregatingMergeTree()
ORDER BY (metric_id, instrument_id)
AS SELECT
    metric_id,
    instrument_id,
    argMaxState(value, observed_at)       AS latest_value,
    maxState(observed_at)                 AS latest_observed_at,
    maxState(ingested_at)                 AS latest_ingested_at
FROM forge.observations
GROUP BY metric_id, instrument_id;
```

**Query pattern:** `SELECT metric_id, instrument_id, argMaxMerge(latest_value) AS value, maxMerge(latest_observed_at) AS observed_at FROM forge.current_values GROUP BY metric_id, instrument_id`.

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

**Authoritative counts:** Phase 0 seed = 74 metrics (per `0001_catalog_schema.sql`
verification comment). Phase 1 additions = 8 metrics (2 DeFi protocol + 4 CFTC COT +
2 derived). Total at Phase 1 completion = 82 metrics across 9 domains.

**Derivatives domain (per instrument, 8h):**
`derivatives.perpetual.funding_rate` · `derivatives.perpetual.open_interest_usd` ·
`derivatives.perpetual.liquidations_long_usd` · `derivatives.perpetual.liquidations_short_usd` ·
`derivatives.perpetual.price_usd` · `derivatives.options.delta_skew_25` ·
`derivatives.options.iv_1w` · `derivatives.options.iv_1m`

**Spot domain (per instrument, 1d):**
`spot.price.close_usd` · `spot.volume.usd_24h` · `spot.market_cap.usd`

**Flows domain (per instrument, 1d):**
`flows.exchange.inflow_usd` · `flows.exchange.outflow_usd` ·
`flows.exchange.net_flow_usd` · `flows.onchain.transfer_volume_usd`

> **Note on `flows.exchange.net_flow_usd`:** Derived metric: `inflow_usd - outflow_usd`.
> Computed by the Etherscan/Explorer adapter at collection time and written to Silver
> alongside the source metrics. Phase 1 catalog addition (not in Phase 0 seed —
> Etherscan adapter not yet built).

**Stablecoin domain (1d):**
`stablecoin.supply.total_usd` · `stablecoin.supply.per_asset_usd` ·
`stablecoin.peg.price_usd`

**ETF domain (per product, 1d):**
`etf.flows.net_flow_usd` · `etf.aum.total_usd`

**DeFi domain (12h cadence for lending, 1d for protocol/dex):**
`defi.aggregate.tvl_usd` · `defi.protocol.tvl_usd` · `defi.dex.volume_usd_24h` ·
`defi.lending.utilization_rate` · `defi.lending.supply_apy` ·
`defi.lending.borrow_apy` · `defi.lending.reward_apy`

> **Note on `defi.lending.utilization_rate`:** The canonical name is `utilization_rate`
> (concept-driven, not implementation-driven). Phase 1 replaces the v1 proxy
> (borrow/supply TVL ratio) with direct pool utilization from the DeFiLlama `/yields`
> endpoint. The canonical name is unchanged (schema immutability). The `methodology`
> field is updated in place. See RESULT_ID_E3.

> **Note on `defi.lending.reward_apy`:** Instruments sourced solely from this metric
> are not promoted to `collection_tier = 'signal_eligible'` until v1.1. High reward
> APY distorts signal interpretation without incentive-regime context. The eligibility
> gate is the `instruments.collection_tier` column, not a flag on `metric_catalog`.

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

**CFTC COT domain (weekly, BTC + ETH) — Phase 1 addition (E4):**
`macro.cot.institutional_net_position` · `macro.cot.institutional_long_pct` ·
`macro.cot.open_interest_contracts` · `macro.cot.dealer_net_position`

> **Note on CFTC COT:** Source is the CFTC Traders in Financial Futures (TFF) report
> via Socrata API (`data.cftc.gov`). Released Fridays at 15:30 ET; `observed_at` is
> the Tuesday as-of date (not Friday release). Features must account for 3-day
> publication lag. Instruments: BTC (CME Bitcoin futures + Micro BTC aggregated) and
> ETH (CME Ether futures). Signal relevance: EDSx-05 Tactical Macro (REM-22/23),
> ML Capital Flow Direction.

**Derived (market-level, 1d):**
`spot.market_cap.total_crypto_usd` · `spot.dominance.btc_pct`

### Database Engine Summary

**Catalog (PostgreSQL):** instruments, metrics, sources, and operational tables.
Relational integrity, foreign keys, audit trail. No time series data here.

**Observation store (ClickHouse):** `forge.observations` and `forge.dead_letter`.
Columnar storage, ReplacingMergeTree, write-only except for the event-triggered
export Dagster asset (`SELECT ... FINAL` with 3-minute lag floor).

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
No transformations occur before Bronze write. Append-only. S3-compatible — MinIO
endpoint swap migrates to S3 with zero code changes.

**Two-Bucket Architecture (C2):**

| Attribute | `bronze-hot` | `bronze-archive` |
|---|---|---|
| Retention | 90 days (MinIO lifecycle expiry) | Indefinite — no expiry |
| Written by | Collection adapters (Layer 2) exclusively | Dagster archive asset exclusively |
| Read by | Silver export Dagster asset exclusively | Reprocessing operations only (ad hoc) |
| MinIO credential | `MINIO_BRONZE_HOT_USER` | `MINIO_BRONZE_ARCHIVE_USER` (isolated) |
| Iceberg schema | `(source_id, date, metric_id)` partitioned | Identical schema and partition spec |
| Post-migration (S3) | S3 Standard | S3 Intelligent-Tiering |

`bronze-archive` is a separate Iceberg table with its own catalog entry, not a
continuation of `bronze-hot`. Credential isolation: `MINIO_BRONZE_HOT_USER` has no
write permission to `bronze-archive` and vice versa.

**Archive Job:** Daily at 02:00 UTC via Dagster. Window: `today-9` to `today-2` (2-day
lag, 88-day safety margin before hot expiry). Idempotency via `forge.bronze_archive_log`
(PostgreSQL — admin metadata only, Rule 3 compliant). Partition discovery via DuckDB
over Iceberg metadata (~50ms). Verification: file count + byte count comparison,
`checksum_verified` flag.

**`bronze_archive_log` DDL (Phase 1 deliverable):**

```sql
CREATE TABLE forge.bronze_archive_log (
    id                BIGSERIAL PRIMARY KEY,

    -- identity
    source_id         INTEGER     NOT NULL REFERENCES forge.source_catalog(id),
    metric_id         INTEGER     NOT NULL REFERENCES forge.metric_catalog(id),
    partition_date    DATE        NOT NULL,

    -- location
    archive_path      TEXT        NOT NULL,   -- MinIO object key, e.g. s3://bronze-archive/coinalyze/2026/03/06/funding_rate.parquet
    byte_size         BIGINT,

    -- content
    row_count         INTEGER     NOT NULL,
    observed_at_min   TIMESTAMPTZ,            -- earliest observed_at in the file
    observed_at_max   TIMESTAMPTZ,            -- latest observed_at in the file

    -- integrity
    checksum          TEXT        NOT NULL,   -- SHA-256 of the archive file
    checksum_verified BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_at       TIMESTAMPTZ,

    -- audit
    archived_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archive_job_run_id TEXT,                  -- Dagster run ID for traceability

    -- idempotency
    UNIQUE (source_id, metric_id, partition_date)
);

CREATE INDEX ON forge.bronze_archive_log (source_id, partition_date);
CREATE INDEX ON forge.bronze_archive_log (archived_at);
-- Rule 3 compliant: admin metadata only, no observed_at + value columns.
-- observed_at_min/max are coverage bounds, not metric observations.
-- Archive job uses INSERT ... ON CONFLICT (source_id, metric_id, partition_date)
--   DO UPDATE SET checksum_verified = FALSE, verified_at = NULL
--   to reset verification state on re-runs.
```

**Reprocessing Path:** `mc cp --recursive` archive → hot → force-materialize Bronze →
Silver deduplication via ReplacingMergeTree `data_version` → trigger Silver→Gold export
→ rematerialize features. 8-step documented procedure.

**Storage Projections:** `bronze-hot` ~2 GB steady state. `bronze-archive` ~8 GB/year
(~40 GB at 5 years). Total estimated 5yr: ~192 GB against 4TB SSD (95% headroom).

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

### Great Expectations — Bronze → Silver Validation

**Framework:** Great Expectations (GE). Runs inside the adapter process after Bronze
write, before Silver write. Validates the Bronze payload against catalog expectations.

**Suite names (one per validation scope):**

| Suite | Scope | Runs when |
|-------|-------|-----------|
| `bronze_core` | Universal expectations — all adapters | Every collection run |
| `bronze_{source_id}` | Source-specific expectations (e.g., `bronze_coinalyze`) | Per-adapter, additive to core |

**Core expectations (`bronze_core` suite):**

1. `expect_column_values_to_not_be_null` — `metric_id` (every observation must map)
2. `expect_column_values_to_not_be_null` — `instrument_id` (for instrument-scoped metrics)
3. `expect_column_values_to_be_in_set` — `metric_id` must exist in `forge.metric_catalog`
4. `expect_column_values_to_be_in_set` — `instrument_id` must exist in `forge.instruments`
5. `expect_column_values_to_be_between` — `value` within `(range_min, range_max)` from metric catalog (where defined)
6. `expect_compound_columns_to_be_unique` — `(metric_id, instrument_id, observed_at)` no duplicates within batch
7. `expect_column_values_to_not_be_null` — `observed_at` (temporal anchor required)

**Failure behavior:**
- **Per-observation:** Failed rows are rejected to `forge.dead_letter` with the
  appropriate `rejection_code`. The batch continues — GE never blocks the pipeline.
- **Per-batch:** If rejection rate > 50% for a single batch, the adapter logs a
  warning to `collection_events.notes` but still completes. No circuit breaker in v1.
- **Checkpoint:** GE checkpoint result is stored as Dagster asset metadata for
  observability. The Phase 1 gate criterion "GE checkpoint passes" means: the
  checkpoint runs, core expectations execute, and dead-lettered rows have valid
  rejection codes.

**Source-specific suites (Phase 1, additive):** Adapter-specific expectations extend
the core suite. Examples: Coinalyze funding rate in `(-0.01, 0.01)`, FRED series IDs
match known set, ETF flow values in USD (not cents). These are defined during adapter
build, not pre-specified.

### Silver → Gold Export

The export asset is the sole authorized ClickHouse reader (Rule 2). It transfers
observations from Silver (ClickHouse) to Gold (Iceberg on MinIO) incrementally.

**Asset identity:** `@asset def gold_observations(...)` · AssetKey: `"gold_observations"`.
Referenced by `multi_asset_sensor` and `@hourly` fallback schedule.

**Trigger model — hybrid event-driven + fallback:**
- **Primary:** `multi_asset_sensor` monitors 11 collection asset keys at 30-second
  polling intervals. Fires when any collection asset materializes.

**Collection AssetKey enumeration (11 keys):**

| Source | AssetKey | Cadence |
|--------|----------|---------|
| BGeometrics | `collect_bgeometrics` | 24h |
| Binance BLC-01 | `collect_binance_blc01` | real-time |
| Coinalyze | `collect_coinalyze` | 8h |
| CoinMetrics | `collect_coinmetrics` | 24h |
| CoinPaprika | `collect_coinpaprika` | 24h |
| CFTC COT | `collect_cftc_cot` | weekly |
| DeFiLlama | `collect_defillama` | 12h |
| Etherscan | `collect_etherscan` | 8h |
| FRED | `collect_fred` | 24h |
| SoSoValue | `collect_sosovalue` | 24h |
| Tiingo | `collect_tiingo` | 6h |
- **Guard:** `minimum_interval_seconds=600` prevents export thrashing when multiple
  adapters land at shared cadence boundaries.
- **Fallback:** `@hourly` schedule ensures Gold never lags >1 hour even if the sensor
  misses an event or Dagster daemon restarts.

**ClickHouse query pattern:**

```sql
SELECT metric_id, instrument_id, observed_at, value,
       ingested_at, data_version
FROM forge.observations FINAL
WHERE ingested_at > {last_watermark}
  AND ingested_at <= {run_start_ts} - INTERVAL 3 MINUTE
ORDER BY metric_id, instrument_id, observed_at
```

- `SELECT ... FINAL` forces deduplication at read time. Scoped to the watermark delta
  — not the full table. At normal operation (~1,500 rows per delta), query time is <1s.
- **3-minute lag floor:** Protects against in-flight writes from adapters that have
  begun their Silver write but not yet committed.

**Watermark:** Stored in Dagster asset materialization metadata. Advances only after
successful Iceberg partition commit. Failed writes leave the watermark unchanged —
next run retries the full delta (exactly-once semantics on the Gold side).

**Gold write — partition overwrite (not append):**

1. Derive touched partitions from delta rows: `(year_month, metric_domain)`.
2. Read existing Gold partition via PyIceberg.
3. Merge by `data_version` — for duplicate `(metric_id, instrument_id, observed_at)`
   tuples, keep the row with the higher `data_version`.
4. Atomic Iceberg partition overwrite. Partial write due to MinIO interruption leaves
   prior partition intact (Iceberg ACID guarantee).
5. Advance watermark only after all partition commits succeed.

**Partition key:** `(year_month, metric_domain)`. Monthly granularity with 5 domains
at v1 (`derivatives`, `macro`, `flows`, `defi`, `onchain`). DuckDB prunes by date
range and query domain efficiently.

**Anomaly guard:** Export fails if delta exceeds 10× rolling 7-day average or >2M rows.
Operator bypass: `force_backfill=True` in Dagster run config (single run only).

**Freshness SLA verification — worst-case path (44 minutes):**

| Step | Elapsed | Event |
|------|---------|-------|
| 1 | T+0:00 | Collection asset completes |
| 2 | T+0:02 | Silver write committed |
| 3 | T+0:04 | Sensor fires, export job starts |
| 4 | T+0:09 | FINAL query + result transfer |
| 5 | T+0:14 | Iceberg partition overwrite committed — Gold available |
| 6 | T+0:34 | Feature compute completes (dbt + forge_compute) |
| 7 | T+0:44 | EDSx signal scoring completes |

Result: 44-minute end-to-end. SLA: 90 minutes. Margin: 46 minutes.

**Cold start / bootstrap:** On first run (no prior watermark), chunked bootstrap mode
processes 7-day windows (~42k rows/chunk at full v1 volume). Each chunk advances the
watermark and commits before starting the next. Full-table FINAL is never run.

**Failure modes:**
- ClickHouse/MinIO unreachable: retry 3× with 30s jitter delay. Freshness alert at
  T+60min. Next sensor fire or hourly fallback re-exports from unchanged watermark.
- Zero-row delta: not a failure — materializes with `rows_exported=0`.
- Anomalous delta: fails immediately, no Gold write. Operator inspects and re-runs
  with `force_backfill=True` if legitimate.
- Concurrent runs: structurally impossible (Dagster blocks concurrent materialization
  of the same asset).

**Dagster asset metadata logged per run:** `rows_exported`, `partitions_touched`,
`watermark_prev`, `watermark_new`, `lag_seconds`, `watermark_advanced`.

**Volume at full v1 buildout:** ~5,800–6,000 Silver rows/day. Per-export delta
(normal): ~1,300–1,600 rows. Annual Silver accumulation: ~2.1M rows (Year 1).

**Phase 2 note — derivatives sub-partitioning:** At full BLC-01 volume, the
derivatives domain partition reaches ~36k rows/month. Monitor DuckDB scan latency;
if >500ms, sub-partition by week or instrument prefix. Iceberg schema evolution
supports non-disruptive partition spec changes.

**Monthly maintenance:** Check ClickHouse parts count. If >200 active parts on
`forge.observations`, schedule `OPTIMIZE TABLE forge.observations FINAL` during a
maintenance window (not on the hot path).

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

**Provides:** Protocol TVL, DEX volume, stablecoin metrics, lending yields
**ToS:** Free, low risk, attribution recommended
**Cadence:** Daily at 06:00 UTC (protocols/dex/stablecoins), 12h at 00:00/12:00 UTC (yields)
**Four separate collection jobs:** protocols · dex · stablecoins · yields

**Field mappings (protocols/dex/stablecoins):**

| Source field | Canonical metric | Notes |
|---|---|---|
| `tvl_usd` (protocol) | `defi.protocol.tvl_usd` | instrument_id = protocol slug in instruments catalog |
| Sum of tvl_usd | `defi.aggregate.tvl_usd` | Computed by adapter from protocol data |
| `volume_usd_24h` | `defi.dex.volume_usd_24h` | Market-level, instrument_id = NULL |
| `circulating_usd` | `stablecoin.supply.per_asset_usd` | |
| Sum of circulating_usd | `stablecoin.supply.total_usd` | Computed by adapter |
| `price_usd` | `stablecoin.peg.price_usd` | Range [0.90, 1.10]. Values outside = dead-letter |

**Field mappings (yields — E3 extension, `/yields` endpoint):**

| Source field | Canonical metric | Notes |
|---|---|---|
| `apyBase` | `defi.lending.supply_apy` | ÷100 (percent → decimal). Not nullable. |
| `apyBorrow` | `defi.lending.borrow_apy` | ÷100. Nullable — supply-only pools lack borrow side. |
| `apyReward` | `defi.lending.reward_apy` | ÷100. Nullable. Instruments not promoted to `signal_eligible` tier until v1.1. |
| `utilization` | `defi.lending.utilization_rate` | Direct pool utilization (decimal 0–1). Replaces v1 proxy. Verify unit via PF-6 before build. |

**Yields pool scope:** Aave v3/v2, Compound v3/v2, Curve on Ethereum + Arbitrum.
Assets: USDC, USDT, DAI, WETH, WBTC. `instrument_id` = underlying asset canonical
symbol; `__market__` system instrument for exotic tokens outside v1 catalog (see
below).

**Yields Dagster assets:** 4 assets fanned out from single `collect_yields()` op
(one HTTP request to `/pools`). `observed_at` = request time truncated to 12h
boundary (midnight/noon UTC). Idempotent via ReplacingMergeTree.

**Yields validation:** Null `borrow_apy` is valid (supply-only pool). Null
`utilization_rate` is a dead_letter violation (`NULL_VIOLATION`). Values
exceeding `expected_range_high` are dead-lettered as `EXTREME_VALUE_PENDING_REVIEW`.

**Known issues:** Shallow existing history — backfill from DeFiLlama historical API
(`/chart/{pool_id}`) must run before live collection starts. Protocol slugs change
on rebrands — maintain normalization map. Historical `/chart` records may not include
`utilization` — handle null for backfill rows.

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

#### CFTC COT (E4 — Phase 1 addition)

**Provides:** Commitments of Traders (TFF report) — institutional and dealer futures
positioning for BTC and ETH
**Source:** CFTC Traders in Financial Futures (TFF) via Socrata API (`data.cftc.gov`)
**ToS:** Public domain. No restrictions. Free.
**Cadence:** Weekly. Released Fridays at 15:30 ET. Collection schedule: Fridays 20:00 UTC.

**Field mappings:**

| Source field | Canonical metric | Notes |
|---|---|---|
| Non-commercial long − short | `macro.cot.institutional_net_position` | TFF non-commercial net contracts |
| Non-commercial long / total reportable | `macro.cot.institutional_long_pct` | Fraction, decimal 0–1 |
| Total open interest | `macro.cot.open_interest_contracts` | Total OI in contracts |
| Dealer long − short | `macro.cot.dealer_net_position` | Dealer/intermediary net position |

**Instruments:** BTC (CME Bitcoin futures + Micro BTC aggregated) and ETH (CME Ether
futures). `instrument_id` = `BTC` or `ETH`.

**Timestamp handling:** `observed_at` = Tuesday as-of date from the report (not the
Friday release date). `ingested_at` = Friday collection timestamp (actual wall-clock
time the adapter ran, e.g. `2026-03-06T20:00:00Z`). Features must account for the
3-day publication lag — the data describes positioning as of Tuesday but is not
available until Friday. PIT queries use `ingested_at` to prevent look-ahead bias.

**Signal relevance:** EDSx-05 Tactical Macro (REM-22/23), ML Capital Flow Direction.

**Known issues:** CFTC may combine Micro BTC and standard BTC contracts under a single
reporting category — verify during integration test. Socrata API pagination may be
required for historical backfill.

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
| `defi.lending.utilization_rate` | **Resolved (E3).** DeFiLlama `/yields` provides direct pool utilization. Proxy retired Phase 1. | — |
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
| Redistribution | Three-state enum (`allowed`/`pending`/`blocked`). Option C propagation (`propagate_restriction` per source). Enforced at serving layer via `metric_redistribution_tags` + middleware step 10. Null-with-flag response schema. See §Redistribution Enforcement (A2). |
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

**The v1 customer (Profile A — F1):** Technically sophisticated independent crypto
trader, fund analyst, or quant researcher. Currently builds own signal infrastructure
or pays $99–499/month for raw data (Glassnode, Coinalyze, similar). Understands
funding rates, OI, MVRV, macro overlays. Likely running a sub-$10M discretionary book,
managing research for a small fund, or operating as a serious independent. Evaluates
by methodology legibility + sample output. Discovered via content pull (PIT post,
methodology doc, GitHub schema), not cold outreach.

**Profile B (Protocol — F1):** Protocol foundation or ecosystem DAO. Evaluates by
methodological rigor + data provenance + reference clients. Acquired via direct
outreach after ≥3 Profile A subscribers + 60-day live history.

**Primary use case:** Informing entry and exit decisions. **Secondary use case:**
Portfolio allocation context. Broad coverage is part of the value proposition — the
customer discovers instruments they were not watching.

**"Institutional grade" defined:** An **internal quality bar**, not a customer
descriptor. PIT-correct historical data, reproducible outputs with full audit trails,
calibrated ML probabilities, no self-certification, dead letter logging for every
rejected value. The customer never sees most of this — but it makes the signal
defensible when they ask how it was produced.

**What is not in v1:** Dashboard or UI · self-serve account creation · Stripe billing
(trigger: 20+ active subscribers) · index or benchmark licensing · white-label /
embedded analytics. Manual key issuance and SQL-based customer management in v1.

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
`X-API-Key` header. API key required on ALL endpoints including Free tier — enables
uniform audit trail and rate limiting. No unauthenticated bypass.

**Key format:** `key_prefix` = first 12 characters (`ftb_` + 8 chars token entropy).
Raw key never stored — only argon2id hash in `forge.api_keys`. Customer delivery via
1Password secure share (one-time-view link); never email plaintext.

**5-Tier Hybrid Model (supersedes all prior Free/Paid binary and 4-tier references):**

Tiers set access level and contract structure. Modules set entitlement scope within
tiers. See §Revenue Streams for the module-tier access matrix.

| Dimension | Preview (Free) | Signal API (Pro) | Intelligence Suite (Institutional) | Risk Feed (Exchange) | Ecosystem Monitor (Protocol) |
|---|---|---|---|---|---|
| Endpoints | /v1/market/prices, /v1/macro, /v1/instruments, /v1/health | All Free + /v1/signals, /v1/signals/{id}, /v1/signals/performance, /v1/regime | All Signal API + /v1/features/{id} | All Signal API + /v1/liquidations (BLC-01) | All Signal API |
| Instruments | Full catalog (metadata only) | All signal-eligible | All signal-eligible | Custom instrument coverage | Scoped to customer instrument set (customer override) |
| Signal fields | N/A | Composite direction + confidence + confidence_tier + pillar breakdown + ML components (per module access) | All Signal API + raw feature values + provenance trace | Signal API fields + real-time BLC-01 | Signal API fields scoped to customer instruments |
| Lookback window | 30 days | 365 days | 5 years (1,825 days) | 365 days | 365 days |
| Rate limit (RPM) | 30 | 120 | 300 | 300 | 120 |
| Rate limit (RPD) | 1,000 | 20,000 | 100,000 | 100,000 | 20,000 |
| Burst allowance | +10 above RPM | +30 above RPM | +80 above RPM | +80 above RPM | +30 above RPM |
| Concurrent limit | 2 | 5 | 20 | 20 | 5 |
| Webhook | No | Yes | Yes | Yes | Yes |
| Telegram alerts | No | Yes | Yes | Yes | Yes |
| Provenance trace | No | No | Yes | No | No |
| Raw feature values | No | No | Yes (/v1/features/{id}) | No | No |
| Redistribution-blocked | Always suppressed + `_redistribution_notice` | Same | Same | Same | Same |

**Redistribution enforcement:** Three-state enum (`allowed`/`pending`/`blocked`)
per source. Configurable propagation via `propagate_restriction` flag per source
(Option C — see §Redistribution Enforcement). Both `pending` and `blocked` fields
return `value: null` with inline `redistribution_status` and `blocking_sources` —
null-with-flag, never silent omission. HTTP 200 — the request is valid; field-level
enforcement is not a request-level error. Composite scores degrade gracefully when
components are blocked. See §Redistribution Enforcement for full spec.

**v1 source redistribution status:**

| Source | Status | propagate_restriction | Rationale |
|---|---|---|---|
| Tiingo | allowed | false | Paid license, redistribution permitted |
| FRED | allowed | false | Public domain |
| DeFiLlama | allowed | false | Open API, no redistribution restriction |
| CoinPaprika | allowed | false | Free tier, metadata only |
| Coinalyze | pending | true | Unaudited; conservative default until Phase 6 audit |
| BGeometrics | pending | true | Unaudited; conservative default until Phase 6 audit |
| Etherscan/Explorer | pending | true | Unaudited; conservative default until Phase 6 audit |
| Binance BLC-01 | pending | true | Unaudited; conservative default until Phase 6 audit |
| CoinMetrics | blocked | true | Internal-only ToS; derived restriction likely |
| SoSoValue | blocked | true | Non-commercial ToS; derived use restricted |
| CFTC COT | allowed | false | Public government data |

### Rate Limits

| Tier | RPM | RPD | Burst | Concurrent |
|---|---|---|---|---|
| Preview (Free) | 30 | 1,000 | +10 | 2 |
| Signal API (Pro) | 120 | 20,000 | +30 | 5 |
| Intelligence Suite (Institutional) | 300 | 100,000 | +80 | 20 |
| Risk Feed (Exchange) | 300 | 100,000 | +80 | 20 |
| Ecosystem Monitor (Protocol) | 120 | 20,000 | +30 | 5 |

Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
HTTP 429 on breach with `Retry-After` header.

**Rate limit backend (v1):** PostgreSQL sliding window against `audit_access_log`:
`COUNT(*) WHERE logged_at > now() - interval '1 minute'`. Viable for v1 scale
(10–30 customers). Migrate to Redis token bucket when Redis is deployed.

**Concurrent limit:** `asyncio.Semaphore` per `customer_id` in FastAPI middleware.
HTTP 429 with concurrency-specific `denial_reason` (distinct from
`rate_limit_exceeded` in audit log).

### Customer Identity Model

**v1 scope:** Manual customer management. Sufficient for early access (≤30
customers). Not designed for self-serve at scale — that requires Stripe integration
(deferred, trigger: 20+ active subscribers).

#### Entity Model

```
Customer (org-level)
  ├── 1+ API Keys (independent lifecycle)
  ├── 1 Subscription (plan + modules + billing period)
  ├── 0-1 Customer Instrument Overrides (Ecosystem Monitor + Risk Feed tiers)
  └── Audit trail (via audit_access_log)
```

**`customers` table (Phase 5 DDL):**

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | Internal ID |
| `name` | TEXT NOT NULL | Organization or individual name |
| `email` | TEXT NOT NULL UNIQUE | Primary contact — used for 1Password delivery |
| `account_state` | TEXT NOT NULL | `active`, `suspended`, `closed` |
| `tier` | TEXT NOT NULL | `preview`, `signal_api`, `intelligence_suite`, `risk_feed`, `ecosystem_monitor` |
| `notes` | TEXT | Operator notes — engagement context, contract reference |
| `created_at` | TIMESTAMPTZ | |
| `suspended_at` | TIMESTAMPTZ | NULL unless suspended |

**No org hierarchy in v1.** Each customer is a flat entity. If an Ecosystem Monitor
or Risk Feed customer needs multiple API keys for different systems, those keys share
the same `customer_id`. No sub-accounts, no teams, no delegated admin.

#### Contract Representation

**v1:** Contracts are implicit in the `subscriptions` table (`plan_id`, `starts_at`,
`ends_at`). Intelligence Suite, Risk Feed, and Ecosystem Monitor contracts reference
a written agreement (stored externally — email thread or PDF). The `customers.notes`
field records the contract reference.

**No entitlement versioning in v1.** Plan changes take effect immediately via
`UPDATE subscriptions SET plan_id = $new_plan`. Old entitlements are not preserved —
the audit log captures historical access patterns, not historical entitlement state.

**Future:** Entitlement versioning (effective_from/effective_to on `subscriptions`)
when contract complexity requires it. Trigger: first customer requesting mid-cycle
plan change with prorated billing.

**v2 entity model expansion (deferred):** When self-serve billing or contract
complexity warrants it, split into: `customer_org` (org hierarchy), `contract`
(explicit agreement records), `plan_version` (versioned entitlement snapshots),
`entitlement_override` (per-customer exceptions), `redistribution_addendum`
(source-specific licensing terms per customer), `key_scope_history` (audit trail of
key permission changes). Trigger: 20+ active subscribers or first Intelligence Suite
contract with complex terms. Not in v1 — the flat model is sufficient for ≤30
manual-managed customers.

#### Override Scopes

**`customer_instrument_overrides`:** Ecosystem Monitor and Risk Feed tier customers
receive a scoped instrument list defined during engagement (the instrument coverage
set). This table maps `(customer_id, instrument_id)` with inclusion/exclusion
semantics. Default: exclude (only explicitly listed instruments are accessible).
Overrides apply to all endpoints. The entitlement record for these tiers must carry
an `instrument_coverage_set` concept — the set of instruments the customer has
contracted access to.

#### Revocation Semantics

**API key revocation:** `UPDATE forge.api_keys SET revoked_at = now() WHERE id = $key_id`.
Takes effect within 60s (LRU cache TTL). Revoked keys return 401. Revocation is
permanent — issue a new key instead of un-revoking.

**Account suspension:** `UPDATE forge.customers SET account_state = 'suspended', suspended_at = now()`.
All keys for this customer return 403 `account_suspended`. Suspension is reversible
(`account_state = 'active'`, `suspended_at = NULL`).

**Account closure:** `account_state = 'closed'`. Permanent. All keys return 403.
Audit data retained per retention policy.

### Entitlement Middleware

Every request passes through a 12-step middleware chain. Each step short-circuits
with an error response on failure. Steps ordered cheapest-to-most-expensive.

| Step | Check | Failure code |
|---|---|---|
| 0 | API key format (X-API-Key header, length ≥ 32) | 401 `missing_api_key` / `invalid_api_key_format` |
| 1 | Key lookup (in-process LRU → PostgreSQL on miss) | 401 `invalid_api_key` |
| 2 | Key state (active, not expired/revoked) | 401 `api_key_revoked` / `api_key_expired` |
| 3 | Account state (customer.account_state = active) | 403 `account_suspended` / `account_closed` |
| 4 | Active subscription (ends_at IS NULL) | 403 `no_active_subscription` |
| 5 | Endpoint access (glob match against plan_endpoint_access) | 403 `endpoint_not_permitted` |
| 6 | Rate limit (RPM + burst + RPD) | 429 `rate_limit_exceeded` |
| 7 | Instrument access (plan or customer override) | 403 `instrument_not_permitted` |
| 8 | Lookback window (date range vs plan_lookback_config) | 400 `lookback_exceeded` |
| 9 | Handler executes | — |
| 10 | Redistribution filter (strip blocked fields, inject `_redistribution_notice`) | 200 |
| 11 | Field-level filter (strip per plan_field_access) | 200 |
| 12 | Async audit write (background task, dedicated asyncpg pool) | — |

**Entitlement bundle cache:** In-process LRU per worker. `cachetools.TTLCache`,
1,000 entries, 60-second TTL. Contains all data for steps 0–8 with zero additional
database reads on cache hit. Redis deferred to horizontal scaling.

**Redistribution tag cache:** `dict[str, RedistributionTag]` loaded on worker
startup from `forge.metric_redistribution_tags`. Refreshed via PostgreSQL
`pg_notify('redistribution_cache_invalidated')` — FastAPI LISTEN handler reloads
within 60 seconds. O(1) dict key lookup on hot path. Unknown metric_ids default to
`pending` (safe fallback). See §Redistribution Enforcement for full cache design.

### Redistribution Enforcement

Full specification per Thread A2 result. Governs how redistribution restrictions
on source data propagate to derived metrics, features, signal components, and
composite scores in the external API.

#### Three-State Enum

| State | Meaning | API behavior |
|---|---|---|
| `allowed` | ToS audited; redistribution confirmed permitted | Value returned normally |
| `pending` | Unaudited source; status unknown | `value: null`, `redistribution_status: "pending"`, `blocking_sources: [source_id]` |
| `blocked` | ToS confirmed; redistribution not permitted | `value: null`, `redistribution_status: "blocked"`, `blocking_sources: [source_id]` |

Both `pending` and `blocked` produce `value: null` in responses. The distinction is
the reason code — `pending` implies the field may become available; `blocked` implies
it will not unless the vendor relationship changes.

#### Propagation Rule (Option C — Configurable Per Source)

`source_catalog` carries a `propagate_restriction` boolean per source. When `true`
(default), redistribution blocks propagate through `metric_lineage` to all downstream
derived metrics, features, ML model outputs, EDSx pillar scores, and composite signals.
When `false`, only direct metric values from that source are filtered.

**Default:** `propagate_restriction = true` for all gated sources. Conservative posture
until ToS audit confirms derived works are unrestricted. Operator can relax per source
with zero code changes (single SQL UPDATE).

**Product impact at v1 launch (default settings):** Derivatives features (Coinalyze
inputs) return `pending`. Capital Flow Direction ML model (SoSoValue input) returns
`blocked`. Composite `final_score` degrades gracefully — EDSx uses available pillars,
ML uses available models, synthesis reweights accordingly.

#### Schema: `source_catalog` Redistribution Columns

```sql
-- Phase 5 migration: add redistribution enforcement columns
ALTER TABLE forge.source_catalog
    ADD COLUMN redistribution_status    TEXT NOT NULL DEFAULT 'pending'
        CHECK (redistribution_status IN ('allowed', 'pending', 'blocked')),
    ADD COLUMN propagate_restriction    BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN redistribution_notes     TEXT,
    ADD COLUMN redistribution_audited_at TIMESTAMPTZ;
```

Replaces the boolean `redistribution` column with the three-state enum. Migration:
`redistribution = true → 'allowed'`, `redistribution = false → 'blocked'`,
`NULL → 'pending'`.

#### Schema: `forge.metric_redistribution_tags`

Pre-computed redistribution state for every metric, feature, and signal output.
Single authoritative store — not replicated to ClickHouse, Gold, or Marts (catalog
object, not observation). Rule 3 compliant.

```sql
CREATE TABLE forge.metric_redistribution_tags (
    metric_id           TEXT        NOT NULL PRIMARY KEY,
    redist_status       TEXT        NOT NULL
                            CHECK (redist_status IN ('allowed', 'pending', 'blocked')),
    blocking_source_ids TEXT[]      NOT NULL DEFAULT '{}',
    propagated          BOOLEAN     NOT NULL DEFAULT false,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- `blocking_source_ids`: empty when `allowed`; one or more source_ids when `pending`/`blocked`
- `propagated`: `true` = inherited from upstream via `metric_lineage`; `false` = direct from source

#### Tag Computation

`forge.recompute_redistribution_tags()` — PostgreSQL function. Breadth-first traversal
of `metric_lineage` from root metrics outward. Worst upstream status wins
(`blocked > pending > allowed`). Respects `propagate_restriction` flag per source.
Idempotent via TRUNCATE + re-insert. Emits `pg_notify('redistribution_cache_invalidated')`
on completion.

Triggered automatically by PostgreSQL trigger on `source_catalog` redistribution column
changes. Also callable manually and by nightly Dagster consistency check.

**Coverage:** Raw metrics (seeded from `source_catalog`), derived features (via
`metric_lineage`), ML model outputs and EDSx pillar scores (registered as synthetic
`metric_id` entries in `metric_catalog` + `metric_lineage` during Phase 3/4).

#### Response Schema: Null-With-Flag

Redistribution enforcement never silently drops a field. Every blocked or pending field
is present in the response with `value: null` plus inline metadata:

```json
{
  "metric_id": "flows.etf_flow_normalized",
  "value": null,
  "redistribution_status": "blocked",
  "blocking_sources": ["sosovalue"],
  "observed_at": null
}
```

For composite signals, a top-level `_redistribution_notice` block summarizes all
suppressed fields:

```json
{
  "_redistribution_notice": {
    "fields_suppressed": 3,
    "detail": [
      {"field": "ml.capital_flow_direction.p_bullish", "status": "blocked", "sources": ["sosovalue"]},
      {"field": "edsx.pillars.derivatives", "status": "pending", "sources": ["coinalyze"]},
      {"field": "edsx.pillars.tactical_macro", "status": "pending", "sources": ["coinalyze"]}
    ]
  }
}
```

#### Composite Score Degradation

The composite score degrades gracefully rather than collapsing when components are blocked:

1. EDSx available + ML fully blocked → `final_score = edsx_composite`, `synthesis_degraded = true`
2. ML partially blocked → `ml_composite` from unblocked models only, `synthesis_degraded = true`
3. Both tracks fully blocked → `final_score = null`
4. EDSx partially blocked → `edsx_composite` from available pillars, `synthesis_degraded = true`

Degradation metadata included in response: `synthesis_degraded`, `synthesis_note`
(human-readable), blocked model/pillar listings with `redistribution_status` and
`blocking_sources`.

#### Operator Resolution Path (Zero Code Changes)

When ToS audit completes for a source:

```
Operator SQL UPDATE on source_catalog
  → PostgreSQL trigger fires recompute_redistribution_tags()
    → metric_redistribution_tags updated (graph traversal)
      → pg_notify emitted
        → FastAPI cache reloads (≤60 seconds)
          → Next API responses reflect updated state
```

No deployment. No code change. No service restart.

**Pending → Allowed (e.g., Coinalyze audit clears):**
```sql
UPDATE forge.source_catalog SET
    redistribution_status = 'allowed',
    propagate_restriction = false,
    redistribution_notes = 'ToS audited YYYY-MM-DD. Redistribution permitted.',
    redistribution_audited_at = NOW(),
    updated_at = NOW()
WHERE source_id = 'coinalyze';
```

**Relax propagation only (raw blocked, derived works permitted):**
```sql
UPDATE forge.source_catalog SET
    propagate_restriction = false,
    redistribution_notes = 'Raw blocked. Derived signals permitted per contract.',
    updated_at = NOW()
WHERE source_id = 'coinmetrics';
```

#### Audit Evidence Package

Five query patterns documented for vendor compliance (full SQL in Thread A2 result):

1. **Prove blocked fields never returned** — join `audit_access_log` → `redistribution_events` JSONB; confirm all blocked metrics have `action = 'redistribution_filtered'`
2. **Prove zero data exposure for specific source** — cross-reference blocked metrics with audit events; `requests_NOT_filtered` must equal 0
3. **Prove pending fields flagged correctly** — all pending-source metrics returned with `redistribution_status: "pending"`
4. **Source-level enforcement summary** — per-source filter rate report for periodic compliance review
5. **Point-in-time proof for vendor audit** — date-range scoped evidence for specific vendor request

All queries filter to external customers only (`tier NOT IN ('internal')`).

#### Decision Gate: Redistribution Impact on Product Quality (DG-R1)

**Status:** Resolved — Option B chosen. Date: 2026-03-06.

**Decision:** Option B — Expedite ToS audits for `pending` sources during Phase 4
shadow period.

**Resolution details:**

1. **Coinalyze, BGeometrics, Etherscan, BLC-01** ToS audits run in parallel during
   Phase 4 shadow period (not deferred to Phase 6). Target: all four cleared to
   `allowed` before Phase 5 gate.
2. **SoSoValue and CoinMetrics** remain `blocked` (harder ToS — non-commercial and
   internal-only respectively). Phase 6 ToS audit or paid tier negotiation.
3. **Phase 5 launch posture:** Flow Intelligence ETF flow fields (SoSoValue-sourced)
   launch with conditional null-flagging if SoSoValue ToS is unresolved at Phase 5.
   Fields return `value: null` with `redistribution_status: "blocked"` and
   `blocking_sources: ["sosovalue"]`. This is not product degradation — it is
   expected and documented in methodology.
4. **Phase dependency:** Phase 4 shadow period is the audit window for the four
   `pending` sources. If any audit is not complete by Phase 5 gate, those sources
   remain `pending` and their derived fields are null-flagged at launch (same
   mechanism as SoSoValue). This is a graceful degradation, not a blocker.

**Rationale:** Option B eliminates the severe product degradation (both live pillars
suppressed) while keeping the harder ToS cases (SoSoValue/CoinMetrics) on their
natural timeline. The Phase 4 shadow period provides 30+ days — sufficient for
straightforward ToS audits of free-tier APIs.

**Context (preserved for reference):** At launch with default `propagate_restriction
= true`, suppression of `pending` sources would affect EDSx derivatives_pressure
pillar (Coinalyze), EDSx liquidity_flow pillar (Etherscan + SoSoValue), ML Capital
Flow Direction model (SoSoValue), ML Derivatives Pressure model (Coinalyze), and
composite score (severely degraded). Option B resolves the `pending` sources; the
`blocked` sources (SoSoValue/CoinMetrics) produce documented null-flagged fields.

#### Remaining Open Assumptions

3. **ML/EDSx registration:** All model outputs and pillar scores must be registered as synthetic `metric_id` entries in `metric_catalog` + `metric_lineage` (Phase 3/4 deliverable)
5. **Pending in customer docs:** Confirm whether pending status should appear in `GET /v1/instruments` catalog response (before Phase 5)
7. **Cache reload latency:** ≤60 seconds acceptable for source_catalog changes to propagate to live API (before Phase 5)

*Items 1, 2, 6 resolved via DG-R1 (Option B). Item 4 resolved — `audit_access_log` DDL defined in §Database Layer.*

---

### API Endpoints

#### Preview Tier (Free)

**`GET /v1/market/prices`**
Current price, market cap, and 24h change for the crypto instrument universe.
Source: CoinPaprika. Parameters: `instruments` (comma-separated, optional),
`page`, `per_page` (max 500).

**`GET /v1/macro`**
Current values for all FRED macro series in the catalog. Public domain — no
redistribution restriction. No parameters.

**`GET /v1/instruments`**
The full instrument catalog. Parameters: `domain`, `signal_eligible` (boolean).

#### Signal API Tier (and above)

**`GET /v1/signals`**
Full universe signal snapshot. The primary entry point. Parameters: `direction`,
`confidence_min`, `regime`, `domain`, `instruments`, `page`, `per_page` (max 200).

Response includes `generated_at`, market `regime` block, `stale_sources` list,
and per-instrument signal objects with `direction`, `confidence`, `confidence_tier`,
`magnitude`, `horizon`, `as_of`, and `staleness_flag`.

---

**`GET /v1/signals/{instrument_id}`**
Single instrument detail with full component breakdown and provenance. Response
conforms to the §L2.8 `SignalResponse` schema defined in the Signal Synthesis
section. Key fields per instrument:

- `signals.{1D,7D,30D}`: score, direction, confidence, track, synthesis_weights,
  agreement, disagreement_flag, staleness_seconds
- `edsx.{1D,7D,30D}`: composite, confidence, active_pillars, pillar_coverage,
  degraded, per-pillar PillarDetail (score, confidence, weight_used, guardrail,
  null_reason, freshness_seconds)
- `ml`: composite, direction_scalar, active_models, model_coverage, degraded,
  graduated, vol_regime_conditioning_applied, per-model MLModelDetail
- `flow_magnitude`: from Capital Flow Direction model (null until ML graduates)
- `provenance`: pillar/model weights used, SR modulation, guardrails applied,
  vol regime multipliers, agreement boost/penalty, null inputs and reasons

Null states are always explicit. A null score with no null_reason is a bug, not a
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

### Performance History Endpoint (B3)

**`GET /v1/signals/performance`** — Phase 5 scope addition. Customer conversion gate:
a sophisticated buyer evaluating Signal API or Intelligence Suite subscription requires a
machine-readable track record before committing.

#### Design Principles

- **PIT-first:** Performance calculation is the highest-risk location for inadvertent
  lookahead bias. Every design decision prioritises PIT correctness over convenience.
- **Pre-materialised, not on-the-fly:** All computationally intensive metrics are stored
  in the Marts layer. The API reads pre-aggregated tables via DuckDB. Target p95: ≤ 500ms.
- **Stable consumer contract:** Null states are always returned as present objects with
  `min_observations_met: false` — never as absent keys.
- **Middleware enforcement:** Entitlement field redaction is a post-compute serialisation
  concern. The computation layer is tier-agnostic; the middleware strips fields based on
  the API key tier.

#### REST Contract

| Parameter | Type | Values | Default | Notes |
|---|---|---|---|---|
| `instrument_id` | string | Any valid instrument_id | omitted | Omit for cross-sectional summary |
| `horizon` | enum | `1d` · `7d` · `14d` · `30d` · `all` | `14d` | EDSx: 1d/7d/30d; ML: 14d only |
| `window` | enum | `30` · `90` · `180` · `365` · `all` | `365` | Trailing calendar days. Signal API capped at 365. |
| `track` | enum | `edsx` · `ml` · `composite` · `all` | `composite` | Which signal track to evaluate |
| `from` | ISO 8601 | timestamp | null | Overrides window start if both present — from/to wins, warning returned |
| `to` | ISO 8601 | timestamp | null | Defaults to latest resolved outcome date |

Authentication: API key required. Preview tier returns HTTP 403. Signal API tier: window capped
at 365 days. Intelligence Suite: all values permitted. `from`/`to` and `window` are mutually
exclusive. If both supplied, `from`/`to` takes precedence and `window` is ignored; an
explicit `parameter_override_warning` field is returned.

#### Response Schema — Meta Block

```json
{
  "meta": {
    "instrument_id": "BTC-USD | null",
    "scope": "instrument | cross_sectional",
    "horizon": "14d",
    "track": "composite",
    "window_requested": 365,
    "window_effective_days": 312,
    "window_start": "2025-03-19T00:00:00Z",
    "window_end": "2026-02-20T00:00:00Z",
    "signals_evaluated": 891,
    "signals_pending_outcome": 14,
    "coverage_fraction": 0.94,
    "mean_confidence": 0.71,
    "pillar_coverage_fraction": 0.40,
    "regime_engine_version": "m2_only | vla",
    "performance_table_as_of": "2026-03-05T06:00:00Z",
    "parameter_override_warning": null
  }
}
```

Coverage fields are always returned regardless of tier — they are metadata, not
proprietary analytics: `coverage_fraction`, `mean_confidence`,
`pillar_coverage_fraction`, `instruments_included` / `instruments_excluded_insufficient_history`,
`null_states_summary`.

#### Response Schema — Performance Blocks

The full response nests the following blocks at top level. When `track=all` and
`horizon=all`, each block is keyed by `(track, horizon)` combination — at most 7
combinations. No pagination required.

- **`directional_accuracy`** — hit rates, signal counts, neutral threshold
- **`return_attribution`** — quintile returns, Sharpe, max drawdown, benchmark comparison
- **`calibration`** — ECE, Brier score, reliability diagram (ML/composite only)
- **`regime_conditional`** — per-regime hit rates and Sharpe
- **`pillar_attribution`** — per-pillar hit rates (Intelligence Suite + Ecosystem Monitor scoped)
- **`null_states_summary`** — breakdown of null event causes
- **`entitlement`** — tier and `fields_redacted` array

**Null contract:** Any metric where `min_observations_met` is false returns the metric
object with all numeric fields as null and `min_observations_met: false`. The object is
always present — never omitted.

#### Metric Definitions

| Metric | Definition | Min Observations | PIT Verification |
|---|---|---|---|
| `hit_rate_overall` | count(correct) / count(resolved) | 30 resolved signals | `observed_at > computed_at` in JOIN |
| `hit_rate_bullish` | count(score>0.10 AND return>0) / count(score>0.10) | 30 bullish signals | Pre-materialised outcomes only |
| `hit_rate_bearish` | count(score<-0.10 AND return<0) / count(score<-0.10) | 30 bearish signals | Pre-materialised outcomes only |
| `hit_rate_neutral` | fraction where |realized return| < trailing realised vol | 30 neutral signals | Pre-materialised outcomes only |
| `quintile_returns` | Mean fwd return per signal quintile (Q1=most bearish, Q5=most bullish) | 50 per quintile | Outcomes use close > `computed_at` |
| `strategy_sharpe_annualized` | annualized(mean_return) / annualized(std_return). Population std. | 50 resolved signals | Position return from pre-materialised outcomes |
| `strategy_max_drawdown` | min(equity_t / max(equity_0..t) - 1) on cumulative equity curve | 50 resolved signals | Time-ordered on `computed_at`, not `ingested_at` |
| `excess_return_period` | strategy_return - benchmark_return over evaluation window. Benchmark: BTC buy-and-hold return over same period. Per-instrument: uses own buy-and-hold as benchmark. | 50 resolved signals | Same window as `strategy_sharpe_annualized`. PIT: outcomes use close > `computed_at`. |
| `calibration.ece` | sum_b(|n_b/N| × |acc_b - conf_b|), 10 equal-width `p_bullish` bins | 100 signals, ≥5 non-empty bins | ML/composite only. Bins with n<10 excluded. |
| `calibration.brier_score` | mean((p_bullish_i - outcome_i)²), outcome=1 if return>0 | 50 resolved signals | ML/composite only |
| `calibration.reliability_diagram` | 10 bins: bin_center, predicted_prob, realized_freq, n, low_count flag | n≥10 per bin for `low_count=false` | Request-time DuckDB <50ms |
| `regime_conditional.hit_rate` | hit_rate filtered by regime at signal time | 30 per regime | Regime at `computed_at`, not current regime |
| `regime_conditional.sharpe` | Sharpe filtered by regime at signal time | 50 per regime | Same PIT rule |

**Neutral threshold:** Fixed at ±0.10. At 2/5 pillars active, values within this range
are below the composite score noise floor. Not configurable per-request. Documented in
methodology.

**Sharpe convention:** Population std (no Bessel correction). Appropriate for large N.

**Return definition:** Daily close prices from Tiingo for both anchor and outcome.
Calendar days, not trading days. VWAP alternative explicitly rejected.

**Calibration:** Applies to `track=ml` and `track=composite` only. EDSx is deterministic
and does not produce probability estimates. Calibration fields for `track=edsx` return
null with a `not_applicable` annotation.

**ECE target:** < 0.05. 10 equal-width bins of `p_bullish` [0, 1]. Bins with n < 10
excluded from ECE and flagged `low_count: true` in reliability diagram.

#### Computation Architecture

**Mart: `signal_outcomes`** — Joins every resolved signal to its forward price return.
The foundational PIT-correct outcome record. All downstream aggregations derive from
this mart.

Inputs:
- `marts.signals_history` — complete signal record per `(instrument_id, computed_at, track, horizon)`,
  including `final_score`, `edsx_composite`, `ml_composite`, `p_bullish`, `p_neutral`,
  `p_bearish`, `confidence`, `regime`, `pillar_scores`, `null_states`, `ingested_at`
- `gold.prices_ohlcv` — Tiingo OHLCV, Iceberg table on MinIO, readable via DuckDB

Grain: One row per `(signal_id, horizon_days)`. A signal with 3 active EDSx horizons
and 1 ML horizon produces 4 rows.

Key fields: `signal_id`, `instrument_id`, `track`, `horizon_days`, `computed_at`,
`ingested_at_signal`, `price_at_signal`, `anchor_observed_at`, `price_at_outcome`,
`outcome_observed_at`, `forward_return`, `direction_signal` (bullish/neutral/bearish),
`direction_realized`, `is_correct_direction`, `outcome_resolved`.

Update cadence: Incremental, once per day. Reads all rows where `outcome_resolved = false`
AND `outcome_date <= CURRENT_DATE - 1`, fetches closing prices from Gold, writes resolved
outcome values, flips `outcome_resolved = true`. Existing resolved rows are never rewritten.

**Mart: `performance_metrics`** — Rolling aggregations of `signal_outcomes` for standard
`(track, horizon, window)` combinations. All heavy computation lives here. The API reads
a single row at request time.

Grain: One row per `(instrument_id, track, horizon_days, window_days)`. `instrument_id`
is nullable for cross-sectional rows.

Pre-materialised combinations: Windows: 30, 90, 180, 365, all. Tracks: edsx, ml,
composite. Horizons: all valid per track (1d/7d/30d for EDSx, 14d for ML). Maximum ~60
rows per instrument at 121 instruments: ~7,380 rows total.

Update cadence: Recomputed daily, triggered by Dagster asset dependency after
`signal_outcomes` update. `performance_table_as_of` in response meta reflects the last
successful recompute.

**Dagster Asset Dependency Graph:**

```
gold.prices_ohlcv (Tiingo, 6h cadence)
       │
marts.signals_history (Phase 3 output, hourly)
       ├──────────────────────────────────┐
       │                                  │
signal_outcomes (daily incremental)    signal_snapshot_writer (post-signal)
       │                                  │
performance_metrics (daily)            FastAPI /internal/cache/refresh
       │
FastAPI /v1/signals/performance (reads DuckDB)
```

The reliability diagram bins are the sole exception to pre-materialisation. Computed at
request time from `signal_outcomes` via DuckDB grouped aggregation — at most a few
thousand rows per window, execution time under 50ms.

#### PIT Compliance Verification

The performance calculation is the highest-risk location for inadvertent forward-look
bias in the entire system. These rules are non-negotiable and enforced structurally in
the `signal_outcomes` mart.

**The JOIN Condition** — the single most important PIT rule:

```sql
-- Step 1: anchor price — first close strictly after signal emission
WITH signal_anchor AS (
  SELECT
    s.signal_id, s.instrument_id, s.computed_at,
    s.ingested_at AS ingested_at_signal,
    MIN(p.observed_at)                           AS anchor_observed_at,
    arg_min(p.close, p.observed_at)        AS price_at_signal
  FROM marts.signals_history s
  JOIN gold.prices_ohlcv p
    ON  p.instrument_id = s.instrument_id
    AND p.observed_at   > s.computed_at   -- strictly after: no same-bar lookahead
  GROUP BY s.signal_id, s.instrument_id, s.computed_at, s.ingested_at
),

-- Step 2: outcome price — close at anchor + horizon or next available
signal_with_outcome AS (
  SELECT
    a.*,
    a.anchor_observed_at + INTERVAL '{horizon_days} days'  AS target_outcome_date,
    MIN(p.observed_at)                                       AS outcome_observed_at,
    arg_min(p.close, p.observed_at)                    AS price_at_outcome
  FROM signal_anchor a
  JOIN gold.prices_ohlcv p
    ON  p.instrument_id = a.instrument_id
    AND p.observed_at  >= a.anchor_observed_at + INTERVAL '{horizon_days} days'
  WHERE a.anchor_observed_at + INTERVAL '{horizon_days} days' <= CURRENT_DATE - 1
  GROUP BY ...
)

-- Backfill PIT guard
WHERE ingested_at_signal <= outcome_observed_at
```

**Outcome Resolution Rule:**
- Anchor price: closing price of the first daily bar where `close_time > computed_at`.
- Outcome price: closing price of the first daily bar at or after `anchor_date + H`
  calendar days. Calendar days, not trading days — crypto trades continuously.
- Forward return: `price_at_outcome / price_at_signal - 1`. No annualisation in the raw
  return. Annualisation occurs only in Sharpe computation.

**Signals Near the Current Date:** Signals where `anchor_date + H > CURRENT_DATE - 1`
are written with `outcome_resolved = false`. Excluded from all performance calculations.
The 1-day buffer ensures the outcome close is final. These appear in
`signals_pending_outcome` in the response meta.

**Backfill Signal Handling:** The `WHERE ingested_at_signal <= outcome_observed_at` guard
excludes any backfilled signal from performance history if the signal's `ingested_at` is
after the outcome date. Signals where `ingested_at > outcome_observed_at` are silently
excluded, not rejected at ingestion.

**Pre-Phase 5 blocking action:** EDSx-02 and EDSx-03 R3 backfill `ingested_at` values
must be audited before Phase 5 gate. If historical scores were backfilled, the verified
performance history may be shorter than the full signal history window.

#### Cross-Sectional Performance

When `instrument_id` is omitted, the endpoint aggregates across all instruments that
were signal-eligible during the requested window. Per-instrument metrics are aggregated
as equal-weighted means.

**Equal-weight rationale:** The signal product is systematic and domain-driven, not
AUM-weighted. Capitalisation-weighting would be dominated by BTC and ETH, understating
performance on the long tail of 121 instruments.

**Sparse instrument handling:** Instruments with fewer than 30 resolved signals in the
window are excluded entirely. They do not contribute zeros — they are absent. The
response meta reports `instruments_included` and `instruments_excluded_insufficient_history`.

**Cross-sectional Sharpe:** Treats each signal emission across all instruments as an
independent position in a unit-sized portfolio:

```
portfolio_return_t = mean(position_i,t * return_i,t)  for all instruments i at time t
cross_sectional_sharpe = annualized(portfolio_return_t) / annualized_std(portfolio_return_t)
```

Caveat surfaced in meta and methodology: assumes equal-sized positions, ignores
transaction costs, does not account for correlation. Directionally correct and
decision-useful for signal evaluation; not a portfolio construction tool.

#### Field-Tier Mapping

| Response Field | Preview | Signal API (365d) | Intelligence Suite | Risk Feed | Ecosystem Monitor |
|---|---|---|---|---|---|
| `meta.*` (all metadata) | HTTP 403 | ✓ | ✓ | ✓ | ✓ |
| `directional_accuracy.*` | HTTP 403 | ✓ (365d) | ✓ (all) | ✓ (365d) | ✓ (365d) |
| `return_attribution.quintile_returns` | HTTP 403 | ✓ | ✓ | ✓ | ✓ |
| `return_attribution.strategy_sharpe` | HTTP 403 | ✓ | ✓ | ✓ | ✓ |
| `calibration.ece` + `brier_score` | HTTP 403 | ✓ | ✓ | ✓ | ✓ |
| `calibration.reliability_diagram` | HTTP 403 | ✗ | ✓ | ✗ | ✗ |
| `regime_conditional.*` | HTTP 403 | ✓ | ✓ | ✓ | ✓ |
| `pillar_attribution.*` | HTTP 403 | ✗ | ✓ | ✗ | ✓ (scoped to pillars with ≥1 metric sourced from instruments in the customer's `customer_instrument_overrides` coverage set; pillars outside coverage return null with `_scope_limited` flag) |
| `window=all` (full history) | HTTP 403 | ✗ (capped 365d) | ✓ | ✗ | ✗ |
| Cross-sectional (no `instrument_id`) | HTTP 403 | ✓ | ✓ | ✓ | ✓ |

Enforcement via the same response middleware pattern as redistribution filtering.
The computation layer is tier-agnostic; redacted fields are replaced with null and a
corresponding entry in `entitlement.fields_redacted[]`.

#### Methodology Documentation Mapping (B3)

Every metric in the performance response maps to a methodology documentation section.
These must be written before Phase 6 gate.

| Metric | Methodology Section | Description |
|---|---|---|
| `hit_rate_overall` / `hit_rate_bullish` / `hit_rate_bearish` | §4.1 Directional Accuracy | Fraction predicting correct direction at stated horizon |
| `neutral_threshold` | §4.1 Directional Accuracy | Rationale for fixed ±0.10 value |
| `quintile_returns` | §4.2 Return Attribution | Average return per signal quintile — primary monotonicity test |
| `strategy_sharpe_annualized` | §4.2 Return Attribution | Risk-adjusted return of systematic signal-following strategy |
| `strategy_max_drawdown` | §4.2 Return Attribution | Worst peak-to-trough loss in evaluation window |
| `excess_return_period` | §4.2 Return Attribution | Strategy return minus buy-and-hold benchmark |
| `calibration.ece` | §4.3 Probability Calibration | ML stated probabilities vs realized frequencies. Target < 0.05. |
| `calibration.brier_score` | §4.3 Probability Calibration | Combined calibration and sharpness measure |
| `calibration.reliability_diagram` | §4.3 Probability Calibration | Predicted probability vs realized outcome per bucket |
| `regime_conditional.*` | §4.4 Regime-Conditional Performance | Accuracy and Sharpe broken down by market regime at signal time |
| `pillar_attribution.*` | §4.5 Pillar Attribution | Per-pillar accuracy before composite synthesis |
| `coverage_fraction` | §3.1 Signal Coverage | Fraction of computation cycles producing non-null signal |
| `mean_confidence` | §3.2 Signal Confidence | Average data completeness score |
| `pillar_coverage_fraction` | §3.3 Pillar Coverage | Average fraction of 5 pillars active per cycle |
| `null_states_summary` | §3.4 Null State Taxonomy | Breakdown: INSUFFICIENT_HISTORY vs SOURCE_STALE vs METRIC_UNAVAILABLE |
| `signals_pending_outcome` | §3.5 Evaluation Lag | Signals too recent for outcome resolution |

---

### Latency SLAs

| Endpoint | p50 | p95 | p99 |
|---|---|---|---|
| `GET /v1/signals` | 200ms | 800ms | 1500ms |
| `GET /v1/signals/{instrument}` | 100ms | 400ms | 800ms |
| `GET /v1/signals/performance` | 200ms | 500ms | 1000ms |
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
`SOURCE_STALE` · `SIGNAL_UNAVAILABLE` · `ENDPOINT_NOT_PERMITTED` ·
`INSTRUMENT_NOT_PERMITTED` · `LOOKBACK_EXCEEDED` · `ACCOUNT_SUSPENDED` ·
`NO_ACTIVE_SUBSCRIPTION` · `CONCURRENCY_LIMIT_EXCEEDED`

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

### Signal Snapshot Cache (C3)

In-process Python dict in FastAPI `app.state.signal_cache`. No external service (no
Redis, no Memcached). ~250 KB heap for 121 instruments. GIL-safe atomic swap on refresh.

**Population:** Dagster HTTP POST to `/internal/cache/refresh` (authenticated via
`INTERNAL_CACHE_TOKEN` header) after `signal_snapshot_writer` asset completes. The asset
writes the canonical snapshot to `MinIO gold/snapshots/latest.json` first, then notifies
FastAPI. Event-driven — no polling.

**Entitlement model (Option C):** Redistribution filter applied at cache populate time.
Gated field values (SoSoValue, CoinMetrics) are never present in the `app.state` cache
object — enforcement is structural, not per-request policy. Per-request tier filtering
(per-tier field redaction) adds microseconds only.

**Warm start:** On FastAPI restart, lifespan reads `gold/snapshots/latest.json` from
MinIO. Cache warm in <5s. `/healthz/ready` returns 503 until `cache.ready=True`. Docker
health check uses this endpoint — container receives no traffic until cache is warm.

**Staleness:** TTL = 6h cadence × 1.5 = 9h (`SIGNAL_CACHE_TTL_SECONDS=32400`). Beyond
TTL: serves stale snapshot with `is_stale=True` + `next_computation_estimated=null`. No
HTTP error. On cache miss (startup only): live DuckDB Gold query with 5s timeout,
`cache_miss=True` in response envelope.

**Response envelope freshness fields:**

```json
{
  "snapshot_computed_at": "2026-03-06T12:00:00Z",
  "snapshot_cached_at": "2026-03-06T12:00:47Z",
  "snapshot_served_at": "2026-03-06T14:23:11Z",
  "cache_age_seconds": 8544,
  "is_stale": false,
  "next_computation_estimated": "2026-03-06T18:00:00Z",
  "cache_miss": false
}
```

**Latency targets (cache hit):**

| Endpoint | p50 | p95 | p99 |
|---|---|---|---|
| `GET /v1/signals` (full universe, cache hit) | < 20ms | < 50ms | < 100ms |
| `GET /v1/signals/{instrument_id}` (cache hit) | < 5ms | < 15ms | < 30ms |
| Cache miss — live DuckDB fallback | ~2s | ~5s | < 5s (timeout) |

### Serving Concurrency Envelope

Design bounds for the FastAPI serving layer. Values are internal build-time targets —
not customer-facing SLAs. Prevents "the API is slow" arguments that are really
"outside design bounds."

| Dimension | v1 Target | Notes |
|-----------|-----------|-------|
| Target concurrent requests | 50 | Single Uvicorn worker, async. Sufficient for ≤30 customers. |
| Hot cache p95 latency | < 50ms | Signal cache hit path — Python dict lookup + JSON serialization |
| Cold cache p95 latency | < 5s | DuckDB Gold query fallback on cache miss (startup only) |
| Memory budget (FastAPI worker) | 512 MB | Signal cache ~250 KB + DuckDB in-process + Python overhead |
| Max response size | 2 MB | Full universe signal snapshot (~121 instruments × ~2 KB each) |
| Cache warm-start failure | Serve 503 on `/healthz/ready` until cache populated; Docker health check prevents traffic routing | Container never receives traffic with empty cache |
| Scale trigger | Migrate to multi-worker Uvicorn + Redis cache when concurrent requests > 50 sustained or p95 > 200ms | — |

### Signal Operational States

When source failures cascade beyond staleness into systemic impairment, the serving
layer needs defined operational modes — not ad-hoc decisions under pressure.

| State | Condition | Behavior | API Response | Customer Notice |
|-------|-----------|----------|--------------|-----------------|
| **Healthy** | All sources fresh, signal compute current | Normal operation | Standard responses | None |
| **Degraded** | 1+ sources stale, signal still computable | Recompute with available data, flag staleness | `staleness_flag: true` + `stale_sources[]` on affected instruments | SLA 3 notification (60 min) |
| **Impaired** | ≥50% of contributing sources stale for a single instrument, OR signal compute pipeline failed | Freeze last good snapshot for affected instruments | Frozen snapshot served with `is_stale: true` + `signal_state: "frozen"` + `frozen_reason` | SLA 3 + explicit "impaired" status page update |
| **Suppressed** | Operator decision: data quality unacceptable for serving | Suppress affected fields/instruments entirely | `signal_state: "suppressed"` + `null` values on affected fields | Email to affected customers within 30 min |

**Transition rules:**
- Healthy → Degraded: automatic (source freshness monitor)
- Degraded → Impaired: automatic (threshold breach) or manual (operator)
- Any → Suppressed: manual only (operator decision via admin endpoint)
- Suppressed → Healthy: manual only (operator restores after verification)
- Frozen snapshots expire after 24h — after that, transition to Suppressed

**Rationale:** "Honestly stale but frozen" is sometimes safer than "freshly
recomputed from half a market." The frozen state preserves the last coherent signal
while making staleness explicit. Suppression is the nuclear option — used when
serving would be actively misleading.

### DuckDB Concurrency Model

DuckDB is embedded in two contexts: FastAPI serving (Layer 8) and `forge_compute`
Python assets (Layer 6). Each has different concurrency characteristics.

#### FastAPI Serving (Layer 8)

**Primary path:** `GET /v1/signals` reads from in-process cache (C3) — no DuckDB
involvement. DuckDB is used only for:
1. Cache miss fallback (startup only, <5s timeout)
2. `GET /v1/signals/performance` — reads pre-materialized `performance_metrics` mart
3. `GET /v1/timeseries` — Arrow Flight bulk reads from Gold
4. Reliability diagram computation — request-time DuckDB on `signal_outcomes`

**Connection model:** Single DuckDB connection per FastAPI worker process. DuckDB
supports concurrent reads from multiple threads within one process. FastAPI runs with
`uvicorn --workers N` (default N=2 for v1). Each worker has its own DuckDB connection
to the Gold Iceberg catalog on MinIO.

**Concurrency limits:**

| Metric | v1 Target | Scale trigger |
|--------|-----------|---------------|
| Max concurrent DuckDB reads per worker | 8 (uvicorn thread pool) | Worker count increase |
| Max concurrent workers | 2 | CPU saturation >80% sustained |
| Max concurrent Arrow Flight streams | 4 per worker | Memory >70% |
| Performance endpoint p95 | ≤ 500ms | Query optimization |

**Workload isolation:** `forge_compute` Dagster assets run in `empire_dagster_code`
container. FastAPI runs in `empire_fastapi` container. They never share a DuckDB
process. Both read Gold (Iceberg on MinIO) via independent DuckDB instances. No
write contention — Gold writes come exclusively from the `gold_observations` export
asset.

**Cache invalidation:** The signal snapshot cache (C3) is invalidated by Dagster
HTTP POST to `/internal/cache/refresh` after `signal_snapshot_writer` completes.
This is event-driven — not timer-based. No cache coherence protocol needed because
there is a single source of truth (`gold/snapshots/latest.json`) and a single
consumer (FastAPI cache). Stale reads are explicitly tolerated (served with
`is_stale=True`).

**Iceberg snapshot consistency:** DuckDB reads Iceberg snapshots, which are immutable.
A concurrent Gold export writes a new snapshot — DuckDB readers on the old snapshot
are unaffected. Snapshot advancement is atomic. No read-write conflicts possible.

**Bottleneck analysis:** The most likely bottleneck under external tenant load is
the `performance_metrics` DuckDB query (~7,380 rows, indexed). At p95 ≤ 500ms this
handles ~4 concurrent requests per worker before queuing. Mitigation: increase
worker count. Arrow Flight bulk reads are memory-bound — 4 concurrent streams at
~50MB each = ~200MB per worker. Monitor with Prometheus
`duckdb_query_duration_seconds` histogram.

**Phase gate:** Phase 5 gate includes `GET /v1/signals/performance` p95 ≤ 500ms
under representative load (already present).

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
change to signal methodology that affects interpretation of outputs. Breach: methodology
change deployed without 14-day advance customer notice.

**Methodology Change Classes:**

| Class | Description | Notice | PIT Impact | Examples |
|-------|-------------|--------|------------|----------|
| A — Cosmetic | Wording, formatting, documentation-only changes | None | None — no output change | Typo fix in methodology doc, clarified pillar description |
| B — Non-breaking refinement | Output-preserving improvements | 7-day changelog entry | None — historical comparability preserved | Source replacement (same metric), bug fix correcting erroneous output, cadence change |
| C — Output distribution shift | Changes that affect backtest comparability | 14-day advance notice (SLA 4) | Backtest discontinuity — methodology version boundary | Pillar weight adjustment, regime classification change, model retrain with new feature set, confidence scoring recalibration |
| D — Schema-breaking | Endpoint or response schema changes | 60-day deprecation (API versioning policy) | New API version required | Field rename, field removal, endpoint restructure |

Class C is the primary SLA 4 trigger. Class D triggers the API versioning policy
(`/v1/` → `/v2/`). Classes A and B do not trigger SLA 4 but are logged in the
methodology changelog for auditability. Customers reviewing PIT-correct historical
data can identify methodology version boundaries from Class C entries.

### Methodology Documentation

**Location:** `fromthebridge.net/methodology` (public, no auth). Versioned in git.
Prior versions accessible at stable URLs indefinitely.

**Eight sections:** (1) What this product produces · (2) Coverage · (3) Signal
architecture · (4) EDSx methodology · (5) ML methodology · (6) Data sources ·
(7) Known limitations · (8) Changelog.

Sections §3.1–§3.5 (Signal Coverage, Confidence, Pillar Coverage, Null State Taxonomy,
Evaluation Lag) and §4.1–§4.5 (Directional Accuracy, Return Attribution, Probability
Calibration, Regime-Conditional Performance, Pillar Attribution) map directly to
`GET /v1/signals/performance` response fields. Full mapping in §Performance History
Endpoint — Methodology Documentation Mapping (B3). These sections must be drafted
before Phase 6 gate.

Methodology doc version increments when: a model is retrained and output distribution
changes materially · a pillar definition changes · a source is added or removed from
signal computation · regime classification logic changes.

### First Customer Onboarding (Complete Sequence)

**Profile A (Signal API tier — self-serve or light-touch):**

1. Discovery via published content (PIT post, methodology doc, GitHub schema, or
   48h-delayed public preview on `fromthebridge.net`)
2. Customer visits pricing page, reviews methodology doc URL and `GET /v1/instruments`
3. Direct pricing conversation. Signal API: $199/month or $1,990/year, paid upfront.
   No free trials. No discounts for testimonials or referrals.
4. Written agreement (structured email sufficient in v1). Covers: deliverables, SLAs,
   redistribution restrictions, module access.
5. API key issued manually (Signal API tier). Webhook secret provisioned separately if
   requested. Key delivered via 1Password secure share.
6. Onboarding note: base URL, auth, recommended first queries, how to read confidence
   tiers and null states, how to check staleness, support contact, methodology URL,
   performance endpoint for track record.
7. First signal pull — the API is the product.
8. Day 7 check-in: "Anything specific you want me to add coverage for, or any edge
   cases you think the model should handle differently?" Early subscribers who engage
   become the first community and word-of-mouth source.
9. Ongoing: methodology change notices per SLA 4, staleness notifications per SLA 3,
   monthly manual invoicing (net 7 payment terms).

**Profile B (Ecosystem Monitor tier — relationship-driven):**

1. Warm intro or direct outreach after ≥3 Profile A subscribers + 60-day live history
2. v0 ecosystem report shared: "I produced a draft report using our systematic signal
   framework. I would like your feedback on whether this is useful. No ask attached."
3. Follow-up with revised report if feedback received. Introduce founding pricing ($4,500).
4. Written agreement. Payment: 50% on engagement, 50% on delivery.
5. API key issued (Ecosystem Monitor tier). Scoped instrument access per coverage
   agreement (instrument coverage set).

**Profile C (Risk Feed tier — relationship-driven):**

1. Direct outreach to exchanges, market makers, prop desks after Liquidation Risk
   Monitor is live (Phase 5+).
2. Demo: real-time BLC-01 liquidation data + Derivatives Intelligence Feed.
3. Custom pricing based on instrument coverage set.
4. Written agreement. Annual contract, direct sales.
5. API key issued (Risk Feed tier). Custom instrument coverage per agreement.

**Intelligence Suite:** Sales-only. Phase 6. Qualifies on: API volume >2k calls/day,
redistribution rights required, or bespoke coverage request.

### Decisions Locked

| Decision | Outcome |
|---|---|
| v1 delivery model | API-first. No dashboard. |
| v1 customer | Savvy retail, technical lean, broad coverage interest |
| "Institutional grade" | Internal quality bar only |
| Tier model | 5-tier hybrid: Preview / Signal API / Intelligence Suite / Risk Feed / Ecosystem Monitor. Tiers set access + contract; modules set entitlement scope. |
| Preview tier endpoints | `GET /v1/market/prices`, `GET /v1/macro`, `GET /v1/instruments`, `GET /v1/health` |
| Signal API+ endpoints | `GET /v1/signals`, `GET /v1/signals/{id}`, `GET /v1/signals/performance`, `GET /v1/regime` |
| Intelligence Suite-only | `GET /v1/features/{id}`, provenance trace, window=all |
| Risk Feed-only | `GET /v1/liquidations` (BLC-01), custom instrument coverage |
| Redistribution gating | Three-state enum. Direct-lineage-only propagation. 12-step middleware enforcement. |
| Authentication | Manual key issuance. API key required on all tiers. No self-serve. |
| Social distribution | Telegram + X. Funnel only — not customer delivery. |
| Social gate | Activates after Phase 4 shadow period passes. Manual by Stephen. |
| Webhook | Available in v1 as customer-initiated integration. At-least-once, HMAC-signed. |
| SLA count | Four: signal freshness, API uptime, staleness notification, methodology change |
| Methodology doc | Public URL. Versioned. 8 sections. Updated on every material change. |
| First customer | Direct engagement. Written agreement. No free trials. Manual invoicing. |
| Performance endpoint | `GET /v1/signals/performance`. Pre-materialised marts. p95 ≤ 500ms. Phase 5 scope (B3). |
| Performance PIT rules | Anchor: `observed_at > computed_at`. Backfill guard: `ingested_at_signal <= outcome_observed_at`. Non-negotiable. |
| Performance metrics | 2 dbt marts: `signal_outcomes` (PIT boundary), `performance_metrics` (rolling aggregations). ~7,380 rows. |
| Neutral threshold | Fixed ±0.10. Noise floor at 2/5 pillars. Not configurable per-request. |
| Sharpe convention | Population std, no Bessel correction. Large N. |
| Cross-sectional method | Equal-weight. <30 resolved signals excluded entirely. |
| Reliability diagram | 10 fixed bins. Request-time DuckDB. Intelligence Suite tier only. |
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
6. Seed sources catalog (all 11 sources including CFTC COT, with redistribution flags)
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
| Redistribution flags | SoSoValue and CoinMetrics have `redistribution_status = 'blocked'`; Coinalyze/BGeometrics/Etherscan/BLC-01 have `redistribution_status = 'pending'`; remaining sources `'allowed'` |

**Timeline estimate:** 3–5 days. Primary risk: schema defects during DDL application.

---

#### Phase 1: Data Ingestion

**Scope:** All collection agents, all adapters, migration execution. ~65 Dagster
Software-Defined Assets at launch (one per (metric_id, source_id), Option B
per-instrument partitioning).

**Steps:**
1. Migration adapters in order: Tiingo → Coinalyze → FRED → DeFiLlama DEX →
   DeFiLlama Lending → CoinMetrics → Exchange Flows (with wei fix) → ETF Flows
2. Backfill jobs for shallow datasets: DeFiLlama protocols, stablecoins,
   CoinPaprika market cap
3. Add `BAMLH0A0HYM2` (HY OAS) and 18 additional FRED series to FRED adapter
4. Extend DeFiLlama adapter with `collect_yields()` method (E3 — 4 lending metrics)
5. Build CFTC COT adapter (E4 — 4 macro positioning metrics, Socrata API)
6. Build BLC-01 rsync pull routine (Server2 → proxmox landing directory)
7. Deploy production collection agents one at a time, verify each before next
8. NAS backup job for MinIO configured and verified
9. Coverage verification query — all active metrics at ≥ 90% completeness for
   signal-eligible instruments
10. First instrument tier promotion run

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Dagster services healthy | 3 Dagster services running: webserver (`empire_dagster_webserver`), daemon (`empire_dagster_daemon`), code server (`empire_dagster_code`) |
| Dagster in docker-compose | Dagster service definitions added to `docker-compose.yml` with correct volume mounts and secrets |
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
| Export round-trip | Full path verified: collection → Bronze → Silver → export trigger → Gold readable via DuckDB |
| FINAL query 50k-row window | Wall time < 10 seconds |
| FINAL query 500k-row window | Wall time < 60 seconds (simulated backfill) |
| Export benchmark baseline | Results documented in Phase 1 completion report for Phase 2 regression testing |
| PF-6 utilization unit | curl DeFiLlama `/yields` → verify utilization field is decimal (not percent); result recorded before adapter build |
| DeFiLlama yields Silver | Yields adapter producing Silver rows for all 4 metrics across scoped pools |
| CFTC COT Silver | CFTC adapter producing Silver rows for 4 metrics (BTC + ETH) |
| `macro.credit.hy_oas` in FRED | `BAMLH0A0HYM2` series confirmed collecting via FRED adapter, Silver rows present |
| `ingested_at` correctness | For ≥1 source with known publication lag (CFTC COT): verify `ingested_at` = actual wall-clock collection time (Friday), not `observed_at` (Tuesday). For ≥1 real-time source: verify `ingested_at` ≈ `observed_at` within adapter execution window. |
| GE checkpoint | `bronze_core` suite runs on every collection asset. Checkpoint result stored in Dagster asset metadata. Dead-lettered rows have valid `rejection_code` values. |
| Dead letter nullability | Tests pass: null `borrow_apy` (valid) vs. null `utilization_rate` (dead letter violation) |
| C2 bronze-archive bucket | `bronze-archive` bucket created, lifecycle policy applied to `bronze-hot` (90-day expiry) |
| C2 archive credentials | `MINIO_BRONZE_ARCHIVE_USER` isolated — no write permission to `bronze-hot` |
| C2 `bronze_archive_log` DDL | `forge.bronze_archive_log` deployed (Rule 3 compliant — admin metadata only) |
| C2 archive job | `bronze_cold_archive` Dagster asset running daily 02:00 UTC, `checksum_verified = true` on test partitions |
| C2 expiry audit | `bronze_expiry_audit` Dagster asset running daily, zero unarchived partitions within 5 days of expiry |
| C2 partition discovery | DuckDB over Iceberg metadata returns partition list in < 100ms |
| C2 reprocessing test | End-to-end reprocessing path verified: archive → hot → Silver dedup → Gold export → features |

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
| Gold Iceberg readable | Gold Iceberg table readable by DuckDB; end-to-end query returns expected rows |
| dbt models pass | All dbt models pass (`dbt test` clean, zero failures) |
| forge_compute features | `forge_compute` Python assets produce feature values for all registered metrics |
| Breadth scores | Breadth scores verified against hand-calculated expected values |
| Compute latency | Full feature compute for 10 instruments completes within 60 minutes on proxmox hardware (smoke test — revisable at Phase 3 gate if profile changes) |

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
| Output schema | Validates against §L2.8 response schema (including provenance block and horizon fields) for all signal-eligible instruments |
| `marts.signals_history` | `forge_compute` Python asset (Dagster SDA, not dbt). Table deployed with all required fields: `computed_at`, `ingested_at`, `regime`, `pillar_scores`, `p_bullish`, `p_neutral`, `p_bearish`, `confidence`, `final_score`, `null_states`. Regime stored at emission time, not recomputed at query time. Writes to Gold (Iceberg on MinIO). |
| Null propagation | Missing pillar degrades confidence, signal still serves |
| Pillar count | 2 live pillars scoring (trend_structure, liquidity_flow); 3 planned pillars (valuation, structural_risk, tactical_macro) producing null states with documented reason codes |
| Regime at emission | Regime classification stored at emission time in `marts.signals_history.regime`; not recomputed at query time |
| Neutral threshold | ±0.10 threshold verified: signals within range classified as neutral; downstream performance metrics (hit_rate, Sharpe) computed correctly with neutral exclusion |
| DG-R1 recorded | ✅ Resolved: Option B — parallel ToS audit during Phase 4 shadow. Coinalyze/BGeometrics/Etherscan/BLC-01 audits during shadow period. SoSoValue/CoinMetrics remain `blocked`. ETF flow fields null-flagged at Phase 5 launch if unresolved. See §Redistribution Enforcement. |

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
| Shadow artifacts | Shadow evaluation report generated: OOS vs shadow accuracy comparison, stability analysis, per-model graduation scorecard |
| 48h public preview | 48h-delayed public preview operational on `fromthebridge.net` by shadow week 2 (EDSx-only composite; ML not included until graduation) |

**Timeline estimate:** 3–4 weeks. Primary risk: graduation criteria not met on first
pass.

---

#### Phase 5: Signal Synthesis and Serving

**Scope:** Layer 2 synthesis, API, delivery mechanisms.

**Note:** Layer 2 synthesis algorithm is fully specified in §L2.1–L2.8 (Thread 2).

**Steps (4 tracks — see consolidated plan §4):**

Track A — Entitlement DDL + Seeding:
1. Deploy `db/migrations/entitlement/0001_entitlement_schema.sql` (12 tables)
2. Bootstrap `audit_access_log` partitions: create current + next month partitions
   via psql before starting FastAPI (prevents partition routing error on first request)
3. Seed plan data: Preview/Signal API/Intelligence Suite/Risk Feed/Ecosystem Monitor +
   module access matrix + rate limits + endpoint access + field access + lookback config
   + instrument coverage sets

Track B — Dagster Assets:
3. `forge_redistribution_refresh` asset (source_catalog → metric_lineage → blocked set)
4. `audit_partition_creator` scheduled asset (monthly partitions)

Track C — FastAPI Serving:
5. Implement synthesis logic per §L2.1–L2.8
6. Build `EntitlementMiddleware` (12-step chain, LRU cache, async audit)
7. Build all API endpoints with tier-aware field filtering
8. Build webhook delivery (at-least-once, HMAC signing)
9. Full end-to-end provenance trace

Track D — Test Suite:
10. D2 Tier 0 tests (T0-1 through T0-4) — gate-blocking
11. D2 Tier 1 + Tier 2 tests

Track E — Performance History (B3):
12. Audit EDSx-02 and EDSx-03 R3 backfill `ingested_at` values — determines verified
    performance history depth at launch (**blocking: must complete before step 13**)
13. Build `signal_outcomes` dbt model — PIT JOIN condition (`observed_at > computed_at`),
    `ingested_at` backfill guard, outcome resolution logic. Verify against actual
    EDSx-02/EDSx-03 R3 signal history.
14. Build `performance_metrics` dbt model — rolling aggregations across standard
    `(instrument_id, track, horizon_days, window_days)` combinations. Dagster asset
    dependency on `signal_outcomes`.
15. Implement `GET /v1/signals/performance` endpoint — DuckDB reads from
    `performance_metrics` + request-time reliability diagram from `signal_outcomes`
16. Integrate performance endpoint into entitlement middleware — tier-based field
    redaction per B3 field-tier mapping table

Track F — Signal Snapshot Cache (C3):
17. Implement `SignalCache` + `SignalSnapshot` dataclasses in FastAPI `app.state`
18. Implement `/internal/cache/refresh` endpoint with `INTERNAL_CACHE_TOKEN` auth
19. Implement `/healthz/ready` readiness endpoint; wire to Docker health check
20. Implement `apply_redistribution_filter()` at cache populate time (Option C — gated
    fields never in cache object)
21. Implement per-request tier filter using Thread 7 field-level tier gate definitions
22. Implement `signal_snapshot_writer` Dagster asset: build snapshot from Marts →
    write to `MinIO gold/snapshots/latest.json` → HTTP POST to FastAPI
23. Implement warm start from MinIO on FastAPI lifespan startup (<5s)
24. Add response envelope freshness fields to all `/v1/signals` responses
25. Wire timing middleware + Prometheus metrics (`signal_cache_computed_at_epoch`,
    `signal_cache_refresh_failure_total`, cache miss rate)

**Hard gate:**

| Criterion | Pass condition |
|---|---|
| Synthesis output | Matches API contract for all signal-eligible instruments |
| Agreement/disagreement | Both scenarios produce correct confidence adjustments |
| Magnitude | Non-null for all signal-eligible instruments |
| API endpoints | All endpoints return correct responses |
| Redistribution filter | T0 tests pass — gate-blocking before any external key issued |
| Provenance | Full trace verified for BTC and ≥ 2 other instruments |
| Delivery | Webhook verified against test endpoints |
| Staleness flag | Simulated source failure triggers flag within 2 collection cycles |
| Latency SLAs | All endpoints meet p95 targets under representative load |
| T0-1 Coinalyze hard block | Signal API key: `liquidity_flow` + `derivatives_pressure` absent; `_redistribution_notice` present; HTTP 200 |
| T0-2 No over-suppression | Same Signal API key: `trend_structure` + `valuation` present and populated |
| T0-3 Revoked key | Returns 401; cache invalidation ≤60s TTL |
| T0-4 Preview tier block | Preview key → `/v1/signals` → 403 `endpoint_not_permitted` |
| Entitlement DDL | All 12 entitlement tables deployed, seed data loaded |
| Entitlement middleware | 12-step chain operational, LRU cache functional |
| Concurrent limit | `asyncio.Semaphore` enforced; distinct 429 from rate limit |
| Audit log | Async write operational; fallback to JSONL on pool failure |
| D1 API key hashing | `forge.api_keys.key_hash` uses argon2id (argon2-cffi); verification by `key_prefix` lookup → argon2id verify. No SHA-256. |
| D1 secrets initialized | `scripts/init_secrets.sh` executed; `grep -r 'REPLACE_ME' secrets/` returns zero results |
| D1 credential isolation | ClickHouse: `ch_writer` INSERT-only, `ch_export_reader` SELECT-only, `default` user suspended. MinIO: 3 service accounts with bucket-scoped policies. No service has credentials outside its scope. |
| D1 secrets file permissions | All `secrets/` files chmod 600, directory chmod 700, chown root:root |
| B3 `ingested_at` audit | EDSx-02 and EDSx-03 R3 backfill `ingested_at` values audited; verified performance history depth documented |
| B3 `signal_outcomes` PIT | JOIN condition verified: `observed_at > computed_at` anchor, `ingested_at_signal <= outcome_observed_at` guard, backfill exclusion confirmed on actual data |
| B3 `performance_metrics` | All standard `(track, horizon, window)` combinations materialised; row count within expected range (~7,380) |
| B3 null contract | Window with < 30 resolved signals returns metric objects with null values and `min_observations_met: false` — no absent keys |
| B3 cross-sectional | `instruments_excluded_insufficient_history` count correct; excluded instruments contribute no zeros |
| B3 entitlement tiers | All 5 tiers verified against B3 field-tier mapping: Preview=403, Signal API=365d cap, Ecosystem Monitor=+pillar scoped, Intelligence Suite=+reliability+all, Risk Feed=+BLC-01 |
| B3 reliability diagram | Request-time DuckDB grouped aggregation on `signal_outcomes` returns 10-bin reliability data in < 50ms |
| B3 latency SLA | `GET /v1/signals/performance` p95 ≤ 500ms under representative load |
| C3 cache warm start | FastAPI reads `gold/snapshots/latest.json` from MinIO on startup; `cache.ready=True` within 5s |
| C3 readiness gate | `/healthz/ready` returns 503 until cache warm; Docker health check prevents premature traffic |
| C3 redistribution isolation | Option C verified: gated field values (SoSoValue, CoinMetrics) never present in `app.state` cache object |
| C3 tier filtering | Per-request tier filter produces correct field sets for all 5 tiers against Thread 7 field-level gates |
| C3 staleness behavior | Cache age > 9h → `is_stale=True` in response; no HTTP error; `next_computation_estimated=null` |
| C3 latency SLA | `GET /v1/signals` full universe p95 < 50ms (cache hit) |
| C3 cache refresh | `signal_snapshot_writer` → `/internal/cache/refresh` → atomic swap verified; no stale window during normal cycle |
| C3 response envelope | All `/v1/signals` responses include freshness fields (`snapshot_computed_at`, `cache_age_seconds`, `is_stale`, `cache_miss`) |
| C3 Dagster asset wired | `signal_snapshot_writer` asset in Dagster repository; dependency on signal compute assets |
| C3 monitoring | Prometheus metrics: `signal_cache_computed_at_epoch`, `signal_cache_refresh_failure_total`, cache miss rate |
| F1 first API key | First external API key issued to a paying customer (Signal API tier) |
| F1 pricing page | Pricing page live at `fromthebridge.net` with Signal API/Intelligence Suite/Risk Feed/Ecosystem Monitor tiers |
| F1 API docs | API documentation live and publicly accessible |
| F1 performance summary | Performance summary page live (linked from pricing page) |
| F1 methodology doc | Methodology document published at `fromthebridge.net/methodology` |

**Timeline estimate:** 1–2 weeks.

---

#### Phase 6: Productization

**Scope:** Health monitoring, methodology docs, ToS audit, first customer.

**Steps:**
1. Health monitoring dashboards (collection, coverage, signal, infrastructure)
2. Methodology documentation (8 sections — see Output Delivery section)
3. ToS audit completion for all 11 sources
4. First customer delivery

**Hard gate (before first customer delivery):**

| Criterion | Required |
|---|---|
| Health monitoring | Any collection failure diagnosable within 15 minutes |
| Methodology docs | All 8 sections complete at `fromthebridge.net/methodology` |
| ToS audit | All sources audited, restrictions enforced in API |
| API key auth | Tier enforcement working |
| Redistribution filter | Verified in production |
| D1 annual rotation | Calendar entry created for March 2027 rotation window |
| D1 secrets runbook | `secrets/` directory structure and `init_secrets.sh` documented in CLAUDE.md runbook |
| D1 isolation verified | ClickHouse credential isolation + MinIO service account scoping confirmed in production |
| Public status page | Exists, manually updatable |
| First customer delivery | First paying customer (Signal API tier) has received API key, confirmed successful API call, and acknowledged data receipt |

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
| Layer 2 synthesis | Designed and locked (§L2.1–L2.8). Implementation in Phase 5. |
| First customer | Signal API tier after Phase 5 gate. Direct engagement. Real pricing. No free trials. |
| ToS audit timing | Coinalyze/BGeometrics/Etherscan/BLC-01: parallel during Phase 4 shadow (DG-R1 Option B). SoSoValue/CoinMetrics: Phase 6. |
| Schema defects | Fixed before Phase 1 begins. No schema changes after Phase 0 gate. |
| Security posture (D1) | File-based secrets (`secrets/` bind mounts, chmod 600). ClickHouse credential isolation (`ch_writer` INSERT-only, `ch_export_reader` SELECT-only). MinIO per-bucket service accounts. Customer API keys: argon2id hashed, 1Password delivery. Annual rotation March. 4 incident playbooks. Phase 0 corrective: SEC-01 through SEC-06. |


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
webserver port 3010 and MinIO console port 9002 are **not published to the host
interface** — internal Docker network only. Dagster accessed via SSH port forward
(`ssh -L 3010:localhost:3010 root@192.168.68.11`); MinIO administered via `mc` CLI
over SSH. Phase 6 upgrade: add `dagster.fromthebridge.net → :3010` to Cloudflare
tunnel behind Zero Trust operator-email policy.

**Correction (locked here):** design_index previously stated "Dagster dedicated LXC."
Corrected to "Dagster dedicated Docker service." LXC containers are Proxmox-specific
and do not translate to cloud deployment. All services run as Docker containers.

### Secrets Management (D1)

**Philosophy:** File-based bind mounts in `/opt/empire/FromTheBridge/secrets/`
(chmod 600, root:root). No credentials in environment variables, `docker-compose.yml`,
or `docker inspect` output. Initialized via `scripts/init_secrets.sh`. Cloud-migration
compatible — `secrets/` directory replaced by AWS Secrets Manager or HashiCorp Vault
with zero application code changes.

**Directory structure:**

```
/opt/empire/FromTheBridge/
├── docker-compose.yml       # No secrets. Service topology and volume mounts only.
├── .env                     # chmod 644. Non-sensitive config only (ports, hostnames, log levels).
├── secrets/                 # chmod 700, chown root:root
│   ├── pg_forge_user.txt
│   ├── pg_forge_reader.txt
│   ├── ch_writer.txt
│   ├── ch_export_reader.txt
│   ├── minio_root_key.txt       # Operator + mc CLI only — never mounted in containers
│   ├── minio_root_secret.txt
│   ├── minio_bronze_key.txt
│   ├── minio_bronze_secret.txt
│   ├── minio_gold_key.txt
│   ├── minio_gold_secret.txt
│   ├── minio_export_key.txt
│   ├── minio_export_secret.txt
│   ├── minio_marts_key.txt
│   ├── minio_marts_secret.txt
│   ├── cf_tunnel_token.txt
│   └── external_apis/          # chmod 700
│       ├── tiingo.txt
│       ├── coinalyze.txt
│       ├── sosovalue.txt
│       ├── etherscan.txt
│       ├── coinpaprika.txt
│       ├── coinmetrics.txt
│       ├── bgeometrics.txt
│       └── defillama.txt       # Empty file (public API, present for uniformity)
```

All files: chmod 600, single line raw credential value, no `KEY=VALUE` format.

**Docker injection pattern:** Services read credentials from bind-mounted files at
`/run/secrets/` via `read_secret()` utility (LRU-cached per process lifetime). Each
service mounts only the credentials it needs — no service has access to secrets
outside its operational scope.

**`crypto_user`:** Operator terminal only for DDL migration runs. Never mounted in
any service container.

### Credential Isolation (D1)

**ClickHouse isolation (enforces Rule 2 structurally):**

| User | Access | Mounted On |
|---|---|---|
| `ch_writer` | INSERT on `forge.observations` + `forge.dead_letter` only. No SELECT. | `empire_dagster_code` only |
| `ch_export_reader` | SELECT on `forge.observations`, `forge.dead_letter`, `forge.current_values`. No INSERT. | `empire_dagster_export` only |
| `ch_admin` | All (DDL + admin) | Never mounted — operator terminal only |

DDL: `db/migrations/clickhouse/0002_credential_isolation.sql`. Includes `default` user
suspension and 5-assertion verification checklist.

**MinIO service accounts:**

| Account | Policy | Mounted On |
|---|---|---|
| `bronze_writer` | PutObject on `bronze-hot/*` only | `empire_dagster_code` |
| `bronze_archive_writer` | PutObject + GetObject + ListBucket on `bronze-archive/*` only (C2) | `empire_dagster_code` (archive asset only) |
| `gold_reader` | GetObject + ListBucket on `gold/*` + `marts/*` | `forge_compute`, `empire_api` |
| `export_writer` | PutObject + GetObject + ListBucket + DeleteObject on `gold/*` | `empire_dagster_export` |
| `marts_writer` | PutObject + GetObject + ListBucket on `marts/*` only | `empire_dagster_code` (dbt + forge_compute) |
| MinIO root | Admin (bucket + service account management) | Never — operator `mc` CLI only |

Setup script: `scripts/setup_minio_service_accounts.sh`.

**Critical isolation enforcement:**
- `ch_export_reader` mounted in `empire_dagster_export` only — no other service has
  ClickHouse SELECT credentials (Rule 2)
- `ch_writer` not mounted on `empire_dagster_export` — export reads Silver, never
  writes back
- MinIO root credentials not mounted on any adapter or compute service

### Customer API Key Lifecycle (D1)

**Hashing:** argon2id (`argon2-cffi>=23.1.0`). Plaintext never stored — only
`key_hash` persists. `key_prefix` (12 chars: `ftb_` + 8 token entropy) stored for
log identification without brute-force risk.

**Delivery:** 1Password secure share (one-time-view link). Never email, SMS, or Slack.
After the share is viewed, the plaintext is unrecoverable without a rotation.

**Key format:** `ftb_` + 40 URL-safe base64 chars = 240 bits entropy.

**Verification:** Lookup by `key_prefix` (indexed) → argon2id verify in application.
Rejects revoked and expired keys. `last_used_at` updated on each successful verify.

**Rotation:** `rotate_api_key()` — old key revoked immediately, new key linked via
`rotation_of` FK. Zero-downtime: new key works before old key stops.

### Encryption Posture (D1)

**In transit:** TLS 1.3 at Cloudflare edge. Inter-container traffic unencrypted
(accepted — single Docker bridge on single host, no external network exposure).
NAS backups encrypted in transit via SSH.

**At rest:** PostgreSQL, ClickHouse, MinIO unencrypted at v1 (not PII, not regulated;
API keys stored as argon2id hashes). NAS backups GPG AES-256 encrypted. All cloud
migration targets (RDS, ClickHouse Cloud, S3) provide encryption by default.

### Rotation Policy (D1)

**Annual rotation window:** Last week of March. First rotation: March 2027.
All non-compromised credentials rotated in a single window.

Rotation runbook covers: PostgreSQL (`forge_user`, `forge_reader`), ClickHouse
(`ch_writer`, `ch_export_reader`), MinIO (3 service accounts), external API keys,
Cloudflare tunnel token. Each rotation: ~30s downtime (service restart). Customer
API key rotation: zero downtime (revoke-then-issue).

### Credential Inventory and Access Controls (D1)

**Credential store:** `secrets/` directory on proxmox, bind-mounted per-service in
`docker-compose.yml`. File permissions: `chmod 600` (files), `chmod 700` (directory),
`chown root:root`. No secrets manager in v1 — file-based is acceptable for
single-operator deployment.

**Credential inventory:**

| Credential | Stored in | Used by | Rotation |
|------------|-----------|---------|----------|
| `POSTGRES_FORGE_USER_PASSWORD` | `secrets/postgres_forge_user` | `empire_dagster_code`, `empire_fastapi` | Annual (March) |
| `POSTGRES_FORGE_READER_PASSWORD` | `secrets/postgres_forge_reader` | `empire_fastapi`, MCP server | Annual (March) |
| `CH_WRITER_PASSWORD` | `secrets/clickhouse_ch_writer` | `empire_dagster_code` | Annual (March) |
| `CH_EXPORT_READER_PASSWORD` | `secrets/clickhouse_ch_export_reader` | `empire_dagster_code` (export asset only) | Annual (March) |
| `MINIO_BRONZE_WRITER_ACCESS_KEY/SECRET` | `secrets/minio_bronze_writer` | `empire_dagster_code` | Annual (March) |
| `MINIO_ARCHIVE_WRITER_ACCESS_KEY/SECRET` | `secrets/minio_archive_writer` | `empire_dagster_code` | Annual (March) |
| `MINIO_EXPORT_WRITER_ACCESS_KEY/SECRET` | `secrets/minio_export_writer` | `empire_dagster_code` (export asset) | Annual (March) |
| `MINIO_MARTS_WRITER_ACCESS_KEY/SECRET` | `secrets/minio_marts_writer` | `empire_dagster_code` (dbt + forge_compute) | Annual (March) |
| External API keys (Tiingo, etc.) | `secrets/api_keys/{source_id}` | `empire_dagster_code` | Per-provider policy |
| `CLOUDFLARE_TUNNEL_TOKEN` | systemd env | `cloudflared` service | On compromise only |
| `INTERNAL_CACHE_TOKEN` | `secrets/internal_cache_token` | `empire_dagster_code`, `empire_fastapi` | Annual (March) |

**Admin access:** `root` SSH key auth only (no password). SSH keys stored on
bluefin (`~/.ssh/`) and operator laptop. No shared admin accounts. `docker exec`
requires root. ClickHouse `ch_admin` user: never mounted in any container — used
only via operator terminal (`docker exec -it empire_clickhouse clickhouse-client`).

**Initialization:** `scripts/init_secrets.sh` generates all credentials, creates
directory structure, sets permissions. Run once at initial deployment. Verification:
`grep -r 'REPLACE_ME' secrets/` must return zero results (Phase 0 corrective SEC-01).

**Future (B2B readiness):** Migrate to HashiCorp Vault or AWS Secrets Manager when
customer count exceeds 10 or any B2B customer requires SOC 2 evidence. Zero code
change — swap `secrets/` file reads for Vault API calls in a secrets loader module.

### Incident Response Playbooks (D1)

Four playbooks documented in D1 result with step-by-step procedures:

| Scenario | Severity | Containment Target | Key Actions |
|---|---|---|---|
| A — Customer API key compromised | High | < 5 min | Revoke key immediately (`UPDATE revoked_at`), investigate via audit log, rotate + deliver via 1Password, verify old key returns 401 |
| B — External API key leaked (e.g., committed to git) | Critical | < 2 min | Rotate at provider immediately, update secrets file, restart Dagster, audit git history — if found: treat as ALL-CREDENTIALS compromised |
| C — Cloudflare tunnel token exposed | High | < 5 min | DELETE tunnel entirely (invalidates all tokens), create new tunnel + update token, verify routes |
| D — PostgreSQL `forge_user` compromised | Critical | < 5 min | Terminate all connections + revoke login immediately, assess damage via `pg_stat_statements`, rotate password, restart services, verify catalog row counts match Phase 0 baseline |

### Cloudflare Zero Trust (D1)

| Route | Destination | Protection |
|---|---|---|
| `fromthebridge.net` | `:3002` (landing page) | Public |
| `fromthebridge.net/briefs`, `/launch` | `:3002` | Public |
| `fromthebridge.net/api/*` | `:8000` (FastAPI) | Public route — API key auth by FastAPI |
| `fromthebridge.net/bridge/*` | `:3002` (Bridge UI) | **Zero Trust — operator only** |
| `dagster.fromthebridge.net` (Phase 6) | `:3010` | **Zero Trust — operator only** |

Access policy: Cloudflare Access one-time PIN or Google OAuth, 24h session, operator
email only, optional IP range restriction (192.168.68.0/24).

### Phase 0 Security Corrective Actions (D1)

Six items required before Phase 1 begins:

1. **SEC-01:** `scripts/init_secrets.sh` executed, all `REPLACE_ME` populated
2. **SEC-02:** ClickHouse credential isolation DDL deployed (`0002_credential_isolation.sql`), 5 assertions pass
3. **SEC-03:** MinIO service accounts created, bucket-scoped policies applied
4. **SEC-04:** `docker-compose.yml` secret mounts verified (per-service isolation)
5. **SEC-05:** Initial secrets backup to NAS (GPG encrypted)
6. **SEC-06:** Cloudflare Zero Trust verified on `/bridge/*` routes

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

**Key constraint:** `ReplacingMergeTree` deduplication is eventual. The export asset
uses `SELECT ... FINAL` scoped to the watermark delta (not the full table) to ensure
clean data reaches Gold. See §Silver→Gold Export for the full query pattern,
anomaly guard, and partition overwrite mechanics.

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
at startup. ~65 assets at Phase 1 launch; ~200 at full buildout.

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

**Deployment model:** forge_compute runs as Dagster Software-Defined Assets inside
`empire_dagster_code` — not a separate container. DuckDB runs embedded within the
asset process. Dagster provides triggering (downstream of `gold_observations`),
dependency tracking, retry logic, and observability. MinIO credential: `gold_reader`
(GetObject + ListBucket on `gold/*` + `marts/*`). If forge_compute ever needs GPU or
memory isolation, it can be split to a dedicated container as a zero-code-change
container boundary move — no redesign required.

#### ADR-007: PostgreSQL as Catalog

**Decision:** PostgreSQL self-hosted, Docker. Existing `empire_postgres` container
extended with `forge` schema for catalog tables. No time series data — ever.

**Phase 0 catalog tables (12):** `assets`, `asset_aliases`, `venues`, `instruments`,
`source_catalog`, `metric_catalog`, `metric_lineage`, `event_calendar`,
`supply_events`, `adjustment_factors`, `collection_events`,
`instrument_metric_coverage`.

**Phase 5 entitlement tables (12, additive — D2):** 9 core entitlement tables:
`customers`, `api_keys`, `plans`, `rate_limit_policies`, `subscriptions`,
`plan_endpoint_access`, `plan_field_access`, `plan_lookback_config`,
`plan_instrument_access`. Plus 3 supporting tables: `customer_instrument_overrides`
(Ecosystem Monitor + Risk Feed tier scoping via instrument coverage set),
`metric_redistribution_tags` (pre-computed redistribution
state per metric, refreshed by PostgreSQL trigger + Dagster nightly; see
§Redistribution Enforcement), `audit_access_log` (partitioned, monthly, 90d hot +
7-year MinIO archive). Total: 12 new tables in `forge` schema.

All entitlement tables: `forge_reader` has SELECT, `forge_user` has
INSERT/UPDATE/DELETE. Rule 3 verified: `audit_access_log` stores operational request
events (`logged_at`, `status`, `latency`) — not metric observations.

DDL migration: `db/migrations/entitlement/0001_entitlement_schema.sql`.

**`audit_access_log` DDL (schema locked, migration deferred to Phase 5):**

```sql
CREATE TABLE forge.audit_access_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY,
    customer_id     INTEGER NOT NULL REFERENCES forge.customers(id),
    api_key_id      INTEGER NOT NULL REFERENCES forge.api_keys(id),
    endpoint        TEXT NOT NULL,               -- e.g. '/v1/signals', '/v1/timeseries'
    method          TEXT NOT NULL,               -- GET, POST
    status          SMALLINT NOT NULL,            -- HTTP status code
    latency_ms      INTEGER NOT NULL,
    logged_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    tier            TEXT NOT NULL,                -- 'preview','signal_api','intelligence_suite','risk_feed','ecosystem_monitor','internal'
    denial_reason   TEXT,                         -- 'rate_limit_exceeded','concurrent_limit','endpoint_not_permitted', NULL if 2xx
    redistribution_events JSONB,                  -- array of {metric_id, source_id, action, field}
                                                  -- action: 'redistribution_filtered' | 'pending_flagged'
                                                  -- field:  the redacted field name
    request_id      UUID NOT NULL,                -- correlation ID for tracing
    PRIMARY KEY (id, logged_at)
) PARTITION BY RANGE (logged_at);
-- Monthly partitions created by audit_partition_creator Dagster asset.
-- BOOTSTRAP: At first Phase 5 deployment, create current + next month partitions
-- manually BEFORE starting FastAPI. Without bootstrap partitions, the first API
-- request will fail with a PostgreSQL partition routing error.
-- Example: CREATE TABLE forge.audit_access_log_2026_07 PARTITION OF forge.audit_access_log
--          FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
-- Retention: 90-day hot in PostgreSQL, then exported to MinIO (7-year archive).
-- Rate limit query: COUNT(*) WHERE customer_id = $1 AND logged_at > now() - interval '1 minute'
-- Requires index: CREATE INDEX idx_audit_rate_limit ON forge.audit_access_log (customer_id, logged_at);
```

> **Schema locked.** This DDL is authoritative for Phase 5 implementation. The open
> assumption A2-4 (line 2469) is resolved: the `redistribution_events` JSONB structure
> is `[{metric_id, source_id, action, field}]` where `action` is one of
> `redistribution_filtered` or `pending_flagged`.

**`plan_field_access` DDL (schema locked, migration deferred to Phase 5):**

```sql
CREATE TABLE forge.plan_field_access (
    id              SERIAL PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES forge.plans(id),
    field_path      TEXT NOT NULL,               -- e.g. 'pillar_attribution.*', 'calibration.reliability_diagram'
    access          TEXT NOT NULL DEFAULT 'granted',
    scope           TEXT,                         -- NULL = full access, 'scoped' = tier-specific restriction
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT plan_field_access_valid
        CHECK (access IN ('granted', 'denied')),
    UNIQUE (plan_id, field_path)
);
-- Seed data derived from B3 field-tier mapping table (§Field-Tier Mapping).
-- Each row maps a plan to a response field path and its access level.
-- 'scoped' scope on Ecosystem Monitor tier pillar_attribution means: only pillars
-- relevant to the customer's instrument coverage set are returned.
```

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
   mc mb local/bronze-hot local/bronze-archive local/gold  # if not restored from backup
   # Create service accounts: MINIO_BRONZE_HOT_USER, MINIO_BRONZE_ARCHIVE_USER
   scripts/setup_minio_service_accounts.sh
   # Apply 90-day lifecycle to bronze-hot
   mc ilm rule add local/bronze-hot --expiry-days 90 --prefix "" --status Enabled
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

### Disaster Recovery Objectives (D1)

**Scope:** Single-host deployment on proxmox (192.168.68.11). All services run on one
machine. A full host failure is the worst-case scenario.

#### DR Service Classes

| Class | Audience | RPO | RTO | Failover | v1 Posture |
|-------|----------|-----|-----|----------|------------|
| Internal (build-time) | Stephen / development | 24h | 4h | Manual | Current — manual recovery, NAS backups, no redundancy |
| Signal API (paid) | Signal API subscribers | 24h | 2h | Manual + priority response | Same infrastructure, faster manual response. SLA 2 (99.5% uptime) is the customer commitment. |
| Intelligence Suite (future) | Intelligence Suite subscribers | 4h | 1h | Warm standby | Not in v1. Trigger: first Intelligence Suite contract. Requires PostgreSQL replication + MinIO cross-region. |

**v1 is Class 1 only.** The honest posture is "manual recovery, single host, no
automatic failover." This is acceptable for early-stage with ≤30 customers and
transparent SLA commitments. Class 2 differentiation is response priority, not
infrastructure redundancy. Class 3 requires infrastructure investment triggered by
contract requirements.

#### Recovery Targets

| Component | RPO (max data loss) | RTO (max downtime) | Backup location |
|-----------|--------------------|--------------------|-----------------|
| PostgreSQL catalog | 24h | 30 min | NAS daily `pg_dump` (GPG encrypted) |
| ClickHouse Silver | 0 (reconstructable from Bronze) | 60 min | Rebuild from Bronze + source re-fetch |
| MinIO Bronze-hot | 24h | 30 min | NAS nightly rsync (GPG encrypted) |
| MinIO Bronze-archive | 24h | 60 min | NAS nightly rsync (GPG encrypted) |
| MinIO Gold | 0 (reconstructable from Silver) | 30 min | Re-run `gold_observations` export |
| Dagster metadata | 0 (reconstructable) | 10 min | Rebuilt from asset definitions on restart |
| BLC-01 tick data | Unrecoverable during outage | N/A | Server2 JSONL is sole source; NAS backup of landed files |
| Customer API keys | 24h | 30 min | Part of PostgreSQL backup |
| Secrets directory | On-change | 10 min | NAS GPG-encrypted snapshot after each change |

**RPO rationale:** 24h is acceptable for v1 — catalog data changes infrequently, and
all time-series data (Bronze, Silver, Gold) is either reconstructable or backed up
nightly. BLC-01 is the sole exception: real-time tick data has no historical refetch
path.

#### Proxmox Host Failure Scenario

**Full host loss (hardware failure, disk failure, OS corruption):**

1. **Immediate:** All services down. No automatic failover. Manual recovery only.
2. **Assessment:** SSH to NAS (192.168.68.91) — verify backup integrity, identify latest
   backup timestamps.
3. **Recovery path:**
   - Provision replacement host (or rebuild proxmox from Fedora install media)
   - Restore `secrets/` directory from NAS backup
   - Restore PostgreSQL from latest `pg_dump` backup
   - Restore MinIO data from NAS rsync mirror
   - Deploy docker-compose stack, apply migrations
   - ClickHouse: rebuilt empty — re-run collection for Silver, then export for Gold
   - Dagster: rebuilt from asset definitions — no data to restore
   - BLC-01: rsync pull from Server2 for any JSONL files accumulated during outage
4. **Estimated total recovery time:** 2–4 hours (manual process)
5. **Data loss:** Up to 24h of catalog changes + any BLC-01 ticks between last NAS
   backup and failure

#### Backup Policy

| Backup | Method | Cadence | Retention | Destination |
|--------|--------|---------|-----------|-------------|
| PostgreSQL `pg_dump` | Cron → NAS via rsync over SSH | Daily 03:00 UTC | 30 days rolling | NAS (GPG AES-256) |
| MinIO Bronze/Gold | `mc mirror` → NAS via rsync over SSH | Daily 04:00 UTC | 30 days rolling | NAS (GPG AES-256) |
| BLC-01 JSONL | rsync Server2 → proxmox → NAS | Daily 05:00 UTC | Indefinite | NAS (GPG AES-256) |
| Secrets directory | Manual after each change | On-change | All versions | NAS (GPG AES-256) |
| ClickHouse | Not backed up (reconstructable) | — | — | — |

**Immutability:** NAS backups are append-only at the filesystem level. Old backups are
never overwritten — new backups create new dated directories. Deletion requires SSH
to NAS + manual `rm`. No automated process has NAS delete permissions.

**Restore drill cadence:** Quarterly. First drill before Phase 1 go-live. Drill
procedure: restore PostgreSQL + MinIO to a test Docker stack on bluefin, verify
catalog row counts and Bronze file accessibility. Log results in
`docs/ops/restore-drills.md`.

#### Phase Gate

| Gate | Criterion |
|------|-----------|
| Phase 1 | NAS backup jobs configured and first successful backup verified for PostgreSQL + MinIO |
| Phase 5 | Restore drill completed successfully; RTO ≤ 4h verified |

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
| Silver → Gold export asset (`gold_observations`) — not yet written | Phase 1 |
| NAS backup job for MinIO — not yet configured | Phase 1, before live collection |
| Dagster metadata DB backup — not yet configured | Phase 1 |
| `bronze-archive` bucket + archive job (C2) | Phase 1 |
| `signal_snapshot_writer` + cache refresh endpoint (C3) | Phase 5 |

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
| `defi.lending.utilization_rate` full computation | **Resolved (E3).** DeFiLlama `/yields` direct pool utilization. Subgraph not required. | — |
| Options data (Deribit) | Null-propagate. `METRIC_UNAVAILABLE` propagates through derivatives features. | v1.1 milestone |
| Exchange flows beyond 18 instruments | Accept coverage limit for v1. | v1.1 milestone |
| BTC directional exchange flows | Null-propagate. CryptoQuant (parked, paid) is the resolution path. | v1.1 milestone |
| EDSx Pillar 3 (Valuation) | Planned, not yet built (REM-21) | REM-21 |
| EDSx Pillar 4 (Structural Risk) | Planned, not yet built (REM-24) | REM-24 |
| EDSx Pillar 5 (Tactical Macro) | Planned, not yet built (REM-22/23). Hard prereq: FRG-10. | REM-22/23 after FRG-10 |
| Layer 2 synthesis design | **Resolved.** §L2.1–L2.8 locked 2026-03-06. | — |
| Silver → Gold export cadence (6h) | **Resolved.** Hybrid event-triggered sensor + 1-hour fallback. Worst-case 44min. See §Silver→Gold Export. | — |
| Redistribution enforcement design | **Resolved (A2).** Three-state enum, Option C propagation, `metric_redistribution_tags`, null-with-flag response, 5 audit evidence queries. See §Redistribution Enforcement. | — |
| CoinMetrics redistribution | `blocked` (internal-only ToS). Propagates to derived outputs until `propagate_restriction` relaxed. | Phase 6 ToS audit |
| SoSoValue redistribution | `blocked` (non-commercial ToS). Propagates to Capital Flow Direction ML model. ETF flow fields conditionally null-flagged at Phase 5 launch (DG-R1). | Phase 6 ToS audit or paid tier |
| Coinalyze / BGeometrics / Etherscan / BLC-01 redistribution | `pending` (unaudited). Propagates to derived outputs at default settings. | Phase 4 shadow ToS audit (DG-R1 Option B) |
| BLC-01 ToS audit | Unaudited — internal only | Phase 4 shadow ToS audit (DG-R1 Option B) |
| Index/benchmark licensing | Deferred to v2 | Methodology documented + ToS audited |
| ML H2 regime engine (Volatility-Liquidity Anchor) | Rule-based baseline in production | H2 target, after UNI-01 unblocked |
| PBOC balance sheet via FRED | BOJ confirmed in FRED (boj_total_assets). PBOC: evaluate during FRG-10 build. | During FRG-10 / Phase 1 FRED expansion |
| Security posture baseline | **Resolved (D1).** File-based secrets, credential isolation, 4 incident playbooks, annual rotation March, Phase 0 corrective SEC-01–SEC-06. See §Secrets Management through §Phase 0 Security Corrective Actions. | — |
| Performance history endpoint | **Resolved (B3).** `GET /v1/signals/performance`. 2 dbt marts (`signal_outcomes`, `performance_metrics`), PIT JOIN with `ingested_at` guard, 10-metric specification, field-tier mapping, reliability diagram, cross-sectional aggregation. See §Performance History Endpoint. | — |
| Bronze cold archive | **Resolved (C2).** Two-bucket MinIO (`bronze-hot` 90d + `bronze-archive` indefinite), daily archive job, `forge.bronze_archive_log`, 8-step reprocessing path. See §Layer 1: Landing Zone. | — |
| Signal snapshot cache | **Resolved (C3).** In-process Python dict, Option C redistribution, Dagster POST trigger, MinIO warm start <5s, p95 <50ms cache hit. See §Signal Snapshot Cache. | — |
| Stripe / billing infrastructure | Deferred to post-launch. Manual invoicing at sub-20 customers. (F1) | 20+ active subscribers |
| Dashboard / UI | Not in v1 | v2 trigger conditions met (both) |

---

## SOURCES CATALOG (v1)

11 sources at v1.

| Source | Provides | ToS status | Redistribution |
|--------|----------|------------|----------------|
| Coinalyze | Perpetual futures — funding, OI, liquidations, L/S ratio (121 instruments) | Unaudited | Pending Phase 6 audit |
| CFTC COT | Institutional/dealer futures positioning — BTC + ETH (Socrata API) | None (public domain) | Yes |
| DeFiLlama | Protocol TVL, DEX volume, lending yields, stablecoins, fees, revenue | Low risk | Yes |
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
| Signal API pricing | $199/month · $1,990/year. Manual invoicing. (F1) |
| Ecosystem Monitor pricing | $2,500–$5,000/month API tier. Consulting (Stream 3) separate. (F1) |
| Intelligence Suite pricing | $2,500/month. Sales-only. Phase 6. (F1) |
| Risk Feed pricing | Custom. Annual contract, direct sales. (F1) |
| Profile A acquisition | Content pull. PIT post + methodology doc + GitHub schema. (F1) |
| Profile B acquisition | Direct outreach after ≥3 Profile A + 60d history. (F1) |
| Public preview | 48h-delayed top-10 composite on website. Shadow week 2. (F1) |
| Signal API tier opens | After Phase 5 gate. (F1) |
| Stripe | Deferred. Trigger: 20+ active subscribers. (F1) |
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
| Layer 2 synthesis | Designed and locked (§L2.1–L2.8). Horizon alignment, EDSx/ML aggregation, agreement scoring, VLA regime weights, null propagation, §L2.8 response schema. |

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
| Authentication | Manual key issuance. API key required on all tiers (including Free). |
| Tier model | 5-tier hybrid: Preview / Signal API / Intelligence Suite / Risk Feed / Ecosystem Monitor. Tiers set access + contract; modules set entitlement scope. |
| Entitlement enforcement | 12-step middleware chain. In-process LRU cache. Async audit log. |
| Redistribution gating | Three-state enum (allowed/pending/blocked). Direct-lineage-only propagation. |
| API versioning | Path-level (/v1/). Breaking changes require /v2/. |
| Webhook | At-least-once delivery, HMAC-signed |
| SLA count | Four: signal freshness (90min), API uptime (99.5%), staleness notification (60min), methodology change (14d) |
| Methodology doc | Public URL, versioned, 8 sections |
| First customer | Signal API tier after Phase 5 gate. Direct engagement. Written agreement. Manual invoicing. |
| Performance endpoint | `GET /v1/signals/performance`. Pre-materialised marts (B3). p95 ≤ 500ms. PIT-first. |
| Performance PIT rules | Anchor: `observed_at > computed_at`. Backfill guard: `ingested_at_signal <= outcome_observed_at`. Fixed, non-negotiable. |
| Neutral threshold | Fixed ±0.10. Not configurable per-request. Documented in methodology. |
| Sharpe convention | Population std (no Bessel). Locked before methodology doc written. |
| Return definition | Daily close (Tiingo). Calendar days. VWAP rejected. |
| Cross-sectional aggregation | Equal-weight. <30 resolved signals = excluded (no zero contribution). |
| Reliability diagram | 10 fixed bins, request-time DuckDB. Intelligence Suite tier only. |
| Performance window=all | Intelligence Suite tier only. Forward-looking restriction. |
| Pillar attribution partial | Partial response acceptable (2/5 pillars at Phase 5). `planned_pillars_note` for inactive. |
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
| Bronze architecture | Two-bucket: `bronze-hot` (90d lifecycle) + `bronze-archive` (indefinite). Separate credentials. (C2) |
| Archive idempotency | `forge.bronze_archive_log` in PostgreSQL. Admin metadata only — Rule 3 compliant. (C2) |
| Archive safety model | 88-day buffer (archive at day 2, expiry at day 90). 3-day consecutive failure alert. (C2) |
| Signal cache | In-process Python dict. No Redis. ~250KB. GIL-safe atomic swap. (C3) |
| Cache entitlement | Option C: redistribution filter at populate, tier filter per-request. Gated fields never in cache. (C3) |
| Cache canonical store | MinIO `gold/snapshots/latest.json`. Warm start <5s. (C3) |
| Cache staleness | Serve stale with `is_stale=True`. No HTTP error. TTL = 9h (6h × 1.5). (C3) |

### Build Plan

| Decision | Outcome |
|---|---|
| Build sequence | Schema → Data → Features → EDSx → ML → Serving |
| Phase gate model | Hard pass/fail. No phase begins until previous gate passes. |
| Migration order | Tiingo first (spot price dependency), then remaining |
| Parallel operation | None. Forge read-only 90 days. |
| ML shadow period | Minimum 30 days |
| ToS audit | Coinalyze/BGeometrics/Etherscan/BLC-01: Phase 4 shadow (DG-R1 Option B). SoSoValue/CoinMetrics: Phase 6. |

---

*Document synthesized: 2026-03-06*
*Authority: thread_1 through thread_7 + thread_infrastructure + FromTheBridge_design_v1_1.md*
*All locked decisions require architect approval to reopen.*
*Next action: Phase 0 — provision infrastructure, apply DDL, seed catalogs, verify gates.*
