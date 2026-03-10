"""Dagster asset for empire_to_forge_sync — the EDS->FTB bridge.

Reads empire.observations for promoted metrics, validates against
forge.metric_catalog, writes to forge.observations with source_id='eds_derived'.
Uses cursor-based incremental sync (watermark on ingested_at).
"""
import json
from datetime import datetime, timezone

from dagster import asset, AssetExecutionContext, Output, MetadataValue

from ftb.sync.bridge import map_empire_to_forge, build_empire_query
from ftb.validation.core import Observation, validate_observation
from ftb.writers.silver import DeadLetterRow, write_observations, write_dead_letter
from ftb.writers.collection import write_collection_event


def validate_and_split(
    observations: list[Observation],
    metric_catalog: dict[str, dict],
    instrument_set: set[str],
) -> tuple[list[Observation], list[DeadLetterRow]]:
    """Validate observations and split into valid + dead letter lists."""
    valid = []
    dead = []
    for obs in observations:
        result = validate_observation(obs, metric_catalog, instrument_set)
        if result.is_valid:
            valid.append(obs)
        else:
            dead.append(
                DeadLetterRow(
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
                )
            )
    return valid, dead


def _load_promoted_metrics(pg_reader) -> dict[str, dict]:
    """Load metric_catalog rows that list eds_derived as a source."""
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT metric_id, is_nullable, expected_range_low, expected_range_high "
            "FROM forge.metric_catalog "
            "WHERE 'eds_derived' = ANY(sources) AND status = 'active'"
        )
        return {
            row[0]: {
                "is_nullable": row[1],
                "expected_range_low": row[2],
                "expected_range_high": row[3],
            }
            for row in cur.fetchall()
        }


def _load_instrument_set(pg_reader) -> set[str]:
    """Load instrument IDs from forge.instruments."""
    with pg_reader.cursor() as cur:
        cur.execute("SELECT instrument_id FROM forge.instruments")
        return {row[0] for row in cur.fetchall()}


def _query_empire(ch_reader, metric_ids: list[str], watermark: datetime | None) -> list[dict]:
    """Query empire.observations for promoted metrics since watermark."""
    sql, params = build_empire_query(metric_ids, watermark)
    result = ch_reader.query(sql, parameters=params)
    columns = result.column_names
    return [dict(zip(columns, row)) for row in result.result_rows]


@asset(
    name="empire_to_forge_sync",
    required_resource_keys={"ch_empire_reader", "ch_writer", "pg_forge", "pg_forge_reader"},
    metadata={"source_id": "eds_derived", "cadence_hours": 6},
)
def empire_to_forge_sync(context: AssetExecutionContext):
    """Sync promoted metrics from empire.observations to forge.observations.

    Incremental: uses Dagster cursor to track last ingested_at watermark.
    """
    started_at = datetime.now(timezone.utc)

    # 1. Load catalog
    metric_catalog = _load_promoted_metrics(context.resources.pg_forge_reader)
    if not metric_catalog:
        context.log.info("No promoted metrics found for eds_derived — nothing to sync.")
        return Output(
            value=None,
            metadata={
                "observations_written": MetadataValue.int(0),
                "status": MetadataValue.text("no_promoted_metrics"),
            },
        )

    instrument_set = _load_instrument_set(context.resources.pg_forge_reader)
    metric_ids = list(metric_catalog.keys())

    # 2. Read watermark
    cursor_str = context.cursor
    watermark = datetime.fromisoformat(cursor_str) if cursor_str else None
    context.log.info(f"Sync watermark: {watermark or 'FIRST RUN (full sync)'}")

    # 3. Query empire
    rows = _query_empire(context.resources.ch_empire_reader, metric_ids, watermark)
    context.log.info(f"Queried {len(rows)} rows from empire.observations")
    if not rows:
        return Output(
            value=None,
            metadata={
                "observations_written": MetadataValue.int(0),
                "status": MetadataValue.text("no_new_rows"),
            },
        )

    # 4. Map to forge Observations
    observations = map_empire_to_forge(rows, set(metric_ids))

    # 5. Validate + split
    valid_obs, dead_letters = validate_and_split(observations, metric_catalog, instrument_set)
    context.log.info(f"Valid: {len(valid_obs)}, Dead letter: {len(dead_letters)}")

    # 6. Write Silver
    written = write_observations(context.resources.ch_writer, valid_obs)
    rejected = write_dead_letter(context.resources.ch_writer, dead_letters)

    # 7. Collection event
    write_collection_event(
        context.resources.pg_forge,
        source_id="eds_derived",
        status="completed" if not dead_letters else "partial",
        started_at=started_at,
        observations_written=written,
        observations_rejected=rejected,
        metrics_covered=list({o.metric_id for o in valid_obs}),
        instruments_covered=list({o.instrument_id for o in valid_obs if o.instrument_id}),
    )

    # 8. Update watermark to max ingested_at from batch
    max_ingested = max(row["ingested_at"] for row in rows)
    context.update_cursor(max_ingested.isoformat())
    context.log.info(f"Updated watermark to {max_ingested.isoformat()}")

    return Output(
        value=None,
        metadata={
            "observations_written": MetadataValue.int(written),
            "observations_rejected": MetadataValue.int(rejected),
            "watermark": MetadataValue.text(max_ingested.isoformat()),
            "metrics_synced": MetadataValue.text(
                ", ".join(sorted({o.metric_id for o in valid_obs}))
            ),
        },
    )
