# FTB Assessment: EDS Cross-Source Derived Metrics

**Date:** 2026-03-09
**Source:** EDS design doc `~/Projects/EmpireDataServices/docs/design/EDS_design_v1.1.md` (v1.1.3)
**Trigger:** EDS added two derivation layers (EDS-59, EDS-60) producing cross-source metrics. Assess which, if any, should be registered in the FTB catalog and synced via `empire_to_forge_sync`.
**Status:** Assessment complete. No Phase 1 changes. Candidates revisited at Phase 2 gate.

---

## Approach

Assess now, promote at Phase 2 gate. Rationale:

- These metrics have genuine pillar value. Discovering that mid-flight (Phase 3/4) means retrofitting the sync, re-running feature engineering, and potentially invalidating trained models.
- But EDS-59 and EDS-60 aren't built yet — they're pipeline items, not running code. Promoting phantom metrics into FTB's catalog creates dependencies on things that don't exist.
- Phase 2 (feature engineering) is the natural promotion point: the base sync is proven, EDS derivation layers are running, and you can evaluate candidates against actual data.

**Phase 1 impact: None.** Sync volume (~17,350 rows/day), adapter build order, and gate criteria are all unchanged by this assessment.

---

## Assessment Criteria

For each metric:

1. **Pillar relevance** — Does it feed a specific FTB pillar (Trend/Structure, Liquidity/Flow, Valuation, Structural Risk, Tactical Macro)?
2. **Signal density** — Is it a direct signal input, or better consumed as an EDS API product?
3. **FTB derivability** — Can FTB derive the same metric from data it already receives in its Marts layer?
4. **Sync cost** — Additional rows/day through `empire_to_forge_sync`

Key context: FTB gets derivatives data from Coinalyze (pre-aggregated across exchanges). EDS gets per-exchange granular data from 6 exchanges. Cross-exchange metrics requiring per-exchange granularity have no FTB equivalent — Coinalyze's aggregate data cannot be decomposed back into per-exchange signals.

---

## Cross-Exchange Aggregation (EDS-59, `source_id = 'eds_cross_exchange'`)

| Metric | Description |
|--------|-------------|
| `exchange.aggregate.funding_rate_mean` | Volume-weighted cross-exchange funding rate |
| `exchange.aggregate.funding_rate_dispersion` | Std dev of funding rates across venues |
| `exchange.aggregate.oi_migration` | Detects position migration vs deleveraging across exchanges |
| `exchange.aggregate.basis_spread_range` | Max-min perp basis across venues |
| `exchange.aggregate.volume_share` | Per-exchange share of total derivatives volume |
| `exchange.liquidation.concentration` | Herfindahl index of liquidations across exchanges |
| `exchange.liquidation.cascade_lag_ms` | Propagation delay from first-mover to other venues |

### Assessments

**`exchange.aggregate.funding_rate_mean`** — EDS-EXCLUSIVE

FTB already receives aggregate funding via Coinalyze (`derivatives.perpetual.funding_rate`). Redundant. No pillar value beyond what FTB already has.

**`exchange.aggregate.funding_rate_dispersion`** — CANDIDATE (Structural Risk)

Novel signal with no FTB equivalent. High dispersion = venue-specific stress or arbitrage pressure. Directly feeds Structural Risk pillar's "damage potential" mandate — when funding diverges across venues, it signals structural fragility that aggregate funding rate masks. Requires per-exchange granularity that Coinalyze cannot provide.

**`exchange.aggregate.oi_migration`** — CANDIDATE (Liquidity/Flow)

Distinguishes position migration from deleveraging — a critical distinction Coinalyze's aggregate OI can't make. OI dropping 10% means very different things if it's moving between venues (migration) vs leaving the market (deleveraging). Net OI change ~0 = migration; large negative = actual deleveraging. Directly feeds Liquidity/Flow's "money in motion" mandate.

**`exchange.aggregate.basis_spread_range`** — EDS-EXCLUSIVE

Derivatives microstructure signal. Interesting but narrow — better consumed as an EDS commercial product than a pillar input. Basis spread divergence is a trading signal, not a regime-level indicator.

**`exchange.aggregate.volume_share`** — EDS-EXCLUSIVE

Regime shift detection via volume migration. Descriptive, not predictive. Valuable for EDS's commercial API but not a direct pillar input.

**`exchange.liquidation.concentration`** — CANDIDATE (Structural Risk)

Herfindahl index of liquidation distribution across exchanges. High concentration = isolated venue event (less systemic). Low concentration = market-wide cascade (high systemic risk). Directly feeds Structural Risk's "liquidation cascade proximity" metric. FTB has aggregate liquidation data from Coinalyze but cannot distinguish single-venue from market-wide events.

**`exchange.liquidation.cascade_lag_ms`** — EDS-EXCLUSIVE

Propagation delay measurement. Too granular for daily+ pillar cadence — this is a sub-8h intraday signal while FTB pillars operate on daily horizons. Better as an EDS real-time commercial product.

---

## Cross-Track Derivation (EDS-60, `source_id = 'eds_cross_track'`)

| Metric | Tracks | Description |
|--------|--------|-------------|
| `cross.flow_liquidation_lag` | T1 x T2 | Exchange inflows preceding liquidation events |
| `cross.onchain_vs_exchange_volume_divergence` | T1 x T2 | On-chain moves not reflected in exchange volume |
| `cross.stablecoin_to_onchain_lag` | T1 x T3 | Stablecoin minting preceding transfer spikes |
| `cross.etf_flow_to_onchain_movement` | T1 x T3 | ETF flows vs actual BTC on-chain movement |
| `cross.macro_to_derivatives_lag` | T2 x T3 | Rate decisions to derivatives positioning shift |
| `cross.stablecoin_to_exchange_liquidity` | T2 x T3 | Stablecoin supply growth to exchange depth |

Key question: FTB has the raw inputs for most of these (stablecoin supply from DeFiLlama, transfer volume from CoinMetrics/EDS, funding rates from Coinalyze, ETF flows from SoSoValue, macro rates from FRED). Could FTB derive them in Marts?

### Assessments

**`cross.flow_liquidation_lag`** — CANDIDATE (Structural Risk)

Exchange inflows preceding liquidation events — a leading indicator of leveraged position stress. FTB has exchange flows (Etherscan, Priority 2) and liquidations (Coinalyze) but the lag computation with exchange-tagged addresses requires EDS's per-exchange Track 1 granularity. FTB cannot replicate this from its own sources.

**`cross.onchain_vs_exchange_volume_divergence`** — CANDIDATE (Liquidity/Flow)

On-chain volume diverging from exchange volume signals OTC activity or internal transfers. FTB has `flows.onchain.transfer_volume_usd` and exchange volume independently, so the divergence metric *could* live in Marts as feature engineering. However, EDS's on-chain volume comes from node-derived data (more accurate) vs FTB's CoinMetrics source (CSV-based, delayed). The EDS version is higher quality. Borderline candidate — FTB could derive a lower-quality version.

**`cross.stablecoin_to_onchain_lag`** — EDS-EXCLUSIVE

FTB has both stablecoin supply (DeFiLlama) and transfer volume (CoinMetrics/EDS). The lag computation is straightforward feature engineering FTB can do in Marts from data it already receives. No need to import a pre-derived version.

**`cross.etf_flow_to_onchain_movement`** — EDS-EXCLUSIVE

Same reasoning. FTB has ETF flows (SoSoValue) and on-chain movement. Marts-layer derivation. Note: SoSoValue redistribution is blocked (`redistribution = false`), which limits this signal's commercial use regardless of source.

**`cross.macro_to_derivatives_lag`** — EDS-EXCLUSIVE

FTB has FRED rates and Coinalyze funding/OI. The lag from rate decisions to derivatives positioning is textbook Tactical Macro feature engineering. Belongs in Marts, not imported pre-derived.

**`cross.stablecoin_to_exchange_liquidity`** — EDS-EXCLUSIVE

FTB has stablecoin supply (DeFiLlama) and exchange volume (Coinalyze). Marts derivation.

---

## Decision Summary

| Metric | Decision | Target Pillar | Key Rationale |
|--------|----------|---------------|---------------|
| `exchange.aggregate.funding_rate_mean` | EDS-EXCLUSIVE | — | Redundant with Coinalyze aggregate |
| **`exchange.aggregate.funding_rate_dispersion`** | **CANDIDATE** | **Structural Risk** | Novel. Per-exchange granularity required. Venue stress signal. |
| **`exchange.aggregate.oi_migration`** | **CANDIDATE** | **Liquidity/Flow** | Novel. Migration vs deleveraging distinction. |
| `exchange.aggregate.basis_spread_range` | EDS-EXCLUSIVE | — | Microstructure. Commercial product. |
| `exchange.aggregate.volume_share` | EDS-EXCLUSIVE | — | Descriptive, not predictive. |
| **`exchange.liquidation.concentration`** | **CANDIDATE** | **Structural Risk** | Novel. Systemic vs isolated cascade distinction. |
| `exchange.liquidation.cascade_lag_ms` | EDS-EXCLUSIVE | — | Too granular for daily pillar cadence. |
| **`cross.flow_liquidation_lag`** | **CANDIDATE** | **Structural Risk** | Novel. Leading stress indicator. Needs EDS per-exchange data. |
| **`cross.onchain_vs_exchange_volume_divergence`** | **CANDIDATE** | **Liquidity/Flow** | FTB could derive lower-quality version. EDS version preferred. |
| `cross.stablecoin_to_onchain_lag` | EDS-EXCLUSIVE | — | FTB can derive in Marts. |
| `cross.etf_flow_to_onchain_movement` | EDS-EXCLUSIVE | — | FTB can derive in Marts. |
| `cross.macro_to_derivatives_lag` | EDS-EXCLUSIVE | — | FTB can derive in Marts. |
| `cross.stablecoin_to_exchange_liquidity` | EDS-EXCLUSIVE | — | FTB can derive in Marts. |

**5 CANDIDATES, 8 EDS-EXCLUSIVE.**

The candidates share a common trait: they require per-exchange granularity or node-derived precision that FTB's aggregated sources (Coinalyze, CoinMetrics) cannot provide. The EDS-EXCLUSIVE metrics are all derivable from data FTB already collects — they belong in FTB's Marts layer as feature engineering, not imported pre-derived.

---

## Candidate Details for Phase 2 Review

When EDS-59 and EDS-60 are running and producing data, evaluate these 5 candidates:

### Structural Risk candidates (3)

| Metric | What it adds | Sync volume impact |
|--------|-------------|-------------------|
| `funding_rate_dispersion` | Venue stress invisible in aggregate funding | ~3 rows/day (per signal_eligible instrument, 8h cadence) |
| `liquidation.concentration` | Systemic vs isolated cascade classification | ~3 rows/day |
| `flow_liquidation_lag` | Leading indicator of leveraged stress | ~1 row/day (daily, market-level) |

Structural Risk is currently the least-sourced pillar — only `macro.volatility.vix` and aggregate liquidations. These three candidates would materially strengthen it.

### Liquidity/Flow candidates (2)

| Metric | What it adds | Sync volume impact |
|--------|-------------|-------------------|
| `oi_migration` | Migration vs deleveraging distinction | ~3 rows/day |
| `onchain_vs_exchange_divergence` | OTC activity detection (higher quality than Marts) | ~1 row/day |

Liquidity/Flow is better sourced (funding, liquidations, exchange flows, stablecoins, ETF). These add nuance, not coverage.

### Total sync volume if all 5 promoted

~11 rows/day additional. Negligible vs current ~17,350 rows/day sync volume.

---

## Promotion Prerequisites (Phase 2 Gate)

Before any candidate is promoted to `forge.metric_catalog`:

1. EDS-59 (cross-exchange aggregation) and EDS-60 (cross-track derivation) must be built and running
2. Base `empire_to_forge_sync` must have operated successfully for ≥7 days on the 5 existing metrics
3. Candidate metric must have ≥7 days of observations in `empire.observations`
4. Dead-letter rate < 1% for the candidate metric
5. Architect approval per existing promotion workflow (FTB design §Metric Promotion)

No catalog rows, no pipeline items, and no sync scope changes until these prerequisites are met.

---

## Pipeline Items

**LH-70 — Review EDS cross-source metric candidates for promotion**
- **Trigger:** Phase 2 gate checklist (feature engineering complete)
- **Prerequisite:** EDS-59 and EDS-60 producing data, base `empire_to_forge_sync` operating ≥7 days
- **Action:** Evaluate 5 candidates (`funding_rate_dispersion`, `oi_migration`, `liquidation.concentration`, `flow_liquidation_lag`, `onchain_vs_exchange_volume_divergence`) against actual data. For each: SYNC (add to `forge.metric_catalog` + sync scope) or REJECT (remain EDS-exclusive).
- **Reference:** This document (`docs/plans/eds-cross-source-metrics-assessment.md`)

**EDS-61 — Notify FTB when cross-source derivation layers are live**
- **Trigger:** EDS-59 (cross-exchange aggregation) and EDS-60 (cross-track derivation) both producing observations in `empire.observations` for ≥7 days
- **Action:** Notify FTB architect that LH-70 prerequisites are met. Does not block EDS operations.
- **Owner:** EDS

---

## Phase 1 Impact

**None.** This assessment changes nothing about Phase 1:

- Sync volume assumption: ~17,350 rows/day (unchanged)
- Adapter build order: unchanged
- Phase 1 gate criteria: unchanged
- `forge.metric_catalog` seed: unchanged (74 Phase 0 + 8 Phase 1 additions)

---

## Context Files

- EDS design: `~/Projects/EmpireDataServices/docs/design/EDS_design_v1.1.md` — "Cross-Exchange Derived Metrics" and "Cross-Track Derived Metrics" sections
- FTB design: `~/Projects/FromTheBridge/docs/design/FromTheBridge_design_v3.1.md` — 5-pillar definitions (lines 574–613), metric catalog (lines 1865–1946), `empire_to_forge_sync` specification (lines 154–180, 1683–1690)
- EDS cohesion audit: `~/Projects/FromTheBridge/docs/design/eds_ftb_cohesion_audit.md` — prior cross-project analysis, resolution status table
