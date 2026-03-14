"""Bronze expiry audit — daily check for at-risk partitions nearing 90-day hot expiry.

Design: v4.0 §Layer 1: Landing Zone (C2)
Runs daily after the archive job. Warns about partitions within 5 days of 90-day expiry
that have NOT been archived.
"""

from datetime import date, timedelta

from dagster import AssetExecutionContext, MetadataValue, asset

from ftb.archive.partition_discovery import discover_partitions_duckdb
from ftb.writers.bronze import BRONZE_HOT_TABLE


def find_at_risk_partitions(
    hot_catalog,
    pg_conn,
    today: date,
    table_name: str = BRONZE_HOT_TABLE,
) -> tuple[list[dict], float]:
    """Find hot partitions within 5 days of 90-day expiry that aren't archived.

    At-risk = partition_date < today - 85 AND not in bronze_archive_log.
    Uses DuckDB partition discovery for fast metadata scanning.

    Returns (at_risk_list, discovery_elapsed_ms).
    """
    cutoff = today - timedelta(days=85)
    cutoff_str = cutoff.isoformat()

    # Fast partition discovery via DuckDB over Iceberg metadata
    hot_partitions_list, elapsed_ms = discover_partitions_duckdb(
        hot_catalog,
        table_name,
        partition_date_filter=f"partition_date < '{cutoff_str}'",
    )
    hot_partitions = {
        (p["source_id"], p["metric_id"], p["partition_date"])
        for p in hot_partitions_list
    }

    if not hot_partitions:
        return [], elapsed_ms

    # Check which ones are already archived
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT source_id, metric_id, partition_date::text
            FROM forge.bronze_archive_log
            WHERE partition_date < %s
            """,
            (cutoff,),
        )
        archived = {(r[0], r[1], r[2]) for r in cur.fetchall()}

    at_risk = hot_partitions - archived
    return [
        {"source_id": s, "metric_id": m, "partition_date": d}
        for s, m, d in sorted(at_risk)
    ], elapsed_ms


@asset(
    group_name="bronze_archive",
    required_resource_keys={"iceberg_catalog_hot", "pg_forge"},
    deps=["bronze_cold_archive"],
)
def bronze_expiry_audit(context: AssetExecutionContext) -> None:
    """Audit bronze-hot for partitions at risk of expiry without archive.

    Flags partitions with partition_date < today-85 (5 days before 90-day expiry)
    that are NOT in bronze_archive_log.
    Uses DuckDB over Iceberg metadata for partition discovery (C2 gate criterion).
    """
    today = date.today()
    hot_catalog = context.resources.iceberg_catalog_hot
    pg_conn = context.resources.pg_forge

    at_risk, discovery_ms = find_at_risk_partitions(hot_catalog, pg_conn, today)

    context.add_output_metadata({
        "at_risk_partition_count": MetadataValue.int(len(at_risk)),
        "cutoff_date": MetadataValue.text((today - timedelta(days=85)).isoformat()),
        "partition_discovery_ms": MetadataValue.float(round(discovery_ms, 1)),
    })

    context.log.info(f"Partition discovery completed in {discovery_ms:.1f}ms")

    if at_risk:
        context.log.warning(
            f"ALERT: {len(at_risk)} partitions at risk of expiry without archive!"
        )
        for p in at_risk[:10]:  # Log first 10
            context.log.warning(
                f"  At risk: {p['source_id']}/{p['partition_date']}/{p['metric_id']}"
            )
        if len(at_risk) > 10:
            context.log.warning(f"  ... and {len(at_risk) - 10} more")
    else:
        context.log.info("No partitions at risk of expiry. All clear.")
