"""Dagster asset for empire_to_forge_sync — the EDS->FTB bridge.

Reads empire.observations for promoted metrics, validates against
forge.metric_catalog, writes to forge.observations with source_id='eds_derived'.
Uses cursor-based incremental sync (watermark on ingested_at).
"""
from datetime import UTC, datetime

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from ftb.sync.bridge import build_empire_query, map_empire_to_forge
from ftb.validation.core import Observation
from ftb.validation.expectations import validate_with_ge
from ftb.writers.collection import write_collection_event
from ftb.writers.silver import DeadLetterRow, write_dead_letter, write_observations


def validate_and_split(
    observations: list[Observation],
    metric_catalog: dict[str, dict],
    instrument_set: set[str],
) -> tuple[list[Observation], list[DeadLetterRow], dict]:
    """Validate observations using Great Expectations and split into valid + dead letter.

    Returns (valid_observations, dead_letter_rows, checkpoint_summary).
    """
    return validate_with_ge(observations, metric_catalog, instrument_set)


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


def _load_watermark(ch_writer, metric_ids: list[str]) -> datetime | None:
    """Get sync watermark from forge.observations, aware of new metrics.

    Uses ch_writer which has SELECT on forge tables (per deploy setup).
    Returns None if any promoted metric has no forge rows yet — this triggers
    a full sync. ReplacingMergeTree deduplicates re-inserted rows.
    """
    result = ch_writer.query(
        "SELECT metric_id, max(ingested_at) as wm "
        "FROM forge.observations WHERE source_id = 'eds_derived' "
        "GROUP BY metric_id"
    )
    watermarks = {row[0]: row[1] for row in result.result_rows}

    # If any promoted metric has no forge data, full sync needed
    for mid in metric_ids:
        if mid not in watermarks:
            return None

    if not watermarks:
        return None

    # All metrics present — use minimum watermark so no data is missed
    min_wm = min(watermarks[mid] for mid in metric_ids)

    # Normalize: CH may return naive or tz-aware datetime
    if hasattr(min_wm, 'tzinfo') and min_wm.tzinfo is None:
        min_wm = min_wm.replace(tzinfo=UTC)
    if min_wm <= datetime(1970, 1, 2, tzinfo=UTC):
        return None
    return min_wm


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

    Incremental: watermark derived from max(ingested_at) in forge.observations
    for source_id='eds_derived'. Self-healing — no external cursor state.
    """
    started_at = datetime.now(UTC)

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

    # 2. Read watermark from forge.observations
    watermark = _load_watermark(context.resources.ch_writer, metric_ids)
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

    # 5. Validate + split (GE-powered)
    valid_obs, dead_letters, checkpoint = validate_and_split(observations, metric_catalog, instrument_set)
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

    # 8. Report watermark (derived from forge.observations, not stored externally)
    max_ingested = max(row["ingested_at"] for row in rows)

    return Output(
        value=None,
        metadata={
            "observations_written": MetadataValue.int(written),
            "observations_rejected": MetadataValue.int(rejected),
            "watermark": MetadataValue.text(max_ingested.isoformat()),
            "metrics_synced": MetadataValue.text(
                ", ".join(sorted({o.metric_id for o in valid_obs}))
            ),
            "ge_checkpoint": MetadataValue.text(str(checkpoint)),
        },
    )
