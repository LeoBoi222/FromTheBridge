"""Bronze expiry audit — daily check for at-risk partitions nearing 90-day hot expiry.

Design: v4.0 §Layer 1: Landing Zone (C2)
Runs daily after the archive job. Warns about partitions within 5 days of 90-day expiry
that have NOT been archived.
"""

from datetime import date, timedelta

from dagster import AssetExecutionContext, MetadataValue, asset

from ftb.writers.bronze import BRONZE_HOT_TABLE, ensure_bronze_table


def find_at_risk_partitions(
    hot_catalog,
    pg_conn,
    today: date,
    table_name: str = BRONZE_HOT_TABLE,
) -> list[dict]:
    """Find hot partitions within 5 days of 90-day expiry that aren't archived.

    At-risk = partition_date < today - 85 AND not in bronze_archive_log.
    """
    cutoff = today - timedelta(days=85)
    cutoff_str = cutoff.isoformat()

    # Get all hot partitions older than cutoff
    table = ensure_bronze_table(hot_catalog, table_name)
    scan = table.scan(
        row_filter=f"partition_date < '{cutoff_str}'",
        selected_fields=("source_id", "metric_id", "partition_date"),
    )

    hot_partitions = set()
    for batch in scan.to_arrow_batch_reader():
        for i in range(batch.num_rows):
            hot_partitions.add((
                batch.column("source_id")[i].as_py(),
                batch.column("metric_id")[i].as_py(),
                batch.column("partition_date")[i].as_py(),
            ))

    if not hot_partitions:
        return []

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
    ]


@asset(
    group_name="bronze_archive",
    required_resource_keys={"iceberg_catalog_hot", "pg_forge"},
    deps=["bronze_cold_archive"],
)
def bronze_expiry_audit(context: AssetExecutionContext) -> None:
    """Audit bronze-hot for partitions at risk of expiry without archive.

    Flags partitions with partition_date < today-85 (5 days before 90-day expiry)
    that are NOT in bronze_archive_log.
    """
    today = date.today()
    hot_catalog = context.resources.iceberg_catalog_hot
    pg_conn = context.resources.pg_forge

    at_risk = find_at_risk_partitions(hot_catalog, pg_conn, today)

    context.add_output_metadata({
        "at_risk_partition_count": MetadataValue.int(len(at_risk)),
        "cutoff_date": MetadataValue.text((today - timedelta(days=85)).isoformat()),
    })

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
