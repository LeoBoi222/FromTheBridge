# Instrument Admission Framework

**Date:** 2026-03-09
**Status:** Approved
**Context:** The v3.1 design doc assumed a static 121-instrument universe seeded from legacy Coinalyze data. Investigation revealed the legacy list was arbitrarily curated (147 assets, bulk-stamped rationale, duplicate entries, category padding) and would have introduced noise into the signal pipeline. This framework replaces the static list with data-driven admission criteria.

**Historical lesson:** Previous model builds trained against sparse, incomplete datasets without questioning instrument selection. The foundation was failure in the making. FTB exists to prevent this pattern — admission criteria are the quality firewall.

---

## Core Principle

**Admission criteria are canonical. Instrument lists are ephemeral.**

The criteria govern which instruments enter the signal pipeline. No static list is sacred. Instruments enter and exit based on evidence, not curation.

---

## Three-Tier Instrument Status

Maps to existing `instruments.collection_tier` CHECK constraint. No DDL change required.

| Tier | DB Value | Pipeline Layers | SLA | Capacity |
|------|----------|----------------|-----|----------|
| Collection-only | `collection` | 0–4 (Sources → Silver) | Freshness only | Uncapped |
| Scoring | `scoring` | 0–5 (Sources → Gold) | Freshness + completeness | Uncapped |
| Signal-eligible | `signal_eligible` | 0–8 (Full pipeline) | Full signal SLA | ≤200 |
| System | `system` | N/A | N/A | `__market__` only |

### Collection-only

- Raw data written to Bronze + Silver (same adapter pipeline, no special handling)
- Freshness monitoring (Dagster asset staleness)
- Tracked in `instrument_metric_coverage` (completeness recorded)
- No feature engineering, no signal scores, no customer-facing API exposure, no SLA

### Scoring

- Features computed in Gold/Marts for validation purposes
- Not exposed to customers
- Transitional tier between collection and signal_eligible

### Signal-eligible

- Full pipeline: features, EDSx scoring, ML models, API serving
- Customer-facing signal scores with SLA commitments
- Each instrument carries compute cost (features, models, cache, API responses)

---

## Admission Criteria for Signal-Eligible Promotion

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| Derivatives data depth | ≥180 days in Silver | ML training window minimum |
| Derivatives completeness | ≥90% non-null across funding rate, OI, L/S ratio | Sparse features degrade models |
| Spot price coverage | Tiingo OHLCV available | Unit normalization dependency |
| Market cap | Top 95% by circulating market cap | Pareto — cover the weight |
| Metric density | ≥10 active metrics from ≥3 sources | Signal surface minimum |
| No staleness | No metric stale >7 days at promotion time | Proves collection is working |

All criteria must pass simultaneously. Partial passes don't count.

---

## Demotion Criteria

| Criterion | Threshold | Action |
|-----------|-----------|--------|
| Staleness | Any metric stale >30 days | Demote to `scoring` |
| Completeness decay | Drops below 80% for 30 consecutive days | Demote to `scoring` |
| Source loss | Primary derivatives source stops providing data | Demote to `collection` |

Demotion is automated. Demoted instruments retain historical data — they can be re-promoted when criteria are met again.

---

## Phase 0 Instrument Seed (Minimal)

Structural only. No instrument starts as signal_eligible.

| Table | Rows | Contents |
|-------|------|----------|
| `forge.assets` | 3 | BTC, ETH, SOL |
| `forge.venues` | 1 | `aggregate` (cross-exchange / market-level) |
| `forge.instruments` | 4 | BTC-USD, ETH-USD, SOL-USD (`collection` tier) + `__market__` (`system` tier) |

Replaces v3.1 Phase 0 step 7: ~~"Seed initial instrument universe (BTC, ETH, SOL + full Coinalyze list)"~~ → "Seed structural instruments (BTC, ETH, SOL spot + `__market__` system instrument)"

---

## Phase 1 Instrument Discovery & Promotion

Phase 1 step 10 ("First instrument tier promotion run") populates the universe:

1. **Coinalyze adapter discovers available instruments** — queries API, returns actual list with derivatives data. This is the candidate pool.
2. **Adapter writes to Silver for all discovered instruments** — collection tier, no filtering.
3. **After ≥30 days of collection**, run promotion evaluation against admission criteria.
4. **Instruments passing all criteria promote to `scoring`**, then to `signal_eligible` after feature compute validation.

### Phase 1 Tier 1 Candidates (Evidence-Based)

Based on legacy Forge DB derivatives coverage audit (2026-03-09):

**Full depth (5 years, ~3,384 rows, funding/OI/L-S all >90%):**
BTC, ETH, SOL, BNB, AVAX, UNI, LINK, AAVE

**Probationary (partial depth, 57–75% completeness, require Phase 1 validation):**
ARB, OP, TON, SUI

**Dropped (no evidence to support inclusion):**
- DOGE — zero rows in legacy derivatives table (never collected)
- POL/MATIC — zero rows in legacy derivatives table
- MKR — no funding rate data, collection stopped Jan 2026

**Not instruments (reclassified as metric inputs):**
- USDC, USDT — stablecoin supply/peg feeds Liquidity/Flow pillar at `__market__` level
- WBTC — DeFi protocol metric (TVL, peg ratio), not independent derivatives instrument

### Phase 1 Gate Criterion Recalibration

Original: "≥ 20 instruments at signal_eligible tier"
Revised: "≥ 12 instruments at signal_eligible tier, all passing admission criteria"

Rationale: 8 evidence-backed + 4 probationary = 12–14 realistic ceiling. Setting gate at 20 assumed the 121 list would be seeded with most qualifying automatically.

---

## Sector-Level Screening (Collection-Only Data)

Collection-only instruments enable sector-level intelligence without per-instrument signal scores:

- Aggregate OI by sector (e.g., "DeFi derivatives OI surging")
- Sector-wide funding rate extremes
- TVL shifts across DeFi categories
- These are `__market__`-level or sector-level metrics, not instrument signals

This addresses the product question: "What about outliers and up-and-comers?" The system watches broadly (collection-only, uncapped) while scoring narrowly (signal-eligible, ≤200).

---

## Auto-Promote / Auto-Demote Mechanism

- Dagster scheduled asset (weekly) evaluates all `collection` and `scoring` tier instruments against admission criteria
- Instruments crossing threshold flagged in `collection_events` with `event_type = 'promotion_candidate'`
- Promotion executes automatically — no human gate
- Demotion also automatic per criteria
- All promotions/demotions logged in `collection_events` for audit trail

---

## Customer-Requested Coverage

Intelligence Suite / Risk Feed customers can request specific instruments:
- If collection-only and near threshold → noted, does not override criteria
- If not collected at all → enters collection tier first
- No shortcuts — criteria protect signal quality
- Transparent timeline communicated based on data maturation

---

## Growth Trajectory

| Phase | Expected Signal-Eligible | Collection-Only | Mechanism |
|-------|--------------------------|-----------------|-----------|
| Phase 0 | 0 | 3 (+ `__market__`) | Structural seed |
| Phase 1 (end) | 12–15 | ~100+ | Coinalyze discovery + Forge migration + admission criteria |
| Phase 2 | Same | Same | Feature pipeline validation, no new promotions |
| Phase 3–4 | Possible additions | Growing | Auto-promote running as collection data matures |
| Phase 5+ | Up to 200 | Uncapped | Customer demand + organic growth |

**The 200 cap** is a resource constraint, not a design principle. Can be raised after explicit capacity review. Enforces discipline: every signal_eligible instrument carries compute cost.

---

## v3.1 Updates Required

Every reference to "121 instruments" replaced with contextual language:

| Original | Replacement |
|----------|-------------|
| "Coinalyze alone covers 121 instruments" | "Coinalyze derivatives instrument universe (determined by adapter discovery and admission criteria)" |
| "Per-instrument (121 Coinalyze instruments)" | "Per-instrument (signal-eligible universe) + market-level aggregate" |
| "~7,380 rows total" | "~60 rows per signal-eligible instrument (scales with universe size)" |
| "~250 KB heap for 121 instruments" | "~2 KB per signal-eligible instrument (scales linearly)" |
| "~121 instruments × ~2 KB each" | "signal-eligible universe × ~2 KB each" |

---

## Design-Execution Alignment Audit (Prerequisite)

This framework exposed a class of problem: design docs carry frozen assumptions that contradict each other and the deployed state. Before declaring any design final:

1. **Numeric consistency scan** — trace every hardcoded number to its source
2. **Cross-document reference integrity** — verify cross-references still agree
3. **Design-vs-deployed drift** — DDL spec vs actual schema
4. **Gate criteria feasibility** — can each criterion be measured with what exists?
5. **Assumption archaeology** — grep for "~", "approximately", specific numbers; verify each

Tracked as pipeline item. Must complete before Phase 1 execution begins.
