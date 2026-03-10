"""Dagster Software-Defined Asset for Tiingo crypto OHLCV collection."""
import json
import logging
from datetime import date, datetime, timezone

from dagster import (
    asset,
    AssetExecutionContext,
    DailyPartitionsDefinition,
    MetadataValue,
    Output,
)

from ftb.adapters.tiingo import (
    extract_observations,
    fetch_tiingo_crypto,
    flatten_price_data,
)
from ftb.validation.core import validate_observation
from ftb.writers.bronze import write_bronze
from ftb.writers.silver import write_observations, write_dead_letter, DeadLetterRow
from ftb.writers.collection import write_collection_event

logger = logging.getLogger(__name__)

TIINGO_PARTITIONS = DailyPartitionsDefinition(start_date="2014-01-01")


@asset(
    name="collect_tiingo_price",
    partitions_def=TIINGO_PARTITIONS,
    required_resource_keys={"ch_writer", "pg_forge", "pg_forge_reader", "minio_bronze", "tiingo_api_key"},
    metadata={"source_id": "tiingo", "cadence_hours": 6},
)
def collect_tiingo_price(context: AssetExecutionContext):
    """Collect Tiingo crypto OHLCV -> Bronze + Silver."""
    partition_date = date.fromisoformat(context.partition_key)
    started_at = datetime.now(timezone.utc)

    # Load symbol map from catalog
    pg_reader = context.resources.pg_forge_reader
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT source_symbol, instrument_id "
            "FROM forge.instrument_source_map WHERE source_id = 'tiingo'"
        )
        symbol_map = {row[0]: row[1] for row in cur.fetchall()}

    # Load metric catalog for validation
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT metric_id, is_nullable, expected_range_low, expected_range_high "
            "FROM forge.metric_catalog WHERE 'tiingo' = ANY(sources)"
        )
        metric_catalog = {
            row[0]: {
                "is_nullable": row[1],
                "expected_range_low": row[2],
                "expected_range_high": row[3],
            }
            for row in cur.fetchall()
        }

    # Load instrument set
    with pg_reader.cursor() as cur:
        cur.execute("SELECT instrument_id FROM forge.instruments WHERE is_active = true")
        instrument_set = {row[0] for row in cur.fetchall()}

    tickers = list(symbol_map.keys())
    if not tickers:
        logger.warning("No Tiingo tickers in instrument_source_map")
        return

    # 1. Fetch from Tiingo API
    api_key = context.resources.tiingo_api_key
    next_date = date.fromordinal(partition_date.toordinal() + 1)
    response_data = fetch_tiingo_crypto(
        api_key=api_key,
        tickers=tickers,
        start_date=partition_date.isoformat(),
        end_date=next_date.isoformat(),
    )

    # 2. Bronze write — raw response as flattened Parquet
    flat_rows = flatten_price_data(response_data)
    bronze_path = write_bronze(
        minio_client=context.resources.minio_bronze,
        bucket="bronze-hot",
        source_id="tiingo",
        partition_date=partition_date,
        metric_domain="price",
        payload=flat_rows,
    )
    logger.info("Bronze written: %s (%d rows)", bronze_path, len(flat_rows))

    # 3. Extract observations
    observations = extract_observations(response_data, symbol_map)

    # 4. Validate
    valid_obs = []
    dead_letters = []
    for obs in observations:
        result = validate_observation(obs, metric_catalog, instrument_set)
        if result.is_valid:
            valid_obs.append(obs)
        else:
            dead_letters.append(DeadLetterRow(
                source_id=obs.source_id,
                metric_id=obs.metric_id,
                instrument_id=obs.instrument_id,
                raw_payload=json.dumps({
                    "metric_id": obs.metric_id,
                    "instrument_id": obs.instrument_id,
                    "value": obs.value,
                    "observed_at": obs.observed_at.isoformat(),
                }),
                rejection_reason=result.rejection_reason or "",
                rejection_code=result.rejection_code or "",
            ))

    # 5. Silver write
    written = write_observations(context.resources.ch_writer, valid_obs)
    rejected = write_dead_letter(context.resources.ch_writer, dead_letters)

    # 6. Collection event
    metrics_covered = list({o.metric_id for o in valid_obs})
    instruments_covered = list({o.instrument_id for o in valid_obs if o.instrument_id})

    write_collection_event(
        context.resources.pg_forge,
        source_id="tiingo",
        status="completed" if not dead_letters else "partial",
        started_at=started_at,
        observations_written=written,
        observations_rejected=rejected,
        metrics_covered=metrics_covered,
        instruments_covered=instruments_covered,
    )

    logger.info(
        "Tiingo collection complete: %d written, %d rejected, partition=%s",
        written, rejected, partition_date,
    )

    return Output(
        value=None,
        metadata={
            "observations_written": MetadataValue.int(written),
            "observations_rejected": MetadataValue.int(rejected),
            "bronze_path": MetadataValue.text(bronze_path),
            "partition_date": MetadataValue.text(partition_date.isoformat()),
            "instruments_covered": MetadataValue.text(", ".join(instruments_covered)),
            "metrics_covered": MetadataValue.text(", ".join(metrics_covered)),
        },
    )
