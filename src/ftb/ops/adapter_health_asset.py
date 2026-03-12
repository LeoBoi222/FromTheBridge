"""Dagster asset: ftb_ops.adapter_health — per-source freshness monitoring.

One materialization evaluates ALL active sources from forge.source_catalog.
Emits per-source health as structured metadata.

Uses ch_ops_reader (Rule 2 compliant) and pg_forge_reader.

Source of truth: FromTheBridge_design_v4.0.md §Solo Operator Operations
"""
from datetime import timedelta

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from ftb.ops.health import check_source_health


def _load_source_expectations(pg_reader) -> list[dict]:
    """Load active sources with their cadence and expected metrics/instruments.

    Returns list of dicts with source_id, cadence_hours, expected_metrics,
    expected_instruments.
    """
    sources = []
    with pg_reader.cursor() as cur:
        # Get active sources
        cur.execute(
            "SELECT source_id FROM forge.source_catalog WHERE is_active = true"
        )
        source_ids = [row[0] for row in cur.fetchall()]

    with pg_reader.cursor() as cur:
        for source_id in source_ids:
            # Count metrics listing this source
            cur.execute(
                "SELECT count(*) FROM forge.metric_catalog "
                "WHERE %s = ANY(sources) AND status = 'active'",
                (source_id,),
            )
            expected_metrics = cur.fetchone()[0]

            # Count instruments mapped to this source
            cur.execute(
                "SELECT count(*) FROM forge.instrument_source_map "
                "WHERE source_id = %s",
                (source_id,),
            )
            expected_instruments = cur.fetchone()[0]

            # Get cadence from metric_catalog (use min cadence for this source)
            cur.execute(
                "SELECT min(cadence) FROM forge.metric_catalog "
                "WHERE %s = ANY(sources) AND status = 'active'",
                (source_id,),
            )
            cadence_row = cur.fetchone()
            cadence_interval = cadence_row[0] if cadence_row else None

            # Convert interval to hours (PG returns timedelta)
            if cadence_interval is not None and isinstance(cadence_interval, timedelta):
                cadence_hours = cadence_interval.total_seconds() / 3600
            else:
                cadence_hours = 24.0  # default fallback

            sources.append({
                "source_id": source_id,
                "cadence_hours": cadence_hours,
                "expected_metrics": expected_metrics,
                "expected_instruments": expected_instruments,
            })

    return sources


def _get_source_obs_stats(ch_ops_reader, source_id: str) -> dict:
    """Get observation stats for a source from forge.observations.

    Returns dict with last_observation_at, observations_24h,
    metric_ids_observed, instrument_ids_observed.
    """
    result = ch_ops_reader.query(
        "SELECT "
        "  max(observed_at) as last_obs, "
        "  countIf(observed_at > now() - INTERVAL 1 DAY) as obs_24h, "
        "  uniqExactIf(metric_id, observed_at > now() - INTERVAL 30 DAY) as metrics, "
        "  uniqExactIf(instrument_id, observed_at > now() - INTERVAL 30 DAY "
        "    AND instrument_id != '') as instruments "
        "FROM forge.observations "
        "WHERE source_id = %(source_id)s",
        parameters={"source_id": source_id},
    )
    row = result.result_rows[0]
    last_obs = row[0]
    # ClickHouse returns '1970-01-01 00:00:00' for max() on empty set
    if last_obs is not None and hasattr(last_obs, 'year') and last_obs.year <= 1970:
        last_obs = None
    return {
        "last_observation_at": last_obs,
        "observations_24h": row[1],
        "metric_ids_observed": row[2],
        "instrument_ids_observed": row[3],
    }


def _get_source_dead_letter_24h(ch_ops_reader, source_id: str) -> int:
    """Dead letter count in last 24h for a source."""
    result = ch_ops_reader.query(
        "SELECT count(*) FROM forge.dead_letter "
        "WHERE source_id = %(source_id)s "
        "AND rejected_at > now() - INTERVAL 1 DAY",
        parameters={"source_id": source_id},
    )
    return result.result_rows[0][0]


@asset(
    key_prefix=["ftb_ops"],
    name="adapter_health",
    required_resource_keys={"ch_ops_reader", "pg_forge_reader"},
    metadata={"kind": "health_check", "target": "all_sources"},
)
def adapter_health(context: AssetExecutionContext):
    """Monitor per-source data freshness, coverage, and dead letter rates.

    Evaluates every active source in source_catalog against its catalog
    expectations. Sources with no data yet are flagged yellow (not red)
    since EDS may not have deployed their adapters yet.
    """
    pg = context.resources.pg_forge_reader
    ch = context.resources.ch_ops_reader

    sources = _load_source_expectations(pg)
    results = {}
    overall_severity = "green"
    red_count = 0

    for src in sources:
        sid = src["source_id"]
        stats = _get_source_obs_stats(ch, sid)
        dead_24h = _get_source_dead_letter_24h(ch, sid)

        health = check_source_health(
            source_id=sid,
            last_observation_at=stats["last_observation_at"],
            observations_24h=stats["observations_24h"],
            dead_letter_24h=dead_24h,
            metric_ids_observed=stats["metric_ids_observed"],
            metric_ids_expected=src["expected_metrics"],
            instrument_ids_observed=stats["instrument_ids_observed"],
            instrument_ids_expected=src["expected_instruments"],
            cadence_hours=src["cadence_hours"],
        )

        results[sid] = health.to_metadata()
        context.log.info(f"adapter_health[{sid}]: {health.severity}")

        if health.severity == "red":
            red_count += 1
        if health.severity == "red" and overall_severity != "red":
            overall_severity = "red"
        elif health.severity == "yellow" and overall_severity == "green":
            overall_severity = "yellow"

    # Red alert: >3 sources simultaneously zero data (v4.0 red trigger)
    if red_count >= 3:
        overall_severity = "red"

    return Output(
        value=None,
        metadata={
            "overall_severity": MetadataValue.text(overall_severity),
            "source_count": MetadataValue.int(len(sources)),
            "red_count": MetadataValue.int(red_count),
            "per_source": MetadataValue.text(str(results)),
        },
    )
