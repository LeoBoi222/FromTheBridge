# thread_4_data_universe.md
## FromTheBridge — Data Universe
## Empire Architecture v2.0

**Date:** 2026-03-05
**Status:** Authoritative. Supersedes all prior thread_4 versions.
**Owner:** Stephen (architect, sole operator)
**Depends on:** thread_infrastructure.md (locked). Do not re-open technology
decisions settled there.

> This document is the canonical reference for the PostgreSQL catalog layer
> and the ClickHouse observation store. Every other thread references these
> definitions. Changes to locked decisions require architect approval.

---

## OVERVIEW

Layer 7 (Catalog) and Layer 4 (Silver) are the two layers specified here.

**Layer 7 — Catalog (PostgreSQL):** Ten tables holding relational definitions
of every metric, source, instrument, asset, venue, and lineage relationship
in the system. No time series data here — ever. Full referential integrity
enforced by PostgreSQL constraints. Dagster reads this layer at startup to
build the asset graph.

**Layer 4 — Silver (ClickHouse):** Three objects holding every metric
observation collected from every source, bitemporally. Write-only except
for the 6h Silver → Gold export Dagster asset. ReplacingMergeTree for
idempotent revision handling.

---

## SCHEMA MODEL

**EAV observations table with metric catalog, ClickHouse columnar storage,
and a materialized current-value view.**

Wide tables: rejected. Adding a metric adds a column — violates schema
immutability. Every metric expansion requires a DDL migration.

Hybrid typed tables: rejected. Separate tables per domain leaks source
structure above the adapter layer. Consumer ignorance is broken.

EAV selected: schema immutability, asset-class extensibility, consumer
ignorance, full audit completeness — all satisfied simultaneously.

Performance addressed through: ClickHouse columnar storage and
ReplacingMergeTree engine, composite ordering key with natural prefix
locality, monthly partitioning, and a materialized current-value view
for the hot read path.

---

## DESIGN DECISIONS LOCKED IN THIS SESSION

| Decision | Outcome |
|---|---|
| `metric_id` format in ClickHouse | Canonical name string — `'derivatives.perpetual.funding_rate'`. Not UUID. |
| `instrument_id` format in ClickHouse | Canonical symbol string — `'BTC'`. Not UUID. Sentinel `'__market__'` for market-level metrics. |
| `instrument_id` nullability in ordering key | Non-nullable `String`. Sentinel `'__market__'` replaces NULL. Avoids ReplacingMergeTree deduplication edge cases on nullable ordering key columns. |
| `current_values` implementation | `AggregatingMergeTree` + `argMaxState`. Updates incrementally on insert. Not a refresh-on-demand view. |
| PIT backtest queries | Bypass `current_values`. Query `observations` directly with explicit `ingested_at <= T` filter and `QUALIFY row_number()` deduplication. |
| `dead_letter` retention | TTL 90 days in DDL. Aligns with Bronze retention. Automatic via background merges. |
| `defi.lending.utilization_rate` | Canonical name is `utilization_rate` in v1. V1 uses borrow/supply TVL ratio as proxy. Methodology field documents the proxy computation. No rename at v1.1. |
| `flows.exchange.net_flow_usd` | Category C derived feature in Marts (forge_compute). Not stored in Silver. Only `inflow_usd` and `outflow_usd` have observation store catalog entries. |

---

## LAYER 7: POSTGRESQL CATALOG

### Dependency Graph

Foreign key relationships determine deployment order. Deploy in this sequence:

```
assets
  └── asset_aliases (fk: asset_id → assets)
venues
instruments (fk: asset_id → assets, venue_id → venues)
source_catalog
metric_catalog (no FK dependencies — standalone)
metric_lineage (fk: metric_id → metric_catalog, source_id → source_catalog)
event_calendar (fk: instrument_id → instruments — nullable)
supply_events (fk: instrument_id → instruments)
adjustment_factors (fk: instrument_id → instruments, metric_id → metric_catalog)
```

`collection_events` and `instrument_metric_coverage` deploy after the above.
Both reference `source_catalog` and `metric_catalog`.

### Table: assets

Canonical asset registry. An asset is a tradeable entity (BTC, ETH) distinct
from any instrument (BTC-USDT perpetual on Binance). One asset may have many
instruments.

```sql
CREATE TABLE forge.assets (
    asset_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_symbol    TEXT        NOT NULL UNIQUE,
    name                TEXT        NOT NULL,
    asset_class         TEXT        NOT NULL,
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    metadata            JSONB       NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT assets_asset_class_valid
        CHECK (asset_class IN (
            'crypto', 'equity', 'commodity', 'forex',
            'index', 'etf', 'defi_protocol', 'stablecoin'
        ))
);

CREATE INDEX idx_assets_symbol      ON forge.assets (canonical_symbol);
CREATE INDEX idx_assets_asset_class ON forge.assets (asset_class);
```

**Key query — resolve canonical symbol:**
```sql
SELECT asset_id, canonical_symbol, asset_class
FROM forge.assets
WHERE canonical_symbol = 'BTC'
  AND is_active = true;
```

### Table: asset_aliases

Alternative identifiers for the same asset across sources. Coinalyze may
call it `BTCUSDT`, CoinPaprika `btc-bitcoin`. Both map here.

```sql
CREATE TABLE forge.asset_aliases (
    alias_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        UUID        NOT NULL REFERENCES forge.assets (asset_id),
    source_name     TEXT        NOT NULL,
    alias_symbol    TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT asset_aliases_unique UNIQUE (source_name, alias_symbol)
);

CREATE INDEX idx_asset_aliases_lookup
    ON forge.asset_aliases (source_name, alias_symbol);
CREATE INDEX idx_asset_aliases_asset
    ON forge.asset_aliases (asset_id);
```

**Key query — resolve source symbol to canonical asset:**
```sql
SELECT a.asset_id, a.canonical_symbol
FROM forge.asset_aliases al
JOIN forge.assets a ON al.asset_id = a.asset_id
WHERE al.source_name   = 'coinalyze'
  AND al.alias_symbol  = 'BTCUSDT';
```

### Table: venues

Exchange and protocol venues. Referenced by instruments to establish
where an instrument trades.

```sql
CREATE TABLE forge.venues (
    venue_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT        NOT NULL UNIQUE,
    display_name    TEXT        NOT NULL,
    venue_type      TEXT        NOT NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT venues_type_valid
        CHECK (venue_type IN (
            'cex', 'dex', 'protocol', 'chain', 'etf_issuer'
        ))
);

CREATE INDEX idx_venues_canonical ON forge.venues (canonical_name);
```

### Table: instruments

A specific tradeable instrument at a specific venue. BTC perpetual on
Binance is a different instrument from BTC spot on Coinbase, though both
reference the BTC asset.

The `canonical_symbol` here is what populates `instrument_id` in ClickHouse.
It must be unique and stable — it is the ClickHouse string key.

```sql
CREATE TABLE forge.instruments (
    instrument_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_symbol    TEXT        NOT NULL UNIQUE,
    asset_id            UUID        NOT NULL REFERENCES forge.assets (asset_id),
    venue_id            UUID        REFERENCES forge.venues (venue_id),
    instrument_type     TEXT        NOT NULL,
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    collection_tier     TEXT        NOT NULL DEFAULT 'collection',
    base_currency       TEXT,
    quote_currency      TEXT        DEFAULT 'USD',
    metadata            JSONB       NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deprecated_at       TIMESTAMPTZ,

    CONSTRAINT instruments_type_valid
        CHECK (instrument_type IN (
            'perpetual', 'spot', 'etf', 'defi_protocol',
            'stablecoin', 'index', 'equity'
        )),
    CONSTRAINT instruments_tier_valid
        CHECK (collection_tier IN (
            'collection', 'scoring', 'signal_eligible'
        ))
);

CREATE INDEX idx_instruments_symbol    ON forge.instruments (canonical_symbol);
CREATE INDEX idx_instruments_asset     ON forge.instruments (asset_id);
CREATE INDEX idx_instruments_tier      ON forge.instruments (collection_tier)
    WHERE is_active = true;
```

**Tier semantics:**
- `collection` — data collected, not yet sufficient for scoring
- `scoring` — sufficient data quality and history for EDSx and feature
  computation
- `signal_eligible` — meets all thresholds for signal output to customers

Tier promotion is rule-driven and automatic. Every promotion is logged with
timestamp and reason.

**Key query — all signal-eligible instruments:**
```sql
SELECT instrument_id, canonical_symbol, instrument_type
FROM forge.instruments
WHERE collection_tier = 'signal_eligible'
  AND is_active = true
ORDER BY canonical_symbol;
```

**Key query — resolve ClickHouse instrument_id to PostgreSQL instrument:**
```sql
SELECT instrument_id, asset_id, collection_tier
FROM forge.instruments
WHERE canonical_symbol = 'BTC';
-- canonical_symbol here is the same string stored in ClickHouse instrument_id
```

### Table: source_catalog

Every data source in the system. `canonical_name` is the string stored
in ClickHouse `source_id` column. The `redistribution` flag is enforced
at the serving layer (Layer 8) — any metric whose only source has
`redistribution = false` cannot appear in external data products.

```sql
CREATE TABLE forge.source_catalog (
    source_id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name          TEXT        NOT NULL UNIQUE,
    display_name            TEXT        NOT NULL,
    tier                    INTEGER     NOT NULL,
    tos_status              TEXT        NOT NULL DEFAULT 'unaudited',
    commercial_use          BOOLEAN,
    redistribution          BOOLEAN,
    attribution_required    BOOLEAN     NOT NULL DEFAULT true,
    cost_tier               TEXT        NOT NULL DEFAULT 'free',
    reliability_slo         NUMERIC(4,3),
    metadata                JSONB       NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT source_tos_valid
        CHECK (tos_status IN (
            'none', 'low', 'unaudited', 'restricted', 'prohibited'
        )),
    CONSTRAINT source_cost_valid
        CHECK (cost_tier IN ('free', 'freemium', 'paid', 'enterprise'))
);

CREATE INDEX idx_source_canonical ON forge.source_catalog (canonical_name);
```

### Table: metric_catalog

The canonical definition of every metric in the system. `canonical_name`
is the string stored in ClickHouse `metric_id`. Names are immutable once
assigned — deprecate and replace, never rename.

```sql
CREATE TABLE forge.metric_catalog (
    metric_id               UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name          TEXT            NOT NULL UNIQUE,
    domain                  TEXT            NOT NULL,
    subdomain               TEXT,
    description             TEXT            NOT NULL,
    unit                    TEXT            NOT NULL,
    value_type              TEXT            NOT NULL DEFAULT 'numeric',
    granularity             TEXT            NOT NULL,
    cadence_hours           NUMERIC(6,2)    NOT NULL,
    staleness_threshold_hours NUMERIC(6,2)  NOT NULL,
    expected_range_low      DOUBLE PRECISION,
    expected_range_high     DOUBLE PRECISION,
    is_nullable             BOOLEAN         NOT NULL DEFAULT false,
    methodology             TEXT,
    signal_pillar           TEXT,
    status                  TEXT            NOT NULL DEFAULT 'active',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
    deprecated_at           TIMESTAMPTZ,

    CONSTRAINT metric_domain_valid
        CHECK (domain IN (
            'derivatives', 'spot', 'flows', 'defi',
            'macro', 'etf', 'stablecoin'
        )),
    CONSTRAINT metric_value_type_valid
        CHECK (value_type IN ('numeric', 'categorical', 'boolean')),
    CONSTRAINT metric_granularity_valid
        CHECK (granularity IN (
            'per_instrument', 'per_protocol',
            'per_product', 'market_level'
        )),
    CONSTRAINT metric_pillar_valid
        CHECK (signal_pillar IN (
            'derivatives_pressure', 'capital_flows',
            'defi_health', 'macro_context', NULL
        )),
    CONSTRAINT metric_status_valid
        CHECK (status IN ('active', 'deprecated', 'planned'))
);

CREATE INDEX idx_metric_canonical   ON forge.metric_catalog (canonical_name);
CREATE INDEX idx_metric_domain      ON forge.metric_catalog (domain);
CREATE INDEX idx_metric_pillar      ON forge.metric_catalog (signal_pillar)
    WHERE status = 'active';
CREATE INDEX idx_metric_granularity ON forge.metric_catalog (granularity);
```

**Canonical name convention:** `domain.subdomain.metric_name`
Examples: `derivatives.perpetual.funding_rate` ·
`flows.exchange.inflow_usd` · `macro.rates.yield_10y` ·
`defi.aggregate.tvl_usd`

Names are immutable once assigned. To change: deprecate the existing row,
create a new row with the new name, update all adapters and feature
catalog entries.

**Key query — all active metrics for a pillar:**
```sql
SELECT canonical_name, granularity, cadence_hours
FROM forge.metric_catalog
WHERE signal_pillar = 'derivatives_pressure'
  AND status = 'active'
ORDER BY canonical_name;
```

**Key query — staleness check inputs:**
```sql
SELECT canonical_name, cadence_hours, staleness_threshold_hours
FROM forge.metric_catalog
WHERE status = 'active'
ORDER BY cadence_hours, canonical_name;
```

### Table: metric_lineage

Many-to-many: which sources provide which metrics. One row per
(metric, source) pair. Dagster reads this at startup to build
one Software-Defined Asset per row.

```sql
CREATE TABLE forge.metric_lineage (
    lineage_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_id       UUID        NOT NULL REFERENCES forge.metric_catalog (metric_id),
    source_id       UUID        NOT NULL REFERENCES forge.source_catalog (source_id),
    is_primary      BOOLEAN     NOT NULL DEFAULT true,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT metric_lineage_unique UNIQUE (metric_id, source_id)
);

CREATE INDEX idx_lineage_metric ON forge.metric_lineage (metric_id);
CREATE INDEX idx_lineage_source ON forge.metric_lineage (source_id);
```

**Key query — all sources for a metric:**
```sql
SELECT sc.canonical_name, sc.redistribution, ml.is_primary
FROM forge.metric_lineage ml
JOIN forge.source_catalog sc ON ml.source_id = sc.source_id
JOIN forge.metric_catalog mc ON ml.metric_id = mc.metric_id
WHERE mc.canonical_name = 'derivatives.perpetual.funding_rate';
```

**Key query — Dagster asset graph construction (all metric/source pairs):**
```sql
SELECT
    mc.canonical_name   AS metric_name,
    sc.canonical_name   AS source_name,
    mc.cadence_hours,
    sc.redistribution
FROM forge.metric_lineage ml
JOIN forge.metric_catalog  mc ON ml.metric_id  = mc.metric_id
JOIN forge.source_catalog  sc ON ml.source_id  = sc.source_id
WHERE mc.status = 'active'
ORDER BY mc.cadence_hours, mc.canonical_name;
```

### Table: event_calendar

Scheduled events with market impact. Used by feature category E (calendar
features) and by the regime classifier. `instrument_id` nullable —
market-level events (Fed meetings) have no instrument association.

```sql
CREATE TABLE forge.event_calendar (
    event_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT        NOT NULL,
    event_name      TEXT        NOT NULL,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    instrument_id   UUID        REFERENCES forge.instruments (instrument_id),
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT event_type_valid
        CHECK (event_type IN (
            'fed_meeting', 'options_expiry', 'futures_expiry',
            'token_unlock', 'halving', 'fork', 'macro_release'
        ))
);

CREATE INDEX idx_event_scheduled   ON forge.event_calendar (scheduled_at);
CREATE INDEX idx_event_type        ON forge.event_calendar (event_type, scheduled_at);
CREATE INDEX idx_event_instrument  ON forge.event_calendar (instrument_id, scheduled_at)
    WHERE instrument_id IS NOT NULL;
```

**Key query — upcoming events within 14 days (feature computation):**
```sql
SELECT event_type, event_name, scheduled_at, instrument_id
FROM forge.event_calendar
WHERE scheduled_at BETWEEN now() AND now() + INTERVAL '14 days'
ORDER BY scheduled_at;
```

### Table: supply_events

Scheduled supply-side events for crypto assets: token unlocks, halvings,
emissions changes. Used by structural risk pillar features.

```sql
CREATE TABLE forge.supply_events (
    event_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id       UUID        NOT NULL REFERENCES forge.instruments (instrument_id),
    event_type          TEXT        NOT NULL,
    scheduled_at        TIMESTAMPTZ NOT NULL,
    supply_change_usd   NUMERIC(20,2),
    supply_change_pct   NUMERIC(8,4),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT supply_event_type_valid
        CHECK (event_type IN (
            'token_unlock', 'halving', 'emissions_change',
            'burn_event', 'vesting_cliff'
        ))
);

CREATE INDEX idx_supply_instrument ON forge.supply_events (instrument_id, scheduled_at);
CREATE INDEX idx_supply_scheduled  ON forge.supply_events (scheduled_at);
```

### Table: adjustment_factors

Correction multipliers applied to historical observations — exchange rate
changes, denomination shifts, data corrections from sources. Applied at
the Gold layer during feature compute, never retroactively to Silver.

```sql
CREATE TABLE forge.adjustment_factors (
    factor_id           UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id       UUID            NOT NULL REFERENCES forge.instruments (instrument_id),
    metric_id           UUID            NOT NULL REFERENCES forge.metric_catalog (metric_id),
    effective_from      TIMESTAMPTZ     NOT NULL,
    effective_to        TIMESTAMPTZ,
    multiplier          DOUBLE PRECISION NOT NULL,
    reason              TEXT            NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_adjustment_lookup
    ON forge.adjustment_factors (instrument_id, metric_id, effective_from);
```

### Table: collection_events

Operational log of every adapter run. One row per collection job execution.
Used for: staleness detection, rejection rate monitoring, coverage
completeness queries.

```sql
CREATE TABLE forge.collection_events (
    event_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id               UUID        NOT NULL REFERENCES forge.source_catalog (source_id),
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
        CHECK (status IN ('running', 'completed', 'failed', 'partial'))
);

CREATE INDEX idx_collection_source  ON forge.collection_events (source_id, started_at DESC);
CREATE INDEX idx_collection_status  ON forge.collection_events (status, started_at DESC)
    WHERE status IN ('failed', 'partial');
```

**Key query — recent failures by source:**
```sql
SELECT sc.canonical_name, ce.started_at, ce.error_detail
FROM forge.collection_events ce
JOIN forge.source_catalog sc ON ce.source_id = sc.source_id
WHERE ce.status IN ('failed', 'partial')
  AND ce.started_at > now() - INTERVAL '48 hours'
ORDER BY ce.started_at DESC;
```

### Table: instrument_metric_coverage

Tracks observed data completeness per (instrument, metric) pair. Updated
after each collection event. Used for: tier promotion eligibility checks,
SOURCE_STALE null state evaluation, signal confidence inputs.

```sql
CREATE TABLE forge.instrument_metric_coverage (
    instrument_id       UUID        NOT NULL REFERENCES forge.instruments (instrument_id),
    metric_id           UUID        NOT NULL REFERENCES forge.metric_catalog (metric_id),
    first_observation   TIMESTAMPTZ,
    latest_observation  TIMESTAMPTZ,
    expected_cadence    INTERVAL,
    completeness_30d    NUMERIC(5,4),
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, metric_id)
);

CREATE INDEX idx_coverage_completeness
    ON forge.instrument_metric_coverage (completeness_30d)
    WHERE is_active = true;
CREATE INDEX idx_coverage_latest
    ON forge.instrument_metric_coverage (latest_observation)
    WHERE is_active = true;
```

**Key query — tier promotion eligibility (all metrics ≥ 90% for 30d):**
```sql
SELECT i.canonical_symbol, COUNT(*) AS metrics_tracked,
       MIN(imc.completeness_30d) AS min_completeness
FROM forge.instrument_metric_coverage imc
JOIN forge.instruments i ON imc.instrument_id = i.instrument_id
WHERE imc.is_active = true
GROUP BY i.canonical_symbol, i.instrument_id
HAVING MIN(imc.completeness_30d) >= 0.90
ORDER BY min_completeness DESC;
```

---

## LAYER 4: CLICKHOUSE OBSERVATION STORE

### Design Decisions

**Why canonical strings as IDs (not UUIDs):**
ClickHouse merge tree performance is determined by physical data locality.
Rows with adjacent ordering key values are stored together and compressed
together. UUIDs are random 128-bit values — two consecutive rows for the
same metric on different instruments have unrelated UUIDs and land in
arbitrary positions in the merge tree, destroying compression and scan
locality.

The canonical name has natural prefix structure: all `derivatives.*`
metrics cluster together. Within that, all `derivatives.perpetual.*` rows
cluster. A query for "all observations for BTC across all derivatives
metrics over 30 days" physically scans a contiguous range of rows.

PostgreSQL tables keep their UUID primary keys — correct for catalog tables
with hundreds of rows. ClickHouse references them by canonical string.
The mapping is: `instruments.canonical_symbol` → ClickHouse `instrument_id`,
`metric_catalog.canonical_name` → ClickHouse `metric_id`,
`source_catalog.canonical_name` → ClickHouse `source_id`.

**Why sentinel `'__market__'` (not Nullable(String)):**
ClickHouse's `ReplacingMergeTree` deduplication behavior on nullable ordering
key columns is not guaranteed to be deterministic across all versions. NULL
has no defined sort position, which means the engine cannot guarantee
consistent deduplication when two rows have `instrument_id = NULL`. The
sentinel `'__market__'` is explicit, sorts consistently, and is
self-documenting. It is stripped by the application layer before results
reach consumers — nothing above the adapter layer ever sees it.

### Object: forge.observations

```sql
CREATE TABLE forge.observations
(
    metric_id       String          NOT NULL,
    -- Canonical metric name: 'derivatives.perpetual.funding_rate'
    -- Maps to metric_catalog.canonical_name in PostgreSQL

    instrument_id   String          NOT NULL,
    -- Canonical symbol: 'BTC', 'ETH', etc.
    -- '__market__' for market-level metrics (macro, DeFi aggregate, stablecoin aggregate)
    -- Maps to instruments.canonical_symbol in PostgreSQL

    source_id       String          NOT NULL,
    -- Canonical source name: 'coinalyze', 'defillama', 'fred'
    -- Maps to source_catalog.canonical_name in PostgreSQL

    observed_at     DateTime64(3)   NOT NULL,
    -- When this value was true in the world (millisecond precision)
    -- Source timestamp, normalized to UTC by the adapter

    ingested_at     DateTime64(3)   NOT NULL,
    -- When this value entered this store (millisecond precision)
    -- Set by the adapter at write time. Never modified after insert.

    value           Nullable(Float64),
    -- NULL is valid for metrics where is_nullable = true in metric_catalog

    data_version    UInt64          NOT NULL
    -- Revision counter. Incremented when a source provides a corrected value
    -- for the same (metric_id, instrument_id, observed_at) triple.
    -- ReplacingMergeTree retains the highest data_version row on merge.
)
ENGINE = ReplacingMergeTree(data_version)
ORDER BY (metric_id, instrument_id, observed_at)
PARTITION BY toYYYYMM(observed_at)
SETTINGS index_granularity = 8192;
```

**Engine justification — ReplacingMergeTree(data_version):**
Adapters write idempotently — re-sending the same observation must not
create duplicates. ReplacingMergeTree deduplicates rows with the same
ordering key during background merges, retaining the row with the highest
`data_version`. This handles both duplicate re-sends (same data_version,
same value) and genuine revisions (incremented data_version, corrected value).

**Ordering key justification — (metric_id, instrument_id, observed_at):**
Three access patterns must be efficient:
1. All observations for one metric across all instruments — leading key
2. All observations for one (metric, instrument) pair over time — leading two keys
3. Time-range scan within a (metric, instrument) pair — all three keys

The ordering key serves all three. Canonical strings provide natural
clustering: all `derivatives.*` metrics are physically adjacent, so
queries for the derivatives_pressure pillar scan a contiguous range.

**Partition key justification — toYYYYMM(observed_at):**
The export job reads all new observations since the last export (the dominant
read pattern). Monthly partitions let ClickHouse skip all partitions outside
the incremental window entirely — no full-table scan. Partition granularity
of one month balances: too fine (daily) creates too many parts; too coarse
(yearly) makes the export job scan too much data per partition.

**Deduplication timing caveat:**
ReplacingMergeTree deduplication is eventual — rows are merged during
background OPTIMIZE operations, not immediately on write. Queries against
recently written data must use the `FINAL` keyword or `QUALIFY` deduplication
to guarantee clean results. The export job uses `FINAL`. Feature compute
uses `current_values` MV for the hot path (see below).

### Object: forge.dead_letter

Receives every observation rejected during adapter validation or Great
Expectations validation at the Bronze → Silver boundary. Nothing is
silently dropped. Every rejection is logged with the raw payload,
rejection reason, and rejection code for operational triage.

```sql
CREATE TABLE forge.dead_letter
(
    source_id           String                      NOT NULL,
    metric_id           Nullable(String),
    -- NULL if rejection occurred before metric could be resolved
    instrument_id       Nullable(String),
    -- NULL if rejection occurred before instrument could be resolved
    raw_payload         String                      NOT NULL,
    -- Original JSON payload from the source, preserved exactly
    rejection_reason    String                      NOT NULL,
    -- Human-readable description of what failed
    rejection_code      LowCardinality(String)      NOT NULL,
    -- Machine-readable code for operational queries (see valid codes below)
    collected_at        DateTime64(3)               NOT NULL,
    -- When the adapter fetched this from the source
    rejected_at         DateTime64(3)               NOT NULL
    -- When this row was written to dead_letter
)
ENGINE = MergeTree()
ORDER BY (rejection_code, source_id, rejected_at)
TTL rejected_at + INTERVAL 90 DAY DELETE
SETTINGS index_granularity = 8192;
```

**Valid rejection codes:**

| Code | Meaning |
|---|---|
| `RANGE_VIOLATION` | Value outside expected_range_low / expected_range_high |
| `TYPE_MISMATCH` | Value is not the declared value_type |
| `NULL_VIOLATION` | NULL value for a metric where is_nullable = false |
| `UNKNOWN_METRIC` | Adapter could not resolve to a canonical_name in metric_catalog |
| `UNKNOWN_INSTRUMENT` | Adapter could not resolve to a canonical_symbol in instruments |
| `DUPLICATE_OBSERVATION` | Exact duplicate of an already-ingested row (same data_version) |
| `STALE_OBSERVATION` | observed_at older than staleness_threshold_hours from metric_catalog |
| `SCHEMA_ERROR` | Raw payload failed structural parsing before field extraction |
| `UNIT_UNKNOWN` | Source unit could not be mapped to canonical unit |
| `EXTREME_VALUE_PENDING_REVIEW` | Value within type/range constraints but flagged as extreme outlier — requires manual triage (Coinalyze ANKR, FRAX, OGN funding rates) |
| `GE_VALIDATION_FAILURE` | Great Expectations rule failure at Bronze → Silver boundary |

**TTL justification:**
90 days aligns with Bronze retention. After 90 days, the raw payload in
Bronze is expired. A dead_letter row without its Bronze source context is
operationally useless for triage. Automatic TTL via background merges —
no Dagster job required.

**Operational procedure — dead letter triage:**

```sql
-- Daily rejection rate by source and code
SELECT
    source_id,
    rejection_code,
    count()     AS rejection_count,
    min(rejected_at) AS first_seen,
    max(rejected_at) AS last_seen
FROM forge.dead_letter
WHERE rejected_at >= now() - INTERVAL 24 HOUR
GROUP BY source_id, rejection_code
ORDER BY rejection_count DESC;

-- Inspect raw payload for a specific rejection
SELECT raw_payload, rejection_reason, collected_at
FROM forge.dead_letter
WHERE source_id      = 'coinalyze'
  AND rejection_code = 'EXTREME_VALUE_PENDING_REVIEW'
  AND rejected_at   >= now() - INTERVAL 24 HOUR
LIMIT 10;
```

**Response procedure by code:**
- `RANGE_VIOLATION` / `TYPE_MISMATCH`: Investigate source API change. Check if
  metric_catalog range definitions need updating. Do not widen ranges without
  architect review.
- `EXTREME_VALUE_PENDING_REVIEW`: Manual review required. If value is valid,
  update metric_catalog range. If it is a source error, discard.
- `UNKNOWN_METRIC` / `UNKNOWN_INSTRUMENT`: Add catalog entry or alias if the
  source introduced a new instrument. Do not add catalog entries without
  verifying the source data is trustworthy.
- `GE_VALIDATION_FAILURE`: Review the Great Expectations suite for the
  affected source. May indicate a source format change.
- Global rejection rate > 5% for any source: halt the adapter, investigate
  before resuming. Do not silently absorb high rejection rates.

### Object: forge.current_values

Materialized view over `forge.observations`. Provides the hot-path read for
feature compute — the current value of every (metric, instrument) pair
without requiring a `FINAL` scan of the full observations table.

Updates incrementally on every insert to `forge.observations`. No scheduled
refresh. Stale window is bounded by the insert cadence of the upstream
adapter, not by any batch job.

```sql
CREATE MATERIALIZED VIEW forge.current_values
ENGINE = AggregatingMergeTree()
ORDER BY (metric_id, instrument_id)
AS
SELECT
    metric_id,
    instrument_id,
    argMaxState(value,       observed_at)   AS latest_value,
    argMaxState(observed_at, observed_at)   AS latest_observed_at,
    argMaxState(ingested_at, observed_at)   AS latest_ingested_at,
    argMaxState(source_id,   observed_at)   AS latest_source_id,
    argMaxState(data_version, observed_at)  AS latest_data_version
FROM forge.observations
GROUP BY metric_id, instrument_id;
```

**Query pattern — current value for one (metric, instrument):**
```sql
SELECT
    metric_id,
    instrument_id,
    argMaxMerge(latest_value)           AS value,
    argMaxMerge(latest_observed_at)     AS observed_at,
    argMaxMerge(latest_ingested_at)     AS ingested_at,
    argMaxMerge(latest_source_id)       AS source_id
FROM forge.current_values
WHERE metric_id     = 'derivatives.perpetual.funding_rate'
  AND instrument_id = 'BTC'
GROUP BY metric_id, instrument_id;
```

**Query pattern — current values for all instruments of one metric:**
```sql
SELECT
    instrument_id,
    argMaxMerge(latest_value)       AS value,
    argMaxMerge(latest_observed_at) AS observed_at
FROM forge.current_values
WHERE metric_id = 'derivatives.perpetual.funding_rate'
GROUP BY metric_id, instrument_id
ORDER BY instrument_id;
```

**Stale read caveat:**
argMax returns the row with the highest `observed_at`. If a revision arrives
with a corrected value for an existing `observed_at` timestamp (same time,
incremented `data_version`), the MV does not deterministically reflect the
revision until the next insert with a later `observed_at` arrives. For live
feature compute, this is acceptable. For PIT-correct backtest queries, bypass
this view entirely and use Pattern 3 below.

---

## FOUR BITEMPORAL QUERY PATTERNS

### Pattern 1 — Current value (live feature compute hot path)

Use `current_values` MV. Do not query `observations` directly for this
use case.

```sql
SELECT
    instrument_id,
    argMaxMerge(latest_value)       AS value,
    argMaxMerge(latest_observed_at) AS observed_at
FROM forge.current_values
WHERE metric_id = 'derivatives.perpetual.funding_rate'
GROUP BY metric_id, instrument_id
ORDER BY instrument_id;
```

### Pattern 2 — Historical range (clean deduplicated history)

Use when you need all observations for a (metric, instrument) pair over a
time window, with one row per timestamp (duplicates from re-ingestion
collapsed to the highest data_version).

```sql
SELECT
    observed_at,
    value,
    ingested_at,
    data_version
FROM forge.observations FINAL
WHERE metric_id      = 'derivatives.perpetual.funding_rate'
  AND instrument_id  = 'BTC'
  AND observed_at   >= toDateTime64('2025-01-01 00:00:00', 3)
  AND observed_at   <  toDateTime64('2026-01-01 00:00:00', 3)
ORDER BY observed_at ASC;
```

`FINAL` triggers a blocking merge during the query, ensuring
ReplacingMergeTree deduplication is applied regardless of background merge
state. Required for clean historical reads.

### Pattern 3 — PIT backtest-safe (what was known as of a specific time)

This is the pattern that makes backtests correct. At `as_of` time T, you
see only observations with `ingested_at ≤ T` — data backfilled after T is
excluded. A backtest at 2024-01-01 does not see data ingested in 2026.

Do not use `FINAL` here. `FINAL` collapses to highest `data_version`
regardless of `ingested_at`. You want the highest `data_version` that was
ingested by T — a different result if a revision arrived after T.

```sql
SELECT
    observed_at,
    value,
    ingested_at,
    data_version
FROM forge.observations
WHERE metric_id      = 'derivatives.perpetual.funding_rate'
  AND instrument_id  = 'BTC'
  AND observed_at   >= toDateTime64('2024-01-01 00:00:00', 3)
  AND observed_at   <  toDateTime64('2025-01-01 00:00:00', 3)
  AND ingested_at   <= toDateTime64('2025-01-01 00:00:00', 3)
QUALIFY row_number() OVER (
    PARTITION BY metric_id, instrument_id, observed_at
    ORDER BY data_version DESC
) = 1
ORDER BY observed_at ASC;
```

`QUALIFY` with `row_number()` selects the highest `data_version` row per
`(metric_id, instrument_id, observed_at)` triple within the `ingested_at`
filter. This is the correct PIT deduplication — it respects both the
time filter and the revision ordering.

### Pattern 4 — Revision detection (audit and data quality)

Finds observations that have been revised — more than one `data_version`
exists for the same `(metric_id, instrument_id, observed_at)` triple.
Does not use `FINAL` — you want all versions visible.

```sql
SELECT
    metric_id,
    instrument_id,
    observed_at,
    count()             AS version_count,
    min(ingested_at)    AS first_ingested,
    max(ingested_at)    AS latest_ingested,
    min(data_version)   AS original_version,
    max(data_version)   AS current_version
FROM forge.observations
WHERE metric_id = 'derivatives.perpetual.funding_rate'
GROUP BY metric_id, instrument_id, observed_at
HAVING version_count > 1
ORDER BY latest_ingested DESC
LIMIT 100;
```

---

## METRIC CATALOG — COMPLETE SEED DATA

Every row below must exist in `forge.metric_catalog` at Phase 0 gate.
All fields shown. `staleness_threshold_hours` = 2 × `cadence_hours` unless
noted. Market-level metrics have `granularity = 'market_level'`.

### Derivatives domain

| canonical_name | unit | cadence_hours | staleness_threshold_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|---|
| `derivatives.perpetual.funding_rate` | rate_per_8h | 8 | 16 | -0.05 | 0.05 | false | derivatives_pressure |
| `derivatives.perpetual.open_interest_usd` | usd | 8 | 16 | 0 | — | false | derivatives_pressure |
| `derivatives.perpetual.liquidations_long_usd` | usd | 8 | 16 | 0 | — | false | derivatives_pressure |
| `derivatives.perpetual.liquidations_short_usd` | usd | 8 | 16 | 0 | — | false | derivatives_pressure |
| `derivatives.perpetual.price_usd` | usd | 8 | 16 | 0 | — | false | derivatives_pressure |
| `derivatives.options.delta_skew_25` | dimensionless | 8 | 24 | -1.0 | 1.0 | true | derivatives_pressure |
| `derivatives.options.iv_1w` | pct_annualized | 8 | 24 | 0 | 5.0 | true | derivatives_pressure |
| `derivatives.options.iv_1m` | pct_annualized | 8 | 24 | 0 | 5.0 | true | derivatives_pressure |

Options metrics (`delta_skew_25`, `iv_1w`, `iv_1m`) are nullable — no
options data source in v1. Null-propagate in pillar confidence.

### Spot domain

| canonical_name | unit | cadence_hours | staleness_threshold_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|---|
| `spot.price.close_usd` | usd | 24 | 48 | 0 | — | false | — |
| `spot.volume.usd_24h` | usd | 24 | 48 | 0 | — | false | — |
| `spot.market_cap.usd` | usd | 24 | 48 | 0 | — | false | — |
| `spot.market_cap.total_crypto_usd` | usd | 24 | 48 | 0 | — | false | — |
| `spot.dominance.btc_pct` | pct | 24 | 48 | 0 | 100 | false | — |

`total_crypto_usd` and `btc_pct` are market-level (`granularity = 'market_level'`,
`instrument_id = '__market__'` in ClickHouse).

### Flows domain

| canonical_name | unit | cadence_hours | staleness_threshold_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|---|
| `flows.exchange.inflow_usd` | usd | 24 | 48 | 0 | — | false | capital_flows |
| `flows.exchange.outflow_usd` | usd | 24 | 48 | 0 | — | false | capital_flows |
| `flows.onchain.transfer_volume_usd` | usd | 24 | 48 | 0 | — | false | capital_flows |

Note: `flows.exchange.net_flow_usd` is not stored in Silver. It is a
Category C derived feature (`inflow - outflow`) computed in Marts by
forge_compute.

### Stablecoin domain

| canonical_name | granularity | unit | cadence_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|---|
| `stablecoin.supply.total_usd` | market_level | usd | 24 | 0 | — | false | capital_flows |
| `stablecoin.supply.per_asset_usd` | per_instrument | usd | 24 | 0 | — | false | capital_flows |
| `stablecoin.peg.price_usd` | per_instrument | usd | 24 | 0.90 | 1.10 | false | capital_flows |

Values outside [0.90, 1.10] for `peg.price_usd` → `RANGE_VIOLATION` → dead_letter.

### ETF domain

| canonical_name | granularity | unit | cadence_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|---|
| `etf.flows.net_flow_usd` | per_product | usd | 24 | — | — | false | capital_flows |
| `etf.aum.total_usd` | per_product | usd | 24 | 0 | — | false | capital_flows |

`redistribution = false` for SoSoValue-sourced ETF metrics. Serving layer
enforces this — never appears in external data products.

### DeFi domain

| canonical_name | granularity | unit | cadence_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|---|
| `defi.aggregate.tvl_usd` | market_level | usd | 24 | 0 | — | false | defi_health |
| `defi.protocol.tvl_usd` | per_protocol | usd | 24 | 0 | — | false | defi_health |
| `defi.dex.volume_usd_24h` | market_level | usd | 24 | 0 | — | false | defi_health |
| `defi.lending.utilization_rate` | market_level | ratio | 24 | 0 | 1.0 | false | defi_health |
| `defi.protocol.fees_usd_24h` | per_protocol | usd | 24 | 0 | — | true | defi_health |
| `defi.protocol.revenue_usd_24h` | per_protocol | usd | 24 | 0 | — | true | defi_health |

**`defi.lending.utilization_rate` — v1 methodology note:**
V1 uses a proxy computation: `borrow_tvl / supply_tvl` from DeFiLlama
lending data. This is stored under the canonical name `utilization_rate`
(not `utilization_proxy`) because the metric name represents what it
measures, not how it is currently computed. The `methodology` field in
metric_catalog documents the proxy. V1.1 replaces the proxy with direct
utilization rate from Aave/Compound subgraphs. The canonical name does not
change — zero downstream impact.

### Macro domain (all market_level)

All macro metrics: `granularity = 'market_level'`, `instrument_id = '__market__'`
in ClickHouse.

| canonical_name | unit | cadence_hours | range_low | range_high | nullable | signal_pillar | FRED series |
|---|---|---|---|---|---|---|---|
| `macro.rates.yield_10y` | pct | 24 | -2 | 25 | false | macro_context | DGS10 |
| `macro.rates.yield_2y` | pct | 24 | -2 | 25 | false | macro_context | DGS2 |
| `macro.rates.yield_30y` | pct | 24 | -2 | 25 | false | macro_context | DGS30 |
| `macro.rates.yield_10y_2y_spread` | pct | 24 | -5 | 5 | false | macro_context | T10Y2Y |
| `macro.rates.yield_10y_3m_spread` | pct | 24 | -5 | 5 | false | macro_context | T10Y3M |
| `macro.rates.real_yield_10y` | pct | 24 | -5 | 15 | false | macro_context | DFII10 |
| `macro.rates.breakeven_inflation_10y` | pct | 24 | -2 | 10 | false | macro_context | T10YIE |
| `macro.rates.fed_funds_effective` | pct | 24 | 0 | 25 | false | macro_context | EFFR |
| `macro.fx.dxy` | index | 24 | 50 | 200 | false | macro_context | DTWEXBGS |
| `macro.fx.wti_crude` | usd | 24 | 0 | 300 | false | macro_context | DCOILWTICO |
| `macro.credit.hy_oas` | bps | 24 | 0 | 3000 | false | macro_context | BAMLH0A0HYM2 |
| `macro.equities.sp500` | index | 24 | 0 | — | false | macro_context | SP500 |
| `macro.volatility.vix` | index | 24 | 0 | 100 | false | macro_context | VIXCLS |
| `macro.money.m2_supply` | usd_billions | 168 | 0 | — | false | macro_context | M2SL |
| `macro.money.monetary_base` | usd_billions | 168 | 0 | — | false | macro_context | BOGMBASE |
| `macro.cb.fed_total_assets` | usd_billions | 168 | 0 | — | false | macro_context | WALCL |
| `macro.cb.ecb_total_assets` | eur_billions | 168 | 0 | — | false | macro_context | ECBASSETSW |
| `macro.cb.boj_total_assets` | jpy_trillions | 168 | 0 | — | false | macro_context | JPNASSETS |
| `macro.employment.nonfarm_payrolls` | thousands | 720 | -5000 | 5000 | false | macro_context | PAYEMS |
| `macro.employment.initial_claims` | thousands | 168 | 0 | 5000 | false | macro_context | ICSA |
| `macro.inflation.cpi_all_urban` | index | 720 | 0 | — | false | macro_context | CPIAUCSL |
| `macro.inflation.core_pce` | pct_yoy | 720 | -5 | 20 | false | macro_context | PCEPI |
| `macro.gdp.real_growth` | pct_annualized | 2160 | -30 | 30 | false | macro_context | A191RL1Q225SBEA |

**FRED gap note:** `macro.credit.hy_oas` (BAMLH0A0HYM2) was in the feature
catalog but missing from the FRED adapter in the legacy system. It must be
added to the FRED adapter before Phase 1 collection begins. This is a Phase 1
pre-condition, not a v1.1 item.

**Cadence note for weekly/monthly FRED series:**
`cadence_hours = 168` for weekly (7 × 24). `cadence_hours = 720` for
monthly (~30 × 24). `cadence_hours = 2160` for quarterly (~90 × 24).
FRED returns '.' for missing values (weekends, holidays) — adapter maps
to NULL with source flag `SOURCE_MISSING_VALUE`. These gaps are structural,
not quality issues.

**BGeometrics domain — on-chain valuation (per_instrument, 24h):**

| canonical_name | unit | cadence_hours | range_low | range_high | nullable | signal_pillar |
|---|---|---|---|---|---|---|
| `onchain.valuation.mvrv` | ratio | 24 | 0 | 20 | false | — |
| `onchain.valuation.sopr` | ratio | 24 | 0 | 5 | false | — |
| `onchain.valuation.nupl` | dimensionless | 24 | -1 | 1 | false | — |
| `onchain.valuation.puell_multiple` | ratio | 24 | 0 | 10 | false | — |

These are currently unassigned to a signal_pillar. Structural Risk pillar
(planned, REM-24) will consume them. `signal_pillar = NULL` until that
pillar is designed.

---

## SOURCE CATALOG — COMPLETE SEED DATA

Every row below must exist in `forge.source_catalog` at Phase 0 gate.
10 sources at v1.

| canonical_name | display_name | tier | tos_status | commercial_use | redistribution | attribution_required | cost_tier |
|---|---|---|---|---|---|---|---|
| `coinalyze` | Coinalyze | 1 | unaudited | NULL | NULL | true | free |
| `defillama` | DeFiLlama | 1 | low | true | true | true | free |
| `fred` | Federal Reserve (FRED) | 1 | none | true | true | false | free |
| `tiingo` | Tiingo | 1 | low | true | true | true | paid |
| `sosovalue` | SoSoValue | 1 | restricted | false | false | true | free |
| `etherscan` | Etherscan V2 / Explorer | 2 | unaudited | NULL | NULL | true | freemium |
| `coinpaprika` | CoinPaprika | 1 | low | true | true | true | free |
| `coinmetrics` | CoinMetrics | 2 | unaudited | NULL | false | true | free |
| `bgeometrics` | BGeometrics | 2 | unaudited | NULL | NULL | true | free |
| `binance_blc01` | Binance (BLC-01) | 2 | unaudited | NULL | NULL | true | free |

**Redistribution enforcement:** `sosovalue` and `coinmetrics` have
`redistribution = false`. The serving layer (Layer 8) must filter any
response that would expose observations from these sources to external
customers. This is enforced at query time, not at ingestion time.

**`NULL` in commercial_use / redistribution:** `unaudited` status means
these fields cannot be set until the Phase 6 ToS audit completes.
The serving layer treats `NULL redistribution` as `false` — conservative
default that prevents accidental redistribution of unaudited source data.

---

## SCHEMA IMMUTABILITY PROCEDURES

The schema never changes. New metrics and new sources require only catalog
entries. Zero DDL.

### Procedure: Adding a new metric

**Step 1 — Verify the metric does not already exist:**
```sql
SELECT metric_id, canonical_name, status
FROM forge.metric_catalog
WHERE canonical_name = 'your.proposed.metric_name';
-- Must return zero rows. If status = 'deprecated', use a different name.
```

**Step 2 — Verify the source exists (or add it first via source procedure):**
```sql
SELECT source_id, canonical_name, redistribution
FROM forge.source_catalog
WHERE canonical_name = 'your_source_name';
-- Must return exactly one row.
```

**Step 3 — Insert the metric catalog row:**
```sql
INSERT INTO forge.metric_catalog (
    canonical_name,
    domain,
    subdomain,
    description,
    unit,
    value_type,
    granularity,
    cadence_hours,
    staleness_threshold_hours,
    expected_range_low,
    expected_range_high,
    is_nullable,
    methodology,
    signal_pillar,
    status
) VALUES (
    'domain.subdomain.metric_name',
    'domain',               -- must satisfy domain CHECK constraint
    'subdomain',
    'Human-readable description of what this metric measures',
    'unit_string',
    'numeric',              -- or 'categorical', 'boolean'
    'per_instrument',       -- or 'market_level', 'per_protocol', 'per_product'
    8,                      -- cadence in hours
    16,                     -- staleness threshold in hours (typically 2× cadence)
    NULL,                   -- expected_range_low (NULL if unbounded)
    NULL,                   -- expected_range_high (NULL if unbounded)
    false,                  -- is_nullable
    'Methodology description. For proxy metrics, document the proxy computation explicitly.',
    NULL,                   -- signal_pillar (or the pillar name if known)
    'active'
);
```

**Step 4 — Add the metric_lineage row:**
```sql
INSERT INTO forge.metric_lineage (metric_id, source_id, is_primary, notes)
SELECT
    (SELECT metric_id FROM forge.metric_catalog
     WHERE canonical_name = 'domain.subdomain.metric_name'),
    (SELECT source_id FROM forge.source_catalog
     WHERE canonical_name = 'your_source_name'),
    true,
    NULL;
```

**Step 5 — Add feature catalog entry (forge_compute requirement):**
Before any feature that consumes this metric can be computed, a feature
catalog entry must exist. This is a forge_compute pre-condition, not a
database operation — documented here as a reminder.

**Step 6 — Verification:**
```sql
-- Confirm the metric exists and is active
SELECT mc.canonical_name, mc.domain, mc.cadence_hours, sc.canonical_name AS source
FROM forge.metric_catalog mc
JOIN forge.metric_lineage ml ON mc.metric_id = ml.metric_id
JOIN forge.source_catalog sc ON ml.source_id = sc.source_id
WHERE mc.canonical_name = 'domain.subdomain.metric_name'
  AND mc.status = 'active';
-- Must return exactly one row with correct values.

-- Confirm no TimescaleDB / time-series columns were accidentally added
SELECT column_name, table_name
FROM information_schema.columns
WHERE table_schema = 'forge'
  AND column_name IN ('observed_at', 'value', 'value_numeric', 'ingested_at')
  AND table_name NOT IN ('observations', 'dead_letter');
-- Must return zero rows. If any rows returned: violation of Rule 3.
```

---

### Procedure: Adding a new source

**Step 1 — Verify the source does not already exist:**
```sql
SELECT source_id, canonical_name, tos_status
FROM forge.source_catalog
WHERE canonical_name = 'your_source_name';
-- Must return zero rows.
```

**Step 2 — Insert the source catalog row:**
```sql
INSERT INTO forge.source_catalog (
    canonical_name,
    display_name,
    tier,
    tos_status,
    commercial_use,
    redistribution,
    attribution_required,
    cost_tier,
    metadata
) VALUES (
    'source_canonical_name',    -- lowercase, underscore-separated
    'Display Name',
    1,                          -- tier: 1 (primary), 2 (secondary), 3 (fallback)
    'unaudited',                -- tos_status: none/low/unaudited/restricted/prohibited
    NULL,                       -- commercial_use: NULL until ToS audit complete
    NULL,                       -- redistribution: NULL until ToS audit complete
                                -- NOTE: serving layer treats NULL as false
    true,                       -- attribution_required
    'free',                     -- cost_tier: free/freemium/paid/enterprise
    '{}'
);
```

**Step 3 — Add metric_lineage rows for each metric this source provides:**

Repeat for each metric:
```sql
INSERT INTO forge.metric_lineage (metric_id, source_id, is_primary, notes)
SELECT
    (SELECT metric_id FROM forge.metric_catalog
     WHERE canonical_name = 'domain.subdomain.metric_name'),
    (SELECT source_id FROM forge.source_catalog
     WHERE canonical_name = 'source_canonical_name'),
    false,  -- is_primary: false if an existing primary source already covers this metric
    'Notes on coverage differences or known issues';
```

**Step 4 — Add asset_aliases rows for source-specific instrument identifiers:**
```sql
INSERT INTO forge.asset_aliases (asset_id, source_name, alias_symbol)
SELECT
    (SELECT asset_id FROM forge.assets WHERE canonical_symbol = 'BTC'),
    'source_canonical_name',
    'BTCUSDT';
-- Repeat for each instrument the source covers.
```

**Step 5 — Verification:**
```sql
-- Confirm source exists with correct redistribution flag
SELECT canonical_name, tos_status, redistribution, commercial_use
FROM forge.source_catalog
WHERE canonical_name = 'source_canonical_name';
-- Verify redistribution = NULL (unaudited) or explicit true/false as expected.

-- Confirm lineage rows exist
SELECT mc.canonical_name AS metric, ml.is_primary
FROM forge.metric_lineage ml
JOIN forge.metric_catalog mc ON ml.metric_id = mc.metric_id
JOIN forge.source_catalog sc ON ml.source_id = sc.source_id
WHERE sc.canonical_name = 'source_canonical_name'
ORDER BY mc.canonical_name;
-- Returns one row per metric this source provides.

-- Confirm Dagster will see the new assets at next startup
-- (no query — Dagster reads metric_catalog + metric_lineage at code server startup)
-- Restart empire_dagster_code after adding new metric/source lineage rows.
```

---

## KNOWN GAPS WITH DOCUMENTED PLANS

| Gap | V1 handling | Resolution trigger |
|---|---|---|
| `defi.lending.utilization_rate` | Proxy: borrow/supply TVL ratio from DeFiLlama. Stored under canonical name. Methodology field documents proxy. | v1.1 milestone — Aave/Compound subgraph adapter |
| Options data (Deribit) | `delta_skew_25`, `iv_1w`, `iv_1m` null-propagate in v1. Pillar confidence decreases. Signal still serves. | v1.1 milestone — Deribit adapter |
| Exchange flows beyond 18 instruments | Explorer/Etherscan limited to ETH + ARB, 9 exchanges. Accept for v1. | v1.1 milestone — additional on-chain sources |
| BTC directional exchange flows | BTC inflow/outflow by exchange not covered by any v1 source. CoinMetrics provides transfer volume only, not exchange-specific directional flows. Null-propagates in Capital Flows pillar for BTC. | v1.1 milestone — CryptoQuant (parked, paid) or dedicated on-chain address tracking |
| `macro.credit.hy_oas` FRED adapter | In metric catalog and feature catalog. Not in FRED adapter in legacy system. FRED series: `BAMLH0A0HYM2`. | Phase 1 pre-condition — add before FRED adapter build begins |
| CoinMetrics redistribution | `redistribution = false`. Internal signal use only. | Phase 6 ToS audit |
| SoSoValue redistribution | `redistribution = false`. Non-commercial confirmed. | v2 data product launch — paid tier evaluation or source replacement |
| BLC-01 rsync routine | Pull routine from Server2 to proxmox unbuilt. BLC-01 data unavailable in new system until built. | Phase 1 |
| `macro.cb.boj_total_assets` PBOC equivalent | BOJ confirmed in FRED (JPNASSETS). PBOC: evaluate FRED availability during Phase 1 FRED adapter build. | During Phase 1 FRED adapter build |
| BGeometrics signal_pillar assignment | MVRV, SOPR, NUPL, Puell currently unassigned to pillar. Structural Risk pillar (REM-24) will consume them. | Structural Risk pillar design |

---

## PHASE 0 GATE CHECKLIST

The Phase 0 gate does not pass until every item below is verified. No
self-certification. Run each verification query and record the result.

```sql
-- 1. All ten catalog tables exist in forge schema
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'forge'
ORDER BY table_name;
-- Expected: assets, asset_aliases, venues, instruments, source_catalog,
-- metric_catalog, metric_lineage, event_calendar, supply_events,
-- adjustment_factors, collection_events, instrument_metric_coverage

-- 2. Source catalog: 10 rows
SELECT COUNT(*) FROM forge.source_catalog;
-- Expected: 10

-- 3. Metric catalog: count should match seed data above
SELECT COUNT(*) FROM forge.metric_catalog WHERE status = 'active';
-- Expected: ≥ 50 (exact count from seed data above)

-- 4. Redistribution flags set correctly on critical sources
SELECT canonical_name, redistribution
FROM forge.source_catalog
WHERE canonical_name IN ('sosovalue', 'coinmetrics');
-- Expected: both rows with redistribution = false

-- 5. No time series columns in PostgreSQL forge schema
SELECT column_name, table_name
FROM information_schema.columns
WHERE table_schema = 'forge'
  AND column_name IN ('observed_at', 'value', 'value_numeric', 'ingested_at');
-- Expected: zero rows
```

```sql
-- ClickHouse verification

-- 6. Three objects exist in forge database
SHOW TABLES FROM forge;
-- Expected: dead_letter, observations, current_values

-- 7. observations engine is correct
SELECT engine FROM system.tables
WHERE database = 'forge' AND name = 'observations';
-- Expected: ReplacingMergeTree

-- 8. Test write and read round-trip
INSERT INTO forge.observations VALUES (
    'spot.price.close_usd', 'BTC', 'tiingo',
    now64(), now64(), 50000.0, 1
);
SELECT metric_id, instrument_id, value
FROM forge.observations FINAL
WHERE metric_id = 'spot.price.close_usd' AND instrument_id = 'BTC'
ORDER BY observed_at DESC LIMIT 1;
-- Expected: one row with value = 50000.0

-- 9. Dead letter test write
INSERT INTO forge.dead_letter VALUES (
    'tiingo', 'spot.price.close_usd', 'BTC',
    '{"raw": "test"}', 'Test rejection', 'RANGE_VIOLATION',
    now64(), now64()
);
SELECT count() FROM forge.dead_letter WHERE source_id = 'tiingo';
-- Expected: 1

-- 10. current_values MV reflects the test write
SELECT argMaxMerge(latest_value) AS value
FROM forge.current_values
WHERE metric_id = 'spot.price.close_usd' AND instrument_id = 'BTC'
GROUP BY metric_id, instrument_id;
-- Expected: 50000.0

-- 11. PIT query pattern returns correct result (Pattern 3)
-- Insert a revision (same observed_at, higher data_version)
INSERT INTO forge.observations VALUES (
    'spot.price.close_usd', 'BTC', 'tiingo',
    (SELECT observed_at FROM forge.observations
     WHERE metric_id = 'spot.price.close_usd' AND instrument_id = 'BTC'
     ORDER BY observed_at DESC LIMIT 1),
    now64(), 51000.0, 2
);
-- PIT query as_of before revision should return original value
-- PIT query as_of after revision should return revised value
-- (exact timestamps depend on test execution time — verify manually)
```

**Phase 0 gate passes when all 11 checks return expected results.**
**Phase 1 does not begin until architect confirms Phase 0 gate.**

---

*Document authored: 2026-03-05. Supersedes all prior thread_4 versions.*
*Locked decisions require architect approval to reopen.*
*Next document: thread_5_collection.md rewrite (Phase 1 scope).*
