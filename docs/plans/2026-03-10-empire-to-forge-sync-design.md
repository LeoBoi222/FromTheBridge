# empire_to_forge_sync — Design

**Date:** 2026-03-10
**Source:** FromTheBridge_design_v4.0.md §Sync Layer, §Metric Promotion, §EDS Cohesion
**Status:** Design complete, ready for implementation

## Purpose

One-directional sync bridge: `empire.observations` → `forge.observations`. FTB's sole
authorized reader of EDS data. Writes with `source_id='eds_derived'`. Metric promotion
is manual — only metrics already in `forge.metric_catalog` are synced.

## Schema Mapping

| empire.observations | forge.observations | Mapping |
|--------------------|--------------------|---------|
| metric_id (String) | metric_id (String) | Direct — EDS v1.1.2 adopts FTB canonical names (C3) |
| instrument_id (String, default `'__market__'`) | instrument_id (Nullable(String)) | `'__market__'` → NULL (C2 resolution) |
| source_id (String) | source_id (String) | Overwritten to `'eds_derived'` |
| observed_at (DateTime64) | observed_at (DateTime64) | Direct |
| ingested_at (DateTime64) | ingested_at (DateTime64) | Set to sync wall-clock time (not empire's ingested_at) |
| value (Float64) | value (Nullable(Float64)) | Direct |
| data_version (UInt64) | data_version (UInt64) | Always 1 for fresh sync |
| chain_id | — | Dropped (forge schema doesn't have it) |
| block_height | — | Dropped |
| derivation_version | — | Dropped |

## Incremental Strategy

Watermark-based on `empire.observations.ingested_at`. Each run:
1. Read last successful watermark from Dagster asset metadata (via `context.instance`)
2. Query `empire.observations WHERE ingested_at > :watermark AND metric_id IN (:promoted_metrics)`
3. On success, new watermark = max `ingested_at` from queried batch
4. First run (no watermark): full sync of all promoted metrics

Dagster cursor (`context.cursor`) persists the watermark across runs — no external
cursor table needed.

## Components

### 1. SQL Migration: `eds_derived` source

```sql
-- db/migrations/postgres/0005_eds_derived_source.sql
INSERT INTO forge.source_catalog (
    source_id, source_name, source_type, api_base_url,
    rate_limit_per_minute, is_active, redistribution_allowed,
    requires_api_key, api_key_secret_path, notes
) VALUES (
    'eds_derived', 'EDS Derived Metrics', 'internal',
    NULL, NULL, true, true, false, NULL,
    'Metrics derived by EDS and synced via empire_to_forge_sync'
);
```

### 2. ClickHouse Reader Resource: `ch_empire_reader`

New Dagster resource in `resources.py`. Read-only credentials on `empire` database.
Requires a new ClickHouse user `ch_empire_reader` with SELECT-only on `empire.observations`.

```sql
-- ClickHouse user creation (ops task)
CREATE USER IF NOT EXISTS ch_empire_reader
    IDENTIFIED BY '<secret>'
    SETTINGS PROFILE 'readonly_profile';
GRANT SELECT ON empire.observations TO ch_empire_reader;
```

Docker secret: `/run/secrets/ch_empire_reader`.

### 3. Business Logic: `src/ftb/sync/bridge.py`

Pure functions, no Dagster imports:
- `query_empire_observations(client, metric_ids, watermark) → list[dict]`
- `map_empire_to_forge(rows, promoted_metrics) → list[Observation]`
  - Maps `instrument_id='__market__'` → `None`
  - Sets `source_id='eds_derived'`
  - Drops chain_id, block_height, derivation_version

### 4. Dagster Asset: `src/ftb/sync/sync_asset.py`

```
@asset(
    name="empire_to_forge_sync",
    required_resource_keys={"ch_empire_reader", "ch_writer", "pg_forge", "pg_forge_reader"},
    metadata={"source_id": "eds_derived", "cadence_hours": 6},
)
def empire_to_forge_sync(context: AssetExecutionContext):
```

**Flow:**
1. Load promoted metric_ids from `forge.metric_catalog` (via pg_forge_reader)
2. Load instrument_set from `forge.instruments` (via pg_forge_reader)
3. Read watermark from `context.cursor`
4. Query `empire.observations` for promoted metrics since watermark (via ch_empire_reader)
5. Map empire rows → Observation dataclasses
6. Validate each observation via `validate_observation()`
7. Split into valid + dead_letter
8. Write valid to `forge.observations` (via ch_writer — existing Silver writer)
9. Write rejected to `forge.dead_letter` (via ch_writer — existing dead letter writer)
10. Write collection event to `forge.collection_events` (via pg_forge)
11. Update cursor to max(ingested_at) from batch
12. Return Output with metadata (observations_written, observations_rejected, watermark)

**Schedule:** 6h via ScheduleDefinition, cron `"15 */6 * * *"`.

**No partitions.** This is a streaming cursor asset, not date-partitioned. Each run
picks up where the last left off.

### 5. Validation

Reuse existing `validate_observation()` from `src/ftb/validation/core.py`. Same rules:
- metric_id must exist in forge.metric_catalog
- instrument_id must exist in forge.instruments (if non-null)
- value nullability check against catalog
- value range check against catalog bounds

### 6. Writers

Reuse all existing writers:
- `writers/silver.py` → `write_observations()`
- `writers/silver.py` → `write_dead_letter()`
- `writers/collection.py` → `write_collection_event()`

No new writer code needed.

## Metric Promotion Protocol

Per v4.0: promotion is manual, not auto-discovered.

1. Add row to `forge.metric_catalog` via SQL migration (source includes `eds_derived`)
2. Verify `eds_derived` in `forge.source_catalog` (one-time, this design handles it)
3. Sync asset picks it up on next run — queries empire for that metric_id
4. Prerequisites: architect approval + 7-day EDS freshness + <1% dead-letter rate

## What's Currently Promotable

`empire.observations` has 32 metrics from 3 sources (6,589 rows). The sync asset will
only flow metrics that already exist in `forge.metric_catalog`. Currently 0 metrics
reference `eds_derived` as a source — first promotions happen after this asset is
deployed and validated.

## Files to Create/Modify

| File | Action |
|------|--------|
| `db/migrations/postgres/0005_eds_derived_source.sql` | Create |
| `db/migrations/clickhouse/0003_empire_reader_user.sql` | Create |
| `src/ftb/sync/__init__.py` | Create |
| `src/ftb/sync/bridge.py` | Create |
| `src/ftb/sync/sync_asset.py` | Create |
| `src/ftb/resources.py` | Modify — add `ch_empire_reader` resource |
| `src/ftb/definitions.py` | Modify — register asset + schedule |
| `docker-compose.yml` | Modify — add `ch_empire_reader` secret mount |
| `secrets/` | Ops — generate ch_empire_reader password |
| `tests/test_sync_bridge.py` | Create |
