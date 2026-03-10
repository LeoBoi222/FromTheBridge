# Tiingo Adapter Design

**Date:** 2026-03-10
**Scope:** First adapter build — Tiingo crypto OHLCV
**Status:** Approved

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Code organization | Composition (Option C) | Shared writers, adapter owns orchestration. Scales across sources without rigid inheritance. |
| OHLCV composite | Drop from Silver (Option A) | Scalar metrics (close_usd, volume_usd_24h) carry signal. Bronze preserves full shape. |
| Instrument resolution | New mapping table (Option B) | `forge.instrument_source_map` — central lookup, adapters query at init. |
| Backfill/live | Dagster daily partitions | `DailyPartitionsDefinition(start_date="2014-01-01")`. Backfill = historical partitions. Live = 6h schedule. |
| Fetch pattern | Single fetch, triple extract | One API call returns OHLCV. Extract close_usd + volume_usd_24h as Silver observations. |
| Tiingo lifecycle | Transitional | EDS replaces live spot/OHLCV post-completion. Historical backfill is permanent value. Evaluate at Phase 2 gate. |

## Module Structure

```
src/ftb/
  adapters/
    tiingo.py              # Fetch, map, orchestrate (calls writers)
  writers/
    bronze.py              # write_bronze() — Parquet to MinIO
    silver.py              # write_observations(), write_dead_letter() — ClickHouse
    collection.py          # write_collection_event() — PostgreSQL
  validation/
    core.py                # validate_observation() — per-row checks
  resources.py             # Dagster resource definitions (MinIO, CH, PG clients)
  definitions.py           # Asset registration
```

## New Catalog Table

```sql
CREATE TABLE forge.instrument_source_map (
    instrument_id TEXT NOT NULL REFERENCES forge.instruments(instrument_id),
    source_id     TEXT NOT NULL REFERENCES forge.source_catalog(source_id),
    source_symbol TEXT NOT NULL,
    PRIMARY KEY (instrument_id, source_id)
);
```

Seeded with Tiingo mappings: btcusd → btc_usd_spot, ethusd → eth_usd_spot, solusd → sol_usd_spot.

## Dagster Asset

```python
@asset(
    name="collect_tiingo_price",
    partitions_def=DailyPartitionsDefinition(start_date="2014-01-01"),
    metadata={"source_id": "tiingo", "cadence_hours": 6},
)
```

Single asset covers 2 Silver metrics. Partition key = date. Live schedule via AutomationCondition.

## Data Flow (per partition)

1. **Fetch:** GET /tiingo/crypto/prices with startDate/endDate per partition, resampleFreq=1day, for each instrument. Auth via TIINGO_API_KEY from /opt/empire/.env.
2. **Bronze write:** Raw JSON → Parquet to bronze-hot/tiingo/{YYYY-MM-DD}/price/data.parquet.
3. **Instrument resolution:** Query instrument_source_map at init. Unknown tickers → dead letter (UNKNOWN_INSTRUMENT).
4. **Metric extraction:** Per instrument per timestamp, extract 2 observations:
   - price.spot.close_usd → value = close
   - price.spot.volume_usd_24h → value = volumeNotional
5. **Validation:** Non-null value, metric_id in catalog, instrument_id in catalog. No range bounds for price metrics.
6. **Silver write:** Batch INSERT valid rows to forge.observations via ch_writer.
7. **Dead letter:** Rejected rows → forge.dead_letter with rejection_code + raw payload.
8. **Collection event:** Summary to forge.collection_events (observations_written, rejected, instruments covered).
9. **Return:** AssetMaterialization with metadata for Dagster UI.

## Shared Writers

**bronze.py** — write_bronze(client, bucket, source_id, date, metric_domain, payload) → path
- PyArrow table → Parquet to MinIO. Partition path: {bucket}/{source_id}/{date}/{metric_domain}/data.parquet.

**silver.py** — write_observations(client, rows) → int, write_dead_letter(client, rows) → int
- Batch INSERT via clickhouse-connect, ch_writer credentials. Returns row count.

**collection.py** — write_collection_event(conn, source_id, asset_name, stats) → None
- INSERT to forge.collection_events in PostgreSQL.

## Validation (core.py)

```python
def validate_observation(obs, metric_catalog, instrument_set) -> tuple[bool, str | None]:
    """Returns (is_valid, rejection_code_or_none)"""
```

Checks: non-null value (if is_nullable=false), metric_id in catalog, instrument_id in catalog, range bounds (if defined). Stateless, per-row.

## Dagster Resources

```python
resources = {
    "minio_client": MinIOResource(...),
    "ch_client": ClickHouseResource(host=..., port=9000, user="ch_writer", ...),
    "pg_conn": PostgresResource(host=..., port=5433, user="forge_user", dbname="crypto_structured"),
}
```

Credentials from environment/secrets at Dagster startup.

## Dependencies (pyproject.toml)

- httpx
- clickhouse-connect
- minio
- pyarrow

## Not Included

- Great Expectations integration (separate concern, built after first end-to-end works)
- Equity support (crypto only — asset_class branching deferred)
- Full Iceberg catalog registration (Parquet with Iceberg-compatible partitioning first)
- price.spot.ohlcv Silver writes (Bronze only)
