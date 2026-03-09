# CC PROMPT — T3b: FORGE DATA QUALITY AUDIT
# FromTheBridge — Historical Data Quality, Continuity & Sourcing Assessment
# Prerequisite: Complete before Phase 1 adapter build begins
# Scope: Every populated table in legacy Forge DB (empire_forge, port 5435)
# Constraint: Read-only. No data writes. No schema changes.

## CONTEXT

T3 assessed API depth and rate limits for 11 v1 sources. It did not assess the quality of data already collected. A reactive audit of `forge.derivatives` revealed:

- 11 months of systematically degraded Coinalyze data (OI at 3.4% pre-Feb 2022)
- Cross-source OI divergence (Coinalyze aggregate < Binance single-exchange)
- Extreme funding rate outliers mixing real events with data errors
- No provenance tracking (collected_at = load time, not observation time)

These findings came from ONE table. The Forge DB has 328 tables including monthly partitions. This audit covers everything else.

## PHILOSOPHY

Historical data is irreplaceable. We cannot wait years for organically collected clean data — we must source it in its purest form and validate rigorously before migration. The cost of discovering dirty data after it's in ClickHouse Silver and feeding ML models is catastrophic: wrong signals, wasted compute, lost trust. Every hour spent on this audit saves weeks of debugging downstream.

---

## AUDIT METHODOLOGY

For each table group below, run the following checks:

### Standard Quality Checks (apply to every table)

```sql
-- 1. Row count and date range
SELECT COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date
FROM {table};

-- 2. Null rate per column (adapt column names per table)
SELECT
  COUNT(*) as total,
  COUNT(col1) as has_col1,
  ROUND(100.0 * COUNT(col1) / COUNT(*), 1) as pct_col1,
  -- repeat for each value column
FROM {table};

-- 3. Continuity check (gap days for key instruments)
WITH daily AS (
  SELECT instrument_id, observation_date::date as day
  FROM {table}
  GROUP BY instrument_id, observation_date::date
),
gaps AS (
  SELECT instrument_id, day,
    day - LAG(day) OVER (PARTITION BY instrument_id ORDER BY day) as gap
  FROM daily
)
SELECT instrument_id,
  COUNT(*) as total_days,
  COUNT(*) FILTER (WHERE gap > 1) as gap_count,
  MAX(gap) as max_gap_days,
  MIN(day) as first_day, MAX(day) as last_day
FROM gaps
GROUP BY instrument_id
ORDER BY gap_count DESC
LIMIT 20;

-- 4. Outlier detection (per numeric column)
SELECT
  MIN(value), MAX(value),
  PERCENTILE_CONT(0.01) WITHIN GROUP (ORDER BY value) as p1,
  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY value) as p99,
  AVG(value), STDDEV(value)
FROM {table}
WHERE value IS NOT NULL;

-- 5. Duplicate check
SELECT COUNT(*) FROM (
  SELECT {primary_key_columns}, COUNT(*)
  FROM {table}
  GROUP BY {primary_key_columns}
  HAVING COUNT(*) > 1
) d;

-- 6. Source/agent provenance
SELECT source_agent, source_id, COUNT(*),
  MIN(collected_at)::date, MAX(collected_at)::date
FROM {table}
GROUP BY source_agent, source_id;
```

---

## TABLE GROUP 1: MACRO INDICATORS (50 MB — largest single table)

**Source:** FRED API (23 macro series)
**Expected:** Decades of daily/monthly data. Government-sourced, high quality.

```sql
-- Schema discovery
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'macro_indicators'
ORDER BY ordinal_position;

-- Per-series coverage and quality
SELECT series_id,
  COUNT(*) as rows,
  MIN(observation_date)::date as earliest,
  MAX(observation_date)::date as latest,
  COUNT(value) as has_value,
  ROUND(100.0 * COUNT(value) / COUNT(*), 1) as pct_complete,
  COUNT(*) FILTER (WHERE value = 0) as zero_count
FROM forge.macro_indicators
GROUP BY series_id
ORDER BY series_id;

-- Verify all 23 expected series are present
-- Cross-reference against CLAUDE.md FRED series list:
-- DFF, T10Y2Y, DTWEXBGS, CPIAUCSL, M2SL, BAMLH0A0HYM2, VIXCLS, DCOILWTICO
-- + 15 others from the metric catalog

-- Check for FRED revision artifacts (same date, different values at different collected_at)
SELECT series_id, observation_date, COUNT(DISTINCT value) as distinct_values
FROM forge.macro_indicators
GROUP BY series_id, observation_date
HAVING COUNT(DISTINCT value) > 1
LIMIT 20;

-- VIX: check for methodology break around Jan 2003
-- M2SL: check for definition break around May 2020
```

**Key questions:**
- Are all 23 series present and complete?
- Any series with gaps > 7 days (excluding weekends/holidays for daily series)?
- Any zero or negative values where they shouldn't exist (e.g., CPI, M2)?
- Is BAMLH0A0HYM2 (HY OAS) present? (This was a specific Phase 0 gate criterion)

---

## TABLE GROUP 2: CHAIN ACTIVITY (~200 monthly partitions, 2009–2026)

**Source:** CoinMetrics community CSV (BTC + ETH)
**Expected:** Daily from BTC genesis (2009-01-03), ETH genesis (2015-07-30)

```sql
-- Discover partition structure
SELECT table_name, pg_size_pretty(pg_total_relation_size('forge.' || table_name))
FROM information_schema.tables
WHERE table_schema = 'forge' AND table_name LIKE 'chain_activity%'
ORDER BY table_name
LIMIT 10;

-- Schema
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name LIKE 'chain_activity_2026%'
LIMIT 30;

-- Sample recent partition
SELECT * FROM forge.chain_activity_2026_02 LIMIT 5;

-- Per-instrument coverage across all partitions (use the parent table if it's a view/inheritance)
-- If partitioned: query a representative sample of partitions

-- Key metric: transfer_volume_usd — this is what we use
-- Check for nulls, zeros, and outliers in this specific column
```

**Key questions:**
- How many instruments? (Should be BTC + ETH only for community edition)
- Any gaps in daily continuity for BTC (2009+) or ETH (2015+)?
- Are the early BTC rows (2009-2010) actually populated or mostly nulls?
- What columns are populated vs sparse?
- Does the data match a fresh download of the CoinMetrics CSV? (Spot-check a few dates)

---

## TABLE GROUP 3: DEX VOLUME (21 MB)

**Source:** DeFiLlama DEX overview API
**Expected:** Daily aggregate and per-protocol DEX volume

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'dex_volume'
ORDER BY ordinal_position;

-- Coverage summary
SELECT COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date,
  COUNT(DISTINCT protocol) as protocols
FROM forge.dex_volume;

-- Per-protocol coverage
SELECT protocol, COUNT(*) as rows,
  MIN(observation_date)::date as earliest,
  MAX(observation_date)::date as latest
FROM forge.dex_volume
GROUP BY protocol
ORDER BY rows DESC
LIMIT 20;

-- Volume outlier check (DEX volumes can spike legitimately but check for data errors)
SELECT protocol, observation_date, volume_usd
FROM forge.dex_volume
WHERE volume_usd > 1e12  -- > $1 trillion single-day would be suspicious
ORDER BY volume_usd DESC
LIMIT 10;
```

**Key questions:**
- Which protocols are tracked? Do they match the DeFiLlama universe we need?
- Any gaps in the aggregate daily volume?
- Volume spikes that don't correlate with known market events?

---

## TABLE GROUP 4: DEFI LENDING FEES (2.5 MB)

**Source:** DeFiLlama yields/lending API
**Expected:** Lending rates, borrow rates, TVL per protocol/pool

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'defi_lending_fees'
ORDER BY ordinal_position;

SELECT COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date
FROM forge.defi_lending_fees;

-- Per-protocol coverage
SELECT protocol, COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date
FROM forge.defi_lending_fees
GROUP BY protocol
ORDER BY COUNT(*) DESC
LIMIT 15;
```

---

## TABLE GROUP 5: EXCHANGE FLOWS (monthly partitions)

**Source:** Etherscan API (ETH + Arbitrum exchange wallet tracking)
**Expected:** Net flow per exchange address, 9 exchanges × 2 chains

```sql
-- Schema and partitions
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'forge' AND table_name LIKE 'exchange_flows%'
ORDER BY table_name;

SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name LIKE 'exchange_flows_2026%'
LIMIT 20;

-- Coverage: which exchanges, how far back, any gaps?
-- This is critical: exchange flow data depends on correct wallet addresses.
-- A wrong address = completely wrong flow signal.
```

**Key questions:**
- Which exchange addresses are tracked?
- Are flows in ETH, USD, or both?
- Any periods where flow = 0 for all exchanges simultaneously (collector outage)?
- Are Arbitrum flows present or only Ethereum mainnet?

---

## TABLE GROUP 6: ETF FLOWS (496 KB)

**Source:** SoSoValue
**Expected:** Daily net inflow/outflow per ETF, from launch dates

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'etf_flows'
ORDER BY ordinal_position;

SELECT COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date
FROM forge.etf_flows;

-- Per-ETF coverage
SELECT etf_name, COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date
FROM forge.etf_flows
GROUP BY etf_name
ORDER BY etf_name;

-- Verify floors: BTC ETF >= 2024-01-11, ETH ETF >= 2024-07-23, SOL ETF >= 2025-10-28
```

**Key questions:**
- Are individual ETF funds tracked or just aggregates?
- Any weekend/holiday data that shouldn't exist (ETFs trade on market days only)?
- Does the data include AUM/NAV or just flows?

---

## TABLE GROUP 7: STABLECOIN METRICS (336 KB)

**Source:** DeFiLlama stablecoins API
**Expected:** Market cap, peg ratio per stablecoin

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'stablecoin_metrics'
ORDER BY ordinal_position;

SELECT COUNT(*), MIN(observation_date)::date, MAX(observation_date)::date,
  COUNT(DISTINCT stablecoin) as coins
FROM forge.stablecoin_metrics;
```

---

## TABLE GROUP 8: SUPPLY SNAPSHOTS (1.5 MB)

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'supply_snapshots'
ORDER BY ordinal_position;

SELECT COUNT(*), MIN(snapshot_date)::date, MAX(snapshot_date)::date
FROM forge.supply_snapshots;
```

---

## TABLE GROUP 9: DEAD LETTERS (280 KB, 451 rows)

**These are the quality problems the system already caught.** Analyze them.

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name = 'dead_letters'
ORDER BY ordinal_position;

-- What failed and why?
SELECT agent_id, table_name, COUNT(*) as failures,
  MIN(created_at)::date as first_failure,
  MAX(created_at)::date as last_failure,
  COUNT(*) FILTER (WHERE resolved = true) as resolved
FROM forge.dead_letters
GROUP BY agent_id, table_name
ORDER BY failures DESC;

-- Sample the error messages
SELECT agent_id, instrument_id, error, created_at
FROM forge.dead_letters
ORDER BY created_at DESC
LIMIT 20;
```

**Key question:** Are the dead letters concentrated around specific instruments, dates, or agents? Patterns here reveal systematic collection problems.

---

## TABLE GROUP 10: COMPUTED METRICS (monthly partitions, ~48 MB)

**Source:** forge_compute (derived features from raw data)
**Note:** These are computed, not raw. Quality issues here are inherited from upstream data.

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'forge' AND table_name LIKE 'computed_metrics_2026%'
LIMIT 20;

-- What metrics are computed?
SELECT metric_name, COUNT(*), MIN(computed_at)::date, MAX(computed_at)::date
FROM forge.computed_metrics_2026_02
GROUP BY metric_name
ORDER BY metric_name;

-- Null rate in computed values — high null rate means upstream data problems
SELECT metric_name,
  COUNT(*) as total,
  COUNT(value) as has_value,
  ROUND(100.0 * COUNT(value) / COUNT(*), 1) as pct_complete
FROM forge.computed_metrics_2026_02
GROUP BY metric_name
ORDER BY pct_complete ASC
LIMIT 20;
```

---

## OUTPUT FORMAT

Save to: `.claude/reports/T3b_data_quality_full.json`

```json
{
  "audit_date": "ISO timestamp",
  "scope": "All populated tables in legacy Forge DB (empire_forge, port 5435)",
  "table_groups": {
    "macro_indicators": {
      "rows": number,
      "date_range": {"earliest": "YYYY-MM-DD", "latest": "YYYY-MM-DD"},
      "series_count": number,
      "series_coverage": {"series_id": {"rows": N, "pct_complete": N, "earliest": "date", "latest": "date"}},
      "missing_series": ["list of expected but absent series"],
      "quality_issues": ["description of each issue found"],
      "recommendation": "string"
    },
    "chain_activity": { ... },
    "dex_volume": { ... },
    "defi_lending_fees": { ... },
    "exchange_flows": { ... },
    "etf_flows": { ... },
    "stablecoin_metrics": { ... },
    "supply_snapshots": { ... },
    "dead_letters": { ... },
    "computed_metrics": { ... }
  },
  "cross_cutting_findings": [
    {
      "id": "string",
      "severity": "CRITICAL|MATERIAL|INFO",
      "title": "string",
      "detail": "string",
      "affected_tables": ["list"],
      "ml_impact": "string",
      "remediation": ["list of actions"]
    }
  ],
  "migration_readiness_assessment": {
    "ready_to_migrate": ["tables that are clean enough"],
    "migrate_with_caveats": ["tables with known issues, document caveats"],
    "do_not_migrate_without_remediation": ["tables with critical issues"],
    "re_source_recommended": ["tables where fresh backfill from API is cleaner than migrating legacy data"]
  },
  "revised_phase_1_prerequisites": [
    "List of actions that must complete before Phase 1 adapters write to Silver"
  ]
}
```

## TABLE GROUP 11: DESIGN ASSUMPTION VALIDATION

The FromTheBridge design (v3.1) was built around 74 metrics across 9 domains from 11 v1 sources. This audit must assess whether the actual data in Forge matches, exceeds, or falls short of those design assumptions.

**Questions to answer:**

1. **Scope gap:** How many distinct data series exist in Forge that are NOT represented in the 74-metric catalog? Are there useful signals being ignored because they weren't enumerated during design?

2. **Quality gap:** The design assumes clean data inputs to the feature layer (thread_3). Where does the actual data fall short? Map each metric catalog entry to its Forge source table and assess whether the data exists, is complete, and is clean enough for the stated use case.

3. **450-day assumption:** Thread_3 states "Coinalyze, Explorer, and DeFiLlama collection started ~February 2026. ML requires 450 days minimum." But Forge contains 4+ years of backfilled derivatives data, 17 years of chain activity, and decades of macro data. The 450-day wait was overstated. Document which features can begin ML training NOW vs which genuinely require forward collection.

4. **Migration complexity:** Thread_5 describes Forge-to-Silver migration as an ETL operation. Based on the quality findings in this audit, reclassify each table as: (a) clean copy, (b) copy with quality tagging, (c) remediation required before copy, or (d) re-source from API is cleaner than migrating legacy data.

5. **Design revision candidates:** Based on findings, list any design decisions in v3.1 that should be revisited. Examples: metric_ids that assumed clean aggregate OI, feature definitions that assumed complete funding rate history, training window estimates that didn't account for existing backfill.

Save this analysis as a separate section in the output JSON under `"design_assumption_validation"`.

---

## CRITICAL CONSTRAINT

This audit must be COMPLETED and REVIEWED before any Phase 1 adapter writes to ClickHouse Silver. Migrating unaudited data from Forge to Silver would permanently embed quality problems in the new architecture. The whole point of the FromTheBridge rebuild is to start clean.

**End of T3b prompt. Read-only audit. No data writes. No schema changes.**
