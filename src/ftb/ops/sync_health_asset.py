"""Dagster asset: ftb_ops.sync_health — monitors empire_to_forge_sync.

Checks freshness, dead letter rate, metric coverage against catalog.
Uses ch_ops_reader (Rule 2 compliant) and pg_forge_reader.

Source of truth: FromTheBridge_design_v4.0.md §Solo Operator Operations
"""
from dagster import AssetExecutionContext, MetadataValue, Output, asset

from ftb.ops.health import check_sync_health


def _get_last_sync_event(pg_reader) -> dict | None:
    """Most recent collection_event for eds_derived."""
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT completed_at, status, observations_written, "
            "observations_rejected, metrics_covered "
            "FROM forge.collection_events "
            "WHERE source_id = 'eds_derived' "
            "ORDER BY completed_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "completed_at": row[0],
            "status": row[1],
            "observations_written": row[2],
            "observations_rejected": row[3],
            "metrics_covered": row[4],
        }


def _get_promoted_metric_count(pg_reader) -> int:
    """Count metrics in catalog with eds_derived as source."""
    with pg_reader.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM forge.metric_catalog "
            "WHERE 'eds_derived' = ANY(sources) AND status = 'active'"
        )
        return cur.fetchone()[0]


def _get_dead_letter_24h(ch_ops_reader) -> int:
    """Dead letters in last 24h for eds_derived."""
    result = ch_ops_reader.query(
        "SELECT count(*) FROM forge.dead_letter "
        "WHERE source_id = 'eds_derived' "
        "AND rejected_at > now() - INTERVAL 1 DAY"
    )
    return result.result_rows[0][0]


def _get_observation_stats(ch_ops_reader) -> tuple[int, int]:
    """Total obs count and distinct metric count for eds_derived.

    Returns (total_count, distinct_metrics).
    """
    result = ch_ops_reader.query(
        "SELECT count(*) as cnt, uniqExact(metric_id) as metrics "
        "FROM forge.observations WHERE source_id = 'eds_derived'"
    )
    row = result.result_rows[0]
    return row[0], row[1]


@asset(
    key_prefix=["ftb_ops"],
    name="sync_health",
    required_resource_keys={"ch_ops_reader", "pg_forge_reader"},
    metadata={"kind": "health_check", "target": "empire_to_forge_sync"},
)
def sync_health(context: AssetExecutionContext):
    """Monitor empire_to_forge_sync freshness, coverage, and dead letter rate."""
    pg = context.resources.pg_forge_reader
    ch = context.resources.ch_ops_reader

    last_event = _get_last_sync_event(pg)
    promoted_count = _get_promoted_metric_count(pg)
    dead_24h = _get_dead_letter_24h(ch)
    total_obs, metrics_with_data = _get_observation_stats(ch)

    result = check_sync_health(
        last_event=last_event,
        dead_letter_24h=dead_24h,
        total_observations=total_obs,
        promoted_metric_count=promoted_count,
        metrics_with_data=metrics_with_data,
    )

    meta = result.to_metadata()
    context.log.info(f"sync_health: severity={result.severity} fields={result.fields}")

    return Output(
        value=None,
        metadata={k: MetadataValue.text(str(v)) for k, v in meta.items()},
    )
